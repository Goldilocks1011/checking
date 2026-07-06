from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
from services.holdings_engine import (
    get_ca_aware_holdings,
    get_ca_aware_holding_lots,
    get_ca_event_log,
)
import pandas as pd
from services.engine import get_fy_holdings, get_holding_lots
from datetime import date
from typing import Optional
router = APIRouter(tags=["Holdings"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/holdings/ca-aware/{user_id}")
def ca_aware_holdings(user_id: int):
    df = get_ca_aware_holdings(user_id)
    return df.to_dict(orient="records") if not df.empty else []

@router.get("/holdings/ca-lots/{user_id}")
def ca_aware_lots(user_id: int):
    df = get_ca_aware_holding_lots(user_id)
    return df.to_dict(orient="records") if not df.empty else []

@router.get("/holdings/ca-events/{user_id}")
def ca_event_log(user_id: int):
    df = get_ca_event_log(user_id)
    return df.to_dict(orient="records") if not df.empty else []

@router.get("/holdings/{user_id}")
def get_holdings(user_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM holdings WHERE user_id = :uid AND segment = 'EQ' ORDER BY symbol"),
        {"uid": user_id}
    ).fetchall()
    return [dict(row._mapping) for row in rows]

@router.get("/holdings/fy/{user_id}")
def historical_holdings(user_id: int, fy_end: Optional[str] = None, db: Session = Depends(get_db)):
    if not fy_end:
        # default to current FY
        today = date.today()
        y = today.year if today.month > 3 else today.year - 1
        fy_end = f"{y+1}-03-31"
    df = get_fy_holdings(user_id, fy_end)
    return df.to_dict(orient="records") if not df.empty else []

@router.get("/holdings/lots/{user_id}")
def holding_lots(user_id: int, db: Session = Depends(get_db)):
    df = get_holding_lots(user_id)
    return df.to_dict(orient="records") if not df.empty else []