"""
Portfolio Engine – MySQL version (identical logic to old SQLite project)
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime
import pandas as pd
from sqlalchemy import text
from database import SessionLocal, engine
import logging
logger = logging.getLogger(__name__)

# ---------- Broker detection (unchanged) ----------
def detect_broker(file_bytes: bytes, filename: str) -> str | None:
    fn = filename.lower()
    if "tradebook" in fn:
        return "Zerodha"
    if "trade_listing" in fn or "tradelisting" in fn:
        return "IIFL"
    if "equity_transaction" in fn:
        return "5paisa"
    for engine in ("openpyxl", "xlrd"):
        try:
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None, nrows=15, engine=engine)
            text_content = " ".join(str(v) for row in df.values for v in row if str(v) != "nan").lower()
            if "5paisa" in text_content or "support@5paisa" in text_content:
                return "5paisa"
            if "iifl" in text_content or "iiflcapital" in text_content:
                return "IIFL"
            if "zerodha" in text_content:
                return "Zerodha"
            break
        except Exception:
            continue
    return None


# ---------- File processing ----------
def process_file(uploaded_file, user_id: int, broker: str, file_type: str = 'EQ') -> dict:
    from parsers.zerodha import parse as z_parse
    from parsers.iifl import parse as i_parse
    from parsers.fivepaisa import parse as f_parse

    filename = uploaded_file.name
    file_bytes = uploaded_file.getvalue()

    db = SessionLocal()
    try:
        effective_broker = broker if broker and broker != "Auto-detect" else detect_broker(file_bytes, filename)
        if not effective_broker:
            return {"status": "error", "message": f"Cannot detect broker for '{filename}'"}

        # IIFL uses the same Trade Listing file for both EQ and FNO uploads.
        # Skip the duplicate check for IIFL so both passes are always allowed.
        # For all other brokers, block re-upload of the same (filename, file_type).
        if effective_broker != "IIFL":
            existing = db.execute(
                text("SELECT id FROM processed_files WHERE user_id=:uid AND filename=:fn AND file_type=:ft"),
                {"uid": user_id, "fn": filename, "ft": file_type}
            ).first()
            if existing:
                return {"status": "skipped", "message": f"'{filename}' already processed as {file_type}"}

        buf = io.BytesIO(file_bytes)
        buf.name = filename
        if effective_broker == "Zerodha":
            txns = z_parse(buf, effective_broker)
        elif effective_broker == "IIFL":
            txns = i_parse(buf, effective_broker)
        elif effective_broker == "5paisa":
            txns = f_parse(buf, effective_broker)
        else:
            return {"status": "error", "message": f"Unknown broker: {effective_broker}"}

        if not txns:
            return {"status": "error", "message": "No transactions parsed"}

        inserted = 0
        for t in txns:
            try:
                db.execute(
                    text("""
                        INSERT IGNORE INTO transactions
                        (user_id, symbol, company_name, exchange, isin, segment,
                         trade_date, quantity, price, trade_type,
                         brokerage, tax_charges, broker, source_file, remarks)
                        VALUES (:uid, :sym, :comp, :exch, :isin, :seg,
                                :tdate, :qty, :price, :tt,
                                :brok, :tax, :brk, :src, :rem)
                    """),
                    {
                        "uid": user_id,
                        "sym": t["symbol"],
                        "comp": t.get("company_name", t["symbol"]),
                        "exch": t.get("exchange", "NSE"),
                        "isin": t.get("isin", ""),
                        "seg": t.get("segment", "EQ"),
                        "tdate": t["trade_date"],
                        "qty": t["quantity"],
                        "price": t["price"],
                        "tt": t["trade_type"],
                        "brok": t.get("brokerage", 0),
                        "tax": t.get("tax_charges", 0),
                        "brk": t.get("broker", effective_broker),
                        "src": filename,
                        "rem": t.get("remarks", ""),
                    }
                )
                inserted += 1
            except Exception as e:
                logger.error(f"Insert error: {e}", exc_info=True)
                continue

        db.execute(
            text("""INSERT INTO processed_files (user_id, filename, records_added, file_type)
                    VALUES (:uid, :fn, :rec, :ft)
                    ON DUPLICATE KEY UPDATE records_added = :rec"""),
            {"uid": user_id, "fn": filename, "rec": inserted, "ft": file_type}
        )
        db.commit()

        # Rebuild holdings, P&L, intraday
        recalculate_derived(user_id, db)

        # Enrich ISINs
        #from isin_resolver import enrich_transactions_with_isin
        # Need a raw connection for that – optional, skip for now
        # enrich_transactions_with_isin(db, user_id)

        return {
            "status": "success",
            "broker_detected": effective_broker,
            "message": f"✅ '{filename}' — {inserted} transactions imported",
            "inserted": inserted,
        }
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": f"DB error: {e}"}
    finally:
        db.close()


# ---------- Core FIFO recalculation (exactly like old project) ----------
def recalculate_derived(user_id: int, db):
    """Full FIFO recalculation – deletes and rebuilds holdings, pnl, intraday."""
    logger.info(f"[FIFO] Starting for user {user_id}")
    db.execute(text("DELETE FROM holdings WHERE user_id=:uid"), {"uid": user_id})
    db.execute(text("DELETE FROM pnl WHERE user_id=:uid"), {"uid": user_id})
    db.execute(text("DELETE FROM intraday WHERE user_id=:uid"), {"uid": user_id})
    db.commit()

    # Load all transactions in order
    rows = db.execute(
        text("SELECT * FROM transactions WHERE user_id=:uid ORDER BY trade_date ASC, id ASC"),
        {"uid": user_id}
    ).fetchall()

    if not rows:
        logger.warning("[FIFO] No transactions found")
        return

    buy_lots = defaultdict(list)   # key = symbol only (critical!)
    pnl_rows = []
    intraday_rows = []

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 0: Group transactions by (symbol, date) and apply INTRADAY NETTING
    # ─────────────────────────────────────────────────────────────────────────────
    daily_txns = defaultdict(lambda: {"buy": [], "sell": [], "meta": {}})

    for row in rows:
        symbol = row.symbol
        trade_date = row.trade_date
        trade_type = row.trade_type
        qty = float(row.quantity)
        price = float(row.price)
        
        key = (symbol, trade_date)
        
        meta = {
            "company_name": row.company_name or symbol,
            "isin": row.isin or "",
            "exchange": row.exchange or "NSE",
            "segment": row.segment or "EQ",
            "broker": row.broker or "",
        }
        
        if trade_type in ("BUY", "TRANSFER_IN", "BONUS", "DEMERGER_IN"):
            buy_price = 0.0 if trade_type in ("BONUS", "DEMERGER_IN") else price
            daily_txns[key]["buy"].append({
                "date": trade_date,
                "price": buy_price,
                "qty": qty,
                "type": trade_type,
                **meta
            })
            daily_txns[key]["meta"] = meta
        
        elif trade_type == "SELL":
            daily_txns[key]["sell"].append({
                "date": trade_date,
                "price": price,
                "qty": qty,
            })

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 1: Apply INTRADAY NETTING — offset same-day BUYs vs SELLs
    # ─────────────────────────────────────────────────────────────────────────────
    # After netting, we have:
    #   remaining_buys  = BUY qty not matched to same-day SELL
    #   remaining_sells = SELL qty not matched to same-day BUY
    # Only remaining_sells touch the historical lot queue (FIFO).

    netting_result = defaultdict(lambda: {"buy_lots": [], "sell_qty": 0.0, "meta": {}})

    for (symbol, trade_date), daily_data in daily_txns.items():
        buys = daily_data["buy"]
        sells = daily_data["sell"]
        meta = daily_data["meta"]
        
        # Total quantities for the day
        total_buy_qty = sum(b["qty"] for b in buys)
        total_sell_qty = sum(s["qty"] for s in sells)
        
        # Net the smaller against the larger
        net_buy_qty = max(0, total_buy_qty - total_sell_qty)
        net_sell_qty = max(0, total_sell_qty - total_buy_qty)
        
        # Only REMAINING buys (after netting) go into the lot queue
        if net_buy_qty > 0:
            # Distribute the net buy qty across buy transactions (prioritize earlier buys)
            remaining = net_buy_qty
            for buy in buys:
                if remaining <= 0:
                    break
                allocate = min(remaining, buy["qty"])
                netting_result[symbol]["buy_lots"].append({
                    "date": trade_date,
                    "price": buy["price"],
                    "remaining": allocate,
                    **meta
                })
                remaining -= allocate
        
        # REMAINING sells (after netting) will be applied to historical lots
        netting_result[symbol]["sell_qty"] = net_sell_qty
        netting_result[symbol]["meta"] = meta


    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 2: Process with FIFO using NETTING RESULTS
    # ─────────────────────────────────────────────────────────────────────────────
    buy_lots = defaultdict(list)
    pnl_rows = []
    intraday_rows = []

    # First, add all the netting results (intraday-netted buys)
    for symbol, data in netting_result.items():
        buy_lots[symbol].extend(data["buy_lots"])

    # Now process sells against the netting-aware lot queue
    for (symbol, trade_date), daily_data in daily_txns.items():
        sells = daily_data["sell"]
        if not sells:
            continue
        
        meta = daily_data["meta"]
        net_sell_qty = netting_result[symbol]["sell_qty"]
        
        if net_sell_qty <= 0:
            continue  # This sell was fully netted; skip FIFO application
        
        # Apply only the REMAINING sell quantity to historical lots (FIFO)
        rem_sell = net_sell_qty
        sell_price = sells[0]["price"]  # Use first sell price of the day
        
        if not buy_lots[symbol]:
            # Uncovered sell — all shares are short
            pnl_rows.append(_pnl_row(user_id, symbol, meta, None, trade_date, 0, sell_price, net_sell_qty, None, "SHORT", 0.20))
            continue
        
        while rem_sell > 0 and buy_lots[symbol]:
            lot = buy_lots[symbol][0]
            matched = min(lot["remaining"], rem_sell)
            
            try:
                days = (datetime.strptime(trade_date, "%Y-%m-%d") - datetime.strptime(lot["date"], "%Y-%m-%d")).days
            except Exception:
                days = 0
            
            gross = (sell_price - lot["price"]) * matched
            
            if days == 0:
                intraday_rows.append({
                    "user_id": user_id,
                    "symbol": symbol,
                    "company_name": lot["company_name"],
                    "exchange": meta["exchange"],
                    "segment": meta["segment"],
                    "trade_date": trade_date,
                    "buy_price": lot["price"],
                    "sell_price": sell_price,
                    "quantity": matched,
                    "gross_pnl": round(gross, 2),
                    "broker": meta["broker"],
                })
            else:
                term = "LONG" if days > 365 else "SHORT"
                tax_rate = 0.125 if term == "LONG" else 0.20
                pnl_rows.append(
                    _pnl_row(user_id, symbol, lot, lot["date"], trade_date,
                            lot["price"], sell_price, matched, days, term, tax_rate)
                )
            
            lot["remaining"] -= matched
            rem_sell -= matched
            if lot["remaining"] <= 0:
                buy_lots[symbol].pop(0)
    # ----- Insert holdings -----
    holdings_inserted = 0
    for symbol, lots in buy_lots.items():
        active = [l for l in lots if l["remaining"] > 0]
        if not active:
            continue
        total_qty = sum(l["remaining"] for l in active)
        total_cost = sum(l["price"] * l["remaining"] for l in active)
        avg_price = total_cost / total_qty if total_qty else 0
        first_date = active[0]["date"]
        db.execute(
            text("""
                INSERT INTO holdings
                (user_id, symbol, company_name, exchange, isin, segment,
                 quantity, avg_buy_price, total_invested, first_buy_date)
                VALUES (:uid, :sym, :comp, :exch, :isin, :seg,
                        :qty, :avg, :inv, :first)
            """),
            {
                "uid": user_id,
                "sym": symbol,
                "comp": active[0]["company_name"],
                "exch": active[-1].get("exchange", "NSE"),
                "isin": active[0]["isin"],
                "seg": active[0]["segment"],
                "qty": round(total_qty, 4),
                "avg": round(avg_price, 4),
                "inv": round(total_cost, 2),
                "first": first_date,
            }
        )
        holdings_inserted += 1

    # ----- Insert P&L rows -----
    pnl_inserted = 0
    for row in pnl_rows:
        db.execute(
            text("""
                INSERT INTO pnl
                (user_id, symbol, company_name, isin, exchange, segment,
                 buy_date, sell_date, buy_price, sell_price, quantity, holding_days,
                 term_type, gross_pnl, tax_rate, tax_amount, net_pnl, broker)
                VALUES (:user_id, :symbol, :company_name, :isin, :exchange, :segment,
                        :buy_date, :sell_date, :buy_price, :sell_price, :quantity, :holding_days,
                        :term_type, :gross_pnl, :tax_rate, :tax_amount, :net_pnl, :broker)
            """),
            row
        )
        pnl_inserted += 1

    # ----- Insert intraday rows -----
    intra_inserted = 0
    for row in intraday_rows:
        db.execute(
            text("""
                INSERT INTO intraday
                (user_id, symbol, company_name, exchange, segment,
                 trade_date, buy_price, sell_price, quantity, gross_pnl, broker)
                VALUES (:user_id, :symbol, :company_name, :exchange, :segment,
                        :trade_date, :buy_price, :sell_price, :quantity, :gross_pnl, :broker)
            """),
            row
        )
        intra_inserted += 1

    db.commit()
    logger.info(f"[FIFO] Done: holdings={holdings_inserted}, pnl={pnl_inserted}, intraday={intra_inserted}")


def _pnl_row(user_id, symbol, meta, buy_date, sell_date, buy_price, sell_price, qty, days, term, tax_rate):
    gross = (sell_price - buy_price) * qty
    tax = max(0.0, gross) * tax_rate
    return {
        "user_id": user_id,
        "symbol": symbol,
        "company_name": meta.get("company_name", symbol),
        "isin": meta.get("isin", ""),
        "exchange": meta.get("exchange", ""),
        "segment": meta.get("segment", "EQ"),
        "buy_date": buy_date,
        "sell_date": sell_date,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "quantity": qty,
        "holding_days": days,
        "term_type": term,
        "gross_pnl": round(gross, 2),
        "tax_rate": tax_rate,
        "tax_amount": round(tax, 2),
        "net_pnl": round(gross - tax, 2),
        "broker": meta.get("broker", ""),
    }


# ---------- Query helpers (used by frontend) ----------
def get_holdings(user_id: int) -> pd.DataFrame:
    db = SessionLocal()
    df = pd.read_sql_query(
        text("SELECT * FROM holdings WHERE user_id=:uid AND segment='EQ' ORDER BY symbol"),
        db, params={"uid": user_id}
    )
    db.close()
    return df


def get_transactions(user_id: int, symbol: str = None, trade_type: str = None) -> pd.DataFrame:
    db = SessionLocal()
    sql = "SELECT * FROM transactions WHERE user_id=:uid"
    params = {"uid": user_id}
    if symbol:
        sql += " AND symbol LIKE :sym"
        params["sym"] = f"%{symbol}%"
    if trade_type and trade_type != "All":
        sql += " AND trade_type=:tt"
        params["tt"] = trade_type
    sql += " ORDER BY trade_date DESC, id DESC"
    df = pd.read_sql_query(text(sql), db, params=params)
    db.close()
    return df


def get_pnl(user_id: int, term: str = None) -> pd.DataFrame:
    db = SessionLocal()
    sql = "SELECT * FROM pnl WHERE user_id=:uid"
    params = {"uid": user_id}
    if term and term != "All":
        sql += " AND term_type=:term"
        params["term"] = term
    sql += " ORDER BY sell_date DESC"
    df = pd.read_sql_query(text(sql), db, params=params)
    db.close()
    return df


def get_intraday(user_id: int) -> pd.DataFrame:
    db = SessionLocal()
    df = pd.read_sql_query(
        text("SELECT * FROM intraday WHERE user_id=:uid ORDER BY trade_date DESC"),
        db, params={"uid": user_id}
    )
    db.close()
    return df


def get_quick_stats(user_id: int) -> dict:
    db = SessionLocal()
    h = db.execute(
        text("SELECT COUNT(*), COALESCE(SUM(total_invested),0) FROM holdings WHERE user_id=:uid AND segment='EQ'"),
        {"uid": user_id}
    ).first()
    p = db.execute(
        text("SELECT COALESCE(SUM(gross_pnl),0), COALESCE(SUM(tax_amount),0) FROM pnl WHERE user_id=:uid"),
        {"uid": user_id}
    ).first()
    i = db.execute(
        text("SELECT COUNT(*), COALESCE(SUM(gross_pnl),0) FROM intraday WHERE user_id=:uid"),
        {"uid": user_id}
    ).first()
    t = db.execute(
        text("SELECT COUNT(*) FROM transactions WHERE user_id=:uid"),
        {"uid": user_id}
    ).first()
    db.close()
    return {
        "stocks_held": h[0] or 0,
        "total_invested": h[1] or 0,
        "realized_pnl": p[0] or 0,
        "tax_due": p[1] or 0,
        "intraday_trades": i[0] or 0,
        "intraday_pnl": i[1] or 0,
        "total_txns": t[0] or 0,
    }


def get_holding_lots(user_id: int) -> pd.DataFrame:
    with engine.connect() as conn:
        txns = pd.read_sql_query(
            text("""
                SELECT symbol, company_name, exchange, segment, trade_date,
                       quantity, price, trade_type
                FROM transactions
                WHERE user_id = :uid AND segment = 'EQ'
                ORDER BY trade_date ASC, id ASC
            """),
            conn, params={"uid": user_id}
        )

    if txns.empty:
        return pd.DataFrame()

    # ... keep the rest of the existing logic ...

    buy_lots = defaultdict(list)
    for _, t in txns.iterrows():
        sym = t["symbol"]
        qty = float(t["quantity"])
        price = float(t["price"])
        ttype = t["trade_type"]
        if ttype in ("BUY", "TRANSFER_IN", "BONUS", "DEMERGER_IN"):
            lot_price = 0.0 if ttype in ("BONUS", "DEMERGER_IN") else price
            buy_lots[sym].append({
                "buy_date": t["trade_date"],
                "price": lot_price,
                "remaining": qty,
                "trade_type": ttype,
                "company": t["company_name"],
                "exchange": t["exchange"] or "NSE",
                "segment": t["segment"] or "EQ",
            })
        elif ttype in ("SELL", "TRANSFER_OUT", "MERGER_OUT"):
            rem = qty
            while rem > 0 and buy_lots[sym]:
                lot = buy_lots[sym][0]
                take = min(lot["remaining"], rem)
                lot["remaining"] -= take
                rem -= take
                if lot["remaining"] <= 0:
                    buy_lots[sym].pop(0)

    today = date.today()
    rows = []
    for sym, lots in buy_lots.items():
        for lot in lots:
            if lot["remaining"] <= 0:
                continue
            try:
                buy_dt = datetime.strptime(lot["buy_date"], "%Y-%m-%d").date()
                days = (today - buy_dt).days
            except Exception:
                days = 0
            term = "LONG (>1yr)" if days > 365 else "SHORT (≤1yr)"
            tax_rate = 0.125 if days > 365 else 0.20
            rows.append({
                "Symbol": sym,
                "Company": lot["company"],
                "Exchange": lot["exchange"],
                "Segment": lot["segment"],
                "Lot Type": lot["trade_type"],
                "Buy Date": lot["buy_date"],
                "Days Held": days,
                "Term": term,
                "Qty": round(lot["remaining"], 4),
                "Avg Cost (₹)": round(lot["price"], 2),
                "Invested (₹)": round(lot["price"] * lot["remaining"], 2),
                "Tax Rate": f"{tax_rate*100:.1f}%",
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["Symbol", "Buy Date"])


def get_fy_holdings(user_id: int, fy_end_date: str) -> pd.DataFrame:
    """Holdings as of a given date (e.g. '2025-03-31')."""
    with engine.connect() as conn:
        df = pd.read_sql_query(
            text("""
                SELECT * FROM transactions
                WHERE user_id = :uid AND trade_date <= :asof AND segment = 'EQ'
                ORDER BY trade_date ASC, id ASC
            """),
            conn, params={"uid": user_id, "asof": fy_end_date}
        )

    if df.empty:
        return pd.DataFrame()


    buy_lots = defaultdict(list)
    for _, t in df.iterrows():
        sym = t["symbol"]
        qty = float(t["quantity"])
        price = float(t["price"])
        ttype = t["trade_type"]
        if ttype in ("BUY", "TRANSFER_IN", "BONUS", "DEMERGER_IN"):
            lot_price = 0.0 if ttype in ("BONUS", "DEMERGER_IN") else price
            buy_lots[sym].append({
                "price": lot_price,
                "remaining": qty,
                "company": t["company_name"] or sym,
                "exchange": t["exchange"] or "NSE",
                "segment": t["segment"] or "EQ",
            })
        elif ttype in ("SELL", "TRANSFER_OUT", "MERGER_OUT"):
            rem = qty
            while rem > 0 and buy_lots[sym]:
                lot = buy_lots[sym][0]
                take = min(lot["remaining"], rem)
                lot["remaining"] -= take
                rem -= take
                if lot["remaining"] <= 0:
                    buy_lots[sym].pop(0)

    rows = []
    for sym, lots in buy_lots.items():
        active = [l for l in lots if l["remaining"] > 0]
        if not active:
            continue
        total_qty = sum(l["remaining"] for l in active)
        total_cost = sum(l["price"] * l["remaining"] for l in active)
        rows.append({
            "Symbol": sym,
            "Company": active[0]["company"],
            "Exchange": active[0]["exchange"],
            "Segment": active[0]["segment"],
            "Qty": total_qty,
            "Avg Price (₹)": round(total_cost / total_qty, 2) if total_qty else 0,
            "Invested (₹)": round(total_cost, 2),
        })
    return pd.DataFrame(rows).sort_values("Symbol") if rows else pd.DataFrame()


# ---------- Price fetch (keep using your existing engine_price_fetch) ----------
def fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    from engine_price_fetch import fetch_current_prices as _fetch
    return _fetch(symbols)

def fetch_prices_with_change(symbols: list[str]) -> dict[str, dict]:
    from engine_price_fetch import fetch_prices_with_change as _fetch
    return _fetch(symbols)

def fetch_fno_prices(op_df) -> dict[tuple, float]:
    from engine_price_fetch import fetch_fno_prices as _fetch
    return _fetch(op_df)

# Also keep F&O helpers (get_fno_* etc.) if you need them – they are not shown here but you already have them.