from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["Manual Entries"])

class EquityTxn(BaseModel):
    symbol: str
    company_name: str = ""
    exchange: str = "NSE"
    isin: str = ""
    segment: str = "EQ"
    trade_date: str        # YYYY-MM-DD
    quantity: float
    price: float
    trade_type: str        # BUY, SELL, TRANSFER_IN, etc.
    brokerage: float = 0
    tax_charges: float = 0
    broker: str = "Manual"
    remarks: str = "Manual entry"

class FnoTxn(BaseModel):
    underlying: str
    instrument_type: str   # FUT, CE, PE
    exchange: str = "NSE"
    expiry_date: str
    strike_price: float = 0
    trade_date: str
    trade_type: str        # BUY, SELL
    quantity: float
    price: float
    brokerage: float = 0
    tax_charges: float = 0
    broker: str = "Manual"
    remarks: str = "Manual entry"

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/manual/equity")
def add_equity(txn: EquityTxn, user_id: int, db: Session = Depends(get_db)):
    try:
        db.execute(
            text("""
                INSERT INTO transactions (user_id, symbol, company_name, exchange, isin, segment,
                    trade_date, quantity, price, trade_type, brokerage, tax_charges, broker, source_file, remarks)
                VALUES (:uid, :sym, :comp, :exch, :isin, :seg, :tdate, :qty, :px, :tt,
                        :brok, :tax, :brk, '__manual__', :rem)
            """),
            {"uid": user_id, "sym": txn.symbol.upper(), "comp": txn.company_name or txn.symbol,
             "exch": txn.exchange, "isin": txn.isin, "seg": txn.segment, "tdate": txn.trade_date,
             "qty": txn.quantity, "px": txn.price, "tt": txn.trade_type,
             "brok": txn.brokerage, "tax": txn.tax_charges, "brk": txn.broker, "rem": txn.remarks}
        )
        db.commit()
        # Recalculate derived tables
        from services.engine import recalculate_derived
        recalculate_derived(user_id, db)
        return {"status": "success", "message": f"Added {txn.trade_type} {txn.symbol} qty {txn.quantity}"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}

@router.post("/manual/fno")
def add_fno(txn: FnoTxn, user_id: int, db: Session = Depends(get_db)):
    try:
        sym = f"{txn.underlying}_{txn.instrument_type}_{int(txn.strike_price)}_{txn.expiry_date[:7]}"
        db.execute(
            text("""
                INSERT INTO fno_transactions (user_id, symbol, underlying, exchange, instrument_type,
                    expiry_date, strike_price, trade_date, trade_type, quantity, price, brokerage,
                    tax_charges, broker, source_file, remarks)
                VALUES (:uid, :sym, :und, :exch, :itype, :exp, :strike, :tdate, :tt, :qty, :px,
                        :brok, :tax, :brk, '__manual__', :rem)
            """),
            {"uid": user_id, "sym": sym, "und": txn.underlying, "exch": txn.exchange,
             "itype": txn.instrument_type, "exp": txn.expiry_date, "strike": txn.strike_price,
             "tdate": txn.trade_date, "tt": txn.trade_type, "qty": txn.quantity, "px": txn.price,
             "brok": txn.brokerage, "tax": txn.tax_charges, "brk": txn.broker, "rem": txn.remarks}
        )
        db.commit()
        # Recalculate F&O P&L
        from services.fno_engine import rebuild_fno_pnl
        rebuild_fno_pnl(user_id, db)
        return {"status": "success", "message": f"Added {txn.trade_type} {sym}"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}

@router.delete("/manual/equity/{txn_id}")
def delete_equity(txn_id: int, user_id: int, db: Session = Depends(get_db)):
    try:
        db.execute(text("DELETE FROM transactions WHERE id=:id AND user_id=:uid AND source_file='__manual__'"),
                   {"id": txn_id, "uid": user_id})
        db.commit()
        from services.engine import recalculate_derived
        recalculate_derived(user_id, db)
        return {"status": "deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.delete("/manual/fno/{txn_id}")
def delete_fno(txn_id: int, user_id: int, db: Session = Depends(get_db)):
    try:
        db.execute(text("DELETE FROM fno_transactions WHERE id=:id AND user_id=:uid AND source_file='__manual__'"),
                   {"id": txn_id, "uid": user_id})
        db.commit()
        from services.fno_engine import rebuild_fno_pnl
        rebuild_fno_pnl(user_id, db)
        return {"status": "deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}