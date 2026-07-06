from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
import asyncio

router = APIRouter(tags=["Intraday"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/intraday/{user_id}")
async def get_intraday(user_id: int, db: Session = Depends(get_db)):
    def _fetch():
        rows = db.execute(
            text("SELECT * FROM intraday WHERE user_id=:uid ORDER BY trade_date DESC"),
            {"uid": user_id}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)