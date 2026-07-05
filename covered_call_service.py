"""
covered_call_service.py
========================
Service functions for:
  1. get_covered_call_analysis(user_id)
     → Table A: Covered call positions (Sold CE + matching holding/FUT)
     → Table B: Uncovered holdings/FUTs (no sold CE yet)
     → Table C: Correction module (loss > ₹10,000)

  2. get_master_reference_positions(requesting_account_id)
     → Fetches Account 1 (master) uncovered positions for child accounts to reference
     → Filters OUT positions that are already covered calls in Account 1

Logic:
  - "Covered Call" = has sold CE (open_qty < 0, instrument_type='CE')
                     AND (equity holding > 0 OR long FUT open_qty > 0)
                     on the same underlying/canonical symbol
  - "Uncovered"    = has holding > 0 OR long FUT, but NO sold CE
  - "Correction"   = any open position with unrealized loss > ₹10,000
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Dict, List

from sqlalchemy import text
from database import SessionLocal
from services.symbol_resolver import get_canonical
from services.ce_pe_service import _get_atm_option_info, _get_two_expiry_option_info, _get_5paisa_client
import logging
logger = logging.getLogger(__name__)
# ─────────────────────────────────────────────────────────────────────────────
# Safe float helper
# ─────────────────────────────────────────────────────────────────────────────

def _safe(v) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Helper: resolve canonical symbol for a raw symbol via stock_master_mapping
# ─────────────────────────────────────────────────────────────────────────────

def _get_canonical(raw_symbol: str, db) -> str:
    """Return canonical NSE ticker for a raw broker symbol."""
    sym = str(raw_symbol or "").strip().upper()
    if not sym:
        return raw_symbol
    try:
        row = db.execute(
            text("""
                SELECT sm.canonical_symbol
                FROM user_stock_symbol_mapping usm
                JOIN stock_master_mapping sm ON sm.isin = usm.isin
                WHERE UPPER(usm.symbol) = :sym
                  AND sm.canonical_symbol IS NOT NULL
                  AND sm.canonical_symbol != ''
                LIMIT 1
            """),
            {"sym": sym}
        ).first()
        if row and row.canonical_symbol:
            return row.canonical_symbol.strip().upper()
    except Exception:
        pass
    return sym


# ─────────────────────────────────────────────────────────────────────────────
# Helper: load all users belonging to an account
# ─────────────────────────────────────────────────────────────────────────────

def _get_user_ids_for_account(account_id: int, db) -> List[int]:
    rows = db.execute(
        text("SELECT id FROM users WHERE account_id = :aid"),
        {"aid": account_id}
    ).fetchall()
    return [r.id for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Core data loaders
# ─────────────────────────────────────────────────────────────────────────────

def _load_holdings_map(user_ids: List[int], db) -> Dict[str, dict]:
    """
    Returns { canonical_upper: {qty, avg_buy_price, total_invested} }
    Aggregated across all user_ids (for group/account aggregation).
    """
    if not user_ids:
        return {}
    uid_list = ",".join(str(u) for u in user_ids)
    rows = db.execute(
        text(f"""
            SELECT h.symbol, h.quantity, h.avg_buy_price, h.total_invested,
                   sm.canonical_symbol
            FROM holdings h
            LEFT JOIN user_stock_symbol_mapping usm
                ON usm.user_id = h.user_id AND UPPER(usm.symbol) = UPPER(h.symbol)
            LEFT JOIN stock_master_mapping sm ON sm.isin = usm.isin
            WHERE h.user_id IN ({uid_list})
              AND h.quantity > 0
              AND h.segment = 'EQ'
              AND sm.fno_available = 1
        """)
    ).fetchall()

    result: Dict[str, dict] = defaultdict(lambda: {"qty": 0.0, "avg_buy_price": 0.0, "total_invested": 0.0, "symbol": ""})
    for r in rows:
        can = str(r.canonical_symbol or r.symbol or "").strip().upper()
        if not can:
            continue
        result[can]["qty"]           += _safe(r.quantity)
        result[can]["total_invested"] += _safe(r.total_invested)
        result[can]["symbol"]          = r.symbol
        # Recalculate weighted avg
        total_qty = result[can]["qty"]
        if total_qty > 0:
            result[can]["avg_buy_price"] = result[can]["total_invested"] / total_qty

    return dict(result)


def _load_fno_positions_map(user_ids: List[int], db) -> Dict[str, List[dict]]:
    """
    Returns { canonical_upper: [ {instrument_type, strike, expiry, open_qty, avg_price}, ... ] }
    Snapshot table first; falls back to computed from transactions.
    Only non-expired contracts.
    """
    if not user_ids:
        return {}
    uid_list   = ",".join(str(u) for u in user_ids)
    today_str  = date.today().isoformat()

    rows = db.execute(
        text(f"""
            SELECT underlying, instrument_type, strike_price, expiry_date,
                   open_qty, avg_price, unrealized_pnl, closing_price
            FROM fno_open_positions
            WHERE user_id IN ({uid_list})
              AND ABS(open_qty) > 0.001
              AND (expiry_date IS NULL OR expiry_date >= :today)
        """),
        {"today": today_str}
    ).fetchall()

    if not rows:
        # Fallback: compute net from transactions
        rows_txn = db.execute(
            text(f"""
                SELECT underlying, instrument_type, expiry_date, strike_price,
                       SUM(CASE WHEN trade_type='BUY' THEN quantity ELSE 0 END) AS buy_qty,
                       SUM(CASE WHEN trade_type='SELL' THEN quantity ELSE 0 END) AS sell_qty,
                       SUM(CASE WHEN trade_type='BUY' THEN quantity*price ELSE 0 END) AS buy_val,
                       SUM(CASE WHEN trade_type='SELL' THEN quantity*price ELSE 0 END) AS sell_val
                FROM fno_transactions
                WHERE user_id IN ({uid_list})
                  AND (expiry_date IS NULL OR expiry_date >= :today)
                GROUP BY underlying, instrument_type, expiry_date, strike_price
                HAVING ABS(SUM(CASE WHEN trade_type='BUY' THEN quantity ELSE -quantity END)) > 0.001
            """),
            {"today": today_str}
        ).fetchall()

        result: Dict[str, List[dict]] = defaultdict(list)
        for r in rows_txn:
            bq  = _safe(r.buy_qty)
            sq  = _safe(r.sell_qty)
            net = bq - sq
            if abs(net) < 0.001:
                continue
            avg = (_safe(r.buy_val) / bq) if net > 0 and bq > 0 else (
                  (_safe(r.sell_val) / sq) if sq > 0 else 0.0)
            can = str(r.underlying or "").strip().upper()
            result[can].append({
                "instrument_type": str(r.instrument_type or "").upper(),
                "strike":          _safe(r.strike_price),
                "expiry":          str(r.expiry_date or "")[:10],
                "open_qty":        net,
                "avg_price":       avg,
                "unrealized_pnl":  0.0,
                "closing_price":   0.0,
            })
        return dict(result)

    result: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        can = str(r.underlying or "").strip().upper()
        result[can].append({
            "instrument_type": str(r.instrument_type or "").upper(),
            "strike":          _safe(r.strike_price),
            "expiry":          str(r.expiry_date or "")[:10],
            "open_qty":        _safe(r.open_qty),
            "avg_price":       _safe(r.avg_price),
            "unrealized_pnl":  _safe(r.unrealized_pnl),
            "closing_price":   _safe(r.closing_price),
        })
    return dict(result)


# ─────────────────────────────────────────────────────────────────────────────
# Correction suggestions
# ─────────────────────────────────────────────────────────────────────────────

def _correction_suggestion(pos: dict, spot: float) -> str:
    """
    Generate a plain-text suggestion for a losing position.
    spot=0 means we don't have live price; use closing_price fallback.
    """
    itype   = pos["instrument_type"]
    strike  = pos["strike"]
    avg     = pos["avg_price"]
    qty     = pos["open_qty"]
    expiry  = pos["expiry"]
    pnl     = pos["unrealized_pnl"]
    cmp     = spot if spot > 0 else pos.get("closing_price", 0)

    suggestions = []

    if itype == "FUT":
        if qty > 0:  # long FUT, price falling
            suggestions.append(
                f"Long FUT @ avg ₹{avg:,.2f} — price falling. "
                f"Consider: SELL CE at strike ≈ ₹{avg:,.0f} (your breakeven) "
                f"to collect premium and reduce cost basis."
            )
        else:  # short FUT, price rising
            suggestions.append(
                f"Short FUT @ avg ₹{avg:,.2f} — price rising. "
                f"Consider: BUY CE at higher strike as hedge, or roll to next expiry."
            )
    elif itype == "CE":
        if qty < 0:  # sold CE, price rising toward strike
            suggestions.append(
                f"Sold CE @ strike ₹{strike:,.0f} — spot ₹{cmp:,.0f} approaching. "
                f"Consider: BUY PE at lower strike (convert to short strangle) "
                f"or roll CE to higher strike / next expiry."
            )
        else:  # bought CE losing value
            suggestions.append(
                f"Bought CE @ strike ₹{strike:,.0f}, avg ₹{avg:,.2f} — losing. "
                f"Consider: SELL near ATM CE to partially recover premium "
                f"(create bear spread) or exit if DTE < 10."
            )
    elif itype == "PE":
        if qty < 0:  # sold PE, price falling toward strike
            suggestions.append(
                f"Sold PE @ strike ₹{strike:,.0f} — spot ₹{cmp:,.0f} falling. "
                f"Consider: BUY CE at higher strike (convert to short strangle) "
                f"or roll PE to lower strike / next expiry."
            )
        else:  # bought PE losing value
            suggestions.append(
                f"Bought PE @ strike ₹{strike:,.0f}, avg ₹{avg:,.2f} — losing. "
                f"Consider: SELL near ATM PE to partially recover premium "
                f"(create bull spread) or exit if DTE < 10."
            )

    # DTE check
    if expiry:
        try:
            from datetime import datetime
            exp_dt = datetime.strptime(expiry, "%Y-%m-%d").date()
            dte    = (exp_dt - date.today()).days
            if 0 <= dte < 15:
                suggestions.append(
                    f"⚠️ Expiry in {dte} days — consider rolling to next month "
                    f"before time decay accelerates losses."
                )
        except Exception:
            pass

    return " | ".join(suggestions) if suggestions else "Review position manually."


# ─────────────────────────────────────────────────────────────────────────────
# Main: Covered Call Analysis
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# NEW: Sell Call Condition Checks (4 conditions for Table B eligibility)
# ─────────────────────────────────────────────────────────────────────────────

def _check_profit_condition(avg_buy_price: float, spot: float) -> dict:
    """
    CONDITION 1: Profit Check
    Compares avg_buy_price vs current LTP.
    If in loss, confidence drops and avoids "Buy" suggestion.
    
    Returns:
      {
        "status": "✅ PASS" | "⚠️ FAIL",
        "message": str,
        "profit_pct": float,
      }
    """
    if not (avg_buy_price > 0 and spot > 0):
        return {
            "status": "⚠️ FAIL",
            "message": "Cannot determine profit (missing price data)",
            "profit_pct": 0.0,
        }
    
    profit_pct = ((spot - avg_buy_price) / avg_buy_price) * 100
    
    if profit_pct > 0:
        return {
            "status": "✅ PASS",
            "message": f"Holding in profit (+{profit_pct:.1f}%)",
            "profit_pct": round(profit_pct, 2),
        }
    else:
        return {
            "status": "⚠️ FAIL",
            "message": f"Holding in loss ({profit_pct:.1f}%). Wait for recovery.",
            "profit_pct": round(profit_pct, 2),
        }


def _check_market_condition(spot: float, high_52w: float) -> dict:
    """
    CONDITION 2: Market Condition
    Checks if market/stock is near all-time high (within 5% of 52W high).
    If yes, boosts confidence for selling CE (correction more likely).
    
    Returns:
      {
        "status": "✅ PASS" | "⚠️ FAIL",
        "message": str,
        "pct_to_52w_high": float,
      }
    """
    if not (spot > 0 and high_52w > 0):
        return {
            "status": "⚠️ FAIL",
            "message": "Cannot determine 52W high (missing data)",
            "pct_to_52w_high": 0.0,
        }
    
    pct_to_high = ((high_52w - spot) / high_52w) * 100
    
    if pct_to_high <= 5:  # Within 5% of 52W high
        return {
            "status": "✅ PASS",
            "message": f"Near 52W high ({100 - pct_to_high:.1f}%). Correction likely.",
            "pct_to_52w_high": round(pct_to_high, 2),
        }
    else:
        return {
            "status": "⚠️ FAIL",
            "message": f"Stock {pct_to_high:.1f}% below 52W high. Still room to rise.",
            "pct_to_52w_high": round(pct_to_high, 2),
        }


def _check_seasonal_condition(symbol: str) -> dict:
    """
    CONDITION 3: Seasonal Pattern Check
    Checks if current month is "BEST" or "WORST" seasonally.
    
    BEST month   → WAIT for price rally before suggesting strike
    WORST month  → IMMEDIATELY suggest strike and premium
    
    Returns:
      {
        "status": "✅ READY" | "⏸️ WAIT",
        "message": str,
        "season_rank": "best" | "worst" | "neutral",
      }
    """
    try:
        # Import the seasonal pattern analyzer
        from services.analysis_service import get_seasonal_pattern
        
        # We need scrip_code. Let's fetch it from DB.
        db = SessionLocal()
        try:
            row = db.execute(
                text("""
                    SELECT scrip_code FROM scrip_master_cache
                    WHERE UPPER(symbol_root) = :sym
                    AND scrip_code IS NOT NULL
                    LIMIT 1
                """),
                {"sym": symbol.upper()}
            ).first()
            
            if not row or not row.scrip_code:
                return {
                    "status": "✅ READY",
                    "message": "Seasonal data not available. Proceed.",
                    "season_rank": "neutral",
                }
            
            scrip_code = int(row.scrip_code)
        finally:
            db.close()
        
        # Get seasonal pattern
        seasonal = get_seasonal_pattern(scrip_code)
        rank = seasonal.get("current_month_rank", "neutral")
        best_month = seasonal.get("best_month", "")
        worst_month = seasonal.get("worst_month", "")
        
        if rank == "best":
            return {
                "status": "⏸️ WAIT",
                "message": f"Currently in {best_month} (best seasonal month). WAIT for rally, then strike.",
                "season_rank": "best",
            }
        elif rank == "worst":
            return {
                "status": "✅ READY",
                "message": f"Currently in {worst_month} (worst seasonal month). IMMEDIATELY suggest strike.",
                "season_rank": "worst",
            }
        else:
            return {
                "status": "✅ READY",
                "message": f"Neutral seasonal month (Best: {best_month}, Worst: {worst_month}). Proceed.",
                "season_rank": "neutral",
            }
    
    except Exception as e:
        logger.debug(f"[Seasonal] Error for {symbol}: {e}")
        return {
            "status": "✅ READY",
            "message": "Seasonal check skipped (data unavailable). Proceed.",
            "season_rank": "neutral",
        }


def _check_lot_size_condition(symbol: str, eq_qty: float = 0) -> dict:
    """
    CONDITION 4: Lot Size Check (with Completeness)
    Verifies if a valid F&O lot exists AND if user has enough shares.
    
    Returns:
      {
        "status": "✅ PASS" | "⚠️ FAIL",
        "message": str,
        "lot_exists": bool,
        "lot_size": int,
        "qty_available": int,
      }
    """
    try:
        db = SessionLocal()
        try:
            rows = db.execute(
                text("""
                    SELECT lot_size
                    FROM scrip_master_cache
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
                    "status": "⚠️ FAIL",
                    "message": "No F&O contracts available (stock not in derivative universe)",
                    "lot_exists": False,
                    "lot_size": 0,
                    "qty_available": int(eq_qty),
                }
            
            lot_size = int(rows[0].lot_size)
            qty_available = int(eq_qty)
            
            # Check lot completeness: do we have at least 1 lot?
            if qty_available >= lot_size:
                # Calculate how many complete lots
                complete_lots = qty_available // lot_size
                return {
                    "status": "✅ PASS",
                    "message": f"Valid F&O lot exists ({lot_size} share lot). You have {complete_lots} complete lot(s).",
                    "lot_exists": True,
                    "lot_size": lot_size,
                    "qty_available": qty_available,
                }
            else:
                # Not enough shares for even 1 lot
                return {
                    "status": "⚠️ FAIL",
                    "message": f"Insufficient shares for 1 lot. You have {qty_available} shares, need {lot_size}.",
                    "lot_exists": True,  # Lot exists, but user doesn't have enough
                    "lot_size": lot_size,
                    "qty_available": qty_available,
                }
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[LotSize] Error for {symbol}: {e}")
        return {
            "status": "⚠️ FAIL",
            "message": "Cannot verify F&O availability",
            "lot_exists": False,
            "lot_size": 0,
            "qty_available": int(eq_qty),
        }

