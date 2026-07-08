from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database import SessionLocal
import asyncio
from datetime import datetime

router = APIRouter(tags=["PNL"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/pnl/{user_id}")
async def get_pnl(user_id: int, db: Session = Depends(get_db)):
    """Get all realized P&L (backward compatible - returns all)"""
    def _fetch():
        rows = db.execute(
            text("SELECT * FROM pnl WHERE user_id=:uid ORDER BY sell_date DESC"),
            {"uid": user_id}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)


@router.get("/pnl/{user_id}/fy/{fy_end_date}")
async def get_pnl_by_fy(user_id: int, fy_end_date: str, db: Session = Depends(get_db)):
    """
    Get realized P&L for a specific financial year.
    fy_end_date should be in format 'YYYY-MM-DD' (e.g., '2025-03-31')
    """
    def _fetch():
        rows = db.execute(
            text("""
                SELECT * FROM pnl 
                WHERE user_id=:uid AND sell_date <= :fy_end 
                ORDER BY sell_date DESC
            """),
            {"uid": user_id, "fy_end": fy_end_date}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)


@router.get("/pnl/{user_id}/fy-range/{fy_start}/{fy_end}")
async def get_pnl_by_fy_range(user_id: int, fy_start: str, fy_end: str, db: Session = Depends(get_db)):
    """
    Get realized P&L within a financial year range.
    fy_start: format 'YYYY-MM-DD' (e.g., '2024-04-01')
    fy_end: format 'YYYY-MM-DD' (e.g., '2025-03-31')
    """
    def _fetch():
        rows = db.execute(
            text("""
                SELECT * FROM pnl 
                WHERE user_id=:uid 
                  AND sell_date >= :fy_start 
                  AND sell_date <= :fy_end 
                ORDER BY sell_date DESC
            """),
            {"uid": user_id, "fy_start": fy_start, "fy_end": fy_end}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)