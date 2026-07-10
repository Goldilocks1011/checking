"""
Holdings Reconciliation Router
================================
Endpoints for uploading broker holdings and reconciling with transaction-derived holdings.

Endpoints:
  POST /holdings/reconcile/upload — upload broker holdings file
  GET /holdings/reconcile/diff/{user_id} — get reconciliation diff
  POST /holdings/reconcile/apply — apply user-confirmed corrections
"""
from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Form, Body
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database import SessionLocal
from backend.services.holdings_reconciliation import compare_holdings, apply_corrections
from backend.services.task_status import start_task, finish_task
from io import BytesIO
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Holdings Reconciliation"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def detect_holdings_broker(filename: str) -> str | None:
    """Detect broker from holdings filename."""
    fn = filename.lower()
    if "zerodha" in fn or "holdings" in fn:
        return "Zerodha"
    if "iifl" in fn or "portfolio" in fn:
        return "IIFL"
    if "5paisa" in fn or "paisa" in fn:
        return "5paisa"
    return None


@router.post("/holdings/reconcile/upload")
async def upload_holdings(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    broker: str = Form(...),          # 👈 NEW required field
    db: Session = Depends(get_db),
):
    """
    Upload a broker holdings statement (CSV/Excel).
    Auto-detects broker from filename.
    Returns reconciliation diff (matched, extra, missing).
    """
    if not start_task(user_id, "holdings_reconcile", f"Uploading {file.filename}..."):
        return {
            "status": "busy",
            "message": "A holdings reconciliation is already in progress for this user.",
        }

    try:
        file_bytes = await file.read()
        if broker not in ("Zerodha", "IIFL", "5paisa"):
            raise HTTPException(status_code=400, )

        # Select parser based on broker
        if broker == "Zerodha":
            from backend.parsers.zerodha_holdings import parse
        elif broker == "IIFL":
            from backend.parsers.iifl_holdings import parse
        elif broker == "5paisa":
            from backend.parsers.fivepaisa_holdings import parse
        else:
            finish_task(user_id, "holdings_reconcile")
            raise HTTPException(
                status_code=400,
                detail=f"Unknown broker: {broker}. Supported: Zerodha, IIFL, 5paisa."
            )

        # Parse the file
        buf = BytesIO(file_bytes)
        buf.name = file.filename
        broker_holdings = await asyncio.to_thread(parse, buf, broker)

        # Compare with transaction-derived holdings
        diff = await asyncio.to_thread(compare_holdings, user_id, broker_holdings)

        finish_task(user_id, "holdings_reconcile")
        return {
            "status": "success",
            "broker_detected": broker,
            "file_name": file.filename,
            "diff": diff,
        }

    except Exception as e:
        logger.error(f"Holdings upload failed for user {user_id}: {e}", exc_info=True)
        finish_task(user_id, "holdings_reconcile", error=str(e))
        raise HTTPException(status_code=400, detail=f"Upload failed: {e}")


@router.get("/holdings/reconcile/diff/{user_id}")
async def get_reconciliation_diff(user_id: int, db: Session = Depends(get_db)):
    """
    Return the last reconciliation diff (if any cached in session).
    This is a read-only endpoint; use the upload endpoint to run a fresh comparison.
    """
    # For now, return empty — the upload endpoint provides the diff directly.
    # In a real app, you'd cache the diff in a DB table or Redis.
    return {"status": "no_diff_cached", "message": "Upload a holdings file to generate a diff."}


@router.post("/holdings/reconcile/apply")
async def apply_reconciliation_corrections(
    user_id: int = Form(...),
    corrections: str = Form(...),  # JSON string from frontend
    db: Session = Depends(get_db),
):
    """
    Apply user-confirmed corrections from reconciliation UI.

    corrections (JSON): [
        {
            "symbol": "TCS",
            "isin": "INE467B01014",
            "quantity": 10,
            "source": "IPO|BONUS|SPLIT|MERGER|DEMERGER|TRANSFER|MANUAL_BUY|SELL|IGNORE",
            "price": 2000 (optional)
        },
        ...
    ]
    """
    import json

    if not start_task(user_id, "apply_reconciliation", "Applying corrections..."):
        return {
            "status": "busy",
            "message": "A reconciliation correction is already in progress.",
        }

    try:
        # Parse corrections JSON
        try:
            corr_list = json.loads(corrections) if isinstance(corrections, str) else corrections
        except json.JSONDecodeError:
            finish_task(user_id, "apply_reconciliation")
            raise HTTPException(status_code=400, detail="Invalid corrections JSON")

        # Apply corrections
        result = await asyncio.to_thread(apply_corrections, user_id, corr_list)

        finish_task(user_id, "apply_reconciliation")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Apply corrections failed for user {user_id}: {e}", exc_info=True)
        finish_task(user_id, "apply_reconciliation", error=str(e))
        raise HTTPException(status_code=500, detail=f"Apply failed: {e}")