def _evaluate_sell_call_conditions(
    symbol: str,
    avg_buy_price: float,
    spot: float,
    high_52w: float,
    eq_qty: float = 0,
) -> dict:
    """
    MAIN EVALUATION LOGIC
    Combines all 4 conditions into a single decision.
    
    Returns:
      {
        "overall_status": "✅ READY" | "⚠️ AVOID" | "⏸️ WAIT",
        "note": str (for Table B "Note" column),
        "conditions": {
          "profit": {...},
          "market": {...},
          "seasonal": {...},
          "lot_size": {...},
        },
        "confidence_score": int (1-5),
      }
    """
    # Evaluate each condition
    profit_check     = _check_profit_condition(avg_buy_price, spot)
    market_check     = _check_market_condition(spot, high_52w)
    seasonal_check   = _check_seasonal_condition(symbol)
    lot_check        = _check_lot_size_condition(symbol, eq_qty)  
    
    # Count passes and fails
    all_checks = [profit_check, market_check, seasonal_check, lot_check]
    pass_count = sum(1 for c in all_checks if c["status"].startswith("✅"))
    fail_count = sum(1 for c in all_checks if c["status"].startswith("⚠️"))
    wait_count = sum(1 for c in all_checks if c["status"].startswith("⏸️"))
    
    # Decision logic
    if lot_check["status"].startswith("⚠️"):
        # If lot doesn't exist, always AVOID
        overall_status = "⚠️ AVOID"
        note = f"⚠️ AVOID: No F&O contracts. {lot_check['message']}"
        confidence = 1
    
    elif profit_check["status"].startswith("⚠️"):
        # If in loss, AVOID or WAIT
        overall_status = "⚠️ AVOID"
        note = f"⚠️ AVOID: {profit_check['message']} before selling calls."
        confidence = 1
    
    elif wait_count > 0:  # seasonal is WAIT
        # If any WAIT condition, WAIT overall
        overall_status = "⏸️ WAIT"
        note = f"⏸️ WAIT: {seasonal_check['message']}"
        confidence = 2
    
    elif pass_count == 4:  # All 4 conditions pass
        # All conditions favorable
        overall_status = "✅ READY"
        note = f"✅ Ready: All conditions favorable (profit, market near ATH, seasonal OK, lot exists). Suggested CE below."
        confidence = 5
    
    elif pass_count >= 2:  # At least 2 conditions pass
        # Most conditions favorable
        overall_status = "✅ READY"
        failed_reasons = [c["message"] for c in all_checks if c["status"].startswith("⚠️")]
        note = f"✅ Ready (cautiously): {'; '.join(failed_reasons[:2])}"
        confidence = 3
    
    else:
        # Multiple unfavorable conditions
        overall_status = "⚠️ AVOID"
        failed_reasons = [c["message"] for c in all_checks if c["status"].startswith("⚠️")]
        note = f"⚠️ AVOID: {'; '.join(failed_reasons[:2])}"
        confidence = 1
    
    return {
        "overall_status": overall_status,
        "note": note,
        "conditions": {
            "profit": profit_check,
            "market": market_check,
            "seasonal": seasonal_check,
            "lot_size": lot_check,
        },
        "confidence_score": confidence,
    }
