"""
Background task runner for the API.

Fetching mail, extracting 200 CVs or scoring 50 candidates takes minutes - far too
long for one HTTP request. So the API starts the work in a background thread and
immediately returns a task id. The React UI then polls GET /api/tasks/{id} to show
a progress bar and the final result.

"Lanes" stop conflicting work from running at the same time:
  * lane "pipeline"  - fetch / extract (both write data/cvs + candidates.xlsx) -> one at a time
  * lane "job:<slug>" - job-fit for that job -> one at a time per job

A task submitted with queue=True waits its turn in the lane instead of being refused
(manual uploads do this). Queued lanes have their own single worker, so a waiting task
doesn't hold up job-fit tasks on the shared pool.

Tasks live in memory (they're lost when the server restarts - results are already
saved to disk by the pipeline steps themselves, so nothing important is lost).
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from app.core.control import RunControl, reset_control, use_control
from app.core.logger import get_logger

log = get_logger("tasks")

QUEUED_LANES = {"pipeline"}
WAITING_MESSAGE = "Waiting for the current run to finish"
ACTIVE = ("queued", "running", "paused")


class TaskNotControllable(Exception):
    pass


@dataclass
class Task:
    id: str
    kind: str
    lane: str
    params: dict
    status: str = "queued"          # queued | running | paused | done | cancelled | error
    done: int = 0
    total: int = 0
    current: str = ""
    result: Any = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    controllable: bool = False      # can be paused, resumed and cancelled
    pause_requested: bool = False
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _was_cancelled(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("cancelled")) or any(_was_cancelled(v) for v in result.values() if isinstance(v, dict))
    return False


class LaneBusy(Exception):
    def __init__(self, task: Task):
        self.task = task
        super().__init__(f"A '{task.kind}' task is already {task.status} ({task.id})")


class TaskManager:
    def __init__(self, max_threads: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="task")
        self._lane_pools = {
            lane: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"lane-{lane}") for lane in QUEUED_LANES
        }
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, lane: str, fn: Callable[..., Any], params: Optional[dict] = None,
               queue: bool = False, controllable: bool = False) -> Task:
        """fn(progress) is called in a thread; progress(done, total, current) updates the task.

        With queue=True in a queued lane, the task waits for its turn rather than raising LaneBusy.
        A controllable task can be paused, resumed and cancelled at the work's checkpoints.
        """
        with self._lock:
            active = next((t for t in self._tasks.values() if t.lane == lane and t.status in ACTIVE), None)
            if active and not (queue and lane in QUEUED_LANES):
                raise LaneBusy(active)
            task = Task(id=uuid.uuid4().hex[:12], kind=kind, lane=lane, params=params or {},
                        current=WAITING_MESSAGE if active else "", controllable=controllable)
            self._tasks[task.id] = task

        def on_state(state: str) -> None:
            task.status = state
            if state == "running":
                task.pause_requested = False

        task.control = RunControl(on_state) if controllable else None   # not a field: kept out of to_dict()

        def progress(done: int, total: int, current: str = "") -> None:
            task.done, task.total, task.current = done, total, current

        def run():
            task.status = "running"
            task.current = ""
            task.started_at = datetime.now().isoformat(timespec="seconds")
            token = use_control(task.control)
            try:
                task.result = fn(progress)
                task.status = "cancelled" if _was_cancelled(task.result) else "done"
            except Exception as e:  # noqa: BLE001
                task.status, task.error = "error", str(e)
                task.exception = e          # not a dataclass field, so it stays out of to_dict()
                log.error("Task %s (%s) failed: %s\n%s", task.id, kind, e, traceback.format_exc())
            finally:
                reset_control(token)
                task.pause_requested = False
                task.finished_at = datetime.now().isoformat(timespec="seconds")

        task.future = self._lane_pools.get(lane, self._pool).submit(run)
        return task

    def _controlled(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if not task.controllable or task.status not in ("running", "paused"):
            raise TaskNotControllable(f"This {task.kind} task can't be paused or stopped now ({task.status}).")
        return task

    def pause(self, task_id: str) -> Task:
        task = self._controlled(task_id)
        task.pause_requested = True
        task.control.pause()
        return task

    def resume(self, task_id: str) -> Task:
        task = self._controlled(task_id)
        task.pause_requested = False
        task.control.resume()
        return task

    def cancel(self, task_id: str) -> Task:
        task = self._controlled(task_id)
        task.cancel_requested = True
        task.pause_requested = False
        task.control.cancel()
        return task

    def run_exclusive(self, kind: str, lane: str, fn: Callable[[], Any], timeout: float = 600) -> Any:
        """Run fn now, holding the lane so nothing else starts in it meanwhile.

        Raises LaneBusy if the lane is in use, or fn's own error. Used for deleting candidates.
        """
        task = self.submit(kind, lane, lambda progress: fn())
        task.future.result(timeout=timeout)
        if task.status == "error":
            raise task.exception
        return task.result

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list(self, limit: int = 50) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]


tasks = TaskManager()
