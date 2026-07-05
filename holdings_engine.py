# backend/services/holdings_engine.py
from __future__ import annotations
import json, math
from collections import defaultdict
from datetime import date, datetime
import pandas as pd
from sqlalchemy import text
from database import SessionLocal


# ---------- ensure corporate_actions has target columns ----------
def _ensure_ca_columns():
    db = SessionLocal()
    try:
        for col_def in [
            "target_isin VARCHAR(20) DEFAULT ''",
            "target_symbol VARCHAR(100) DEFAULT ''",
            "cost_split_pct FLOAT DEFAULT 0.0",
        ]:
            try:
                db.execute(text(f"ALTER TABLE corporate_actions ADD COLUMN {col_def}"))
                db.commit()
            except Exception:
                db.rollback()
    finally:
        db.close()

# ---------- ISIN / canonical helpers ----------
def _resolve_isin(symbol: str, raw_isin: str = "") -> str:
    isin = str(raw_isin or "").strip().upper()
    if isin and isin not in ("NAN", "NONE", ""):
        return isin
    from services.isin_resolver import resolve_isin
    return resolve_isin(symbol.strip().upper()) or ""

def _canonical(symbol: str) -> str:
    from services.symbol_resolver import get_canonical
    return get_canonical(str(symbol).strip().upper())

def _isin_or_sym_key(symbol: str, raw_isin: str) -> str:
    isin = _resolve_isin(symbol, raw_isin)
    return isin if isin else f"SYM:{_canonical(symbol)}"

# ---------- ratio helpers ----------
def _parse_ratio(ratio_str: str) -> tuple[float, float]:
    try:
        a, b = str(ratio_str).replace(" ", "").split(":")
        return float(a), float(b)
    except Exception:
        return 1.0, 1.0

def _bonus_multiplier(ratio_str: str) -> float:
    new_per, per_held = _parse_ratio(ratio_str)
    if per_held > 0:
        return new_per / per_held
    return 0.0

def _split_multiplier(ratio_str: str, details: dict) -> float:
    new_shares, old_shares = _parse_ratio(ratio_str)
    if old_shares > 0 and new_shares > old_shares:
        return new_shares / old_shares
    try:
        old_fv = float(details.get("old_fv", 0) or 0)
        new_fv = float(details.get("new_fv", 0) or 0)
        if new_fv > 0 and old_fv > new_fv:
            return old_fv / new_fv
    except Exception:
        pass
    return 1.0

# ---------- state management ----------
def _new_state(isin_key, symbol, company):
    return {"isin_key": isin_key, "symbol": symbol, "company": company,
            "qty": 0.0, "invested_value": 0.0, "lots": [],
            "first_buy_date": "", "last_updated": "", "ca_events": []}

def _ensure(portfolio, isin_key, symbol, company):
    if isin_key not in portfolio:
        portfolio[isin_key] = _new_state(isin_key, symbol, company)
    return portfolio[isin_key]

def _add_lot(state, trade_date, qty, price, source):
    if qty <= 0:
        return
    state["lots"].append({"date": trade_date, "qty": qty, "price": price, "source": source})
    state["qty"] += qty
    state["invested_value"] += qty * price
    if not state["first_buy_date"] or trade_date < state["first_buy_date"]:
        state["first_buy_date"] = trade_date
    state["last_updated"] = trade_date

def _consume_fifo(state, sell_qty, trade_date):
    rem = min(sell_qty, state["qty"])
    if rem <= 0:
        return 0.0
    cost = 0.0
    surviving = []
    for lot in state["lots"]:
        if rem <= 0:
            surviving.append(lot)
            continue
        take = min(lot["qty"], rem)
        cost += take * lot["price"]
        rem -= take
        left = lot["qty"] - take
        if left > 1e-6:
            surviving.append({**lot, "qty": left})
    state["lots"] = surviving
    state["qty"] = max(0.0, state["qty"] - (sell_qty - rem))
    state["invested_value"] = sum(l["qty"] * l["price"] for l in state["lots"])
    state["last_updated"] = trade_date
    return cost

def _apply_split(state, multiplier, event_date):
    if multiplier <= 1.0:
        return
    for lot in state["lots"]:
        lot["qty"] = round(lot["qty"] * multiplier, 6)
    state["qty"] = round(sum(l["qty"] for l in state["lots"]), 6)
    state["last_updated"] = event_date

