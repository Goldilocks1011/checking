"""
fno_engine.py  — modified to support dividend adjustment engine.

Changes vs original:
  1. process_fno_file now calls backfill_past_adjustments() before P&L rebuild.
  2. P&L rebuild now calls rebuild_fno_pnl_with_synthetic() instead of rebuild_fno_pnl().
  3. Original rebuild_fno_pnl() kept intact for backward-compat (used by old callers).
"""
import io
from sqlalchemy import text
from database import SessionLocal
from models import FnoTransaction, FnoOpenPosition, FnoPnl, ProcessedFile
from sqlalchemy.exc import IntegrityError
from collections import defaultdict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def process_fno_file(uploaded_file, user_id: int, broker: str, original_filename: str) -> dict:
    filename   = original_filename
    file_bytes = uploaded_file.read() if hasattr(uploaded_file, "read") else uploaded_file

    # ----- 1. Parse -----
    try:
        if broker == "5paisa":
            from parsers.fivepaisa_fno import parse as f_parse
            txns     = f_parse(io.BytesIO(file_bytes), broker)
            open_pos = []
        elif broker == "IIFL":
            from parsers.iifl_fno import parse as i_parse
            txns, open_pos = i_parse(io.BytesIO(file_bytes), broker)
        elif broker == "Zerodha":
            from parsers.zerodha_fno import parse as z_parse
            txns, open_pos = z_parse(io.BytesIO(file_bytes), broker)
        else:
            return {"status": "error", "message": f"Unknown broker: {broker}"}
    except Exception as e:
        return {"status": "error", "message": f"Parse error: {e}"}

    if not txns:
        return {"status": "error", "message": "No F&O transactions parsed"}

    # ----- 2. Insert into DB -----
    db = SessionLocal()
    try:
        # IIFL uses the same Trade Listing file for both EQ and FNO uploads.
        if broker.upper() != "IIFL":
            existing = db.query(ProcessedFile).filter_by(
                user_id=user_id, filename=filename, file_type="FNO"
            ).first()
            if existing:
                db.close()
                return {"status": "skipped", "message": f"'{filename}' already processed as FNO"}

        inserted = 0
        for txn in txns:
            try:
                new_txn = FnoTransaction(
                    user_id=user_id,
                    symbol=txn["symbol"],
                    underlying=txn["underlying"],
                    exchange=txn.get("exchange", "NSE"),
                    instrument_type=txn["instrument_type"],
                    expiry_date=txn.get("expiry_date", ""),
                    strike_price=txn.get("strike_price", 0),
                    trade_date=txn["trade_date"],
                    trade_type=txn["trade_type"],
                    quantity=txn["quantity"],
                    price=txn["price"],
                    brokerage=txn.get("brokerage", 0),
                    tax_charges=txn.get("tax_charges", 0),
                    broker=txn.get("broker", broker),
                    source_file=filename,
                    remarks=txn.get("remarks", ""),
                )
                db.add(new_txn)
                db.flush()
                inserted += 1
            except IntegrityError:
                db.rollback()
                continue

        db.execute(
            text("""
                INSERT INTO processed_files (user_id, filename, records_added, file_type)
                VALUES (:uid, :fn, :rec, 'FNO')
                ON DUPLICATE KEY UPDATE records_added = :rec
            """),
            {"uid": user_id, "fn": filename, "rec": inserted},
        )
        db.commit()

        # Clear old open positions for this broker
        db.execute(
            text("DELETE FROM fno_open_positions WHERE user_id=:uid AND broker=:br"),
            {"uid": user_id, "br": broker},
        )
        for p in open_pos:
            db.execute(
                text("""
                    INSERT INTO fno_open_positions
                        (user_id, symbol, underlying, exchange, instrument_type,
                         expiry_date, strike_price, open_qty, avg_price,
                         closing_price, unrealized_pnl, as_of_date,
                         trade_date, broker, source_file)
                    VALUES
                        (:uid, :sym, :und, :exch, :itype, :exp, :strike,
                         :qty, :avg, :close, :upnl, :asof, :tdate, :broker, :src)
                """),
                {
                    "uid":    user_id,
                    "sym":    p["symbol"],
                    "und":    p["underlying"],
                    "exch":   p.get("exchange", "NSE"),
                    "itype":  p["instrument_type"],
                    "exp":    p.get("expiry_date", ""),
                    "strike": p.get("strike_price", 0),
                    "qty":    p["open_qty"],
                    "avg":    p.get("avg_price", 0),
                    "close":  p.get("closing_price", 0),
                    "upnl":   p.get("unrealized_pnl", 0),
                    "asof":   p.get("as_of_date", ""),
                    "tdate":  p.get("trade_date", ""),
                    "broker": p.get("broker", broker),
                    "src":    filename,
                },
            )
        db.commit()

        # ----- 3. NEW: Backfill past pending adjustments before P&L rebuild -----
        backfill_summary = {"auto_applied": 0, "user_uploaded": 0}
        try:
            from services.fno_dividend_adjustment_service import backfill_past_adjustments
            backfill_summary = backfill_past_adjustments(user_id, db)
            logger.info(
                f"[FNO Upload] Backfill result for user {user_id}: "
                f"auto_applied={backfill_summary['auto_applied']} "
                f"user_uploaded={backfill_summary['user_uploaded']}"
            )
        except Exception as bf_err:
            # Non-fatal — log and continue with normal rebuild
            logger.error(f"[FNO Upload] backfill_past_adjustments error: {bf_err}", exc_info=True)

        # ----- 4. Rebuild F&O P&L (with synthetic merge) ----------------------
        try:
            from services.fno_dividend_adjustment_service import rebuild_fno_pnl_with_synthetic
            rebuild_fno_pnl_with_synthetic(user_id, db)
        except Exception as rb_err:
            logger.error(f"[FNO Upload] rebuild_fno_pnl_with_synthetic failed: {rb_err}", exc_info=True)
            # Fallback to original rebuild so P&L is never left blank
            rebuild_fno_pnl(user_id, db)

        adj_note = ""
        if backfill_summary["auto_applied"] or backfill_summary["user_uploaded"]:
            adj_note = (
                f" | Dividend adj: {backfill_summary['auto_applied']} auto-applied, "
                f"{backfill_summary['user_uploaded']} from your file"
            )

        return {
            "status":           "success",
            "broker_detected":  broker,
            "message":          (
                f"✅ F&O '{filename}' — {inserted} trades imported"
                + (f", {len(open_pos)} open positions" if open_pos else "")
                + adj_note
            ),
            "inserted":         inserted,
            "backfill":         backfill_summary,
        }
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": f"DB error: {e}"}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Original rebuild_fno_pnl  (kept for backward-compat / fallback)
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_fno_pnl(user_id: int, db) -> None:
    """FIFO match F&O trades per contract key → populate fno_pnl.
    Original implementation — does NOT include synthetic transactions.
    Kept as fallback; prefer rebuild_fno_pnl_with_synthetic.
    """
    db.execute(text("DELETE FROM fno_pnl WHERE user_id=:uid"), {"uid": user_id})
    db.commit()

    rows = db.execute(
        text("""
            SELECT * FROM fno_transactions
            WHERE user_id=:uid
            ORDER BY expiry_date ASC, trade_date ASC, id ASC
        """),
        {"uid": user_id},
    ).fetchall()

    if not rows:
        return

    buy_lots: dict[tuple, list] = defaultdict(list)

    for r in rows:
        key = (
            r.underlying.strip().upper(),
            r.instrument_type.strip().upper(),
            str(r.expiry_date or "").strip(),
            float(r.strike_price or 0),
            str(r.broker or ""),
        )
        qty   = float(r.quantity)
        price = float(r.price)
        tdate = r.trade_date

        if r.trade_type == "BUY":
            buy_lots[key].append({"date": tdate, "price": price, "remaining": qty})

        elif r.trade_type == "SELL":
            rem = qty
            while rem > 0 and buy_lots[key]:
                lot   = buy_lots[key][0]
                match = min(lot["remaining"], rem)
                gross = (price - lot["price"]) * match
                db.execute(
                    text("""
                        INSERT INTO fno_pnl
                            (user_id, symbol, underlying, exchange, instrument_type,
                             expiry_date, strike_price, buy_date, sell_date,
                             buy_price, sell_price, quantity, gross_pnl, broker)
                        VALUES
                            (:uid, :sym, :und, :exch, :itype, :exp, :strike,
                             :bdate, :sdate, :bprice, :sprice, :qty, :pnl, :broker)
                    """),
                    {
                        "uid":    user_id,
                        "sym":    r.symbol,
                        "und":    r.underlying,
                        "exch":   r.exchange,
                        "itype":  r.instrument_type,
                        "exp":    r.expiry_date,
                        "strike": r.strike_price,
                        "bdate":  lot["date"],
                        "sdate":  tdate,
                        "bprice": lot["price"],
                        "sprice": price,
                        "qty":    match,
                        "pnl":    round(gross, 2),
                        "broker": r.broker,
                    },
                )
                lot["remaining"] -= match
                rem             -= match
                if lot["remaining"] <= 0:
                    buy_lots[key].pop(0)

    db.commit()