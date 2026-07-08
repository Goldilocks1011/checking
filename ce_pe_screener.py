from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.services.ce_pe_service import (
    get_ce_pe_screener,
    get_advanced_options_screener,
    get_group_advanced_options_screener,
)
import asyncio

try:
    from backend.services.covered_call_service import (
        get_covered_call_analysis,
        get_master_reference_positions,
    )
except ImportError:
    from backend.services.covered_call_service import (
        get_covered_call_analysis,
        get_master_reference_positions,
    )

router = APIRouter(tags=["CE/PE Screener"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/ce-pe-screener/{user_id}")
async def ce_pe_screener(user_id: int, db: Session = Depends(get_db)):
    """
    Section A — Basic covered-call / CSP screener.
    OHLC + option chain fetched live (~15-20 sec).
    Runs in a background thread so other users aren't blocked.
    """
    try:
        data = await asyncio.to_thread(get_ce_pe_screener, user_id)
        return {"status": "success", "rows": len(data), "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/advanced-options-screener/{user_id}")
async def advanced_options_screener(user_id: int, db: Session = Depends(get_db)):
    """
    Section B — Full 8-step advanced options screener.
    Heavy call — allow 30-40 sec. Runs in a background thread.
    """
    try:
        data = await asyncio.to_thread(get_advanced_options_screener, user_id)
        return {"status": "success", "rows": len(data), "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/advanced-options-screener/group/{group_id}")
async def group_advanced_options_screener(group_id: int, db: Session = Depends(get_db)):
    """
    Section B aggregated across all members of a group.
    Heavy call — allow 30-50 sec. Runs in a background thread.
    """
    try:
        data = await asyncio.to_thread(get_group_advanced_options_screener, group_id)
        return {"status": "success", "rows": len(data), "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/covered-call-analysis/{user_id}")
async def covered_call_analysis(user_id: int, db: Session = Depends(get_db)):
    """
    Returns three tables for the F&O tab:
      covered_calls      — Sold CE + matching holding/FUT
      uncovered          — Holding/FUT with no sold CE yet
      correction_module  — Any position with loss > ₹10,000
    Runs in a background thread.
    """
    try:
        data = await asyncio.to_thread(get_covered_call_analysis, user_id)
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/master-reference-positions/{requesting_account_id}")
async def master_reference_positions(
    requesting_account_id: int, db: Session = Depends(get_db)
):
    """
    For child accounts (account_id > master).
    Returns Account 1 (master) positions that are NOT covered calls there —
    i.e. positions the child account can use as reference signals.
    Runs in a background thread.
    """
    try:
        data = await asyncio.to_thread(
            get_master_reference_positions, requesting_account_id
        )
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}
