"""
suggestion_engine.py
====================
Step 7–8 of the module spec.
Given a symbol + user_id, returns a structured suggestion dict.

Output shape:
{
  "signal":      "SELL_CE" | "SELL_PE" | "SQUARE_OFF" | "ROLLOVER" | "NEUTRAL",
  "reason":      str,
  "strike":      float | None,
  "expiry":      str | None,
  "breakeven":   float | None,
  "confidence":  "HIGH" | "MEDIUM" | "LOW",
  "flags":       list[str],   # e.g. ["corp_event_soon", "low_iv", "near_52w_high"]
  "conditions":  dict | None, # ⭐ NEW: 4-condition breakdown for SELL_CE
}
"""

from __future__ import annotations
from sqlalchemy import text
from backend.database import SessionLocal
from datetime import datetime, timedelta

import logging

logger = logging.getLogger(__name__)

# ⭐ NEW IMPORTS for 4-condition check
try:
    from backend.services.ce_pe_service import get_price_ohlc
except ImportError:
    get_price_ohlc = None

try:
    from backend.services.analysis_service import get_seasonal_pattern
except ImportError:
    get_seasonal_pattern = None

# ─────────────────────────────────────────────────────────────────────────────
# ⭐ NEW: 4-Condition Check Helpers for SELL_CE Eligibility
# ─────────────────────────────────────────────────────────────────────────────


def _check_profit_condition_wl(avg_buy_price: float, spot: float) -> dict:
    """
    CONDITION 1: Profit Check
    Is the holding in profit or loss?
    """
    if not (avg_buy_price > 0 and spot > 0):
        return {
            "status": "⚠️",
            "message": "Cannot determine profit (missing price data)",
            "profit_pct": 0.0,
        }

    profit_pct = ((spot - avg_buy_price) / avg_buy_price) * 100

    if profit_pct > 0:
        return {
            "status": "✅",
            "message": f"In profit (+{profit_pct:.1f}%)",
            "profit_pct": round(profit_pct, 2),
        }
    else:
        return {
            "status": "⚠️",
            "message": f"In loss ({profit_pct:.1f}%). Avoid selling CE.",
            "profit_pct": round(profit_pct, 2),
        }


def _check_market_condition_wl(spot: float, high_52w: float) -> dict:
    """
    CONDITION 2: Market Condition
    Is stock near 52W high?
    """
    if not (spot > 0 and high_52w > 0):
        return {
            "status": "⚠️",
            "message": "Cannot determine 52W high",
            "pct_to_52w": 0.0,
        }

    pct_to_high = ((high_52w - spot) / high_52w) * 100

    if pct_to_high <= 5:  # Within 5% of 52W high
        return {
            "status": "✅",
            "message": f"Near 52W high ({100-pct_to_high:.1f}%). Good for CE.",
            "pct_to_52w": round(pct_to_high, 2),
        }
    else:
        return {
            "status": "⚠️",
            "message": f"Still {pct_to_high:.1f}% below 52W high. More room to rise.",
            "pct_to_52w": round(pct_to_high, 2),
        }


def _check_seasonal_condition_wl(symbol: str) -> dict:
    """
    CONDITION 3: Seasonal Pattern Check
    Is current month BEST/WORST/NEUTRAL seasonally?
    """
    if not get_seasonal_pattern:
        return {
            "status": "✅",
            "message": "Seasonal check unavailable. Proceed.",
            "season_rank": "neutral",
        }

    try:
        db = SessionLocal()
        try:
            row = db.execute(
                text(
                    "SELECT scrip_code FROM scrip_master_cache WHERE UPPER(symbol_root) = :sym AND scrip_code IS NOT NULL LIMIT 1"
                ),
                {"sym": symbol.upper()},
            ).first()

            if not row or not row.scrip_code:
                return {
                    "status": "✅",
                    "message": "Seasonal data unavailable. Proceed.",
                    "season_rank": "neutral",
                }

            seasonal = get_seasonal_pattern(int(row.scrip_code))
            rank = seasonal.get("current_month_rank", "neutral")
            best_month = seasonal.get("best_month", "")
            worst_month = seasonal.get("worst_month", "")

            if rank == "best":
                return {
                    "status": "⏸️",
                    "message": f"Best seasonal month ({best_month}). WAIT for rally first.",
                    "season_rank": "best",
                }
            elif rank == "worst":
                return {
                    "status": "✅",
                    "message": f"Worst seasonal month ({worst_month}). Good to sell CE.",
                    "season_rank": "worst",
                }
            else:
                return {
                    "status": "✅",
                    "message": f"Neutral seasonal month. Proceed.",
                    "season_rank": "neutral",
                }
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[Seasonal] Error for {symbol}: {e}")
        return {
            "status": "✅",
            "message": "Seasonal check skipped. Proceed.",
            "season_rank": "neutral",
        }


