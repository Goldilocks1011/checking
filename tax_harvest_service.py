"""
tax_harvest_service.py  — v2
==============================
Adds:
  • Smart Note categorisation for Unmatched Buys (F&O loss coverage /
    Month-end / Additional buy / Lot completion)
  • Realized P&L for Outstanding Sells   = (avg_sell – orig_cost) × qty
  • FUT columns for Outstanding Sells    (from fno_open_positions)
  • Action text for Outstanding Sells
  • Separate Sell Broker / Sell Dates columns
  • KPI summary totals
"""

from sqlalchemy import text
from database import SessionLocal
from collections import defaultdict
from datetime import datetime, date
import calendar


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _last_thursday(year: int, month: int) -> date:
    """Return the last Thursday of the given month (NSE monthly F&O expiry)."""
    last_day = calendar.monthrange(year, month)[1]
    for day in range(last_day, 0, -1):
        if date(year, month, day).weekday() == 3:   # 3 = Thursday
            return date(year, month, day)
    return date(year, month, last_day)


def _is_near_expiry(date_str: str, days_before: int = 7) -> bool:
    """True if date_str falls within `days_before` calendar days of the monthly F&O expiry."""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        expiry = _last_thursday(dt.year, dt.month)
        diff = (expiry - dt).days
        return 0 <= diff <= days_before
    except Exception:
        return False


def _get_lot_size(can: str, db) -> int:
    """Look up lot size from stock_master_mapping for a canonical symbol."""
    try:
        row = db.execute(
            text("SELECT lot_size, fno_available FROM stock_master_mapping WHERE canonical_symbol=:can LIMIT 1"),
            {"can": can}
        ).first()
        if row and row.fno_available:
            return int(row.lot_size or 0)
    except Exception:
        pass
    return 0


def _get_fno_available(can: str, db) -> bool:
    try:
        row = db.execute(
            text("SELECT fno_available FROM stock_master_mapping WHERE canonical_symbol=:can LIMIT 1"),
            {"can": can}
        ).first()
        return bool(row and row.fno_available)
    except Exception:
        return False


