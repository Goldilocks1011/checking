"""
fno_stale_positions.py
======================
Detects F&O positions in fno_transactions that are "stale" because a
SEBI-mandated dividend adjustment happened AFTER the transaction was recorded
but the transaction still uses the OLD strike/qty.

This is different from fno_dividend_adjustment_service.py which handles the
current/future adjustment workflow.  This module answers the question:

  "Which rows in my fno_transactions table are using a pre-dividend strike
   or quantity that should have been updated?"

Algorithm:
  1. Fetch all APPLIED/USER_UPLOADED adjustments for the user.
  2. For each adjustment, look for fno_transactions rows where:
       - underlying matches
       - instrument_type matches
       - trade_date >= ex_date  (should be post-adjustment)
       - strike_price == old_strike  (still uses the OLD strike — WRONG)
     These rows are "stale" — they reference a strike that no longer exists
     after the adjustment.
  3. Also look for PENDING adjustments where the position was opened BEFORE
     ex_date and is still open — these are "at-risk" positions that haven't
     been adjusted yet.

Returns structured data for the UI to display.

Router: GET /fno/stale-positions/{user_id}
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["F&O Stale Positions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Core detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_stale_positions(user_id: int) -> dict:
    """
    Returns:
    {
        "stale_transactions": [
            {
                "txn_id": int,
                "underlying": str,
                "instrument_type": str,
                "trade_date": str,
                "trade_type": str,
                "quantity": float,
                "price": float,
                "old_strike": float,        # the strike in the transaction row
                "correct_strike": float,    # what it should be after adjustment
                "ex_date": str,             # dividend ex-date
                "dividend_amount": float,
                "adjustment_id": int,
                "adjustment_status": str,   # APPLIED / USER_UPLOADED
                "issue": str,               # human-readable explanation
            }
        ],
        "at_risk_positions": [
            {
                "underlying": str,
                "instrument_type": str,
                "expiry_date": str,
                "strike_price": float,
                "net_qty": float,
                "avg_price": float,
                "ex_date": str,
                "dividend_amount": float,
                "new_strike": float,
                "new_qty": int,
                "days_until_ex": int,
                "adjustment_id": int,
                "issue": str,
            }
        ],
        "summary": {
            "stale_count": int,
            "at_risk_count": int,
            "total_issues": int,
        }
    }
    """
    db = SessionLocal()
    try:
        stale_txns: list[dict] = []
        at_risk: list[dict] = []

        today = date.today()
        today_str = today.strftime("%Y-%m-%d")

        # ── 1. STALE TRANSACTIONS (post-ex-date trades still at old strike) ───
        # Find all adjustments that have been processed (APPLIED or USER_UPLOADED)
        applied_adjs = db.execute(text("""
            SELECT id, underlying, instrument_type, old_strike, new_strike,
                   old_qty, new_qty, ex_date, dividend_amount, status, expiry_date
            FROM fno_dividend_adjustments
            WHERE user_id = :uid
              AND status IN ('APPLIED', 'USER_UPLOADED')
            ORDER BY ex_date DESC
        """), {"uid": user_id}).fetchall()

        for adj in applied_adjs:
            ex_date_str = str(adj.ex_date)[:10]

            # Look for transactions AFTER ex_date still using old_strike
            # These are wrong — they should use new_strike
            stale_rows = db.execute(text("""
                SELECT id, trade_date, trade_type, quantity, price,
                       strike_price, expiry_date, broker
                FROM fno_transactions
                WHERE user_id         = :uid
                  AND underlying      = :und
                  AND instrument_type = :itype
                  AND trade_date      >= :ex_date
                  AND ABS(strike_price - :old_strike) < 1.0
                ORDER BY trade_date ASC
            """), {
                "uid":        user_id,
                "und":        adj.underlying,
                "itype":      adj.instrument_type,
                "ex_date":    ex_date_str,
                "old_strike": float(adj.old_strike),
            }).fetchall()

            for row in stale_rows:
                stale_txns.append({
                    "txn_id":            row.id,
                    "underlying":        adj.underlying,
                    "instrument_type":   adj.instrument_type,
                    "expiry_date":       str(row.expiry_date or "")[:10],
                    "trade_date":        str(row.trade_date or "")[:10],
                    "trade_type":        row.trade_type,
                    "quantity":          float(row.quantity),
                    "price":             float(row.price),
                    "old_strike":        float(adj.old_strike),
                    "correct_strike":    float(adj.new_strike),
                    "ex_date":           ex_date_str,
                    "dividend_amount":   float(adj.dividend_amount),
                    "adjustment_id":     adj.id,
                    "adjustment_status": adj.status,
                    "broker":            str(row.broker or ""),
                    "issue": (
                        f"Trade on {row.trade_date} uses pre-dividend strike "
                        f"₹{adj.old_strike:.0f} — after dividend ₹{adj.dividend_amount}/share "
                        f"(ex-date {ex_date_str}), the correct strike is ₹{adj.new_strike:.0f}. "
                        f"Adjustment status: {adj.status}."
                    ),
                })

        # ── 2. AT-RISK POSITIONS (open before ex_date, not yet adjusted) ──────
        # Find PENDING adjustments — positions that haven't been handled yet
        pending_adjs = db.execute(text("""
            SELECT id, underlying, instrument_type, old_strike, new_strike,
                   old_qty, new_qty, ex_date, dividend_amount, expiry_date, spot_prev
            FROM fno_dividend_adjustments
            WHERE user_id = :uid
              AND status  = 'PENDING'
            ORDER BY ex_date ASC
        """), {"uid": user_id}).fetchall()

        for adj in pending_adjs:
            ex_date_str = str(adj.ex_date)[:10]
            try:
                ex_dt = datetime.strptime(ex_date_str, "%Y-%m-%d").date()
                days_until = (ex_dt - today).days
            except Exception:
                days_until = 0

            # Compute actual open qty for this contract on day before ex_date
            pos_date = (ex_dt - timedelta(days=1)).strftime("%Y-%m-%d") if ex_dt > today else today_str

            net_result = db.execute(text("""
                SELECT
                    SUM(CASE WHEN trade_type='BUY'  THEN  quantity ELSE 0 END) AS buy_qty,
                    SUM(CASE WHEN trade_type='SELL' THEN  quantity ELSE 0 END) AS sell_qty,
                    SUM(CASE WHEN trade_type='BUY'  THEN quantity*price ELSE 0 END) AS buy_val,
                    SUM(CASE WHEN trade_type='SELL' THEN quantity*price ELSE 0 END) AS sell_val
                FROM fno_transactions
                WHERE user_id         = :uid
                  AND underlying      = :und
                  AND instrument_type = :itype
                  AND ABS(strike_price - :strike) < 1.0
                  AND trade_date     <= :pos_date
            """), {
                "uid":      user_id,
                "und":      adj.underlying,
                "itype":    adj.instrument_type,
                "strike":   float(adj.old_strike),
                "pos_date": pos_date,
            }).first()

            if not net_result:
                continue

            buy_qty  = float(net_result.buy_qty  or 0)
            sell_qty = float(net_result.sell_qty or 0)
            net_qty  = buy_qty - sell_qty

            if abs(net_qty) < 0.001:
                continue  # already closed before ex_date

            avg_price = 0.0
            if net_qty > 0 and buy_qty > 0:
                avg_price = float(net_result.buy_val or 0) / buy_qty
            elif net_qty < 0 and sell_qty > 0:
                avg_price = float(net_result.sell_val or 0) / sell_qty

            urgency = "URGENT" if days_until <= 0 else ("SOON" if days_until <= 3 else "UPCOMING")

            at_risk.append({
                "underlying":       adj.underlying,
                "instrument_type":  adj.instrument_type,
                "expiry_date":      str(adj.expiry_date or "")[:10],
                "strike_price":     float(adj.old_strike),
                "net_qty":          round(net_qty, 4),
                "avg_price":        round(avg_price, 4),
                "ex_date":          ex_date_str,
                "dividend_amount":  float(adj.dividend_amount),
                "spot_prev":        float(adj.spot_prev or 0),
                "new_strike":       float(adj.new_strike),
                "new_qty":          int(adj.new_qty),
                "days_until_ex":    days_until,
                "urgency":          urgency,
                "adjustment_id":    adj.id,
                "issue": (
                    f"Open {adj.instrument_type} position in {adj.underlying} "
                    f"(strike ₹{adj.old_strike:.0f}, qty {abs(net_qty):.0f}) "
                    f"will need adjustment on ex-date {ex_date_str} "
                    f"(dividend ₹{adj.dividend_amount}/share = "
                    f"{adj.dividend_amount / adj.spot_prev * 100:.1f}% of spot). "
                    f"New strike: ₹{adj.new_strike:.0f}, new qty: {int(adj.new_qty)}. "
                    f"Status: {urgency} ({days_until} days to ex-date)."
                ),
            })

        return {
            "stale_transactions": stale_txns,
            "at_risk_positions":  at_risk,
            "summary": {
                "stale_count":   len(stale_txns),
                "at_risk_count": len(at_risk),
                "total_issues":  len(stale_txns) + len(at_risk),
            },
        }

    except Exception as e:
        logger.error(f"[StalePositions] detect_stale_positions error: {e}", exc_info=True)
        return {
            "stale_transactions": [],
            "at_risk_positions":  [],
            "summary": {"stale_count": 0, "at_risk_count": 0, "total_issues": 0},
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Router endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/fno/stale-positions/{user_id}")
def get_stale_positions(user_id: int, db: Session = Depends(get_db)):
    """
    Returns two lists:
    1. stale_transactions — post-ex-date trades still using the pre-dividend strike.
    2. at_risk_positions  — currently open positions that face an upcoming adjustment.
    """
    return detect_stale_positions(user_id)