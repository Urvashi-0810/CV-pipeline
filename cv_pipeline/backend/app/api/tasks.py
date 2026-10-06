"""
Background task runner for the API.

Fetching mail, extracting 200 CVs or scoring 50 candidates takes minutes - far too
long for one HTTP request. So the API starts the work in a background thread and
immediately returns a task id. The React UI then polls GET /api/tasks/{id} to show
a progress bar and the final result.

"Lanes" stop conflicting work from running at the same time:
  * lane "pipeline"  - fetch / extract (both write data/cvs + candidates.xlsx) -> one at a time
  * lane "job:<slug>" - job-fit for that job -> one at a time per job

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

from app.core.logger import get_logger

log = get_logger("tasks")


@dataclass
class Task:
    id: str
    kind: str
    lane: str
    params: dict
    status: str = "queued"          # queued | running | done | error
    done: int = 0
    total: int = 0
    current: str = ""
    result: Any = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class LaneBusy(Exception):
    def __init__(self, task: Task):
        self.task = task
        super().__init__(f"A '{task.kind}' task is already {task.status} ({task.id})")


class TaskManager:
    def __init__(self, max_threads: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="task")
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, lane: str, fn: Callable[..., Any], params: Optional[dict] = None) -> Task:
        """fn(progress) is called in a thread; progress(done, total, current) updates the task."""
        with self._lock:
            for t in self._tasks.values():
                if t.lane == lane and t.status in ("queued", "running"):
                    raise LaneBusy(t)
            task = Task(id=uuid.uuid4().hex[:12], kind=kind, lane=lane, params=params or {})
            self._tasks[task.id] = task

        def progress(done: int, total: int, current: str = "") -> None:
            task.done, task.total, task.current = done, total, current

        def run():
            task.status = "running"
            try:
                task.result = fn(progress)
                task.status = "done"
            except Exception as e:  # noqa: BLE001
                task.status, task.error = "error", str(e)
                log.error("Task %s (%s) failed: %s\n%s", task.id, kind, e, traceback.format_exc())
            finally:
                task.finished_at = datetime.now().isoformat(timespec="seconds")

        self._pool.submit(run)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list(self, limit: int = 50) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]


tasks = TaskManager()
