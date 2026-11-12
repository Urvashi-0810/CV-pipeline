"""
Pause, resume and cancel for long pipeline runs (fetch, extract, run both, S3 retry).

The task runner gives each controllable run a RunControl. Long loops call checkpoint() at safe
points: between CVs, and between batches of mail. A pause waits there until resumed; a cancel
stops there, keeping everything finished so far. The code doing the work never needs the
control passed in: it's found through a context variable set for the task's thread.
"""
from __future__ import annotations

import contextvars
import threading
from typing import Callable, Optional


class RunCancelled(Exception):
    """Raised at a checkpoint after Cancel. Loops catch it and stop cleanly."""


class RunControl:
    def __init__(self, on_state: Callable[[str], None] = lambda state: None):
        self._resume = threading.Event()
        self._resume.set()
        self.cancel_requested = False
        self._paused_since_asked = False
        self._on_state = on_state

    @property
    def pause_requested(self) -> bool:
        return not self._resume.is_set()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self.cancel_requested = True
        self._resume.set()                      # a paused run wakes up to stop

    def checkpoint(self) -> None:
        if self.cancel_requested:
            raise RunCancelled()
        if not self._resume.is_set():
            self._paused_since_asked = True
            self._on_state("paused")
            self._resume.wait()
            self._on_state("running")
            if self.cancel_requested:
                raise RunCancelled()

    def was_paused(self) -> bool:
        """True once after a pause, so the fetcher knows to check its mail connection."""
        paused, self._paused_since_asked = self._paused_since_asked, False
        return paused


_current: contextvars.ContextVar[Optional[RunControl]] = contextvars.ContextVar("run_control", default=None)


def use_control(control: Optional[RunControl]):
    """Set the control for the code running in this thread. Returns a token for reset_control()."""
    return _current.set(control)


def reset_control(token) -> None:
    _current.reset(token)


def checkpoint() -> None:
    """A safe point to pause or stop. Does nothing outside a controllable run (e.g. the CLI)."""
    control = _current.get()
    if control is not None:
        control.checkpoint()


def was_paused() -> bool:
    control = _current.get()
    return control.was_paused() if control is not None else False
