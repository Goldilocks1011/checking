"""
ledger.py  — FastAPI router
Updated: added broker Form param, /ledger/periods endpoint,
and background-thread processing so uploads don't block other users.
"""

from fastapi import APIRouter, File, UploadFile, Form, Depends
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.services.ledger_service import (
    process_ledger_file,
    get_ledger_entries,
    get_ledger_periods,
)
import asyncio

router = APIRouter(tags=["Ledger"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/ledger/upload")
async def upload_ledger(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    broker: str = Form("auto"),  # ← "5paisa" | "IIFL" | "Zerodha" | "auto"
    db: Session = Depends(get_db),
):
    """
    Upload a broker ledger file.
    Pass broker='auto' to let the service detect from filename,
    or explicitly pass broker='IIFL' / '5paisa' / 'Zerodha'.
    Runs in a background thread so other users aren't blocked.
    """
    file_bytes = await file.read()
    from io import BytesIO

    buf = BytesIO(file_bytes)

    result = await asyncio.to_thread(
        process_ledger_file, buf, user_id, file.filename, broker
    )
    return result


@router.get("/ledger/{user_id}")
def get_ledger(user_id: int, db: Session = Depends(get_db)):
    """Return all ledger entries for a user, newest first."""
    return get_ledger_entries(user_id)


@router.get("/ledger/periods/{user_id}")
def get_periods(user_id: int, db: Session = Depends(get_db)):
    """Return per-upload period summaries (opening/closing balance per file)."""
    return get_ledger_periods(user_id)
