from fastapi import APIRouter, Depends, Query
from typing import List
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import SessionLocal
from services.tax_harvest_service import run_harvest_multi
import asyncio

router = APIRouter(tags=["Tax Harvest"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# single‑user legacy endpoint – keep or remove
@router.post("/tax-harvest/{user_id}")
async def tax_harvest(user_id: int, start: str, end: str, db: Session = Depends(get_db)):
    # quick wrapper for single user
    user_row = db.execute(text("SELECT username FROM users WHERE id=:uid"), {"uid": user_id}).first()
    if user_row:
        accounts = {user_row.username: user_id}
    else:
        accounts = {str(user_id): user_id}
    return await asyncio.to_thread(run_harvest_multi, accounts, start, end)

# new multi‑user endpoint
@router.post("/tax-harvest")
async def tax_harvest_multi(
    user_ids: List[int] = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    db: Session = Depends(get_db)
):
    accounts = {}
    for uid in user_ids:
        user_row = db.execute(text("SELECT username FROM users WHERE id=:uid"), {"uid": uid}).first()
        accounts[user_row.username if user_row else str(uid)] = uid
    return await asyncio.to_thread(run_harvest_multi, accounts, start, end)