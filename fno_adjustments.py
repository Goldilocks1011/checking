"""
routers/fno_adjustments.py
============================
REST endpoints for the dividend-forced F&O adjustment engine.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import asyncio

from backend.database import SessionLocal
from backend.dependencies.auth import get_current_account
from backend.services.fno_dividend_adjustment_service import (
    detect_pending_adjustments,
    apply_adjustment,
    skip_adjustment,
    mark_user_uploaded,
    get_pending_adjustments,
    get_adjustment_history,
)

router = APIRouter(tags=["F&O Dividend Adjustments"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Detection: runs detection engine + returns PENDING list ──────────────────
@router.get("/fno/adjustments/pending/{user_id}")
async def get_pending(
    user_id: int,
    account_id: int = Depends(get_current_account),
):
    """
    Run detection engine and return pending dividend adjustments.
    This is the heavy call (hits 5paisa API for spot prices).
    Runs in a background thread so other users aren't blocked.
    """
    try:
        return await asyncio.to_thread(detect_pending_adjustments, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Fast read: already-stored PENDING records (no API calls) ─────────────────
@router.get("/fno/adjustments/pending-stored/{user_id}")
def get_pending_stored(
    user_id: int,
    account_id: int = Depends(get_current_account),
):
    """
    Return already-detected PENDING records from DB (no re-detection).
    Fast pure-DB call — no threading needed.
    """
    try:
        return get_pending_adjustments(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Apply a pending adjustment synthetically ─────────────────────────────────
@router.post("/fno/adjustments/apply/{adjustment_id}")
async def apply(
    adjustment_id: int,
    user_id: int,
    account_id: int = Depends(get_current_account),
):
    """
    Apply a dividend adjustment (P&L-neutral synthetic SELL+BUY, then rebuild P&L).
    """
    result = await asyncio.to_thread(apply_adjustment, user_id, adjustment_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ── Skip: user will upload the adjusted trades themselves ─────────────────────
@router.post("/fno/adjustments/skip/{adjustment_id}")
def skip(
    adjustment_id: int,
    user_id: int,
    account_id: int = Depends(get_current_account),
):
    """
    Mark adjustment as SKIPPED. The engine will NOT auto-apply.
    """
    return skip_adjustment(user_id, adjustment_id)


# ── Mark as already handled by uploaded file ─────────────────────────────────
@router.post("/fno/adjustments/mark-uploaded/{adjustment_id}")
def mark_uploaded(
    adjustment_id: int,
    user_id: int,
    account_id: int = Depends(get_current_account),
):
    """
    Mark adjustment as USER_UPLOADED.
    """
    return mark_user_uploaded(user_id, adjustment_id)


# ── Full audit history ────────────────────────────────────────────────────────
@router.get("/fno/adjustments/history/{user_id}")
def get_history(
    user_id: int,
    account_id: int = Depends(get_current_account),
):
    """
    Return full dividend adjustment audit log for a user. Fast pure-DB call.
    """
    try:
        return get_adjustment_history(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
