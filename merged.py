from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from typing import List
from database import SessionLocal
from sqlalchemy.orm import Session
import asyncio

router = APIRouter(tags=["Merged"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/merged/transactions")
async def merged_transactions(user_ids: List[int] = Query(...), db: Session = Depends(get_db)):
    def _fetch():
        placeholders = ",".join(str(uid) for uid in user_ids)
        rows = db.execute(
            text(f"""
                SELECT t.*, u.username as user_name
                FROM transactions t
                JOIN users u ON u.id = t.user_id
                WHERE t.user_id IN ({placeholders})
                ORDER BY trade_date DESC
            """)
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    return await asyncio.to_thread(_fetch)

@router.get("/merged/pnl")
async def merged_pnl(user_ids: List[int] = Query(...), db: Session = Depends(get_db)):
    def _fetch():
        placeholders = ",".join(str(uid) for uid in user_ids)
        rows = db.execute(
            text(f"""
                SELECT p.*, u.username as user_name
                FROM pnl p
                JOIN users u ON u.id = p.user_id
                WHERE p.user_id IN ({placeholders})
                ORDER BY sell_date DESC
            """)
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    return await asyncio.to_thread(_fetch)

@router.get("/merged/intraday")
async def merged_intraday(user_ids: List[int] = Query(...), db: Session = Depends(get_db)):
    def _fetch():
        placeholders = ",".join(str(uid) for uid in user_ids)
        rows = db.execute(
            text(f"""
                SELECT i.*, u.username as user_name
                FROM intraday i
                JOIN users u ON u.id = i.user_id
                WHERE i.user_id IN ({placeholders})
                ORDER BY trade_date DESC
            """)
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    return await asyncio.to_thread(_fetch)

@router.get("/merged/fno_positions")
async def merged_fno_positions(user_ids: List[int] = Query(...), db: Session = Depends(get_db)):
    def _fetch():
        placeholders = ",".join(str(uid) for uid in user_ids)
        rows = db.execute(
            text(f"""
                SELECT f.*, u.username as user_name
                FROM fno_open_positions f
                JOIN users u ON u.id = f.user_id
                WHERE f.user_id IN ({placeholders})
                ORDER BY underlying, expiry_date
            """)
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    return await asyncio.to_thread(_fetch)

@router.get("/merged/fno_pnl")
async def merged_fno_pnl(user_ids: List[int] = Query(...), db: Session = Depends(get_db)):
    def _fetch():
        placeholders = ",".join(str(uid) for uid in user_ids)
        rows = db.execute(
            text(f"""
                SELECT fp.*, u.username as user_name
                FROM fno_pnl fp
                JOIN users u ON u.id = fp.user_id
                WHERE fp.user_id IN ({placeholders})
                ORDER BY sell_date DESC
            """)
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    return await asyncio.to_thread(_fetch)