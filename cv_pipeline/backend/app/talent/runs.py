"""
Pipeline run history (pipeline_runs), so the Pipeline screen's "last run" summary survives a restart.

Task progress lives in memory (app/api/tasks.py); only each run's start, end and summary are stored.
Without DATABASE_URL nothing is recorded and runs work as before.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, update

from app.core.config import settings
from app.core.logger import get_logger
from app.db.models import PipelineRun
from app.db.session import session_scope

log = get_logger("runs")

# Long lists that don't belong in a summary
DROP = {"saved_files", "placements"}
MAX_ERRORS = 50


def summarise(result: Any) -> Any:
    if isinstance(result, dict):
        out = {}
        for key, value in result.items():
            if key in DROP:
                continue
            if isinstance(value, dict) and key.endswith("errors") and len(value) > MAX_ERRORS:
                value = dict(list(value.items())[:MAX_ERRORS]) | {"…": f"{len(value) - MAX_ERRORS} more"}
            out[key] = summarise(value)
        return out
    return result


def _start(kind: str) -> int | None:
    try:
        with session_scope() as session:
            run = PipelineRun(kind=kind, status="running")
            session.add(run)
            session.flush()
            return run.id
    except Exception:  # noqa: BLE001 - history must never stop a run
        log.exception("Could not record the start of a %s run", kind)
        return None


def _finish(run_id: int | None, status: str, summary: dict) -> None:
    if run_id is None:
        return
    try:
        with session_scope() as session:
            run = session.get(PipelineRun, run_id)
            run.status, run.summary, run.finished_at = status, summary, datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001
        log.exception("Could not record the end of run %s", run_id)


def recorded(kind: str, fn: Callable[[Callable], Any]) -> Callable[[Callable], Any]:
    """Wrap a task function so its start, end and summary are stored."""
    def job(progress):
        run_id = _start(kind) if settings.database_url else None
        try:
            result = fn(progress)
        except Exception as e:
            _finish(run_id, "error", {"error": str(e)[:1000]})
            raise
        summary = summarise(result) if isinstance(result, dict) else {"result": result}
        _finish(run_id, "cancelled" if _cancelled(result) else "done", summary)
        return result
    return job


def _cancelled(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("cancelled")) or any(_cancelled(v) for v in result.values() if isinstance(v, dict))
    return False


def close_interrupted_runs() -> int:
    """Runs still 'running' when the server starts were cut off by a restart."""
    if not settings.database_url:
        return 0
    try:
        with session_scope() as session:
            closed = session.execute(
                update(PipelineRun).where(PipelineRun.status == "running")
                .values(status="error", finished_at=datetime.now(timezone.utc),
                        summary={"error": "The server stopped during this run"}))
            return closed.rowcount
    except Exception:  # noqa: BLE001 - a database problem must not stop the server starting
        log.exception("Could not close interrupted runs")
        return 0


def recent_runs(limit: int = 10) -> list[dict]:
    with session_scope() as session:
        runs = session.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
                               .limit(limit))
        return [{"id": r.id, "kind": r.kind, "status": r.status, "summary": r.summary,
                 "started_at": r.started_at, "finished_at": r.finished_at} for r in runs]
