"""
Reconciliation Service
========================
Compares transaction-derived holdings (CA-aware) with broker-uploaded holdings.
Identifies discrepancies and provides resolution functions that insert correcting
transactions into the standard transactions table so the FIFO engine can
naturally incorporate them.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from sqlalchemy import text
from backend.database import SessionLocal
from backend.services.holdings_engine import get_ca_aware_holdings
from backend.services.engine import recalculate_derived

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Load broker holdings from DB
# ─────────────────────────────────────────────────────────────────────────────
def _load_broker_holdings(user_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""SELECT symbol, isin, quantity, avg_buy_price, broker
                    FROM broker_holdings
                    WHERE user_id = :uid
                    ORDER BY symbol"""),
            {"uid": user_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Build ISIN→symbol lookup from user_stock_symbol_mapping + stock_master_mapping
# ─────────────────────────────────────────────────────────────────────────────
def _build_isin_lookup(user_id: int) -> dict:
    """Returns {symbol_upper: isin, isin: symbol_upper} two-way dict."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""SELECT usm.symbol, usm.isin
                    FROM user_stock_symbol_mapping usm
                    WHERE usm.user_id = :uid"""),
            {"uid": user_id},
        ).fetchall()
        lookup = {}
        for r in rows:
            sym = str(r.symbol).strip().upper()
            isin = str(r.isin).strip().upper()
            if sym and isin:
                lookup[sym] = isin
                lookup[isin] = sym
        return lookup
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Core comparison logic
# ─────────────────────────────────────────────────────────────────────────────
def get_reconciliation_report(user_id: int) -> dict:
    """
    Compare CA-aware computed holdings vs broker-uploaded holdings.

    Returns:
        {
          "matched": [...],
          "missing_in_tracker": [...],   # broker has, tracker doesn't (IPO/transfer/CA)
          "extra_in_tracker": [...],     # tracker has, broker doesn't (missed sale/transfer)
          "broker_count": int,
          "tracker_count": int,
        }
    """
    # 1. Get computed holdings (from transactions + corporate actions)
    ca_df = get_ca_aware_holdings(user_id)
    tracker: dict[str, dict] = {}
    if not ca_df.empty:
        for _, row in ca_df.iterrows():
            isin = str(row.get("isin", "")).strip().upper()
            sym = str(row.get("symbol", "")).strip().upper()
            qty = float(row.get("quantity", 0) or 0)
            avg = float(row.get("avg_buy_price", 0) or 0)
            if qty < 0.01:
                continue
            key = isin if isin else sym
            if key in tracker:
                # aggregate in case of duplicates
                old = tracker[key]
                total_qty = old["quantity"] + qty
                total_inv = old["quantity"] * old["avg_buy_price"] + qty * avg
                tracker[key] = {
                    "symbol": old["symbol"],
                    "isin": old["isin"],
                    "quantity": round(total_qty, 4),
                    "avg_buy_price": round(total_inv / total_qty, 4) if total_qty > 0 else 0,
                    "company_name": old.get("company_name", sym),
                }
            else:
                tracker[key] = {
                    "symbol": sym,
                    "isin": isin,
                    "quantity": round(qty, 4),
                    "avg_buy_price": round(avg, 4),
                    "company_name": str(row.get("company_name", sym)),
                }

    # 2. Get broker-uploaded holdings
    broker_rows = _load_broker_holdings(user_id)
    isin_lookup = _build_isin_lookup(user_id)

    broker: dict[str, dict] = {}
    for b in broker_rows:
        isin = str(b.get("isin", "")).strip().upper()
        sym = str(b.get("symbol", "")).strip().upper()
        qty = float(b.get("quantity", 0) or 0)
        avg = float(b.get("avg_buy_price", 0) or 0)
        if qty < 0.01:
            continue

        # Try to resolve to ISIN if not available
        if not isin and sym in isin_lookup:
            isin = isin_lookup[sym]

        key = isin if isin else sym
        if key in broker:
            old = broker[key]
            total_qty = old["quantity"] + qty
            total_inv = old["quantity"] * old["avg_buy_price"] + qty * avg
            broker[key] = {
                "symbol": old["symbol"],
                "isin": old["isin"],
                "quantity": round(total_qty, 4),
                "avg_buy_price": round(total_inv / total_qty, 4) if total_qty > 0 else 0,
                "broker_name": old.get("broker_name", b.get("broker", "")),
            }
        else:
            broker[key] = {
                "symbol": sym,
                "isin": isin,
                "quantity": round(qty, 4),
                "avg_buy_price": round(avg, 4),
                "broker_name": b.get("broker", ""),
            }

    # 3. Compare
    all_keys = set(tracker.keys()) | set(broker.keys())
    matched = []
    missing_in_tracker = []   # broker has it, tracker doesn't
    extra_in_tracker = []     # tracker has it, broker doesn't

    for key in sorted(all_keys):
        t = tracker.get(key)
        b = broker.get(key)

        t_qty = t["quantity"] if t else 0.0
        b_qty = b["quantity"] if b else 0.0
        diff = round(b_qty - t_qty, 4)

        row = {
            "key": key,
            "symbol": (b or t).get("symbol", key),
            "isin": (b or t).get("isin", ""),
            "broker_qty": b_qty,
            "tracker_qty": t_qty,
            "diff": diff,
            "broker_avg": b["avg_buy_price"] if b else 0.0,
            "tracker_avg": t["avg_buy_price"] if t else 0.0,
            "company_name": t.get("company_name", "") if t else (b or {}).get("symbol", key),
        }

        if abs(diff) < 0.01:
            matched.append(row)
        elif diff > 0:
            # Broker has MORE than tracker → missing in tracker
            row["diff"] = round(diff, 4)
            missing_in_tracker.append(row)
        else:
            # Tracker has MORE than broker → extra in tracker
            row["diff"] = round(abs(diff), 4)
            extra_in_tracker.append(row)

    return {
        "matched": matched,
        "missing_in_tracker": missing_in_tracker,
        "extra_in_tracker": extra_in_tracker,
        "broker_count": len(broker),
        "tracker_count": len(tracker),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Resolution actions — insert correcting transactions
# ─────────────────────────────────────────────────────────────────────────────
def resolve_discrepancy(
    user_id: int,
    symbol: str,
    isin: str,
    qty: float,
    price: float,
    trade_date: str,
    resolution_type: str,
) -> dict:
    """
    Insert a correcting transaction based on user's resolution choice.

    resolution_type:
      - "IPO"           → BUY with remark "Reconciliation: IPO Allotment"
      - "TRANSFER_IN"   → TRANSFER_IN with remark "Reconciliation: Transfer In"
      - "MISSED_BUY"    → BUY with remark "Reconciliation: Missed Purchase"
      - "CORP_ACTION"   → BONUS with price=0 and remark "Reconciliation: Corporate Action"
      - "MISSED_SALE"   → SELL with remark "Reconciliation: Missed Sale"
      - "TRANSFER_OUT"  → TRANSFER_OUT with remark "Reconciliation: Transfer Out"
    """
    type_map = {
        "IPO":          ("BUY",          "Reconciliation: IPO Allotment"),
        "TRANSFER_IN":  ("TRANSFER_IN",  "Reconciliation: Transfer In"),
        "MISSED_BUY":   ("BUY",          "Reconciliation: Missed Purchase"),
        "CORP_ACTION":  ("BONUS",        "Reconciliation: Corporate Action"),
        "MISSED_SALE":  ("SELL",         "Reconciliation: Missed Sale"),
        "TRANSFER_OUT": ("TRANSFER_OUT", "Reconciliation: Transfer Out"),
    }

    if resolution_type not in type_map:
        return {"status": "error", "message": f"Unknown resolution type: {resolution_type}"}

    trade_type, remark = type_map[resolution_type]

    # For BONUS / CORP_ACTION, price is always 0
    if resolution_type == "CORP_ACTION":
        price = 0.0

    db = SessionLocal()
    try:
        db.execute(
            text("""
                INSERT IGNORE INTO transactions
                (user_id, symbol, company_name, exchange, isin, segment,
                 trade_date, quantity, price, trade_type,
                 brokerage, tax_charges, broker, source_file, remarks)
                VALUES (:uid, :sym, :comp, 'NSE', :isin, 'EQ',
                        :tdate, :qty, :price, :tt,
                        0, 0, 'Reconciliation', 'reconciliation', :rem)
            """),
            {
                "uid": user_id,
                "sym": symbol,
                "comp": symbol,
                "isin": isin or "",
                "tdate": trade_date,
                "qty": qty,
                "price": price,
                "tt": trade_type,
                "rem": remark,
            },
        )
        db.commit()

        # Recalculate derived tables (holdings, PnL, intraday)
        recalculate_derived(user_id, db)

        logger.info(
            f"[Reconciliation] user={user_id} {trade_type} {qty}x {symbol} @ {price} on {trade_date} — {remark}"
        )
        return {
            "status": "success",
            "message": f"✅ {trade_type} {qty} x {symbol} @ ₹{price:.2f} on {trade_date} — {remark}",
        }
    except Exception as e:
        db.rollback()
        logger.error(f"[Reconciliation] Error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()