def _check_lot_size_condition_wl(symbol: str, eq_qty: float = 0) -> dict:
    """
    CONDITION 4: Lot Size Check
    Does user have enough shares for at least 1 F&O lot?
    """
    try:
        db = SessionLocal()
        try:
            rows = db.execute(
                text("""
                    SELECT lot_size FROM scrip_master_cache
                    WHERE exch='N' AND exch_type='D' AND scrip_type='CE'
                      AND UPPER(symbol_root)=:sym
                      AND expiry >= CURDATE()
                      AND lot_size > 1
                    LIMIT 1
                """),
                {"sym": symbol.upper()},
            ).fetchall()

            if not rows or not rows[0].lot_size or rows[0].lot_size <= 0:
                return {
                    "status": "⚠️",
                    "message": "No F&O contracts available (stock not in derivatives)",
                    "lot_size": 0,
                    "qty": int(eq_qty),
                }

            lot_size = int(rows[0].lot_size)
            qty_available = int(eq_qty)

            # Check lot completeness
            if qty_available >= lot_size:
                complete_lots = qty_available // lot_size
                return {
                    "status": "✅",
                    "message": f"Valid lot ({lot_size} shares). You have {complete_lots} lot(s).",
                    "lot_size": lot_size,
                    "qty": qty_available,
                }
            else:
                return {
                    "status": "⚠️",
                    "message": f"Insufficient shares. You have {qty_available}, need {lot_size}.",
                    "lot_size": lot_size,
                    "qty": qty_available,
                }
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[LotSize] Error for {symbol}: {e}")
        return {
            "status": "⚠️",
            "message": "Cannot verify F&O availability",
            "lot_size": 0,
            "qty": int(eq_qty),
        }


