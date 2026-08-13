"""A one-slot background job runner.

Deliberately not Celery. There is exactly one kind of long task here (a
collection sweep), it must never run twice at once, and the whole thing has to
survive being restarted by `docker compose up -d`. A thread and a dict is the
right size for that.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class JobState:
    name: str = ""
    status: str = "idle"          # idle | running | done | failed
    started_at: float = 0.0
    finished_at: float = 0.0
    step: int = 0
    total: int = 0
    detail: str = ""
    result: dict = field(default_factory=dict)
    error: str = ""

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at if self.started_at else 0.0

    @property
    def pct(self) -> float:
        return (self.step / self.total * 100) if self.total else 0.0

    def as_dict(self) -> dict:
        return {
            "name": self.name, "status": self.status, "step": self.step,
            "total": self.total, "detail": self.detail, "pct": round(self.pct, 1),
            "elapsed": round(self.elapsed), "result": self.result,
            "error": self.error,
        }


_state = JobState()
_lock = threading.Lock()


def current() -> JobState:
    return _state


def is_running() -> bool:
    return _state.status == "running"


def start(name: str, fn: Callable[[Callable[[int, int, str], None]], dict]) -> bool:
    """Run `fn` in a thread. `fn` receives a progress callback.

    Returns False if something is already running — the UI shows that rather
    than silently queueing a second sweep at the site.
    """
    global _state
    with _lock:
        if _state.status == "running":
            return False
        _state = JobState(name=name, status="running", started_at=time.time())

    def progress(step: int, total: int, detail: str) -> None:
        _state.step, _state.total, _state.detail = step, total, detail

    def run() -> None:
        try:
            _state.result = fn(progress) or {}
            _state.status = "done"
        except Exception as exc:
            log.exception("job %s failed", name)
            _state.status = "failed"
            _state.error = f"{type(exc).__name__}: {exc}"
        finally:
            _state.finished_at = time.time()

    threading.Thread(target=run, name=f"job-{name}", daemon=True).start()
    return True
