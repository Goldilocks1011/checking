from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database import SessionLocal
import asyncio

router = APIRouter(tags=["F&O P&L"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/fno/pnl/{user_id}")
async def get_fno_pnl(user_id: int, db: Session = Depends(get_db)):
    def _fetch():
        rows = db.execute(
            text("SELECT * FROM fno_pnl WHERE user_id=:uid ORDER BY sell_date DESC"),
            {"uid": user_id}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)