def get_covered_call_analysis(user_id: int) -> dict:
    """
    Returns:
    {
      "covered_calls":      [ row, ... ],   # Table A
      "uncovered":          [ row, ... ],   # Table B
      "correction_module":  [ row, ... ],   # Table C
    }

    Each row is a plain dict safe for JSON serialisation.
    """
    client = None
    try:
        from services.ce_pe_service import _get_5paisa_client
        client = _get_5paisa_client()
    except Exception:
        pass
    db = SessionLocal()
    try:
        # Load data
        holdings_map = _load_holdings_map([user_id], db)
        fno_map      = _load_fno_positions_map([user_id], db)

        # Try to fetch live spot prices for all relevant symbols
        all_symbols = sorted(set(list(holdings_map.keys()) + list(fno_map.keys())))
        spot_map: Dict[str, float] = {}
        if all_symbols:
            try:
                from services.engine_price_fetch import fetch_current_prices
                spot_map = fetch_current_prices(all_symbols)
            except Exception:
                pass

        covered_calls:     List[dict] = []
        uncovered:         List[dict] = []
        correction_module: List[dict] = []

        # All underlyings across holdings + F&O
        all_underlyings = set(holdings_map.keys()) | set(fno_map.keys())

        # ── Fetch live option prices from 5paisa for ALL open F&O positions ──
        # Build a DataFrame shaped for fetch_fno_prices()
        import pandas as pd
        fno_rows_for_price = []
        for can, pos_list in fno_map.items():
            for pos in pos_list:
                fno_rows_for_price.append({
                    "underlying":      can,
                    "instrument_type": pos["instrument_type"],
                    "expiry_date":     pos["expiry"],
                    "strike_price":    pos["strike"],
                    "exchange":        "NSE",
                })
        # Fetch Nifty 52W high for market condition check
        nifty_52w_high = 0.0
        try:
            from services.ce_pe_service import get_price_ohlc
            nifty_ohlc = get_price_ohlc("NIFTY")
            nifty_52w_high = _safe(nifty_ohlc.get("high_52w", 0))
            logger.info(f"[CovCall] Nifty 52W high: ₹{nifty_52w_high}")
        except Exception as e:
            logger.debug(f"[CovCall] Nifty 52W fetch failed: {e}")
        live_fno_prices: dict[tuple, float] = {}
        if fno_rows_for_price:
            try:
                from services.engine_price_fetch import fetch_fno_prices
                op_df = pd.DataFrame(fno_rows_for_price)
                live_fno_prices = fetch_fno_prices(op_df)
                logger.info(f"[CovCall] Live F&O prices fetched: {len(live_fno_prices)} contracts")
            except Exception as e:
                logger.info(f"[CovCall] fetch_fno_prices failed: {e}")

        def _live_option_price_db(can: str, itype: str, expiry: str, strike: float) -> float:
            """
            Fetch live LTP for an option/FUT.
            PRIORITY 1: Use REST API (get_expiry + get_option_chain) -> MOST RELIABLE.
            PRIORITY 2: Use scrip_master_cache scrip_code + batch_market_feed.
            """
            # ——— PRIORITY 1: REST API method (Proven to work in your test) ———
            try:
                from services.ce_pe_service import _get_5paisa_client, _parse_expiry_ms
                import datetime
                client = _get_5paisa_client()
                if client:
                    exp_resp = client.get_expiry("N", can)
                    if exp_resp and exp_resp.get("Status") == 0:
                        target_date = datetime.datetime.strptime(expiry[:10], "%Y-%m-%d").date()
                        for exp_entry in exp_resp.get("Expiry", []):
                            ts = _parse_expiry_ms(str(exp_entry.get("ExpiryDate", "")))
                            if ts:
                                exp_dt = datetime.datetime.fromtimestamp(ts / 1000).date()
                                if exp_dt == target_date:  # Match expiry
                                    chain = client.get_option_chain("N", can, ts)
                                    opts = []
                                    if isinstance(chain, dict):
                                        opts = chain.get("Options") or chain.get("Data", [])
                                    elif isinstance(chain, list):
                                        opts = chain
                                    for opt in opts:
                                        cp = opt.get("CPType") or opt.get("OptionType", "")
                                        if itype == "CE" and cp not in ("C", "CE"): continue
                                        if itype == "PE" and cp not in ("P", "PE"): continue
                                        # Match strike (allow 0.5 float difference)
                                        if abs(float(opt.get("StrikeRate", 0) or 0) - strike) < 0.5:
                                            ltp = float(opt.get("CPLastRate") or opt.get("LastRate") or 0)
                                            if ltp > 0:
                                                logger.info(f"[CovCall] ✅ REST API LTP: ₹{ltp} for {can} {itype} {strike}")
                                                return ltp
                                    break
            except Exception as e:
                logger.info(f"[CovCall] REST API fallback error: {e}")

            # ——— PRIORITY 2: DB ScripCode + Market Feed (Fallback) ———
            _MONTH_ABBRS = {
                1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
                7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC",
            }
            try:
                from datetime import datetime as _dt
                exp_dt     = _dt.strptime(expiry[:10], "%Y-%m-%d")
                yr_str     = str(exp_dt.year)
                mon_abbr   = _MONTH_ABBRS[exp_dt.month]
                mon_2d     = f"{exp_dt.month:02d}"
                strike_val = float(strike or 0)
                scrip_type = {"FUT": "XX", "CE": "CE", "PE": "PE"}.get(itype.upper(), itype)

                db2 = SessionLocal()
                try:
                    row = db2.execute(text("""
                        SELECT scrip_code, scrip_data FROM scrip_master_cache
                        WHERE exch = 'N' AND exch_type = 'D'
                        AND scrip_type = :st
                        AND (UPPER(symbol_root) = :sym OR UPPER(name) = :sym)
                        AND ABS(strike_rate - :strike) < 1
                        AND expiry LIKE :yr_pat
                        AND (UPPER(expiry) LIKE :mon_abbr_pat OR expiry LIKE :mon_2d_pat)
                        AND scrip_code IS NOT NULL AND scrip_code != ''
                        ORDER BY expiry DESC
                        LIMIT 1
                    """), {
                        "st": scrip_type, "sym": can.upper(), "strike": strike_val,
                        "yr_pat": f"%{yr_str}%", "mon_abbr_pat": f"%{mon_abbr}%", "mon_2d_pat": f"%-{mon_2d}-%",
                    }).first()

                    if not row or not row.scrip_code:
                        return 0.0

                    scrip_code = str(row.scrip_code).strip()
                    from services.engine_price_fetch import _get_client, _batch_market_feed
                    feed_client = _get_client()
                    if feed_client:
                        feed = _batch_market_feed([{"Exch": "N", "ExchType": "D", "ScripData": scrip_code}])
                        ltp = _safe((feed.get(scrip_code) or {}).get("ltp", 0))
                        if ltp > 0:
                            logger.info(f"[CovCall] ✅ Feed LTP via scrip_code={scrip_code}: ₹{ltp}")
                            return ltp
                finally:
                    db2.close()
            except Exception as e:
                logger.info(f"[CovCall] DB ScripCode fallback error: {e}")

            return 0.0
        
        def _live_option_price(can: str, itype: str, expiry: str, strike: float) -> float:
            """
            Look up live LTP for an option/FUT contract.
            1. Check live_fno_prices dict (from fetch_fno_prices — CSV based)
            2. If miss → try DB-based scrip_master_cache lookup + 5paisa feed
            Returns 0.0 if both fail.
            """
            key1 = (can, itype, expiry, strike)
            if key1 in live_fno_prices:
                return _safe(live_fno_prices[key1])
            # Try with strike as int (some keys stored with .0)
            key2 = (can, itype, expiry, float(int(strike)))
            if key2 in live_fno_prices:
                return _safe(live_fno_prices[key2])
            # CSV lookup missed — try DB fallback for options
            if itype in ("CE", "PE"):
                return _live_option_price_db(can, itype, expiry, strike)
            return 0.0

        for can in sorted(all_underlyings):
            
            can = get_canonical(can)
            h_info    = holdings_map.get(can, {})
            pos_list  = fno_map.get(can, [])
            spot      = _safe(spot_map.get(can, 0))

            eq_qty    = _safe(h_info.get("qty",           0))
            avg_price = _safe(h_info.get("avg_buy_price", 0))

            # Split positions by type
            sold_ce_list  = [p for p in pos_list if p["instrument_type"] == "CE"  and p["open_qty"] < 0]
            long_fut_list = [p for p in pos_list if p["instrument_type"] == "FUT" and p["open_qty"] > 0]
            short_fut_list= [p for p in pos_list if p["instrument_type"] == "FUT" and p["open_qty"] < 0]
            sold_pe_list  = [p for p in pos_list if p["instrument_type"] == "PE"  and p["open_qty"] < 0]
            bought_ce_list= [p for p in pos_list if p["instrument_type"] == "CE"  and p["open_qty"] > 0]
            bought_pe_list= [p for p in pos_list if p["instrument_type"] == "PE"  and p["open_qty"] > 0]

            has_holding   = eq_qty > 0
            has_long_fut  = bool(long_fut_list)
            has_sold_ce   = bool(sold_ce_list)

            # ── Table A — Covered Calls ────────────────────────────────────────
            if has_sold_ce and (has_holding or has_long_fut):
                for ce in sold_ce_list:
                    # Use live option price (same feed as Table C); intrinsic as fallback
                    live_ce = _live_option_price(can, "CE", ce["expiry"], ce["strike"])
                    if live_ce > 0:
                        pnl_ce = (ce["avg_price"] - live_ce) * abs(ce["open_qty"])
                    elif spot > 0 and ce["avg_price"] > 0:
                        intrinsic = max(spot - ce["strike"], 0)
                        pnl_ce    = (ce["avg_price"] - intrinsic) * abs(ce["open_qty"])
                    else:
                        pnl_ce = 0.0

                    covered_calls.append({
                        "symbol":           can,
                        "eq_qty":           int(eq_qty),
                        "eq_avg_price":     round(avg_price, 2),
                        "eq_invested":      round(_safe(h_info.get("total_invested", 0)), 2),
                        "long_fut_qty":     int(sum(p["open_qty"] for p in long_fut_list)),
                        "ce_strike":        ce["strike"],
                        "ce_expiry":        ce["expiry"],
                        "ce_qty":           int(abs(ce["open_qty"])),
                        "ce_avg_premium":   round(ce["avg_price"], 2),
                        "ce_unrealized_pnl":round(pnl_ce, 2),
                        "spot":             round(spot, 2) if spot else None,
                        "is_covered":       True,
                    })

            # ── Table B — Uncovered (holding or long FUT but no sold CE) ──────
