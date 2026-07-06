from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
from dependencies.auth import get_current_account, get_portfolio_user
from models import User
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
    def _fetch():
        rows = db.execute(
            text("SELECT * FROM transactions WHERE user_id=:uid ORDER BY trade_date DESC"),
            {"uid": user_id}
        ).fetchall()
        return [dict(row._mapping) for row in rows]

    return await asyncio.to_thread(_fetch)