def _evaluate_sell_ce_conditions_wl(
    symbol: str,
    avg_buy_price: float,
    spot: float,
    high_52w: float,
    eq_qty: float = 0,
) -> dict:
    """
    MAIN EVALUATION: Combines all 4 conditions for SELL_CE

    Returns:
      {
        "verdict": "✅ READY" | "⚠️ AVOID" | "⏸️ WAIT",
        "detail": str,
        "profit": {...},
        "market": {...},
        "seasonal": {...},
        "lot_size": {...},
        "pass_count": int,
      }
    """
    profit_check = _check_profit_condition_wl(avg_buy_price, spot)
    market_check = _check_market_condition_wl(spot, high_52w)
    seasonal_check = _check_seasonal_condition_wl(symbol)
    lot_check = _check_lot_size_condition_wl(symbol, eq_qty)

    all_checks = [profit_check, market_check, seasonal_check, lot_check]
    pass_count = sum(1 for c in all_checks if c.get("status", "").startswith("✅"))

    # Decision logic
    if lot_check.get("status", "").startswith("⚠️"):
        verdict = "⚠️ AVOID"
        detail = f"No F&O or insufficient shares ({eq_qty:.0f} owned, need {lot_check.get('lot_size', '?')})"
    elif profit_check.get("status", "").startswith("⚠️"):
        verdict = "⚠️ AVOID"
        detail = f"Holding in loss. Wait for recovery to break-even."
    elif seasonal_check.get("status", "").startswith("⏸️"):
        verdict = "⏸️ WAIT"
        detail = seasonal_check.get(
            "message", "Seasonal conditions not favorable right now"
        )
    elif pass_count == 4:
        verdict = "✅ READY"
        detail = "All 4 conditions favorable. Write covered call now."
    elif pass_count >= 2:
        verdict = "✅ READY"
        detail = "Most conditions favorable. Proceed with caution."
    else:
        verdict = "⚠️ AVOID"
        detail = "Multiple unfavorable conditions. Do not write CE."

    return {
        "verdict": verdict,
        "detail": detail,
        "profit": profit_check,
        "market": market_check,
        "seasonal": seasonal_check,
        "lot_size": lot_check,
        "pass_count": pass_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_open_positions(user_id: int, symbol: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
            SELECT instrument_type, open_qty, avg_price, expiry_date, strike_price
            FROM fno_open_positions
            WHERE user_id = :uid
              AND UPPER(underlying) = :sym
              AND ABS(open_qty) > 0.001
        """),
            {"uid": user_id, "sym": symbol.upper()},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


def _get_holding(user_id: int, symbol: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
            SELECT quantity, avg_buy_price
            FROM holdings
            WHERE user_id = :uid
              AND UPPER(symbol) = :sym
              AND quantity > 0
            LIMIT 1
        """),
            {"uid": user_id, "sym": symbol.upper()},
        ).first()
        return dict(row._mapping) if row else None
    finally:
        db.close()


def _has_corp_event_soon(symbol: str, days: int = 30) -> bool:
    db = SessionLocal()
    try:
        cutoff = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        row = db.execute(
            text("""
            SELECT id FROM corporate_actions
            WHERE UPPER(symbol) = :sym
              AND ex_date BETWEEN :today AND :cutoff
            LIMIT 1
        """),
            {"sym": symbol.upper(), "today": today, "cutoff": cutoff},
        ).first()
        return row is not None
    finally:
        db.close()