# ── Table B — Uncovered (holding or long FUT but no sold CE) ──────
            if (has_holding or has_long_fut) and not has_sold_ce:
                fut_qty = sum(p["open_qty"] for p in long_fut_list) if long_fut_list else 0

                # For equity holding: (spot - avg_cost) × qty
                unreal_eq = round((spot - avg_price) * eq_qty, 2) if (spot and avg_price and eq_qty) else None

                # For FUT-only rows: show FUT avg entry price and (spot - fut_avg) × fut_qty
                fut_avg_entry = 0.0
                unreal_fut    = None
                if long_fut_list:
                    # Weighted average entry across multiple FUT contracts
                    total_val = sum(p["open_qty"] * p["avg_price"] for p in long_fut_list)
                    total_qty = sum(p["open_qty"] for p in long_fut_list)
                    fut_avg_entry = round(total_val / total_qty, 2) if total_qty else 0.0
                    if spot and fut_avg_entry:
                        unreal_fut = round((spot - fut_avg_entry) * total_qty, 2)

                # Display logic:
                # - If equity holding exists: show equity avg_price and equity unrealized P&L
                # - If FUT-only (no equity): show FUT avg_entry and FUT unrealized P&L
                display_avg   = avg_price if has_holding else fut_avg_entry
                display_unreal = unreal_eq if has_holding else unreal_fut

                # If BOTH exist (HDFCBANK: 350 eq + 1100 FUT), combine them
                if has_holding and has_long_fut:
                    # Show equity avg but add FUT unrealized to total
                    if unreal_eq is not None and unreal_fut is not None:
                        display_unreal = round(unreal_eq + unreal_fut, 2)
                    elif unreal_fut is not None:
                        display_unreal = unreal_fut

                # ⭐ ENHANCED: Fetch CE suggestions from 5paisa (near and far month)
                suggested_ce_near_strike = suggested_ce_near_premium = 0
                suggested_ce_near_expiry = "—"
                suggested_ce_far_strike = suggested_ce_far_premium = 0
                suggested_ce_far_expiry = "—"
                
                if client:
                    try:
                        opt_info = _get_atm_option_info(client, can)
                        two_info = _get_two_expiry_option_info(client, can)
                        if two_info:
                            near = two_info.get("near") or {}
                            far = two_info.get("far") or {}
                            
                            # Near month (always present if two_info exists)
                            suggested_ce_near_strike = _safe(near.get('ce_strike', 0))
                            suggested_ce_near_premium = _safe(near.get('ce_premium', 0))
                            suggested_ce_near_expiry = near.get("expiry_readable", "—")
                            
                            # Far month (optional, may be None)
                            if far:
                                suggested_ce_far_strike = _safe(far.get('ce_strike', 0))
                                suggested_ce_far_premium = _safe(far.get('ce_premium', 0))
                                suggested_ce_far_expiry = far.get("expiry_readable", "—")
                    except Exception as e:
                        logger.debug(f"[CC Analysis] Could not fetch two-expiry options for {can}: {e}")
                        pass # silently ignore if API call fails

                # ⭐⭐⭐ NEW: Evaluate 4 conditions for sell call eligibility ⭐⭐⭐
                stock_52w_high = 0.0
                try:
                    from services.ce_pe_service import get_price_ohlc
                    stock_ohlc = get_price_ohlc(can)
                    stock_52w_high = _safe(stock_ohlc.get("high_52w", 0))
                except Exception as e:
                    logger.debug(f"[CovCall] Could not fetch 52W high for {can}: {e}")
                
                conditions_eval = _evaluate_sell_call_conditions(
                    symbol=can,
                    avg_buy_price=avg_price,
                    spot=spot,
                    high_52w=stock_52w_high if stock_52w_high > 0 else spot + 5000,  # Fallback
                    eq_qty=eq_qty
                )

                uncovered.append({
                    "symbol":            can,
                    "eq_qty":            int(eq_qty),
                    "eq_avg_price":      round(display_avg, 2) if display_avg else 0.0,
                    "eq_invested":       round(_safe(h_info.get("total_invested", 0)), 2),
                    "long_fut_qty":      int(fut_qty),
                    "fut_avg_entry":     fut_avg_entry,
                    "spot":              round(spot, 2) if spot else None,
                    "eq_unrealized_pnl": display_unreal,
                    "reason":            conditions_eval["note"],  # ← NEW: Dynamic based on 4 conditions
                    "confidence_score":  conditions_eval["confidence_score"],  # ← NEW: 1-5 score
                    # ⭐ ENHANCED columns for near and far month CE suggestions:
                    "suggested_ce_near_expiry":   suggested_ce_near_expiry,
                    "suggested_ce_near_strike":   round(suggested_ce_near_strike),
                    "suggested_ce_near_premium":  round(suggested_ce_near_premium, 2),
                    "suggested_ce_far_expiry":    suggested_ce_far_expiry,
                    "suggested_ce_far_strike":    round(suggested_ce_far_strike),
                    "suggested_ce_far_premium":   round(suggested_ce_far_premium, 2)
                })

            # ── Table C — Correction Module (loss > ₹10,000) ─────────────────
            for pos in pos_list:
                itype  = pos["instrument_type"]
                strike = pos["strike"]
                expiry = pos["expiry"]
                avg    = pos["avg_price"]
                qty    = pos["open_qty"]

                # Fetch live price from 5paisa market feed (primary)
                live_price = _live_option_price(can, itype, expiry, strike)

                if live_price > 0:
                    # Live price available — compute exact P&L
                    if qty > 0:
                        # Long position (bought): profit when price rises
                        pnl = (live_price - avg) * qty
                    else:
                        # Short position (sold): profit when price falls
                        pnl = (avg - live_price) * abs(qty)
                    price_source = "live"
                else:
                    # No live price — fall back to intrinsic value estimate
                    # (avoids the old bug of using stock spot as option price)
                    if itype == "FUT" and spot > 0 and avg > 0:
                        pnl = (spot - avg) * qty
                    elif itype in ("CE", "PE") and avg > 0 and spot > 0 and strike > 0:
                        intrinsic = max(spot - strike, 0.0) if itype == "CE" else max(strike - spot, 0.0)
                        pnl = (intrinsic - avg) * qty if qty > 0 else (avg - intrinsic) * abs(qty)
                    else:
                        pnl = 0.0
                    price_source = "intrinsic_estimate"

                if pnl < -10000:
                    suggestion = _correction_suggestion(pos, spot)
                    correction_module.append({
                        "symbol":          can,
                        "instrument_type": itype,
                        "strike":          strike,
                        "expiry":          expiry,
                        "open_qty":        int(qty),
                        "avg_price":       round(avg, 2),
                        "live_price":      round(live_price, 2) if live_price > 0 else None,
                        "spot":            round(spot, 2) if spot else None,
                        "estimated_pnl":   round(pnl, 2),
                        "loss_amount":     round(abs(pnl), 2),
                        "price_source":    price_source,
                        "suggestion":      suggestion,
                    })

        # Sort correction by loss severity (largest loss first)
        correction_module.sort(key=lambda x: x["loss_amount"], reverse=True)

        return {
            "covered_calls":     covered_calls,
            "uncovered":         uncovered,
            "correction_module": correction_module,
        }

    except Exception as e:
        logger.error("update failed", exc_info=True)
        return {"error": str(e), "covered_calls": [], "uncovered": [], "correction_module": []}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Master Reference Positions  (for account > 1)
