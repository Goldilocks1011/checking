from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User
from pydantic import BaseModel
from sqlalchemy import text
from dependencies.auth import get_current_account


router = APIRouter(tags=["Users"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class UserCreate(BaseModel):
    username: str
    broker: str

class UserOut(BaseModel):
    id: int
    username: str
    broker: str
    created_at: datetime

    class Config:
        from_attributes = True

@router.post("/users/", response_model=UserOut)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    new_user = User(username=user.username, broker=user.broker)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.get("/users/")
def list_users(account_id: int = Depends(get_current_account), db: Session = Depends(get_db)):
    return db.query(User).filter(User.account_id == account_id).all()

@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    # 1. Delete all rows that reference this user (foreign key dependencies)
    db.execute(text("DELETE FROM user_stock_symbol_mapping WHERE user_id = :uid"), {"uid": user_id})
    # 2. Optional – clean up other user data to keep the database tidy
    db.execute(text("DELETE FROM transactions WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM holdings WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM pnl WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM intraday WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM fno_transactions WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM fno_open_positions WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM fno_pnl WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM unmatched_symbols WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM ledger_entries WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM corporate_actions WHERE user_id = :uid"), {"uid": user_id})
    # 3. Remove the user itself
    db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    db.commit()
    return {"status": "deleted"}