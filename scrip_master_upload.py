from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
from services.stock_master_service import auto_populate, update_custom_name, get_user_stock_grid
from pydantic import BaseModel
from fastapi import HTTPException
from models import User
from services.task_status import start_task, finish_task
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stock Master"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/stock-master/auto-populate/{user_id}")
async def trigger_auto_populate(user_id: int, db: Session = Depends(get_db)):
    """
    Guarded + threaded so:
      - clicking it twice in a row is rejected with a clear message
        instead of racing and crashing
      - it never blocks other users' requests
      - any internal error is caught and returned as JSON, not a 500
    """
    if not start_task(user_id, "auto_populate", "Auto-populating stock master..."):
        return {"status": "busy", "message": "Auto-populate is already running for this user. Please wait."}

    try:
        result = await asyncio.to_thread(auto_populate, user_id)
        finish_task(user_id, "auto_populate")
        return result
    except Exception as e:
        logger.error(f"[AutoPopulate] failed for user {user_id}: {e}", exc_info=True)
        finish_task(user_id, "auto_populate", error=str(e))
        return {"status": "error", "message": f"Auto-populate failed: {e}"}

@router.get("/stock-master/grid/{user_id}")
def stock_master_grid(user_id: int):
    return get_user_stock_grid(user_id)

@router.get("/stock-master/unmatched/{user_id}")
def get_unmatched(user_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM unmatched_symbols WHERE user_id=:uid AND resolved=0 ORDER BY broker, raw_symbol"),
        {"uid": user_id}
    ).fetchall()
    return [dict(row._mapping) for row in rows]

@router.post("/stock-master/link")
def link_symbol(user_id: int, raw_symbol: str, broker: str, isin: str, db: Session = Depends(get_db)):
    existing = db.execute(text("SELECT isin FROM stock_master_mapping WHERE isin=:isin"), {"isin": isin}).first()
    if not existing:
        db.execute(
            text("INSERT INTO stock_master_mapping (isin, standard_name, canonical_symbol, fno_available, lot_size) VALUES (:isin, :name, :can, 0, 0)"),
            {"isin": isin, "name": raw_symbol, "can": raw_symbol}
        )

    db.execute(
        text("""
            INSERT INTO user_stock_symbol_mapping (user_id, isin, broker, symbol)
            VALUES (:uid, :isin, :br, :sym)
            ON DUPLICATE KEY UPDATE symbol = VALUES(symbol)
        """), {"uid": user_id, "isin": isin, "br": broker, "sym": raw_symbol}
    )

    db.execute(
        text("UPDATE unmatched_symbols SET resolved=1, resolved_isin=:isin WHERE user_id=:uid AND raw_symbol=:sym AND broker=:br"),
        {"uid": user_id, "sym": raw_symbol, "br": broker, "isin": isin}
    )
    db.commit()
    return {"message": f"Linked {raw_symbol} to {isin}"}

class RenameRequest(BaseModel):
    isin: str
    new_name: str

@router.put("/stock-master/rename")
def rename_stock(data: RenameRequest, db: Session = Depends(get_db)):
    ok = update_custom_name(data.isin, data.new_name)
    if ok:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Rename failed")

@router.post("/stock-master/rebuild-all")
async def rebuild_all_stock_master(db: Session = Depends(get_db)):
    if not start_task(0, "rebuild_all", "Rebuilding stock master for all users..."):
        return {"status": "busy", "message": "A rebuild is already running."}

    try:
        db.execute(text("DELETE FROM stock_master_mapping"))
        db.execute(text("DELETE FROM unmatched_symbols"))
        db.commit()

        def _rebuild_all():
            users = db.query(User).all()
            for u in users:
                try:
                    auto_populate(u.id)
                except Exception as e:
                    logger.warning(f"[RebuildAll] auto_populate failed for user {u.id}: {e}")
            return len(users)

        count = await asyncio.to_thread(_rebuild_all)
        finish_task(0, "rebuild_all")
        return {"status": "rebuilt", "users_processed": count}
    except Exception as e:
        finish_task(0, "rebuild_all", error=str(e))
        return {"status": "error", "message": str(e)}

@router.get("/stock-master/unresolved-holdings/{user_id}")
def get_unresolved_holdings(user_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT h.symbol,
                   MAX(h.company_name) AS company_name,
                   SUM(h.quantity) AS quantity
            FROM holdings h
            LEFT JOIN user_stock_symbol_mapping usm
                ON usm.user_id = :uid AND usm.symbol = h.symbol
            WHERE h.user_id = :uid
              AND h.segment = 'EQ'
              AND usm.id IS NULL
            GROUP BY h.symbol
        """), {"uid": user_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]

@router.post("/stock-master/refresh-all")
async def refresh_all_assets(db: Session = Depends(get_db)):
    if not start_task(0, "refresh_all", "Refreshing F&O info for all stocks..."):
        return {"status": "busy", "message": "A refresh is already running."}

    def _do_refresh():
        from services.nse_data_service import get_fno_info_from_nse
        rows = db.execute(text("SELECT isin, canonical_symbol FROM stock_master_mapping")).fetchall()
        updated = 0
        for r in rows:
            fno_avail, lot_sz = get_fno_info_from_nse(r.canonical_symbol or "")
            db.execute(
                text("UPDATE stock_master_mapping SET fno_available=:fno, lot_size=:lot, updated_at=NOW() WHERE isin=:isin"),
                {"fno": 1 if fno_avail else 0, "lot": lot_sz, "isin": r.isin}
            )
            updated += 1
        db.commit()
        return updated

    try:
        updated = await asyncio.to_thread(_do_refresh)
        finish_task(0, "refresh_all")
        return {"refreshed": updated}
    except Exception as e:
        finish_task(0, "refresh_all", error=str(e))
        return {"status": "error", "message": str(e)}