from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import SessionLocal
from services.ce_pe_service import get_ce_pe_screener, get_advanced_options_screener, get_group_advanced_options_screener
try:
    from services.covered_call_service import get_covered_call_analysis, get_master_reference_positions
except ImportError:
    from services.covered_call_service import get_covered_call_analysis, get_master_reference_positions

router = APIRouter(tags=["CE/PE Screener"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/ce-pe-screener/{user_id}")
def ce_pe_screener(user_id: int, db: Session = Depends(get_db)):
    """
    Section A — Basic covered-call / CSP screener.
    OHLC + option chain fetched live (~15-20 sec).
    """
    try:
        data = get_ce_pe_screener(user_id)
        return {"status": "success", "rows": len(data), "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/advanced-options-screener/{user_id}")
def advanced_options_screener(user_id: int, db: Session = Depends(get_db)):
    """
    Section B — Full 8-step advanced options screener.
    Includes: holdings + futures exposure, live spot, 1M/3M/52W OHLC,
    position-aware signals (SQUARE_OFF / ROLLOVER / CORRECTION / FRESH / HOLD),
    nearest + far-month option chain with delta & Prob-OTM, corp-event alerts.
    Heavy call — allow 30-40 sec.
    """
    try:
        data = get_advanced_options_screener(user_id)
        return {"status": "success", "rows": len(data), "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
    
    
@router.get("/advanced-options-screener/group/{group_id}")
def group_advanced_options_screener(group_id: int, db: Session = Depends(get_db)):
    """
    Section B aggregated across all members of a group.
 
    Returns one row per F&O-eligible underlying with:
      • Group-aggregate columns (total qty, lots, pending, spot, OHLC, signal,
        nearest + far-month option chain with IV / Δ / Prob-OTM)
      • Per-member dynamic columns:
            {label}_eq_qty     — equity shares held by this member
            {label}_fut_qty    — FUT shares (+ long / - short); null if none
            {label}_sold_ce    — compact sold-CE strikes, e.g. "2,800 May25"
            {label}_sold_pe    — compact sold-PE strikes
            {label}_bought_ce  — compact bought-CE strikes
            {label}_bought_pe  — compact bought-PE strikes
      • conflict_alert — detects distributed straddles / covered calls across accounts
      • lot_distribution — human-readable per-member qty breakdown
 
    Market data (spot, OHLC, option chain, IV) is fetched once per unique
    underlying regardless of how many members hold it.
 
    Heavy call — allow 30-50 sec depending on group size and number of underlyings.
    """
    try:
        data = get_group_advanced_options_screener(group_id)
        return {"status": "success", "rows": len(data), "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/covered-call-analysis/{user_id}")
def covered_call_analysis(user_id: int, db: Session = Depends(get_db)):
    """
    Returns three tables for the F&O tab:
      covered_calls      — Sold CE + matching holding/FUT
      uncovered          — Holding/FUT with no sold CE yet
      correction_module  — Any position with loss > ₹10,000
    """
    try:
        data = get_covered_call_analysis(user_id)
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/master-reference-positions/{requesting_account_id}")
def master_reference_positions(requesting_account_id: int, db: Session = Depends(get_db)):
    """
    For child accounts (account_id > master).
    Returns Account 1 (master) positions that are NOT covered calls there —
    i.e. positions the child account can use as reference signals.
    """
    try:
        data = get_master_reference_positions(requesting_account_id)
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}