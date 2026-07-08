"""
Portfolio Engine – MySQL version (identical logic to old SQLite project)
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime
import pandas as pd
from sqlalchemy import text
from backend.database import SessionLocal, engine
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
            df = pd.read_excel(
                io.BytesIO(file_bytes),
                sheet_name=0,
                header=None,
                nrows=15,
                engine=engine,
            )
            text_content = " ".join(
                str(v) for row in df.values for v in row if str(v) != "nan"
            ).lower()
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
def process_file(
    uploaded_file, user_id: int, broker: str, file_type: str = "EQ"
) -> dict:
    from backend.parsers.zerodha import parse as z_parse
    from backend.parsers.iifl import parse as i_parse
    from backend.parsers.fivepaisa import parse as f_parse

    filename = uploaded_file.name
    file_bytes = uploaded_file.getvalue()

    db = SessionLocal()
    try:
        effective_broker = (
            broker
            if broker and broker != "Auto-detect"
            else detect_broker(file_bytes, filename)
        )
        if not effective_broker:
            return {
                "status": "error",
                "message": f"Cannot detect broker for '{filename}'",
            }

        # IIFL uses the same Trade Listing file for both EQ and FNO uploads.
        # Skip the duplicate check for IIFL so both passes are always allowed.
        # For all other brokers, block re-upload of the same (filename, file_type).
        if effective_broker != "IIFL":
            existing = db.execute(
                text(
                    "SELECT id FROM processed_files WHERE user_id=:uid AND filename=:fn AND file_type=:ft"
                ),
                {"uid": user_id, "fn": filename, "ft": file_type},
            ).first()
            if existing:
                return {
                    "status": "skipped",
                    "message": f"'{filename}' already processed as {file_type}",
                }

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
                    },
                )
                inserted += 1
            except Exception as e:
                logger.error(f"Insert error: {e}", exc_info=True)
                continue

        db.execute(
            text(
                """INSERT INTO processed_files (user_id, filename, records_added, file_type)
                    VALUES (:uid, :fn, :rec, :ft)
                    ON DUPLICATE KEY UPDATE records_added = :rec"""
            ),
            {"uid": user_id, "fn": filename, "rec": inserted, "ft": file_type},
        )
        db.commit()

        # Rebuild holdings, P&L, intraday
        recalculate_derived(user_id, db)

        # Enrich ISINs
        # from isin_resolver import enrich_transactions_with_isin
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
    """
    Full chronological FIFO recalculation engine.
    Deletes and rebuilds holdings, pnl, and intraday trades securely.
    """
    logger.info(f"[FIFO] Starting correct daily engine for user {user_id}")
    db.execute(text("DELETE FROM holdings WHERE user_id=:uid"), {"uid": user_id})
    db.execute(text("DELETE FROM pnl WHERE user_id=:uid"), {"uid": user_id})
    db.execute(text("DELETE FROM intraday WHERE user_id=:uid"), {"uid": user_id})
    db.commit()

    # 1. Load all transactions in absolute order of execution
    rows = db.execute(
        text(
            "SELECT * FROM transactions WHERE user_id=:uid ORDER BY trade_date ASC, id ASC"
        ),
        {"uid": user_id},
    ).fetchall()

    if not rows:
        logger.warning("[FIFO] No transactions found to re-calculate.")
        return

    # 2. Group transactions strictly by date to ensure day-by-day processing
    transactions_by_date = defaultdict(lambda: defaultdict(list))
    for row in rows:
        date_str = str(row.trade_date)[:10]
        transactions_by_date[date_str][row.symbol].append(row)

    sorted_dates = sorted(transactions_by_date.keys())

    buy_lots = defaultdict(list)  # Persistent chronological queue per symbol
    pnl_rows = []
    intraday_rows = []

    # 3. Process day-by-day chronologically
    for t_date in sorted_dates:
        day_symbols = transactions_by_date[t_date]

        for symbol, txns in day_symbols.items():
            buys = []
            sells = []

            meta = {
                "company_name": symbol,
                "isin": "",
                "exchange": "NSE",
                "segment": "EQ",
                "broker": "",
            }

            for t in txns:
                meta["company_name"] = t.company_name or symbol
                meta["isin"] = t.isin or ""
                meta["exchange"] = t.exchange or "NSE"
                meta["segment"] = t.segment or "EQ"
                meta["broker"] = t.broker or ""

                qty = float(t.quantity)
                price = float(t.price)

                if t.trade_type in ("BUY", "TRANSFER_IN", "BONUS", "DEMERGER_IN"):
                    # Corporate actions like BONUS enter the cost basis queue at 0 value
                    buy_price = (
                        0.0 if t.trade_type in ("BONUS", "DEMERGER_IN") else price
                    )
                    buys.append(
                        {
                            "qty": qty,
                            "price": buy_price,
                            "type": t.trade_type,
                            "meta": meta.copy(),
                        }
                    )
                elif t.trade_type in ("SELL", "TRANSFER_OUT", "MERGER_OUT"):
                    sells.append(
                        {
                            "qty": qty,
                            "price": price,
                            "type": t.trade_type,
                            "meta": meta.copy(),
                        }
                    )

            total_buy_qty = sum(b["qty"] for b in buys)
            total_sell_qty = sum(s["qty"] for s in sells)

            # A. Calculate Intraday Netting first (speculative volume matching)
            intraday_qty = min(total_buy_qty, total_sell_qty)
            if intraday_qty > 0:
                day_total_buy_val = sum(b["qty"] * b["price"] for b in buys)
                day_avg_buy_price = (
                    day_total_buy_val / total_buy_qty if total_buy_qty > 0 else 0.0
                )

                day_total_sell_val = sum(s["qty"] * s["price"] for s in sells)
                day_avg_sell_price = (
                    day_total_sell_val / total_sell_qty if total_sell_qty > 0 else 0.0
                )

                gross_pnl = (day_avg_sell_price - day_avg_buy_price) * intraday_qty

                intraday_rows.append(
                    {
                        "user_id": user_id,
                        "symbol": symbol,
                        "company_name": meta["company_name"],
                        "exchange": meta["exchange"],
                        "segment": meta["segment"],
                        "trade_date": t_date,
                        "buy_price": round(day_avg_buy_price, 4),
                        "sell_price": round(day_avg_sell_price, 4),
                        "quantity": round(intraday_qty, 4),
                        "gross_pnl": round(gross_pnl, 2),
                        "broker": meta["broker"],
                    }
                )

            # B. Handle Delivery Additions (New remaining lots added to FIFO queue)
            if total_buy_qty > total_sell_qty:
                rem_buy_qty = total_buy_qty - total_sell_qty
                for b in buys:
                    if rem_buy_qty <= 0:
                        break
                    allocated_qty = min(rem_buy_qty, b["qty"])
                    buy_lots[symbol].append(
                        {
                            "date": t_date,
                            "price": b["price"],
                            "remaining": allocated_qty,
                            "company_name": b["meta"]["company_name"],
                            "isin": b["meta"]["isin"],
                            "exchange": b["meta"]["exchange"],
                            "segment": b["meta"]["segment"],
                            "broker": b["meta"]["broker"],
                        }
                    )
                    rem_buy_qty -= allocated_qty

            # C. Handle Delivery Sales (Remaining sales consume past historical queue via FIFO)
            elif total_sell_qty > total_buy_qty:
                rem_sell_qty = total_sell_qty - total_buy_qty
                day_total_sell_val = sum(s["qty"] * s["price"] for s in sells)
                day_avg_sell_price = (
                    day_total_sell_val / total_sell_qty if total_sell_qty > 0 else 0.0
                )

                while rem_sell_qty > 0 and buy_lots[symbol]:
                    lot = buy_lots[symbol][0]
                    matched_qty = min(lot["remaining"], rem_sell_qty)

                    try:
                        days = (
                            datetime.strptime(t_date, "%Y-%m-%d")
                            - datetime.strptime(lot["date"], "%Y-%m-%d")
                        ).days
                    except Exception:
                        days = 0

                    term = "LONG" if days > 365 else "SHORT"
                    tax_rate = 0.125 if term == "LONG" else 0.20

                    pnl_rows.append(
                        _pnl_row(
                            user_id,
                            symbol,
                            meta,
                            lot["date"],
                            t_date,
                            lot["price"],
                            day_avg_sell_price,
                            matched_qty,
                            days,
                            term,
                            tax_rate,
                        )
                    )

                    lot["remaining"] -= matched_qty
                    rem_sell_qty -= matched_qty
                    if lot["remaining"] <= 1e-5:
                        buy_lots[symbol].pop(0)

                if rem_sell_qty > 0:
                    # Catch fallback for short delivery sales without an open matching buy history
                    pnl_rows.append(
                        _pnl_row(
                            user_id,
                            symbol,
                            meta,
                            None,
                            t_date,
                            0.0,
                            day_avg_sell_price,
                            rem_sell_qty,
                            None,
                            "SHORT",
                            0.20,
                        )
                    )

    # 4. Save clean processed states to your DB tables
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
                VALUES (:uid, :sym, :comp, :exch, :isin, :seg, :qty, :avg, :inv, :first)
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
            },
        )
        holdings_inserted += 1

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
            row,
        )

    for row in intraday_rows:
        db.execute(
            text("""
                INSERT INTO intraday
                (user_id, symbol, company_name, exchange, segment,
                 trade_date, buy_price, sell_price, quantity, gross_pnl, broker)
                VALUES (:user_id, :symbol, :company_name, :exchange, :segment,
                        :trade_date, :buy_price, :sell_price, :quantity, :gross_pnl, :broker)
            """),
            row,
        )

    db.commit()
    logger.info(
        f"[FIFO] Complete: holdings={holdings_inserted}, pnl={len(pnl_rows)}, intraday={len(intraday_rows)}"
    )


def _pnl_row(
    user_id,
    symbol,
    meta,
    buy_date,
    sell_date,
    buy_price,
    sell_price,
    qty,
    days,
    term,
    tax_rate,
):
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
        text(
            "SELECT * FROM holdings WHERE user_id=:uid AND segment='EQ' ORDER BY symbol"
        ),
        db,
        params={"uid": user_id},
    )
    db.close()
    return df


def get_transactions(
    user_id: int, symbol: str = None, trade_type: str = None
) -> pd.DataFrame:
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
        db,
        params={"uid": user_id},
    )
    db.close()
    return df


def get_quick_stats(user_id: int) -> dict:
    db = SessionLocal()
    h = db.execute(
        text(
            "SELECT COUNT(*), COALESCE(SUM(total_invested),0) FROM holdings WHERE user_id=:uid AND segment='EQ'"
        ),
        {"uid": user_id},
    ).first()
    p = db.execute(
        text(
            "SELECT COALESCE(SUM(gross_pnl),0), COALESCE(SUM(tax_amount),0) FROM pnl WHERE user_id=:uid"
        ),
        {"uid": user_id},
    ).first()
    i = db.execute(
        text(
            "SELECT COUNT(*), COALESCE(SUM(gross_pnl),0) FROM intraday WHERE user_id=:uid"
        ),
        {"uid": user_id},
    ).first()
    t = db.execute(
        text("SELECT COUNT(*) FROM transactions WHERE user_id=:uid"), {"uid": user_id}
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
            conn,
            params={"uid": user_id},
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
            buy_lots[sym].append(
                {
                    "buy_date": t["trade_date"],
                    "price": lot_price,
                    "remaining": qty,
                    "trade_type": ttype,
                    "company": t["company_name"],
                    "exchange": t["exchange"] or "NSE",
                    "segment": t["segment"] or "EQ",
                }
            )
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
            rows.append(
                {
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
                }
            )
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
            conn,
            params={"uid": user_id, "asof": fy_end_date},
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
            buy_lots[sym].append(
                {
                    "price": lot_price,
                    "remaining": qty,
                    "company": t["company_name"] or sym,
                    "exchange": t["exchange"] or "NSE",
                    "segment": t["segment"] or "EQ",
                }
            )
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
        rows.append(
            {
                "Symbol": sym,
                "Company": active[0]["company"],
                "Exchange": active[0]["exchange"],
                "Segment": active[0]["segment"],
                "Qty": total_qty,
                "Avg Price (₹)": round(total_cost / total_qty, 2) if total_qty else 0,
                "Invested (₹)": round(total_cost, 2),
            }
        )
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


# ========== HELPER: Generate FY list and date ranges ==========
def get_fy_list(current_year: int = None, years_back: int = 10) -> list[dict]:
    """
    Generate list of financial years with their date ranges.
    Returns list like:
    [
        {"fy_label": "FY 2026-27", "start_date": "2026-04-01", "end_date": "2027-03-31"},
        {"fy_label": "FY 2025-26", "start_date": "2025-04-01", "end_date": "2026-03-31"},
        ...
    ]
    """
    if current_year is None:
        current_year = date.today().year

    fy_list = []
    for i in range(years_back):
        fy_year = current_year - i
        fy_start = f"{fy_year}-04-01"
        fy_end = f"{fy_year + 1}-03-31"
        fy_label = f"FY {fy_year}-{str(fy_year+1)[-2:]}"
        fy_list.append(
            {"fy_label": fy_label, "start_date": fy_start, "end_date": fy_end}
        )

    return fy_list


# ========== REALIZED P&L - FY WISE ==========
def get_fy_realized_pnl(user_id: int, fy_end_date: str) -> pd.DataFrame:
    """
    Get realized P&L for a financial year (all trades closed by fy_end_date).
    fy_end_date format: 'YYYY-03-31' (e.g., '2025-03-31')
    """
    with engine.connect() as conn:
        df = pd.read_sql_query(
            text("""
                SELECT * FROM pnl 
                WHERE user_id = :uid AND sell_date <= :fy_end 
                ORDER BY sell_date DESC
            """),
            conn,
            params={"uid": user_id, "fy_end": fy_end_date},
        )

    if df.empty:
        return pd.DataFrame()

    # Format the dataframe for display
    df_display = df[
        [
            "sell_date",
            "symbol",
            "quantity",
            "buy_price",
            "sell_price",
            "gross_pnl",
            "tax_amount",
            "net_pnl",
        ]
    ].copy()
    df_display.columns = [
        "Sell Date",
        "Symbol",
        "Qty",
        "Buy Price",
        "Sell Price",
        "Gross P&L",
        "Tax",
        "Net P&L",
    ]

    return df_display.sort_values("Sell Date", ascending=False).reset_index(drop=True)


def get_fy_realized_pnl_summary(user_id: int, fy_end_date: str) -> dict:
    """
    Get summary statistics for realized P&L for a FY.
    Returns dict with total_gross, total_net, num_trades, winning_trades, etc.
    """
    with engine.connect() as conn:
        df = pd.read_sql_query(
            text("""
                SELECT gross_pnl, net_pnl FROM pnl 
                WHERE user_id = :uid AND sell_date <= :fy_end
            """),
            conn,
            params={"uid": user_id, "fy_end": fy_end_date},
        )

    if df.empty:
        return {
            "total_gross_pnl": 0,
            "total_net_pnl": 0,
            "num_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "avg_profit": 0,
            "avg_loss": 0,
        }

    df["gross_pnl"] = pd.to_numeric(df["gross_pnl"], errors="coerce").fillna(0)
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0)

    winning = df[df["gross_pnl"] > 0]
    losing = df[df["gross_pnl"] < 0]

    return {
        "total_gross_pnl": round(df["gross_pnl"].sum(), 2),
        "total_net_pnl": round(df["net_pnl"].sum(), 2),
        "num_trades": len(df),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "avg_profit": round(winning["gross_pnl"].mean(), 2) if len(winning) > 0 else 0,
        "avg_loss": round(losing["gross_pnl"].mean(), 2) if len(losing) > 0 else 0,
    }


# ========== INTRADAY TRADES - FY WISE ==========
def get_fy_intraday(user_id: int, fy_end_date: str) -> pd.DataFrame:
    """
    Get intraday trades for a financial year.
    fy_end_date format: 'YYYY-03-31' (e.g., '2025-03-31')
    """
    with engine.connect() as conn:
        df = pd.read_sql_query(
            text("""
                SELECT * FROM intraday 
                WHERE user_id = :uid AND trade_date <= :fy_end 
                ORDER BY trade_date DESC
            """),
            conn,
            params={"uid": user_id, "fy_end": fy_end_date},
        )

    if df.empty:
        return pd.DataFrame()

    # Format the dataframe for display
    df_display = df[
        ["trade_date", "symbol", "quantity", "buy_price", "sell_price", "gross_pnl"]
    ].copy()
    df_display.columns = ["Date", "Symbol", "Qty", "Buy (₹)", "Sell (₹)", "P&L (₹)"]

    return df_display.sort_values("Date", ascending=False).reset_index(drop=True)


def get_fy_intraday_summary(user_id: int, fy_end_date: str) -> dict:
    """
    Get summary statistics for intraday trades for a FY.
    """
    with engine.connect() as conn:
        df = pd.read_sql_query(
            text("""
                SELECT gross_pnl FROM intraday 
                WHERE user_id = :uid AND trade_date <= :fy_end
            """),
            conn,
            params={"uid": user_id, "fy_end": fy_end_date},
        )

    if df.empty:
        return {
            "total_intraday_pnl": 0,
            "num_intraday_trades": 0,
            "winning_intraday": 0,
            "losing_intraday": 0,
            "intraday_win_rate": 0,
            "best_intraday": 0,
        }

    df["gross_pnl"] = pd.to_numeric(df["gross_pnl"], errors="coerce").fillna(0)

    winning = len(df[df["gross_pnl"] > 0])
    losing = len(df[df["gross_pnl"] < 0])
    total = len(df)
    win_rate = round((winning / total * 100), 1) if total > 0 else 0

    return {
        "total_intraday_pnl": round(df["gross_pnl"].sum(), 2),
        "num_intraday_trades": total,
        "winning_intraday": winning,
        "losing_intraday": losing,
        "intraday_win_rate": win_rate,
        "best_intraday": round(df["gross_pnl"].max(), 2),
    }


# ========== TRANSACTIONS - FY WISE ==========
def get_fy_transactions(
    user_id: int, fy_end_date: str, segment: str = None
) -> pd.DataFrame:
    """
    Get transactions for a financial year.
    fy_end_date format: 'YYYY-03-31' (e.g., '2025-03-31')
    segment: optional filter (e.g., 'EQ', 'FNO', None for all)
    """
    seg_filter = f"AND segment = '{segment}'" if segment else ""

    with engine.connect() as conn:
        query = f"""
            SELECT * FROM transactions 
            WHERE user_id = :uid AND trade_date <= :fy_end {seg_filter}
            ORDER BY trade_date DESC
        """
        df = pd.read_sql_query(
            text(query), conn, params={"uid": user_id, "fy_end": fy_end_date}
        )

    if df.empty:
        return pd.DataFrame()

    return df.reset_index(drop=True)


def get_fy_transactions_summary(
    user_id: int, fy_end_date: str, segment: str = None
) -> dict:
    """
    Get summary statistics for transactions for a FY.
    """
    seg_filter = f"AND segment = '{segment}'" if segment else ""

    with engine.connect() as conn:
        df = pd.read_sql_query(
            text(f"""
                SELECT trade_type, quantity, price FROM transactions 
                WHERE user_id = :uid AND trade_date <= :fy_end {seg_filter}
            """),
            conn,
            params={"uid": user_id, "fy_end": fy_end_date},
        )

    if df.empty:
        return {
            "total_transactions": 0,
            "total_buys": 0,
            "total_sells": 0,
            "total_buy_value": 0,
            "total_sell_value": 0,
        }

    buys = df[df["trade_type"] == "BUY"]
    sells = df[df["trade_type"] == "SELL"]

    buy_value = (
        pd.to_numeric(buys["quantity"], errors="coerce")
        * pd.to_numeric(buys["price"], errors="coerce")
    ).sum()
    sell_value = (
        pd.to_numeric(sells["quantity"], errors="coerce")
        * pd.to_numeric(sells["price"], errors="coerce")
    ).sum()

    return {
        "total_transactions": len(df),
        "total_buys": len(buys),
        "total_sells": len(sells),
        "total_buy_value": round(buy_value, 2),
        "total_sell_value": round(sell_value, 2),
    }


# ========== COMBINED FY SUMMARY ==========
def get_fy_summary(user_id: int, fy_end_date: str) -> dict:
    """
    Get combined summary for all sections (Holdings, PNL, Intraday, Transactions) for a FY.
    """
    return {
        "realized_pnl": get_fy_realized_pnl_summary(user_id, fy_end_date),
        "intraday": get_fy_intraday_summary(user_id, fy_end_date),
        "transactions": get_fy_transactions_summary(user_id, fy_end_date),
        "fy_end_date": fy_end_date,
    }
