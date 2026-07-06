from fastapi import APIRouter, File, UploadFile, Form, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
from services.fno_engine import process_fno_file
from io import BytesIO
import asyncio
import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["F&O Upload"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/upload/fno")
async def upload_fno(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    broker: str = Form(...),
    file_type: str = Form("FNO"),
    db: Session = Depends(get_db)
):
    # Read raw bytes once
    file_bytes = await file.read()
    # Reset stream for the parser
    await file.seek(0)

    file_bytes = await file.read()
    buf = BytesIO(file_bytes)
    buf.name = file.filename

    # Run heavy parsing + dividend backfill + P&L rebuild in a background thread
    result = await asyncio.to_thread(process_fno_file, buf, user_id, broker, file.filename)

    try:
        db.execute(
            text("UPDATE processed_files SET file_content = :content WHERE user_id = :uid AND filename = :fn"),
            {"content": file_bytes, "uid": user_id, "fn": file.filename}
        )
        db.commit()
    except Exception as e:
        logger.error(f"Could not save F&O file content: {e}", exc_info=True)

    return result