from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import SessionLocal
import asyncio

router = APIRouter(tags=["Stats"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/stats/{user_id}")
async def get_user_stats(user_id: int, db: Session = Depends(get_db)):
    def _fetch():
        h = db.execute(
            text("SELECT COUNT(*), COALESCE(SUM(total_invested),0) FROM holdings WHERE user_id=:uid AND segment='EQ'"),
            {"uid": user_id}
        ).first()
        p = db.execute(
            text("SELECT COALESCE(SUM(gross_pnl),0), COALESCE(SUM(tax_amount),0) FROM pnl WHERE user_id=:uid"),
            {"uid": user_id}
        ).first()
        i = db.execute(
            text("SELECT COALESCE(COUNT(*),0), COALESCE(SUM(gross_pnl),0) FROM intraday WHERE user_id=:uid"),
            {"uid": user_id}
        ).first()
        t = db.execute(
            text("SELECT COUNT(*) FROM transactions WHERE user_id=:uid"),
            {"uid": user_id}
        ).first()

        return {
            "stocks_held": h[0] or 0,
            "total_invested": float(h[1]) if h[1] else 0.0,
            "realized_pnl": float(p[0]) if p[0] else 0.0,
            "tax_due": float(p[1]) if p[1] else 0.0,
            "intraday_trades": i[0] or 0,
            "intraday_pnl": float(i[1]) if i[1] else 0.0,
            "total_txns": t[0] or 0,
        }

    return await asyncio.to_thread(_fetch)