# ---------- data loaders ----------
def _load_transactions(user_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(text(
            """SELECT trade_date, symbol, company_name, exchange, segment,
                      isin, trade_type, quantity, price
               FROM transactions
               WHERE user_id = :uid AND segment = 'EQ'
               ORDER BY trade_date ASC, id ASC"""),
            {"uid": user_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()

def _load_corporate_actions(user_id: int) -> list[dict]:
    _ensure_ca_columns()
    db = SessionLocal()
    try:
        rows = db.execute(text(
            """SELECT symbol, isin, company_name, action_type, ex_date,
                      action_details, target_isin, target_symbol, cost_split_pct
               FROM corporate_actions
               WHERE user_id = :uid
                 AND action_type IN ('SPLIT','BONUS','MERGER','DEMERGER')
               ORDER BY ex_date ASC, id ASC"""),
            {"uid": user_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()

def _build_unified_ledger(txns, cas):
    events = []
    for r in txns:
        events.append({
            "date": str(r["trade_date"])[:10], "sort_order": 1, "kind": "TXN",
            "trade_type": str(r.get("trade_type", "")), "symbol": str(r.get("symbol", "")),
            "company": str(r.get("company_name") or r.get("symbol", "")),
            "isin_raw": str(r.get("isin") or ""), "qty": float(r.get("quantity", 0) or 0),
            "price": float(r.get("price", 0) or 0),
        })
    for r in cas:
        try:
            details = json.loads(str(r.get("action_details") or "{}"))
        except Exception:
            details = {}
        events.append({
            "date": str(r.get("ex_date") or "")[:10], "sort_order": 0, "kind": "CA",
            "action_type": str(r.get("action_type", "")), "symbol": str(r.get("symbol", "")),
            "company": str(r.get("company_name") or r.get("symbol", "")),
            "isin_raw": str(r.get("isin") or ""),
            "target_isin": str(r.get("target_isin") or "").strip().upper(),
            "target_symbol": str(r.get("target_symbol") or "").strip().upper(),
            "cost_split_pct": float(r.get("cost_split_pct") or 0.0),
            "details": details,
        })
    events.sort(key=lambda e: (e["date"], e["sort_order"]))
    return events

def compute_ca_aware_holdings(user_id: int) -> dict:
    txns = _load_transactions(user_id)
    cas = _load_corporate_actions(user_id)
    events = _build_unified_ledger(txns, cas)
    portfolio = {}
    for ev in events:
        ev_date = ev["date"]
        if ev["kind"] == "TXN":
            symbol = ev["symbol"]; company = ev["company"]
            key = _isin_or_sym_key(symbol, ev["isin_raw"])
            state = _ensure(portfolio, key, symbol, company)
            tt = ev["trade_type"]; qty = ev["qty"]; price = ev["price"]
            if tt == "BUY":
                _add_lot(state, ev_date, qty, price, "BUY")
            elif tt == "SELL":
                _consume_fifo(state, qty, ev_date)
            elif tt == "TRANSFER_IN":
                _add_lot(state, ev_date, qty, price, "TRANSFER_IN")
            elif tt == "TRANSFER_OUT":
                _consume_fifo(state, qty, ev_date)
            elif tt == "BONUS":
                _add_lot(state, ev_date, qty, 0.0, "BONUS_TXN")
            elif tt == "DEMERGER_IN":
                _add_lot(state, ev_date, qty, 0.0, "DEMERGER_IN")
            elif tt == "MERGER_OUT":
                _consume_fifo(state, state["qty"], ev_date)
        elif ev["kind"] == "CA":
            symbol = ev["symbol"]
            key = _isin_or_sym_key(symbol, ev["isin_raw"])
            if key not in portfolio or portfolio[key]["qty"] < 0.01:
                continue
            state = portfolio[key]; atype = ev["action_type"]; details = ev["details"]
            ratio = str(details.get("ratio") or "1:1")
            if atype == "SPLIT":
                mult = _split_multiplier(ratio, details)
                if mult > 1.001:
                    _apply_split(state, mult, ev_date)
                    state["ca_events"].append(f"SPLIT {ratio} on {ev_date} → qty ×{mult:.4g}")
            elif atype == "BONUS":
                frac = _bonus_multiplier(ratio)
                if frac > 0:
                    bonus_qty = round(state["qty"] * frac, 4)
                    _add_lot(state, ev_date, bonus_qty, 0.0, "BONUS_CA")
                    state["ca_events"].append(f"BONUS {ratio} on {ev_date} → +{bonus_qty:.2f} shares @ ₹0")
            elif atype == "MERGER":
                target_isin = ev.get("target_isin", "").strip().upper()
                target_symbol = ev.get("target_symbol", symbol).strip().upper()
                if not target_isin:
                    state["ca_events"].append(f"⚠️ MERGER on {ev_date}: target_isin not set")
                    continue
                new_per, old_per = _parse_ratio(ratio)
                if old_per <= 0 or state["qty"] < 0.01: continue
                old_qty = state["qty"]; old_value = state["invested_value"]
                new_qty = math.floor((old_qty * new_per) / old_per)
                if new_qty <= 0: continue
                old_ca_events = list(state["ca_events"])
                state["qty"] = 0.0; state["invested_value"] = 0.0; state["lots"] = []
                new_state = _ensure(portfolio, target_isin, target_symbol, ev.get("company", symbol))
                avg_new_price = round(old_value / new_qty, 4) if new_qty > 0 else 0.0
                _add_lot(new_state, ev_date, new_qty, avg_new_price, f"MERGER_FROM:{symbol}({ev_date})")
                if not new_state["first_buy_date"]:
                    new_state["first_buy_date"] = state.get("first_buy_date") or ev_date
                new_state["ca_events"] = old_ca_events + [
                    f"MERGER {ratio} on {ev_date}: {old_qty:.0f} {symbol} → {new_qty} {target_symbol} (cost ₹{old_value:,.2f} transferred)"
                ]
            elif atype == "DEMERGER":
                target_isin = ev.get("target_isin", "").strip().upper()
                target_symbol = ev.get("target_symbol", "").strip().upper()
                cost_pct = float(ev.get("cost_split_pct", details.get("cost_split_pct", 0.0)))
                if not target_isin:
                    state["ca_events"].append(f"⚠️ DEMERGER on {ev_date}: target_isin not set")
                    continue
                new_per, old_per = _parse_ratio(ratio)
                if old_per <= 0 or state["qty"] < 0.01: continue
                old_qty = state["qty"]; old_value = state["invested_value"]
                child_qty = math.floor((old_qty * new_per) / old_per)
                child_value = round(old_value * cost_pct, 2)
                parent_value = round(old_value - child_value, 2)
                if old_value > 0:
                    factor = parent_value / old_value
                    for lot in state["lots"]:
                        lot["price"] = round(lot["price"] * factor, 4)
                state["invested_value"] = parent_value
                if child_qty > 0:
                    child_state = _ensure(portfolio, target_isin, target_symbol, ev.get("company", ""))
                    avg_child = round(child_value / child_qty, 4) if child_qty > 0 else 0.0
                    _add_lot(child_state, ev_date, child_qty, avg_child, f"DEMERGER_FROM:{symbol}({ev_date})")
                    if not child_state["first_buy_date"]:
                        child_state["first_buy_date"] = state.get("first_buy_date") or ev_date
                    child_state["ca_events"].append(f"DEMERGER {ratio} on {ev_date}: {child_qty} shares from {symbol} @ ₹{avg_child:.2f} (cost ₹{child_value:,.2f})")
                state["ca_events"].append(
                    f"DEMERGER {ratio} on {ev_date}: child {target_symbol} allotted {child_qty} shares; parent cost reduced by ₹{child_value:,.2f}"
                )
                state["last_updated"] = ev_date
    return portfolio

# ---------- public API ----------
def get_ca_aware_holdings(user_id: int) -> pd.DataFrame:
    portfolio = compute_ca_aware_holdings(user_id)
    rows = []
    for isin_key, s in portfolio.items():
        qty = round(s["qty"], 4)
        if qty < 0.01: continue
        invested = round(s["invested_value"], 2)
        avg_price = round(invested / qty, 4) if qty > 0 else 0.0
        isin_display = isin_key if not isin_key.startswith("SYM:") else ""
        rows.append({
            "isin": isin_display, "symbol": s["symbol"], "company_name": s["company"],
            "exchange": "NSE", "segment": "EQ", "quantity": qty,
            "avg_buy_price": avg_price, "total_invested": invested,
            "first_buy_date": s.get("first_buy_date", ""),
            "ca_events_count": len(s.get("ca_events", [])),
            "ca_summary": " | ".join(s.get("ca_events", [])) or "—"
        })
    return pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True) if rows else pd.DataFrame()

def get_ca_aware_holding_lots(user_id: int) -> pd.DataFrame:
    portfolio = compute_ca_aware_holdings(user_id)
    today = date.today()
    rows = []
    for isin_key, s in portfolio.items():
        if s["qty"] < 0.01: continue
        isin_display = isin_key if not isin_key.startswith("SYM:") else ""
        for lot in s["lots"]:
            if lot["qty"] < 0.01: continue
            try:
                buy_dt = datetime.strptime(lot["date"], "%Y-%m-%d").date()
                days_held = (today - buy_dt).days
            except Exception:
                days_held = 0
            term = "LONG (>1yr)" if days_held > 365 else "SHORT (≤1yr)"
            tax_rate = "12.5%" if days_held > 365 else "20.0%"
            rows.append({
                "Symbol": s["symbol"], "Company": s["company"], "ISIN": isin_display,
                "Lot Type": lot.get("source", "BUY"), "Buy Date": lot["date"],
                "Days Held": days_held, "Term": term,
                "Qty": round(lot["qty"], 4), "Avg Cost (₹)": round(lot["price"], 2),
                "Invested (₹)": round(lot["qty"] * lot["price"], 2), "Tax Rate": tax_rate,
            })
    return pd.DataFrame(rows).sort_values(["Symbol", "Buy Date"]).reset_index(drop=True) if rows else pd.DataFrame()

def get_ca_event_log(user_id: int) -> pd.DataFrame:
    portfolio = compute_ca_aware_holdings(user_id)
    rows = []
    for isin_key, s in portfolio.items():
        for ev_str in s.get("ca_events", []):
            rows.append({"Symbol": s["symbol"], "ISIN": isin_key if not isin_key.startswith("SYM:") else "", "Event": ev_str})
    return pd.DataFrame(rows) if rows else pd.DataFrame()