# ─────────────────────────────────────────────────────────────────────────────

def _get_master_account_id(db) -> int | None:
    """Return the account_id marked as 'master', falling back to id=1."""
    try:
        row = db.execute(
            text("SELECT id FROM accounts WHERE role='master' ORDER BY id LIMIT 1")
        ).first()
        if row:
            return row.id
    except Exception:
        pass
    # Fallback: lowest account id
    try:
        row = db.execute(text("SELECT MIN(id) as mid FROM accounts")).first()
        if row and row.mid:
            return int(row.mid)
    except Exception:
        pass
    return None


def get_master_reference_positions(requesting_account_id: int) -> dict:
    """
    Called by child accounts (account_id > 1 / not master).

    Returns master positions that are NOT covered calls there, minus any signal
    the child account has ALREADY acted on (same symbol + instrument_type +
    approx strike is already open in the child's own positions).

    Signals:
      SOLD PE in master          → child can mirror (sell same PE)
      SHORT FUT in master        → child can mirror or sell CE on that underlying
      SOLD CE uncovered in master → child can mirror or take opposite view
      HOLDING/LONG FUT uncovered → fresh covered-call opportunity for child
      BOUGHT CE/PE in master     → informational only

    Status column per row:
      "NEW"         — child has no matching position yet → act on it
      "TAKEN"       — child already has the same/similar position open
      "PARTIAL"     — child has a position on same symbol but different strike/type
    """
    db = SessionLocal()
    try:
        # ── Find master account ────────────────────────────────────────────────
        master_account_id = _get_master_account_id(db)
        if master_account_id is None:
            return {
                "error": "No master account found",
                "reference_positions": [],
                "master_account_id": None,
            }

        if requesting_account_id == master_account_id:
            return {
                "error": "This IS the master account — reference tab not applicable",
                "reference_positions": [],
                "master_account_id": master_account_id,
            }

        # ── Get master's user IDs ──────────────────────────────────────────────
        master_user_ids = _get_user_ids_for_account(master_account_id, db)
        if not master_user_ids:
            return {
                "reference_positions": [],
                "master_account_id": master_account_id,
                "note": "Master account has no portfolio users",
            }

        # ── Load master's holdings + positions ────────────────────────────────
        master_holdings = _load_holdings_map(master_user_ids, db)
        master_fno      = _load_fno_positions_map(master_user_ids, db)

        # ── Load child's own open positions (to detect already-taken signals) ──
        child_user_ids  = _get_user_ids_for_account(requesting_account_id, db)
        child_fno       = _load_fno_positions_map(child_user_ids, db) if child_user_ids else {}
        child_holdings  = _load_holdings_map(child_user_ids, db)      if child_user_ids else {}

        def _child_status(can: str, itype: str, strike: float) -> str:
            """
            Check if the child account already has an open position matching
            the master signal:
              TAKEN   — same symbol, same instrument type, same approximate strike (±5%)
              PARTIAL — same symbol + type but different strike
              NEW     — no position on this symbol+type
            """
            child_pos_list = child_fno.get(can, [])
            same_type = [p for p in child_pos_list if p["instrument_type"] == itype and p["open_qty"] != 0]
            if not same_type:
                # For HOLDING/LONG FUT check equity holdings too
                if itype in ("EQ", "EQ/FUT"):
                    if child_holdings.get(can, {}).get("qty", 0) > 0:
                        return "TAKEN"
                return "NEW"
            # Check strike proximity (within 5% or zero for FUT)
            for p in same_type:
                p_strike = _safe(p.get("strike", 0))
                ref_strike = _safe(strike)
                if ref_strike == 0 or p_strike == 0:
                    return "TAKEN"   # FUT or equity — strike irrelevant
                if abs(p_strike - ref_strike) / ref_strike <= 0.05:
                    return "TAKEN"
            return "PARTIAL"

        def _fmt_strike(s: float) -> str:
            """Format strike as integer if whole number, else 2dp — no trailing zeros."""
            if s == 0:
                return "—"
            return str(int(s)) if s == int(s) else f"{s:.2f}"

        # ── Fetch live prices ─────────────────────────────────────────────────
        all_symbols = sorted(set(list(master_holdings.keys()) + list(master_fno.keys())))
        spot_map: Dict[str, float] = {}
        if all_symbols:
            try:
                from services.engine_price_fetch import fetch_current_prices
                spot_map = fetch_current_prices(all_symbols)
            except Exception:
                pass

        # ── Classify each underlying in master ────────────────────────────────
        reference_positions: List[dict] = []

        all_master_underlyings = set(master_holdings.keys()) | set(master_fno.keys())

        for can in sorted(all_master_underlyings):
            h_info   = master_holdings.get(can, {})
            pos_list = master_fno.get(can, [])
            spot     = _safe(spot_map.get(can, 0))

            eq_qty    = _safe(h_info.get("qty", 0))
            avg_price = _safe(h_info.get("avg_buy_price", 0))

            sold_ce_list  = [p for p in pos_list if p["instrument_type"] == "CE"  and p["open_qty"] < 0]
            long_fut_list = [p for p in pos_list if p["instrument_type"] == "FUT" and p["open_qty"] > 0]
            short_fut_list= [p for p in pos_list if p["instrument_type"] == "FUT" and p["open_qty"] < 0]
            sold_pe_list  = [p for p in pos_list if p["instrument_type"] == "PE"  and p["open_qty"] < 0]
            bought_ce_list= [p for p in pos_list if p["instrument_type"] == "CE"  and p["open_qty"] > 0]
            bought_pe_list= [p for p in pos_list if p["instrument_type"] == "PE"  and p["open_qty"] > 0]

            has_holding  = eq_qty > 0
            has_long_fut = bool(long_fut_list)
            has_sold_ce  = bool(sold_ce_list)

            # EXCLUDE: sold CE that IS covered in master (it's a normal covered call there)
            is_covered_in_master = has_sold_ce and (has_holding or has_long_fut)
            if is_covered_in_master:
                continue

            # ── 1. Sold PE ────────────────────────────────────────────────────
            for pe in sold_pe_list:
                pnl_pe = _safe(pe.get("unrealized_pnl", 0))
                strike = _safe(pe["strike"])
                status = _child_status(can, "PE", strike)
                reference_positions.append({
                    "symbol":         can,
                    "position_type":  "SOLD PE",
                    "instrument":     "PE",
                    "strike":         _fmt_strike(strike),
                    "expiry":         pe["expiry"][:10] if pe["expiry"] else "—",
                    "master_qty":     int(pe["open_qty"]),
                    "master_avg":     round(pe["avg_price"], 2),
                    "spot":           round(spot, 2) if spot else None,
                    "master_pnl":     round(pnl_pe, 2),
                    "child_status":   status,
                    "suggestion":     "Mirror: SELL PE same strike. Or SELL CE if spot near resistance.",
                })

            # ── 2. Short FUT ──────────────────────────────────────────────────
            for fut in short_fut_list:
                pnl_fut = _safe(fut.get("unrealized_pnl", 0))
                status  = _child_status(can, "FUT", 0)
                reference_positions.append({
                    "symbol":         can,
                    "position_type":  "SHORT FUT",
                    "instrument":     "FUT",
                    "strike":         "—",
                    "expiry":         fut["expiry"][:10] if fut["expiry"] else "—",
                    "master_qty":     int(fut["open_qty"]),
                    "master_avg":     round(fut["avg_price"], 2),
                    "spot":           round(spot, 2) if spot else None,
                    "master_pnl":     round(pnl_fut, 2),
                    "child_status":   status,
                    "suggestion":     "Mirror: SELL FUT or SELL CE on this underlying.",
                })

            # ── 3. Sold CE uncovered ──────────────────────────────────────────
            if has_sold_ce and not (has_holding or has_long_fut):
                for ce in sold_ce_list:
                    pnl_ce = _safe(ce.get("unrealized_pnl", 0))
                    strike = _safe(ce["strike"])
                    status = _child_status(can, "CE", strike)
                    reference_positions.append({
                        "symbol":         can,
                        "position_type":  "SOLD CE (uncovered)",
                        "instrument":     "CE",
                        "strike":         _fmt_strike(strike),
                        "expiry":         ce["expiry"][:10] if ce["expiry"] else "—",
                        "master_qty":     int(ce["open_qty"]),
                        "master_avg":     round(ce["avg_price"], 2),
                        "spot":           round(spot, 2) if spot else None,
                        "master_pnl":     round(pnl_ce, 2),
                        "child_status":   status,
                        "suggestion":     "Mirror or take opposite view (BUY CE if bearish on master's call).",
                    })

            # ── 4. Holding / long FUT uncovered ───────────────────────────────
            if (has_holding or has_long_fut) and not has_sold_ce:
                unreal_h = round((spot - avg_price) * eq_qty, 2) if (spot and avg_price and eq_qty) else 0
                fut_qty  = int(sum(p["open_qty"] for p in long_fut_list))
                status   = _child_status(can, "EQ/FUT", 0)
                reference_positions.append({
                    "symbol":         can,
                    "position_type":  "HOLDING / LONG FUT",
                    "instrument":     "EQ/FUT",
                    "strike":         "—",
                    "expiry":         "—",
                    "master_qty":     int(eq_qty) + fut_qty,
                    "master_avg":     round(avg_price, 2),
                    "spot":           round(spot, 2) if spot else None,
                    "master_pnl":     unreal_h,
                    "child_status":   status,
                    "suggestion":     "Fresh opportunity: SELL CE above spot to collect premium.",
                })

            # ── 5. Bought CE/PE (informational) ───────────────────────────────
            for pos_list_bce in [bought_ce_list, bought_pe_list]:
                for pos in pos_list_bce:
                    pnl_b  = _safe(pos.get("unrealized_pnl", 0))
                    strike = _safe(pos["strike"])
                    itype  = pos["instrument_type"]
                    status = _child_status(can, itype, strike)
                    reference_positions.append({
                        "symbol":         can,
                        "position_type":  f"BOUGHT {itype}",
                        "instrument":     itype,
                        "strike":         _fmt_strike(strike),
                        "expiry":         pos["expiry"][:10] if pos["expiry"] else "—",
                        "master_qty":     int(pos["open_qty"]),
                        "master_avg":     round(pos["avg_price"], 2),
                        "spot":           round(spot, 2) if spot else None,
                        "master_pnl":     round(pnl_b, 2),
                        "child_status":   status,
                        "suggestion":     f"Info: Master is long {itype} here — speculative / hedge.",
                    })

        # Sort: NEW first (action needed), then PARTIAL, then TAKEN
        status_order = {"NEW": 0, "PARTIAL": 1, "TAKEN": 2}
        reference_positions.sort(key=lambda x: status_order.get(x.get("child_status", "NEW"), 0))

        return {
            "reference_positions":     reference_positions,
            "master_account_id":       master_account_id,
            "master_user_count":       len(master_user_ids),
            "total_reference_signals": len(reference_positions),
            "new_signals":             sum(1 for r in reference_positions if r["child_status"] == "NEW"),
        }

    except Exception as e:
        import traceback; traceback.logger.info_exc()
        return {"error": str(e), "reference_positions": []}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Account role helpers (called from router + migration)
# ─────────────────────────────────────────────────────────────────────────────

def get_account_role(account_id: int) -> str:
    """Returns 'master' or 'child'."""
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT role FROM accounts WHERE id=:aid LIMIT 1"),
            {"aid": account_id}
        ).first()
        if row and row.role:
            return str(row.role)
    except Exception:
        pass
    finally:
        db.close()
    # Fallback: if no role column yet, id=1 is master
    return "master" if account_id == 1 else "child"


def is_master_account(account_id: int) -> bool:
    return get_account_role(account_id) == "master"