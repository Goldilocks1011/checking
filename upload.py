from fastapi import APIRouter, File, UploadFile, Form, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database import SessionLocal
from backend.services.engine import process_file
from backend.services.engine import recalculate_derived
from backend.services.task_status import start_task, finish_task
from io import BytesIO
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Upload"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/upload/equity")
async def upload_equity(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    broker: str = Form(...),
    file_type: str = Form("EQ"),
    db: Session = Depends(get_db),
):
    if not start_task(user_id, "upload_equity", f"Processing '{file.filename}'..."):
        return {
            "status": "busy",
            "message": "An equity upload is already processing for this user. Please wait.",
        }

    try:
        file_bytes = await file.read()

        if broker.upper() != "IIFL":
            existing = db.execute(
                text(
                    "SELECT id FROM processed_files WHERE user_id = :uid AND filename = :fn AND file_type = :ft"
                ),
                {"uid": user_id, "fn": file.filename, "ft": file_type},
            ).first()
            if existing:
                finish_task(user_id, "upload_equity")
                return {
                    "status": "skipped",
                    "message": f"'{file.filename}' already processed as {file_type}",
                }

        buf = BytesIO(file_bytes)
        buf.name = file.filename

        result = await asyncio.to_thread(process_file, buf, user_id, broker, file_type)

        try:
            db.execute(
                text(
                    "UPDATE processed_files SET file_content = :content WHERE user_id = :uid AND filename = :fn"
                ),
                {"content": file_bytes, "uid": user_id, "fn": file.filename},
            )
            db.commit()
        except Exception as e:
            logger.info(f"Could not save file content: {e}")

        finish_task(user_id, "upload_equity")
        return result

    except Exception as e:
        logger.error(f"[UploadEquity] failed for user {user_id}: {e}", exc_info=True)
        finish_task(user_id, "upload_equity", error=str(e))
        return {"status": "error", "message": f"Upload failed: {e}"}


@router.get("/upload/history/{user_id}")
def get_upload_history(user_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT id, filename, records_added, processed_at, file_type FROM processed_files WHERE user_id = :uid ORDER BY processed_at DESC"
        ),
        {"uid": user_id},
    ).fetchall()
    return [dict(row._mapping) for row in rows]


@router.get("/upload/download/{user_id}/{filename:path}")
def download_file(user_id: int, filename: str, db: Session = Depends(get_db)):
    row = db.execute(
        text(
            "SELECT file_content FROM processed_files WHERE user_id = :uid AND filename = :fn"
        ),
        {"uid": user_id, "fn": filename},
    ).first()
    if not row or not row.file_content:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(
        content=row.file_content,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
