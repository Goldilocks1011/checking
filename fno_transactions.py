from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import SessionLocal

router = APIRouter(tags=["F&O Transactions"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/fno/transactions/{user_id}")
def get_fno_transactions(user_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM fno_transactions WHERE user_id = :uid ORDER BY trade_date DESC, id DESC"),
        {"uid": user_id}
    ).fetchall()
    return [dict(row._mapping) for row in rows]