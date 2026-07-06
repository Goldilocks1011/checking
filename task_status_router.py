"""
task_status_router.py
======================
Frontend polls GET /task-status/{user_id} to know if a background job
(upload, auto-populate, delete, etc.) is currently running for this user,
so it can show a "⏳ Processing…" badge instead of letting the user
fire a second overlapping request.
"""
from fastapi import APIRouter
from typing import Optional
from services.task_status import get_status

router = APIRouter(tags=["Task Status"])


@router.get("/task-status/{user_id}")
def task_status(user_id: int, task_name: Optional[str] = None):
    """
    Without task_name: returns all tracked tasks for this user, e.g.
      { "upload_equity": {"status": "running", ...}, "auto_populate": {"status": "idle"} }
    With task_name: returns just that one task's status dict.
    """
    return get_status(user_id, task_name)