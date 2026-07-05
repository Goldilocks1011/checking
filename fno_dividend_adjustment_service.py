"""
fno_dividend_adjustment_service.py
====================================
Implements SEBI-compliant dividend-forced F&O adjustment engine.

Algorithm:
  - Detect dividends > 10% of spot_prev from corporate_actions table
  - Match against active F&O positions on day before ex_date
  - Create PENDING adjustment records
  - On user confirmation → insert synthetic SELL+BUY at carry_avg (P&L neutral)
  - On file upload → auto-detect if user already uploaded adjusted trades
  - Rebuild F&O P&L merging real + synthetic transactions
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import text

from database import SessionLocal

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level spot price cache  (cleared daily via clear_spot_cache)
# ─────────────────────────────────────────────────────────────────────────────
_spot_cache: dict[str, float] = {}


def clear_spot_cache() -> None:
    """Call once per day (e.g. from daily_startup.py) to reset the cache."""
    global _spot_cache
    _spot_cache = {}


# ─────────────────────────────────────────────────────────────────────────────
# Price helpers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_spot_price(underlying: str) -> float:
    """
    Returns previous-day closing price for the underlying.
    Primary: 5paisa fetch_prices_with_change (returns 'prev' = yesterday close).
    Fallback: yfinance .NS ticker.
    Returns 0.0 if unavailable — callers must guard against this.
    """
    sym = underlying.strip().upper()
    if sym in _spot_cache:
        return _spot_cache[sym]

    # ── 5paisa (preferred) ──────────────────────────────────────────────────
    try:
        from services.engine_price_fetch import fetch_prices_with_change
        result = fetch_prices_with_change([sym])
        data = result.get(sym) or {}
        prev = float(data.get("prev", 0) or 0)
        if prev > 0:
            _spot_cache[sym] = prev
            return prev
    except Exception as e:
        logger.warning(f"[DivAdj] 5paisa spot fetch failed for {sym}: {e}")

    # ── yfinance fallback ────────────────────────────────────────────────────
    try:
        import yfinance as yf
        t = yf.Ticker(sym + ".NS")
        prev = float(getattr(t.fast_info, "previous_close", 0) or 0)
        if prev > 0:
            _spot_cache[sym] = prev
            return prev
    except Exception as e:
        logger.warning(f"[DivAdj] yfinance spot fetch failed for {sym}: {e}")

    logger.error(f"[DivAdj] Could not get spot_prev for {sym} — skipping adjustment")
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_adjustment_eligible(dividend: float, spot_prev: float) -> bool:
    """SEBI circular: adjustment required only if dividend > 10% of prev close."""
    if spot_prev <= 0 or dividend <= 0:
        return False
    return dividend > 0.10 * spot_prev


def calculate_new_strike(old_strike: float, dividend: float) -> float:
    """New strike = old_strike − dividend (rounded to 2 decimal places)."""
    return round(old_strike - dividend, 2)


def calculate_new_qty(old_qty: float, spot_prev: float, dividend: float) -> int:
    """
    NSE formula: new_lot_size = round(old_lot_size × (S_prev / (S_prev − D)))
    Uses Python's built-in round() which rounds half to even (banker's rounding).
    """
    if spot_prev <= dividend or spot_prev <= 0:
        return int(old_qty)
    return round(old_qty * (spot_prev / (spot_prev - dividend)))


# ─────────────────────────────────────────────────────────────────────────────
# Position snapshot on a given date
# ─────────────────────────────────────────────────────────────────────────────

def get_active_fno_positions_on_date(user_id: int, as_of_date: str) -> list[dict]:
    """
    Net F&O positions (BUY qty − SELL qty) per contract key as of `as_of_date`.

    Merges:
      • fno_transactions        (real uploaded trades)
      • fno_synthetic_transactions (previously applied adjustments)

    Only returns contracts still open on that date (expiry >= as_of_date)
    with abs(net_qty) > 0.001.

    Returns list of:
      { underlying, instrument_type, expiry_date, strike_price,
        net_qty, avg_price, broker }
    """
    db = SessionLocal()
    try:
        # ── Real transactions ──────────────────────────────────────────────────
        real_rows = db.execute(text("""
            SELECT
                underlying,
                instrument_type,
                expiry_date,
                strike_price,
                broker,
                SUM(CASE WHEN trade_type='BUY'  THEN  quantity ELSE 0 END) AS buy_qty,
                SUM(CASE WHEN trade_type='SELL' THEN  quantity ELSE 0 END) AS sell_qty,
                SUM(CASE WHEN trade_type='BUY'  THEN  quantity * price ELSE 0 END) AS buy_value,
                SUM(CASE WHEN trade_type='SELL' THEN  quantity * price ELSE 0 END) AS sell_value
            FROM fno_transactions
            WHERE user_id  = :uid
              AND trade_date  <= :asof
              AND (expiry_date IS NULL OR expiry_date >= :asof)
            GROUP BY underlying, instrument_type, expiry_date, strike_price, broker
        """), {"uid": user_id, "asof": as_of_date}).fetchall()

        # ── Synthetic transactions (from prior adjustments) ───────────────────
        syn_rows = db.execute(text("""
            SELECT
                underlying,
                instrument_type,
                expiry_date,
                strike_price,
                'SYNTHETIC' AS broker,
                SUM(CASE WHEN trade_type='BUY'  THEN  quantity ELSE 0 END) AS buy_qty,
                SUM(CASE WHEN trade_type='SELL' THEN  quantity ELSE 0 END) AS sell_qty,
                SUM(CASE WHEN trade_type='BUY'  THEN  quantity * price ELSE 0 END) AS buy_value,
                SUM(CASE WHEN trade_type='SELL' THEN  quantity * price ELSE 0 END) AS sell_value
            FROM fno_synthetic_transactions
            WHERE user_id  = :uid
              AND trade_date  <= :asof
              AND (expiry_date IS NULL OR expiry_date >= :asof)
            GROUP BY underlying, instrument_type, expiry_date, strike_price
        """), {"uid": user_id, "asof": as_of_date}).fetchall()

        # ── Merge into dict keyed by (und, itype, expiry, strike) ─────────────
        merged: dict[tuple, dict] = {}
        for r in list(real_rows) + list(syn_rows):
            key = (
                str(r.underlying or "").strip().upper(),
                str(r.instrument_type or "").strip().upper(),
                str(r.expiry_date or "")[:10],
                float(r.strike_price or 0),
            )
            if key not in merged:
                merged[key] = {
                    "underlying":      key[0],
                    "instrument_type": key[1],
                    "expiry_date":     key[2],
                    "strike_price":    key[3],
                    "buy_qty":   0.0,
                    "sell_qty":  0.0,
                    "buy_value": 0.0,
                    "sell_value": 0.0,
                    "broker":    str(r.broker or ""),
                }
            m = merged[key]
            m["buy_qty"]    += float(r.buy_qty    or 0)
            m["sell_qty"]   += float(r.sell_qty   or 0)
            m["buy_value"]  += float(r.buy_value  or 0)
            m["sell_value"] += float(r.sell_value or 0)

        result = []
        for m in merged.values():
            net_qty = m["buy_qty"] - m["sell_qty"]
            if abs(net_qty) < 0.001:
                continue  # fully closed
            if net_qty > 0:
                avg_price = m["buy_value"]  / m["buy_qty"]  if m["buy_qty"]  > 0 else 0.0
            else:
                avg_price = m["sell_value"] / m["sell_qty"] if m["sell_qty"] > 0 else 0.0
            result.append({
                "underlying":      m["underlying"],
                "instrument_type": m["instrument_type"],
                "expiry_date":     m["expiry_date"],
                "strike_price":    m["strike_price"],
                "net_qty":         round(net_qty, 4),
                "avg_price":       round(avg_price, 4),
                "broker":          m["broker"],
            })
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Detection engine
# ─────────────────────────────────────────────────────────────────────────────

def detect_pending_adjustments(user_id: int) -> list[dict]:
    """
    Full detection engine. Scans corporate_actions for DIVIDEND events in a
    ±30-day rolling window, checks 10% threshold, matches against active
    F&O positions, and upserts PENDING records into fno_dividend_adjustments.

    Returns list of dicts for UI display (status=PENDING only).
    """
    import json

    db = SessionLocal()
    try:
        today = date.today()
        window_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        window_end   = (today + timedelta(days=30)).strftime("%Y-%m-%d")

        divs = db.execute(text("""
            SELECT symbol, isin, company_name, action_type,
                   ex_date, action_details
            FROM corporate_actions
            WHERE user_id    = :uid
              AND action_type = 'DIVIDEND'
              AND ex_date BETWEEN :start AND :end
            ORDER BY ex_date ASC
        """), {"uid": user_id, "start": window_start, "end": window_end}).fetchall()

        pending_out: list[dict] = []

        for div in divs:
            try:
                details = json.loads(str(div.action_details or "{}"))
            except Exception:
                details = {}

            dividend_amount = float(details.get("amount_per_share", 0) or 0)
            if dividend_amount <= 0:
                continue

            ex_date_str = str(div.ex_date or "")[:10]
            underlying  = str(div.symbol or "").strip().upper()
            if not underlying or not ex_date_str:
                continue

            # ── Spot price (prev close) ──────────────────────────────────────
            spot_prev = fetch_spot_price(underlying)
            if spot_prev <= 0:
                logger.warning(f"[DivAdj] {underlying}: spot_prev=0, skip detection")
                continue

            # ── Eligibility check ────────────────────────────────────────────
            if not is_adjustment_eligible(dividend_amount, spot_prev):
                logger.info(
                    f"[DivAdj] {underlying} {ex_date_str}: "
                    f"₹{dividend_amount} = {dividend_amount/spot_prev*100:.1f}% of ₹{spot_prev:.0f} "
                    f"→ below 10% threshold, skip"
                )
                continue

            # ── Positions on day before ex_date ─────────────────────────────
            try:
                ex_dt    = datetime.strptime(ex_date_str, "%Y-%m-%d").date()
                pos_date = (ex_dt - timedelta(days=1)).strftime("%Y-%m-%d")
            except ValueError:
                pos_date = ex_date_str

            positions = get_active_fno_positions_on_date(user_id, pos_date)
            matching  = [p for p in positions if p["underlying"] == underlying]

            if not matching:
                logger.debug(
                    f"[DivAdj] {underlying}: no open F&O positions on {pos_date}"
                )
                continue

            for pos in matching:
                old_strike = pos["strike_price"]
                old_qty    = abs(pos["net_qty"])
                new_strike = calculate_new_strike(old_strike, dividend_amount)
                new_qty    = calculate_new_qty(old_qty, spot_prev, dividend_amount)
                scenario   = "A" if ex_dt > today else "B"

                notes = (
                    f"₹{dividend_amount}/share | "
                    f"S_prev=₹{spot_prev:.2f} | "
                    f"threshold=₹{0.10 * spot_prev:.2f} | "
                    f"Scenario {scenario} | "
                    f"expiry={pos['expiry_date']}"
                )

                # ── Upsert into fno_dividend_adjustments ─────────────────────
                existing = db.execute(text("""
                    SELECT id, status FROM fno_dividend_adjustments
                    WHERE user_id        = :uid
                      AND underlying     = :und
                      AND instrument_type = :itype
                      AND old_strike     = :os
                      AND ex_date        = :ex
                """), {
                    "uid": user_id, "und": underlying,
                    "itype": pos["instrument_type"],
                    "os": old_strike, "ex": ex_date_str,
                }).first()

                if existing and existing.status in ("APPLIED", "SKIPPED", "USER_UPLOADED"):
                    continue  # already handled

                if existing:
                    db.execute(text("""
                        UPDATE fno_dividend_adjustments
                        SET spot_prev=:sp, new_strike=:ns, new_qty=:nq,
                            dividend_amount=:da, notes=:notes, scenario=:sc,
                            old_qty=:oq
                        WHERE id = :id
                    """), {
                        "sp": spot_prev, "ns": new_strike, "nq": new_qty,
                        "da": dividend_amount, "notes": notes,
                        "sc": scenario, "id": existing.id, "oq": old_qty,
                    })
                    adj_id = existing.id
                else:
                    res = db.execute(text("""
                        INSERT INTO fno_dividend_adjustments
                            (user_id, underlying, instrument_type,
                             old_strike, new_strike, old_qty, new_qty,
                             ex_date, expiry_date, dividend_amount,
                             spot_prev, status, scenario, notes)
                        VALUES
                            (:uid, :und, :itype,
                             :os, :ns, :oq, :nq,
                             :ex, :expiry, :da,
                             :sp, 'PENDING', :sc, :notes)
                    """), {
                        "uid": user_id, "und": underlying,
                        "itype": pos["instrument_type"],
                        "os": old_strike, "ns": new_strike,
                        "oq": old_qty,    "nq": new_qty,
                        "ex": ex_date_str, "expiry": pos["expiry_date"],
                        "da": dividend_amount, "sp": spot_prev,
                        "sc": scenario, "notes": notes,
                    })
                    adj_id = res.lastrowid

                db.commit()

                pending_out.append({
                    "id":              adj_id,
                    "user_id":         user_id,
                    "underlying":      underlying,
                    "instrument_type": pos["instrument_type"],
                    "expiry_date":     pos["expiry_date"],
                    "old_strike":      old_strike,
                    "new_strike":      new_strike,
                    "old_qty":         old_qty,
                    "new_qty":         new_qty,
                    "ex_date":         ex_date_str,
                    "dividend_amount": dividend_amount,
                    "spot_prev":       spot_prev,
                    "status":          "PENDING",
                    "scenario":        scenario,
                    "notes":           notes,
                })

        return pending_out
    except Exception as e:
        logger.error(f"[DivAdj] detect_pending_adjustments error: {e}", exc_info=True)
        return []
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Apply adjustment  (user-confirmed or auto-backfill)
# ─────────────────────────────────────────────────────────────────────────────

def apply_adjustment(user_id: int, adjustment_id: int) -> dict:
    """
    Creates a P&L-neutral synthetic SELL + BUY pair:
      SELL old_strike @ carry_avg  → closes old position at zero gain/loss
      BUY  new_strike @ carry_avg  → reopens at same cost basis

    Updates status → APPLIED and triggers P&L rebuild.
    """
    db = SessionLocal()
    try:
        adj = db.execute(text("""
            SELECT * FROM fno_dividend_adjustments
            WHERE id=:id AND user_id=:uid
        """), {"id": adjustment_id, "uid": user_id}).first()

        if not adj:
            return {"status": "error", "message": "Adjustment record not found"}
        if adj.status in ("APPLIED", "USER_UPLOADED"):
            return {"status": "error", "message": f"Already {adj.status}"}

        ex_date_str = str(adj.ex_date)[:10]

        # ── Fetch avg_price of existing position for cost-basis carry-forward ─
        try:
            ex_dt    = datetime.strptime(ex_date_str, "%Y-%m-%d").date()
            pos_date = (ex_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            pos_date = ex_date_str

        positions  = get_active_fno_positions_on_date(user_id, pos_date)
        carry_avg  = 0.0
        for p in positions:
            if (p["underlying"]      == adj.underlying and
                p["instrument_type"] == adj.instrument_type and
                abs(p["strike_price"] - float(adj.old_strike)) < 1.0):
                carry_avg = p["avg_price"]
                break

        # ── Synthetic SELL — close old position (P&L = carry_avg - carry_avg = 0) ─
        db.execute(text("""
            INSERT INTO fno_synthetic_transactions
                (user_id, adjustment_id, underlying, instrument_type,
                 expiry_date, strike_price, trade_type, quantity,
                 price, trade_date, source, notes)
            VALUES
                (:uid, :adj_id, :und, :itype,
                 :expiry, :strike, 'SELL', :qty,
                 :price, :tdate, 'SYNTHETIC_ADJUSTMENT', :notes)
        """), {
            "uid":    user_id,
            "adj_id": adjustment_id,
            "und":    adj.underlying,
            "itype":  adj.instrument_type,
            "expiry": adj.expiry_date,
            "strike": float(adj.old_strike),
            "qty":    float(adj.old_qty),
            "price":  carry_avg,
            "tdate":  ex_date_str,
            "notes":  (
                f"SYNTHETIC CLOSE: div_adj id={adjustment_id} "
                f"strike={adj.old_strike} carry_avg={carry_avg:.4f}"
            ),
        })

        # ── Synthetic BUY — reopen at new_strike with same cost basis ─────────
        db.execute(text("""
            INSERT INTO fno_synthetic_transactions
                (user_id, adjustment_id, underlying, instrument_type,
                 expiry_date, strike_price, trade_type, quantity,
                 price, trade_date, source, notes)
            VALUES
                (:uid, :adj_id, :und, :itype,
                 :expiry, :new_strike, 'BUY', :new_qty,
                 :price, :tdate, 'SYNTHETIC_ADJUSTMENT', :notes)
        """), {
            "uid":       user_id,
            "adj_id":    adjustment_id,
            "und":       adj.underlying,
            "itype":     adj.instrument_type,
            "expiry":    adj.expiry_date,
            "new_strike": float(adj.new_strike),
            "new_qty":   float(adj.new_qty),
            "price":     carry_avg,
            "tdate":     ex_date_str,
            "notes":     (
                f"SYNTHETIC OPEN: div_adj id={adjustment_id} "
                f"new_strike={adj.new_strike} new_qty={adj.new_qty} carry_avg={carry_avg:.4f}"
            ),
        })

        db.execute(text("""
            UPDATE fno_dividend_adjustments
            SET status='APPLIED', applied_at=NOW()
            WHERE id=:id
        """), {"id": adjustment_id})

        db.commit()

        # ── Rebuild F&O P&L including synthetic rows ──────────────────────────
        rebuild_fno_pnl_with_synthetic(user_id, db)

        return {
            "status":     "success",
            "message":    (
                f"Applied: {adj.underlying} {adj.instrument_type} "
                f"strike {adj.old_strike}→{adj.new_strike}, "
                f"qty {adj.old_qty}→{adj.new_qty}, "
                f"carry_avg=₹{carry_avg:.4f}"
            ),
            "carry_avg": carry_avg,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"[DivAdj] apply_adjustment error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Skip / mark-uploaded helpers
# ─────────────────────────────────────────────────────────────────────────────

def skip_adjustment(user_id: int, adjustment_id: int) -> dict:
    """User will handle it manually (upload adjusted trades). Mark SKIPPED."""
    db = SessionLocal()
    try:
        db.execute(text("""
            UPDATE fno_dividend_adjustments
            SET status='SKIPPED', applied_at=NOW()
            WHERE id=:id AND user_id=:uid
        """), {"id": adjustment_id, "uid": user_id})
        db.commit()
        return {"status": "success", "message": "Adjustment marked as SKIPPED"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


def mark_user_uploaded(user_id: int, adjustment_id: int) -> dict:
    """
    Called when the uploaded broker file already contains post-adjustment trades.
    Mark USER_UPLOADED so the engine does not double-apply.
    """
    db = SessionLocal()
    try:
        db.execute(text("""
            UPDATE fno_dividend_adjustments
            SET status='USER_UPLOADED', applied_at=NOW()
            WHERE id=:id AND user_id=:uid
        """), {"id": adjustment_id, "uid": user_id})
        db.commit()
        return {"status": "success", "message": "Marked as USER_UPLOADED"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# History / pending query helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_pending_adjustments(user_id: int) -> list[dict]:
    """Fetch all PENDING adjustment records (without re-running detection)."""
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT * FROM fno_dividend_adjustments
            WHERE user_id=:uid AND status='PENDING'
            ORDER BY ex_date ASC
        """), {"uid": user_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


def get_adjustment_history(user_id: int) -> list[dict]:
    """Full audit log — all statuses, newest first."""
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT * FROM fno_dividend_adjustments
            WHERE user_id=:uid
            ORDER BY created_at DESC
        """), {"uid": user_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Backfill  (called inside process_fno_file, before P&L rebuild)
# ─────────────────────────────────────────────────────────────────────────────

def backfill_past_adjustments(user_id: int, db) -> dict:
    """
    Called after a new F&O file is uploaded.
    For each PENDING adjustment where ex_date <= today:
      • If new_strike trades already exist in fno_transactions → mark USER_UPLOADED
      • Otherwise → auto-apply synthetically (Scenario B backfill)

    The caller (process_fno_file) should subsequently call
    rebuild_fno_pnl_with_synthetic() instead of rebuild_fno_pnl().

    Returns {"auto_applied": int, "user_uploaded": int}
    """
    today_str = date.today().strftime("%Y-%m-%d")

    pending = db.execute(text("""
        SELECT * FROM fno_dividend_adjustments
        WHERE user_id=:uid AND status='PENDING' AND ex_date <= :today
        ORDER BY ex_date ASC
    """), {"uid": user_id, "today": today_str}).fetchall()

    auto_applied  = 0
    user_uploaded = 0

    for adj in pending:
        ex_date_str = str(adj.ex_date)[:10]

        # Check if new_strike trades already uploaded by the user
        new_trade = db.execute(text("""
            SELECT id FROM fno_transactions
            WHERE user_id         = :uid
              AND underlying      = :und
              AND instrument_type = :itype
              AND ABS(strike_price - :ns) < 1.0
              AND trade_date     >= :ex
            LIMIT 1
        """), {
            "uid":   user_id,
            "und":   adj.underlying,
            "itype": adj.instrument_type,
            "ns":    float(adj.new_strike),
            "ex":    ex_date_str,
        }).first()

        if new_trade:
            db.execute(text("""
                UPDATE fno_dividend_adjustments
                SET status='USER_UPLOADED', applied_at=NOW()
                WHERE id=:id
            """), {"id": adj.id})
            db.commit()
            user_uploaded += 1
            logger.info(
                f"[DivAdj] Backfill: {adj.underlying} {adj.instrument_type} "
                f"ex={ex_date_str} → USER_UPLOADED (new strike found in transactions)"
            )
        else:
            result = apply_adjustment(user_id, adj.id)
            if result.get("status") == "success":
                auto_applied += 1
                logger.info(
                    f"[DivAdj] Backfill: {adj.underlying} {adj.instrument_type} "
                    f"ex={ex_date_str} → AUTO APPLIED synthetically"
                )
            else:
                logger.warning(
                    f"[DivAdj] Backfill auto-apply FAILED for adj_id={adj.id}: {result}"
                )

    return {"auto_applied": auto_applied, "user_uploaded": user_uploaded}


# ─────────────────────────────────────────────────────────────────────────────
# P&L rebuild  (merges real + synthetic transactions)
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_fno_pnl_with_synthetic(user_id: int, db) -> None:
    """
    Full FIFO F&O P&L rebuild that merges:
      • fno_transactions          (real broker-uploaded trades)
      • fno_synthetic_transactions (adjustment bookkeeping)

    Drops and rewrites fno_pnl for this user.
    Because synthetic trades use carry_avg for both SELL and BUY price,
    the gross_pnl contribution of every synthetic pair is exactly 0.
    """
    db.execute(text("DELETE FROM fno_pnl WHERE user_id=:uid"), {"uid": user_id})
    db.commit()

    # ── Fetch real trades ──────────────────────────────────────────────────────
    real_rows = db.execute(text("""
        SELECT underlying, instrument_type, expiry_date, strike_price,
               trade_date, trade_type, quantity, price,
               broker, symbol, exchange
        FROM fno_transactions
        WHERE user_id=:uid
        ORDER BY expiry_date ASC, trade_date ASC, id ASC
    """), {"uid": user_id}).fetchall()

    # ── Fetch synthetic trades ─────────────────────────────────────────────────
    syn_rows = db.execute(text("""
        SELECT underlying, instrument_type, expiry_date, strike_price,
               trade_date, trade_type, quantity, price,
               'SYNTHETIC' AS broker,
               ''           AS symbol,
               'NSE'        AS exchange
        FROM fno_synthetic_transactions
        WHERE user_id=:uid
        ORDER BY expiry_date ASC, trade_date ASC, id ASC
    """), {"uid": user_id}).fetchall()

    # ── Normalise to list[dict] and merge ─────────────────────────────────────
    def _to_dict(r) -> dict:
        return {
            "underlying":      str(r.underlying      or "").strip().upper(),
            "instrument_type": str(r.instrument_type or "").strip().upper(),
            "expiry_date":     str(r.expiry_date     or "")[:10],
            "strike_price":    float(r.strike_price  or 0),
            "trade_date":      str(r.trade_date      or "")[:10],
            "trade_type":      str(r.trade_type      or "").strip().upper(),
            "quantity":        float(r.quantity       or 0),
            "price":           float(r.price          or 0),
            "broker":          str(r.broker           or ""),
            "symbol":          str(r.symbol           or ""),
            "exchange":        str(r.exchange         or "NSE"),
        }

    all_trades = [_to_dict(r) for r in real_rows] + [_to_dict(r) for r in syn_rows]
    # Primary sort: expiry then trade_date (synthetic and real interleaved correctly)
    all_trades.sort(key=lambda x: (x["expiry_date"], x["trade_date"]))

    # ── FIFO matching ──────────────────────────────────────────────────────────
    # Key includes broker so synthetic/real lots are separate FIFO queues.
    # This ensures synthetic SELL always matches its own synthetic BUY carry-avg.
    buy_lots: dict[tuple, list] = defaultdict(list)
    pnl_rows: list[dict] = []

    for t in all_trades:
        key = (
            t["underlying"],
            t["instrument_type"],
            t["expiry_date"],
            t["strike_price"],
            t["broker"],
        )
        qty   = t["quantity"]
        price = t["price"]
        tdate = t["trade_date"]

        if t["trade_type"] == "BUY":
            buy_lots[key].append({"date": tdate, "price": price, "remaining": qty})

        elif t["trade_type"] == "SELL":
            rem = qty
            while rem > 0 and buy_lots[key]:
                lot   = buy_lots[key][0]
                match = min(lot["remaining"], rem)
                gross = round((price - lot["price"]) * match, 2)

                sym = (
                    t["symbol"]
                    or f"{t['underlying']} {t['instrument_type']} "
                       f"{t['expiry_date']} {t['strike_price']:.0f}"
                )
                pnl_rows.append({
                    "uid":    user_id,
                    "sym":    sym,
                    "und":    t["underlying"],
                    "exch":   t["exchange"],
                    "itype":  t["instrument_type"],
                    "exp":    t["expiry_date"],
                    "strike": t["strike_price"],
                    "bdate":  lot["date"],
                    "sdate":  tdate,
                    "bprice": lot["price"],
                    "sprice": price,
                    "qty":    match,
                    "pnl":    gross,
                    "broker": t["broker"],
                })

                lot["remaining"] -= match
                rem             -= match
                if lot["remaining"] <= 0:
                    buy_lots[key].pop(0)

    # ── Insert P&L rows ────────────────────────────────────────────────────────
    for row in pnl_rows:
        try:
            db.execute(text("""
                INSERT INTO fno_pnl
                    (user_id, symbol, underlying, exchange, instrument_type,
                     expiry_date, strike_price, buy_date, sell_date,
                     buy_price, sell_price, quantity, gross_pnl, broker)
                VALUES
                    (:uid, :sym, :und, :exch, :itype,
                     :exp, :strike, :bdate, :sdate,
                     :bprice, :sprice, :qty, :pnl, :broker)
            """), row)
        except Exception as e:
            logger.error(f"[DivAdj] P&L insert error: {e} | row={row}")
            continue

    db.commit()
    logger.info(
        f"[DivAdj] rebuild_fno_pnl_with_synthetic: "
        f"{len(pnl_rows)} P&L rows written for user {user_id}"
    )