def _build_canon_map(db) -> dict:
    """Build a global symbol → canonical mapping from user_stock_symbol_mapping."""
    rows = db.execute(
        text("""
            SELECT DISTINCT usm.symbol, sm.canonical_symbol
            FROM user_stock_symbol_mapping usm
            JOIN stock_master_mapping sm ON sm.isin = usm.isin
        """)
    ).fetchall()
    return {r.symbol.strip().upper(): (r.canonical_symbol or r.symbol) for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Pre-window holdings (to detect "additional buy" / "lot completion")
# ─────────────────────────────────────────────────────────────────────────────

def _pre_window_holdings(user_ids: list, start: str, canon_map: dict, db) -> dict:
    """
    Returns {canonical: total_qty} held by any of the given users BEFORE the
    harvest window start date (i.e., from FIFO running total up to start-1 day).
    """
    if not user_ids:
        return {}
    uid_list = ",".join(str(u) for u in user_ids)
    rows = db.execute(
        text(f"""
            SELECT symbol, SUM(CASE WHEN trade_type IN ('BUY','TRANSFER_IN','BONUS','DEMERGER_IN') THEN quantity
                                    WHEN trade_type IN ('SELL','TRANSFER_OUT','MERGER_OUT') THEN -quantity
                                    ELSE 0 END) AS net_qty
            FROM transactions
            WHERE user_id IN ({uid_list})
              AND segment = 'EQ'
              AND trade_date < :start
            GROUP BY symbol
            HAVING net_qty > 0
        """),
        {"start": start}
    ).fetchall()
    result = {}
    for r in rows:
        can = canon_map.get(r.symbol.strip().upper(), r.symbol.strip().upper())
        result[can] = result.get(can, 0) + float(r.net_qty or 0)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# F&O P&L losses  (for "F&O loss coverage" note)
# ─────────────────────────────────────────────────────────────────────────────

def _fno_pnl_by_symbol(user_ids: list, db) -> dict:
    """Returns {canonical_underlying: total_gross_pnl} for all closed F&O trades."""
    if not user_ids:
        return {}
    uid_list = ",".join(str(u) for u in user_ids)
    rows = db.execute(
        text(f"""
            SELECT underlying, SUM(gross_pnl) AS total_pnl
            FROM fno_pnl
            WHERE user_id IN ({uid_list})
            GROUP BY underlying
        """)
    ).fetchall()
    return {r.underlying.strip().upper(): float(r.total_pnl or 0) for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# F&O open positions  (for "FUT columns" in Outstanding Sells)
# ─────────────────────────────────────────────────────────────────────────────

def _open_fut_by_symbol(user_ids: list, uid_to_name: dict, db) -> dict:
    """
    Returns {canonical_underlying: {avg_price, expiry_date, open_qty, account_name}}
    Only FUT positions (instrument_type='FUT').
    """
    if not user_ids:
        return {}
    uid_list = ",".join(str(u) for u in user_ids)
    rows = db.execute(
        text(f"""
            SELECT user_id, underlying, avg_price, expiry_date, open_qty
            FROM fno_open_positions
            WHERE user_id IN ({uid_list})
              AND instrument_type = 'FUT'
              AND open_qty != 0
            ORDER BY expiry_date ASC
        """)
    ).fetchall()
    result = {}
    for r in rows:
        key = r.underlying.strip().upper()
        if key not in result:
            result[key] = {
                "avg_price": float(r.avg_price or 0),
                "expiry_date": str(r.expiry_date or ""),
                "open_qty": float(r.open_qty or 0),
                "account": uid_to_name.get(r.user_id, str(r.user_id)),
            }
        else:
            # Accumulate across users/contracts
            result[key]["open_qty"] += float(r.open_qty or 0)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Corp-action summary string for a stock
# ─────────────────────────────────────────────────────────────────────────────

def _corp_action_summary(can: str, user_ids: list, db) -> str:
    if not user_ids:
        return "—"
    uid_list = ",".join(str(u) for u in user_ids)
    rows = db.execute(
        text(f"""
            SELECT action_type, ex_date, action_details
            FROM corporate_actions
            WHERE user_id IN ({uid_list})
              AND (UPPER(symbol)=:can OR UPPER(symbol) LIKE :can_pct)
              AND action_type IN ('BONUS','SPLIT','DEMERGER','MERGER')
            ORDER BY ex_date DESC
            LIMIT 3
        """),
        {"can": can.upper(), "can_pct": f"{can.upper()}%"}
    ).fetchall()
    if not rows:
        return "—"
    parts = []
    for r in rows:
        import json
        try:
            det = json.loads(r.action_details or "{}")
            ratio = det.get("ratio", "")
        except Exception:
            ratio = ""
        label = f"{r.action_type} {ratio} (ex {str(r.ex_date)[:10]})".strip()
        parts.append(label)
    return " | ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Smart note for unmatched buys
# ─────────────────────────────────────────────────────────────────────────────

def _smart_note(
    can: str,
    buy_qty: float,
    buy_dates: list,
    pre_holdings: dict,
    fno_pnl_map: dict,
    db,
) -> str:
    """
    Categorise an unmatched buy into one of:
      🔴 F&O loss coverage
      🔵 Month-end buy
      🔵 Lot completion
      🔵 Additional buy
      ⚪ Unmatched buy (fallback)
    """
    pre_qty = pre_holdings.get(can, 0)
    lot_size = _get_lot_size(can, db)
    first_buy = min(buy_dates) if buy_dates else ""
    last_buy  = max(buy_dates) if buy_dates else ""

    # ── 1. F&O loss coverage ─────────────────────────────────────────────────
    fno_pnl = fno_pnl_map.get(can.upper(), 0)
    if fno_pnl < -1000:          # material loss threshold (₹1,000)
        realized_equity = 0      # placeholder – full equity P&L calc is expensive here
        remaining = abs(fno_pnl) - realized_equity
        return (
            f"🔴 F&O loss coverage — legacy loss: ₹{abs(int(fno_pnl)):,}; "
            f"equity P&L: ₹{int(realized_equity):,}; "
            f"remaining to recover: ₹{int(remaining):,}"
        )

    # ── 2. Lot completion ────────────────────────────────────────────────────
    if lot_size > 0 and pre_qty > 0:
        rem_to_lot = lot_size - (int(pre_qty) % lot_size)
        if rem_to_lot == lot_size:
            rem_to_lot = 0
        if rem_to_lot > 0 and abs(buy_qty - rem_to_lot) <= max(1, lot_size * 0.05):
            return (
                f"🔵 Lot completion — had {int(pre_qty)} shares, "
                f"bought {int(buy_qty)} more to complete lot"
            )

    # ── 3. Month-end buy (near F&O expiry) ───────────────────────────────────
    if _is_near_expiry(last_buy):
        return "🔵 Month-end buy — purchased near F&O expiry; roll/position management"

    # ── 4. Additional buy (already had shares) ───────────────────────────────
    if pre_qty > 0:
        return "🔵 Additional buy — existing position averaged down or topped up"

    # ── 5. Fallback ──────────────────────────────────────────────────────────
    return "⚪ Unmatched buy"


# ─────────────────────────────────────────────────────────────────────────────
# Action text for outstanding sells
# ─────────────────────────────────────────────────────────────────────────────

def _action_text(can: str, qty: float, fno_available: bool) -> str:
    if fno_available:
        return (
            f"⚠️ Buy {int(qty)} {can} in any account ASAP "
            f"— or maintain exposure via FUT / PE"
        )
    else:
        return (
            f"⚠️ Buy {int(qty)} {can} in any account ASAP "
            f"— no FUT/PE available for this stock"
        )


# ─────────────────────────────────────────────────────────────────────────────
# LIFO cost basis (unchanged from v1, just moved here for clarity)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_lifo_cost_across_all(canonical: str, sell_date: str, sell_qty: float, user_ids: list, db) -> dict:
    if not user_ids:
        return {"wavg": 0, "first_date": ""}
    placeholders = ",".join(str(uid) for uid in user_ids)
    rows = db.execute(
        text(f"""
            SELECT t.trade_date, t.quantity, t.price
            FROM transactions t
            JOIN (
                SELECT DISTINCT symbol
                FROM transactions
                WHERE user_id IN ({placeholders})
                  AND trade_type = 'BUY'
                  AND trade_date <= :sdate
            ) t2 ON t.symbol = t2.symbol
            WHERE t.user_id IN ({placeholders})
              AND t.trade_type = 'BUY'
              AND t.trade_date <= :sdate
            ORDER BY t.trade_date DESC
        """),
        {"sdate": sell_date}
    ).fetchall()

    lots = [{"date": r.trade_date, "qty": float(r.quantity), "price": float(r.price)} for r in rows]
    rem = sell_qty
    total_cost = 0.0
    consumed_dates = []
    for lot in lots:
        if rem <= 0:
            break
        take = min(lot["qty"], rem)
        total_cost += take * lot["price"]
        rem -= take
        consumed_dates.append(lot["date"])

    if sell_qty > rem and consumed_dates:
        consumed_qty = sell_qty - rem
        wavg = total_cost / consumed_qty
        return {"wavg": round(wavg, 2), "first_date": min(consumed_dates)}
    return {"wavg": 0, "first_date": ""}


# ─────────────────────────────────────────────────────────────────────────────
# Single-user wrapper (calls multi with one account)
# ─────────────────────────────────────────────────────────────────────────────

def run_harvest_analysis(user_id: int, start: str, end: str) -> dict:
    db = SessionLocal()
    try:
        user_row = db.execute(text("SELECT username FROM users WHERE id=:uid"), {"uid": user_id}).first()
        uname = user_row.username if user_row else str(user_id)
    finally:
        db.close()
    return run_harvest_multi({uname: user_id}, start, end)


# ─────────────────────────────────────────────────────────────────────────────
# Main: multi-user harvest analysis  (v2 — smart notes, FUT cols, action)
# ─────────────────────────────────────────────────────────────────────────────

def run_harvest_multi(accounts_dict: dict, start: str, end: str) -> dict:
    """
    accounts_dict = {username: user_id}

    Returns:
    {
      "matched":       [...],
      "outstanding":   [...],   # now includes realized_pnl, fut_*, action columns
      "unmatched_buy": [...],   # now includes smart Note
      "summary":       {...},
    }
    """
    db = SessionLocal()
    try:
        accts = list(accounts_dict.items())
        user_ids = [uid for _, uid in accts]
        uid_to_name = {uid: uname for uname, uid in accts}

        # ── Canonical map ────────────────────────────────────────────────────
        canon_map = _build_canon_map(db)
        # fallback: self-map
        for _, uid in accts:
            rows = db.execute(
                text("SELECT DISTINCT symbol FROM transactions WHERE user_id=:uid AND segment='EQ'"),
                {"uid": uid}
            ).fetchall()
            for r in rows:
                s = r.symbol.strip().upper()
                if s not in canon_map:
                    canon_map[s] = s

        # ── Pre-fetch supporting data ────────────────────────────────────────
        pre_holdings = _pre_window_holdings(user_ids, start, canon_map, db)
        fno_pnl_map  = _fno_pnl_by_symbol(user_ids, db)
        open_fut_map = _open_fut_by_symbol(user_ids, uid_to_name, db)

        # ── Load window transactions ─────────────────────────────────────────
        all_txns = []
        for uname, uid in accts:
            rows = db.execute(
                text("""
                    SELECT trade_date, symbol, company_name, trade_type,
                           quantity, price, brokerage, tax_charges, broker
                    FROM transactions
                    WHERE user_id = :uid
                      AND trade_date BETWEEN :start AND :end
                      AND segment = 'EQ'
                      AND trade_type IN ('BUY', 'SELL')
                    ORDER BY trade_date
                """),
                {"uid": uid, "start": start, "end": end}
            ).fetchall()
            for r in rows:
                all_txns.append({
                    "trade_date": r.trade_date,
                    "symbol": r.symbol,
                    "company_name": r.company_name,
                    "trade_type": r.trade_type,
                    "quantity": float(r.quantity),
                    "price": float(r.price),
                    "brokerage": float(r.brokerage or 0),
                    "tax_charges": float(r.tax_charges or 0),
                    "broker": r.broker,
                    "account": uname,
                    "user_id": uid,
                })

        if not all_txns:
            return {"matched": [], "outstanding": [], "unmatched_buy": [], "summary": {}}

        # ── Canonicalise each transaction ────────────────────────────────────
        for txn in all_txns:
            sym = txn["symbol"].strip().upper()
            txn["canonical"] = canon_map.get(sym, sym)

        # ── Separate sells / buys ────────────────────────────────────────────
        sells = [t for t in all_txns if t["trade_type"] == "SELL"]
        buys  = [t for t in all_txns if t["trade_type"] == "BUY"]

        # ── Aggregate by canonical ───────────────────────────────────────────
        def _agg(txns):
            agg = defaultdict(lambda: {"total_qty": 0.0, "total_value": 0.0, "txns": []})
            for t in txns:
                can = t["canonical"]
                agg[can]["total_qty"]   += t["quantity"]
                agg[can]["total_value"] += t["quantity"] * t["price"]
                agg[can]["txns"].append(t)
            return agg

        sell_agg = _agg(sells)
        buy_agg  = _agg(buys)

        matched       = []
        outstanding   = []
        unmatched_buy = []

        # ── Match cross-account ──────────────────────────────────────────────
        sell_rem = {can: data["total_qty"] for can, data in sell_agg.items()}
        buy_rem  = {can: data["total_qty"] for can, data in buy_agg.items()}

        for can in set(sell_rem) & set(buy_rem):
            s_total = sell_agg[can]["total_qty"]
            b_total = buy_agg[can]["total_qty"]
            match_qty = min(s_total, b_total)
            if match_qty <= 0:
                continue

            avg_sell = sell_agg[can]["total_value"] / s_total
            avg_buy  = buy_agg[can]["total_value"]  / b_total

            sell_dates = [t["trade_date"] for t in sell_agg[can]["txns"]]
            orig = _compute_lifo_cost_across_all(can, min(sell_dates), match_qty, user_ids, db)
            harvest_pnl = round((avg_sell - orig["wavg"]) * match_qty, 2) if orig["wavg"] else 0

            matched.append({
                "Stock":            can,
                "Qty":              int(match_qty),
                "Avg Sell (₹)":    round(avg_sell, 2),
                "Avg Buy (₹)":     round(avg_buy, 2),
                "Sell Value (₹)":  round(avg_sell * match_qty, 2),
                "Buy Value (₹)":   round(avg_buy  * match_qty, 2),
                "Price Gain (₹)":  round((avg_sell - avg_buy) * match_qty, 2),
                "Broker FROM":     ", ".join(sorted({t["account"] for t in sell_agg[can]["txns"]})),
                "Broker TO":       ", ".join(sorted({t["account"] for t in buy_agg[can]["txns"]})),
                "Sell Date Range": f"{min(sell_dates)} → {max(sell_dates)}",
                "Buy Date Range":  (lambda dd: f"{min(dd)} → {max(dd)}")([t["trade_date"] for t in buy_agg[can]["txns"]]),
                "Orig Buy Date":   orig.get("first_date", ""),
                "Orig Avg Cost (₹)": orig.get("wavg", 0),
                "Harvest P&L (₹)": harvest_pnl,
                "Corp Actions":    _corp_action_summary(can, user_ids, db),
                "Status":          "MATCHED",
            })
            sell_rem[can] -= match_qty
            buy_rem[can]  -= match_qty

        # ── Outstanding sells ────────────────────────────────────────────────
        for can, s_data in sell_agg.items():
            rem = sell_rem.get(can, 0)
            if rem <= 0:
                continue
            s_txns    = s_data["txns"]
            s_total   = s_data["total_qty"]
            avg_sell  = s_data["total_value"] / s_total if s_total else 0
            sell_dates = sorted({t["trade_date"] for t in s_txns})
            sell_date_range = f"{sell_dates[0]} → {sell_dates[-1]}" if sell_dates else ""

            # Separate broker / date for display
            sell_brokers = ", ".join(sorted({t["account"] for t in s_txns}))

            earliest_sell = sell_dates[0] if sell_dates else start
            orig = _compute_lifo_cost_across_all(can, earliest_sell, rem, user_ids, db)
            orig_cost = orig.get("wavg", 0)

            # Realized P&L vs original cost
            realized_pnl = round((avg_sell - orig_cost) * rem, 2) if orig_cost else 0

            # FUT position info
            fut_info = open_fut_map.get(can.upper(), {})

            # Action text
            fno_avail = _get_fno_available(can, db)
            action = _action_text(can, rem, fno_avail)

            outstanding.append({
                "Stock":            can,
                "Qty":              int(rem),
                "Sell Broker":      sell_brokers,
                "Sell Dates":       sell_date_range,
                "Avg Sell (₹)":    round(avg_sell, 2),
                "Orig Avg Cost (₹)": orig_cost,
                "Realized P&L (₹)": realized_pnl,
                "Corp Actions":    _corp_action_summary(can, user_ids, db),
                "CMP (₹)":         None,   # filled by UI after price fetch
                "Unreal. P&L (₹)": None,   # filled by UI
                "FUT Avg Entry (₹)": fut_info.get("avg_price", "—"),
                "FUT Expiry":      fut_info.get("expiry_date", "—"),
                "FUT Total Qty":   fut_info.get("open_qty", 0),
                "FUT Account":     fut_info.get("account", "—"),
                "Status":          "OUTSTANDING",
                "Action":          action,
            })

        # ── Unmatched buys ───────────────────────────────────────────────────
        for can, b_data in buy_agg.items():
            rem = buy_rem.get(can, 0)
            if rem <= 0:
                continue
            b_txns   = b_data["txns"]
            b_total  = b_data["total_qty"]
            avg_buy  = b_data["total_value"] / b_total if b_total else 0
            buy_dates = sorted({t["trade_date"] for t in b_txns})
            date_range = f"{buy_dates[0]} → {buy_dates[-1]}" if buy_dates else ""

            note = _smart_note(
                can, rem, buy_dates,
                pre_holdings, fno_pnl_map, db
            )

            unmatched_buy.append({
                "Stock":          can,
                "Qty":            int(rem),
                "Buy Broker":     ", ".join(sorted({t["account"] for t in b_txns})),
                "Date":           date_range,
                "Avg Buy (₹)":   round(avg_buy, 2),
                "Buy Value (₹)": round(avg_buy * rem, 2),
                "CMP (₹)":        None,   # filled by UI
                "Unreal. P&L (₹)": None, # filled by UI
                "Realized P&L (₹)": None, # filled by UI (from pnl table)
                "Note":           note,
            })

        # ── Summary ──────────────────────────────────────────────────────────
        total_sell_value = sum(o["Avg Sell (₹)"] * o["Qty"] for o in outstanding)
        total_sell_qty   = sum(o["Qty"] for o in outstanding)
        total_harvest    = sum(m["Harvest P&L (₹)"] for m in matched)
        total_buy_value  = sum(u["Buy Value (₹)"]   for u in unmatched_buy)

        summary = {
            "matched_count":      len(matched),
            "total_harvest_pnl":  round(total_harvest, 2),
            "outstanding_count":  len(outstanding),
            "total_sell_value":   round(total_sell_value, 2),
            "total_sell_qty":     total_sell_qty,
            "unmatched_count":    len(unmatched_buy),
            "total_buy_value":    round(total_buy_value, 2),
        }

        return {
            "matched":       matched,
            "outstanding":   outstanding,
            "unmatched_buy": unmatched_buy,
            "summary":       summary,
        }

    except Exception as e:
        import traceback
        traceback.logger.info_exc()
        return {"error": str(e)}
    finally:
        db.close()