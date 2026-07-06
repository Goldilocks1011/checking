"""
task_status.py
================
Lightweight in-memory tracker for long-running background tasks per user.

Lets the frontend ask "is something already running for this user?" so:
  - the UI can show a "⏳ Processing in background" badge
  - a second click on the same button is rejected instead of launching
    an overlapping job that can crash or corrupt data

Not persisted to DB — resets on server restart. That's fine, it's a
UI/safety hint, not a source of truth for actual data.
"""
from __future__ import annotations
import threading
import time

_lock = threading.Lock()
_tasks: dict[str, dict] = {}   # key = "{user_id}:{task_name}"


def start_task(user_id: int, task_name: str, message: str = "") -> bool:
    """
    Mark a task as running for this user.
    Returns False if this exact task is already running — caller should
    refuse to start a duplicate and tell the user to wait.
    """
    key = f"{user_id}:{task_name}"
    with _lock:
        existing = _tasks.get(key)
        if existing and existing.get("status") == "running":
            return False
        _tasks[key] = {
            "status": "running",
            "started_at": time.time(),
            "message": message or f"{task_name} in progress...",
            "error": None,
        }
        return True


def finish_task(user_id: int, task_name: str, error: str | None = None) -> None:
    key = f"{user_id}:{task_name}"
    with _lock:
        if key in _tasks:
            _tasks[key]["status"] = "error" if error else "done"
            _tasks[key]["error"] = error
            _tasks[key]["finished_at"] = time.time()


def get_status(user_id: int, task_name: str | None = None) -> dict:
    """
    If task_name given -> status dict for that task only.
    If task_name is None -> dict of all tasks for this user.
    """
    with _lock:
        if task_name:
            key = f"{user_id}:{task_name}"
            return dict(_tasks.get(key, {"status": "idle"}))
        prefix = f"{user_id}:"
        return {
            k[len(prefix):]: dict(v)
            for k, v in _tasks.items()
            if k.startswith(prefix)
        }


def is_running(user_id: int, task_name: str) -> bool:
    key = f"{user_id}:{task_name}"
    with _lock:
        t = _tasks.get(key)
        return bool(t and t.get("status") == "running")