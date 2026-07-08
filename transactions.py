from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database import SessionLocal
from backend.dependencies.auth import get_current_account, get_portfolio_user
from backend.models import User
import asyncio

router = APIRouter(tags=["Transactions"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/transactions/{user_id}")
async def get_transactions(user_id: int,
                     db: Session = Depends(get_db),
                     _: User = Depends(get_portfolio_user)):
    """Get all transactions (backward compatible - returns all)"""
    def _fetch():
        rows = db.execute(
            text("SELECT * FROM transactions WHERE user_id=:uid ORDER BY trade_date DESC"),
            {"uid": user_id}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)


@router.get("/transactions/{user_id}/fy/{fy_end_date}")
async def get_transactions_by_fy(user_id: int, 
                                fy_end_date: str,
                                db: Session = Depends(get_db),
                                _: User = Depends(get_portfolio_user)):
    """
    Get transactions for a specific financial year.
    fy_end_date should be in format 'YYYY-MM-DD' (e.g., '2025-03-31')
    """
    def _fetch():
        rows = db.execute(
            text("""
                SELECT * FROM transactions 
                WHERE user_id=:uid AND trade_date <= :fy_end 
                ORDER BY trade_date DESC
            """),
            {"uid": user_id, "fy_end": fy_end_date}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)


@router.get("/transactions/{user_id}/fy-range/{fy_start}/{fy_end}")
async def get_transactions_by_fy_range(user_id: int, 
                                      fy_start: str, 
                                      fy_end: str,
                                      db: Session = Depends(get_db),
                                      _: User = Depends(get_portfolio_user)):
    """
    Get transactions within a financial year range.
    fy_start: format 'YYYY-MM-DD' (e.g., '2024-04-01')
    fy_end: format 'YYYY-MM-DD' (e.g., '2025-03-31')
    """
    def _fetch():
        rows = db.execute(
            text("""
                SELECT * FROM transactions 
                WHERE user_id=:uid 
                  AND trade_date >= :fy_start 
                  AND trade_date <= :fy_end 
                ORDER BY trade_date DESC
            """),
            {"uid": user_id, "fy_start": fy_start, "fy_end": fy_end}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)


@router.get("/transactions/{user_id}/segment/{segment}")
async def get_transactions_by_segment(user_id: int,
                                    segment: str,
                                    db: Session = Depends(get_db),
                                    _: User = Depends(get_portfolio_user)):
    """Get transactions filtered by segment (e.g., 'EQ', 'FNO')"""
    def _fetch():
        rows = db.execute(
            text("""
                SELECT * FROM transactions 
                WHERE user_id=:uid AND segment=:seg 
                ORDER BY trade_date DESC
            """),
            {"uid": user_id, "seg": segment}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)


@router.get("/transactions/{user_id}/segment/{segment}/fy/{fy_end_date}")
async def get_transactions_by_segment_fy(user_id: int,
                                        segment: str,
                                        fy_end_date: str,
                                        db: Session = Depends(get_db),
                                        _: User = Depends(get_portfolio_user)):
    """Get transactions filtered by segment and FY end date"""
    def _fetch():
        rows = db.execute(
            text("""
                SELECT * FROM transactions 
                WHERE user_id=:uid 
                  AND segment=:seg 
                  AND trade_date <= :fy_end 
                ORDER BY trade_date DESC
            """),
            {"uid": user_id, "seg": segment, "fy_end": fy_end_date}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)