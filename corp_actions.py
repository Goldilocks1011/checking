from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.services.corp_actions_service import (
    seed_from_transactions,
    get_corporate_actions,
    add_manual_corp_action,
    sync_nse_for_user,
)
from pydantic import BaseModel
from typing import Optional
import asyncio

router = APIRouter(tags=["Corporate Actions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/corp-actions/seed/{user_id}")
async def seed(user_id: int, db: Session = Depends(get_db)):
    return await asyncio.to_thread(seed_from_transactions, user_id)


@router.get("/corp-actions/{user_id}")
def list_corp_actions(user_id: int, db: Session = Depends(get_db)):
    return get_corporate_actions(user_id)


class ManualCA(BaseModel):
    symbol: str
    isin: Optional[str] = ""
    company_name: Optional[str] = ""
    action_type: str
    ex_date: str
    record_date: Optional[str] = None
    action_details: Optional[dict] = {}
    notes: Optional[str] = ""


@router.post("/corp-actions/manual/{user_id}")
def add_manual(user_id: int, data: ManualCA, db: Session = Depends(get_db)):
    return add_manual_corp_action(user_id, data.dict())


@router.post("/corp-actions/sync-nse/{user_id}")
async def sync_nse(user_id: int, db: Session = Depends(get_db)):
    """
    Fetches corporate actions from NSE for every symbol the user holds.
    Loops over many symbols with a small delay each — can take a while
    for large portfolios, so it runs in a background thread.
    """
    return await asyncio.to_thread(sync_nse_for_user, user_id)