def _days_to_expiry(expiry_str: str) -> int:
    try:
        exp = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d")
        return (exp - datetime.now()).days
    except Exception:
        return 999


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────
def _select_strike_expiry(symbol: str, spot: float, signal_type: str) -> dict:
    """
    Query scrip_master_cache for OTM options.
    Returns {strike, expiry, premium, lot_size, breakeven} or {}
    """
    if spot <= 0:
        return {}

    itype = "CE" if signal_type == "SELL_CE" else "PE"
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
            SELECT strike_rate, lot_size, expiry, scrip_data, scrip_code, symbol_root
            FROM scrip_master_cache
            WHERE exch='N' AND exch_type='D' AND scrip_type=:itype
              AND UPPER(symbol_root)=:sym
              AND expiry >= CURDATE()
              AND lot_size > 1
            ORDER BY expiry ASC, 
              CASE WHEN :itype = 'CE' THEN strike_rate ELSE -strike_rate END ASC
        """),
            {"itype": itype, "sym": symbol.upper()},
        ).fetchall()

        if not rows:
            return {}

        for row in rows:
            # ✅ CRITICAL SAFEGUARD: Ensure the database actually returned the correct underlying
            if row.symbol_root.upper() != symbol.upper():
                continue

            strike = float(row.strike_rate or 0)
            lot = int(row.lot_size or 0)
            if lot <= 0:
                continue

            # OTM filter
            if itype == "CE" and strike <= spot:
                continue
            if itype == "PE" and strike >= spot:
                continue

            # ... rest of your existing logic remains exactly the same ...

            # Skip strikes too far OTM (more than 10% from spot)
            pct_from_spot = abs(strike - spot) / spot * 100
            if pct_from_spot > 10:
                continue

            # Intrinsic premium estimate (conservative): use 1-2% of spot
            est_premium = round(spot * 0.015, 2)
            total_premium = est_premium * lot

            if total_premium < 10000:
                continue

            expiry_str = str(row.expiry or "")[:10]
            if itype == "CE":
                breakeven = round(strike + est_premium, 2)
            else:
                breakeven = round(strike - est_premium, 2)

            return {
                "strike": round(strike, 0),
                "expiry": expiry_str,
                "premium": est_premium,
                "lot_size": lot,
                "breakeven": breakeven,
                "note": "Premium estimated (live option price not available)",
            }

        return {}
    except Exception as e:
        logger.error(
            f"[SuggestionEngine] _select_strike_expiry error: {e}", exc_info=True
        )
        return {}
    finally:
        db.close()


def get_suggestion(
    symbol: str,
    user_id: int,
    spot: float = 0.0,
    high_1m: float = 0.0,
    low_1m: float = 0.0,
    high_52w: float = 0.0,
    low_52w: float = 0.0,
) -> dict:
    flags: list[str] = []
    signal: str = "NEUTRAL"
    reason: str = ""
    strike: float | None = None
    expiry: str | None = None
    breakeven: float | None = None
    confidence: str = "LOW"
    conditions: dict | None = None  # ⭐ NEW

    # ── Module 3: Account Router check ───────────────────────────────────────
    account_role = "master"
    master_user_id = None
    if user_id:
        try:
            from backend.services.account_router import get_account_role_and_master

            account_role, master_user_id = get_account_role_and_master(user_id)
        except Exception as e:
            logger.error(f"[SuggestionEngine] account_router error: {e}", exc_info=True)

    # Child account: check if master already has a short CE on this symbol
    if account_role == "child" and master_user_id:
        try:
            from backend.services.account_router import get_master_open_ce_symbols

            master_ce_symbols = get_master_open_ce_symbols(master_user_id)
            if symbol.upper() in master_ce_symbols:
                flags.append("master_has_ce")
                return {
                    "signal": "NEUTRAL",
                    "reason": (
                        f"🚫 Master account already has a short CE on {symbol}. "
                        f"Do not duplicate this position."
                    ),
                    "strike": None,
                    "expiry": None,
                    "breakeven": None,
                    "confidence": "HIGH",
                    "flags": flags,
                    "account_role": "child",
                    "conditions": None,
                }
        except Exception as e:
            logger.error(
                f"[SuggestionEngine] master CE check error: {e}", exc_info=True
            )

    corp_event = _has_corp_event_soon(symbol)
    # ... rest of function continues
    if corp_event:
        flags.append("corp_event_soon")

    holding = _get_holding(user_id, symbol)
    positions = _get_open_positions(user_id, symbol)

    # ── Step 7: Existing position — correction / profit mode ─────────────────
    for pos in positions:
        itype = pos["instrument_type"]
        qty = float(pos["open_qty"])
        avg = float(pos["avg_price"])
        exp_str = pos.get("expiry_date", "")
        dte = _days_to_expiry(exp_str)
        pnl = (spot - avg) * qty if spot else 0

        # Rollover check
        if dte < 15:
            if pnl < -5000:  # in meaningful loss
                flags.append("loss_near_expiry")
                signal = "SQUARE_OFF"
                reason = (
                    f"⚠️ Position expiring in {dte} days AND in loss of ₹{abs(pnl):,.0f}. "
                    f"Do NOT rollover a losing position — square off first to cut losses."
                )
                confidence = "HIGH"
                expiry = exp_str
                break
            else:
                flags.append(f"rollover_due_{itype}")
                signal = "ROLLOVER"
                reason = f"Expiry in {dte} days — consider rolling to next month"
                confidence = "HIGH"
                expiry = exp_str
                break

        # Profit path
        if pnl >= 10_000:
            signal = "SQUARE_OFF"
            reason = f"P&L ₹{pnl:,.0f} ≥ ₹10,000 on {itype} — take profit"
            confidence = "HIGH"
            break

        # Loss path — defense mode
        if pnl < 0 and spot > 0:
            strike_pos = float(pos.get("strike_price", 0) or 0)
            breakeven = avg  # simple BE = avg buy price

            if itype == "FUT" and qty > 0:
                signal = "SELL_CE"
                reason = "FUT long in loss — sell CE at FUT breakeven to hedge"
                strike = breakeven
                confidence = "MEDIUM"
            elif itype == "PE" and qty < 0:
                signal = "SELL_CE"
                reason = "Short PE losing — sell CE to create strangle"
                strike = breakeven
                confidence = "MEDIUM"
            elif itype == "CE" and qty < 0:
                signal = "SELL_PE"
                reason = "Short CE losing — sell PE to create strangle"
                strike = breakeven
                confidence = "MEDIUM"
            expiry = exp_str
            break

    # ── Step 8: Fresh signal — no existing F&O positions ─────────────────────
    if signal == "NEUTRAL" and spot > 0:

        # Block if corp event near
        if corp_event:
            reason = "Corporate event within 30 days — avoid new positions"
            confidence = "LOW"

        # Overbought → sell CE
        elif high_1m > 0 and spot >= (high_1m * 0.95):
            flags.append("near_1m_high")
            if holding and float(holding["avg_buy_price"]) < spot:
                # In profit on holding — covered call candidate
                signal = "SELL_CE"
                reason = f"Spot near 1M high (₹{high_1m:,.0f}). Holding in profit — write covered call"
                confidence = "HIGH"

                # ⭐ NEW: Add 4-condition check for SELL_CE
                eq_qty = float(holding.get("quantity", 0))
                conditions = _evaluate_sell_ce_conditions_wl(
                    symbol=symbol,
                    avg_buy_price=float(holding["avg_buy_price"]),
                    spot=spot,
                    high_52w=high_52w if high_52w > 0 else spot + 5000,
                    eq_qty=eq_qty,
                )
                flags.append(f"ce_conditions_{conditions.get('pass_count', 0)}_pass")

            else:
                signal = "SELL_CE"
                reason = f"Spot near 1M high — overbought signal"
                confidence = "MEDIUM"
                conditions = None

        # Oversold → sell PE
        elif low_1m > 0 and spot <= (low_1m * 1.05):
            flags.append("near_1m_low")
            signal = "SELL_PE"
            reason = f"Spot near 1M low (₹{low_1m:,.0f}) — oversold, sell PE"
            confidence = "MEDIUM"
        if signal in ("SELL_CE", "SELL_PE") and not strike:
            option_info = _select_strike_expiry(symbol, spot, signal)
            if option_info:
                strike = option_info.get("strike")
                expiry = option_info.get("expiry")
                breakeven = option_info.get("breakeven")
                if not reason.endswith("."):
                    reason += f". Suggested: {signal} strike ₹{strike:,.0f}, expiry {expiry}, BE ₹{breakeven:,.0f}"
                confidence = "MEDIUM"
            else:
                # ✅ ADD THIS ELSE BLOCK TO FIX THE BUG
                signal = "NEUTRAL"
                reason = (
                    f"Stock near 1M low, but no F&O contracts available for {symbol}."
                )
                confidence = "LOW"
        # 52W context flags (informational only)
        if high_52w > 0 and spot >= (high_52w * 0.95):
            flags.append("near_52w_high")
        if low_52w > 0 and spot <= (low_52w * 1.05):
            flags.append("near_52w_low")

        if not reason:
            reason = "No clear signal — price in mid-range"

    # ⭐ NEW: Build return with conditions if present
    result = {
        "signal": signal,
        "reason": reason,
        "strike": round(strike, 0) if strike else None,
        "expiry": str(expiry)[:10] if expiry else None,
        "breakeven": round(breakeven, 2) if breakeven else None,
        "confidence": confidence,
        "flags": flags,
    }

    # ⭐ NEW: Add conditions to result if evaluated
    if conditions:
        result["conditions"] = {
            "profit": conditions.get("profit"),
            "market": conditions.get("market"),
            "seasonal": conditions.get("seasonal"),
            "lot_size": conditions.get("lot_size"),
        }
        result["condition_verdict"] = conditions.get("verdict")
        result["condition_detail"] = conditions.get("detail")

    return result
