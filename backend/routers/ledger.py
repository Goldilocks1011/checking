"""
ledger.py  — FastAPI router
Updated: added broker Form param and /ledger/periods endpoint
"""
from fastapi import APIRouter, File, UploadFile, Form, Depends
from sqlalchemy.orm import Session
from database import SessionLocal
from services.ledger_service import process_ledger_file, get_ledger_entries, get_ledger_periods

router = APIRouter(tags=['Ledger'])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post('/ledger/upload')
async def upload_ledger(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    broker: str = Form('auto'),   # ← NEW: "5paisa" | "IIFL" | "Zerodha" | "auto"
    db: Session = Depends(get_db)
):
    """
    Upload a broker ledger file.
    Pass broker='auto' to let the service detect from filename,
    or explicitly pass broker='IIFL' / '5paisa' / 'Zerodha'.
    """
    result = process_ledger_file(
        file.file, user_id, file.filename, broker=broker
    )
    return result


@router.get('/ledger/{user_id}')
def get_ledger(user_id: int, db: Session = Depends(get_db)):
    """Return all ledger entries for a user, newest first."""
    return get_ledger_entries(user_id)


@router.get('/ledger/periods/{user_id}')
def get_periods(user_id: int, db: Session = Depends(get_db)):
    """Return per-upload period summaries (opening/closing balance per file)."""
    return get_ledger_periods(user_id)