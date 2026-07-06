from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
from dependencies.auth import get_current_account, get_portfolio_user
from models import User

router = APIRouter(tags=["Transactions"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

from dependencies.auth import get_portfolio_user

@router.get("/transactions/{user_id}")
def get_transactions(user_id: int,
                     db: Session = Depends(get_db),
                     _: User = Depends(get_portfolio_user)):
    # same code, now safe
    rows = db.execute(
        text("SELECT * FROM transactions WHERE user_id=:uid ORDER BY trade_date DESC"),
        {"uid": user_id}
    ).fetchall()
    return [dict(row._mapping) for row in rows]
