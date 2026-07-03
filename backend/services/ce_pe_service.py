"""
ce_pe_service.py — v9
======================
KEY CHANGES vs v8:
  1. IV now fetched via 5paisa Greeks WebSocket (wss://gateway.5paisa.com/openapi/greeks)
     using get_expiry() + get_option_chain() REST calls to get ATM option tokens,
     then a single WebSocket connection to fetch all IVs in one shot.
     Black-Scholes is kept only as a last-resort fallback.

  2. Option LTP fetched via get_option_chain() REST response (CPLastRate field) —
     no need for separate scrip_master_cache lookup for premiums.

  3. Scrip_master_cache used for:
       - EQ scrip_code → period prices (1M/3M/6M/52W high/low) via 5paisa historical_data
       - ISIN → canonical → scrip_code resolution path fully logged

  4. All floats run through _safe() — NaN/Inf → 0.0, None → 0.0. No more nan in UI.

  5. NSE option chain completely removed (was always returning {}).
     NSE quote kept for 52W fallback only when 5paisa scrip_code missing.

  6. Comprehensive logging at every decision point.

PROCESS FLOW PER HOLDING:
  holdings table
  → JOIN user_stock_symbol_mapping → ISIN
  → JOIN stock_master_mapping → canonical_symbol, lot_size, fno_available
  → scrip_master_cache WHERE symbol_root=canonical, exch='N', exch_type='C'
    → EQ scrip_code → 5paisa historical_data → period high/low (1M/3M/6M/52W)
  → fetch_current_prices([canonical]) → spot price from 5paisa market feed
    fallback: NSE /api/quote-equity → lastPrice + 52W high/low
  → 5paisa get_expiry(symbol) → nearest expiry + underlying LTP
  → 5paisa get_option_chain(symbol, expiry_ts) → ATM call/put tokens + premiums
  → Greeks WebSocket [ALL tokens in ONE connection] → IV per token
  → Black-Scholes fallback IV if WS fails
  → signal logic: within 10% of 52W high → SELL CE; near 52W low → SELL PE
  → fno_open_positions → existing short CE/PE check
"""
from __future__ import annotations

import json
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import calendar
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import text
from database import SessionLocal
from services.nse_data_service import _nse_get, _get_session
from services.price_service import get_period_prices

import logging
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# NaN-safe helpers
# ─────────────────────────────────────────────────────────────────────────────
def parse_expiry_ms(date_str):
    """Extract milliseconds from /Date(milliseconds+offset)/"""
    match = re.search(r'/Date\((\d+)[+-]\d+\)/', date_str)
    return int(match.group(1)) if match else None

def _safe(v) -> float:
    """Coerce to float; return 0.0 for None / NaN / Inf."""
    if v is None:
        return 0.0
    try:
        f = float(v)
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return 0.0


def _nz(v):
    """Return None (shows as '—' in UI) instead of 0.0."""
    f = _safe(v)
    return round(f, 2) if f > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Expiry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _nearest_expiry_date(ref: date | None = None) -> date:
    ref = ref or date.today()
    for delta_month in range(0, 3):
        y = ref.year + (ref.month - 1 + delta_month) // 12
        m = (ref.month - 1 + delta_month) % 12 + 1
        last_day = calendar.monthrange(y, m)[1]
        thursdays = [date(y, m, d) for d in range(1, last_day + 1)
                     if date(y, m, d).weekday() == 3]
        last_thu = thursdays[-1]
        if last_thu >= ref:
            return last_thu
    return ref + timedelta(days=30)


def _parse_expiry_ms(date_str: str) -> int | None:
    """Extract milliseconds from /Date(ms+offset)/ format."""
    match = re.search(r'/Date\((\d+)[+-]\d+\)/', str(date_str))
    return int(match.group(1)) if match else None


# ─────────────────────────────────────────────────────────────────────────────
# 5paisa client helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_5paisa_client():
    try:
        from auth_manager import get_client
        c = get_client()
        if c is None:
            logger.warning("[CE/PE] 5paisa client returned None")
        return c
    except Exception as e:
        logger.error(f"[CE/PE] 5paisa client error: {e}")
        return None


def _get_access_token() -> str | None:
    """Extract raw access_token string from 5paisa client for WebSocket auth."""
    client = _get_5paisa_client()
    if client is None:
        return None
    for attr in ("access_token", "AccessToken", "Jwt", "_token", "jwt"):
        val = getattr(client, attr, None)
        if val:
            logger.debug(f"[CE/PE] access_token found via attr='{attr}'")
            return str(val)
    logger.error("[CE/PE] Cannot find access_token on 5paisa client — Greeks WS will not work")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# EQ scrip_code lookup (ISIN-first, then symbol_root, then name)
# ─────────────────────────────────────────────────────────────────────────────

def _find_eq_scrip_code(canonical: str) -> tuple[str | None, str | None]:
    """
    Returns (scrip_code, scrip_data) for NSE EQ row.

    Resolution:
      1. stock_master_mapping.canonical_symbol → ISIN
         → scrip_master_cache WHERE isin=ISIN AND exch_type='C' AND series='EQ'
      2. scrip_master_cache WHERE symbol_root=canonical AND exch='N' AND exch_type='C'
      3. scrip_master_cache WHERE name=canonical (same filters)
    """
    sym = str(canonical).strip().upper()
    if not sym:
        return None, None

    db = SessionLocal()
    try:
        # Step 1: canonical → ISIN → scrip_code
        row = db.execute(
            text("SELECT isin FROM stock_master_mapping "
                 "WHERE UPPER(canonical_symbol)=:sym LIMIT 1"),
            {"sym": sym}
        ).first()

        if row and row.isin:
            isin = row.isin.strip()
            row2 = db.execute(
                text("""
                    SELECT scrip_code, scrip_data FROM scrip_master_cache
                    WHERE isin=:isin AND exch='N' AND exch_type='C'
                      AND series='EQ' AND scrip_code IS NOT NULL AND scrip_code != ''
                    LIMIT 1
                """),
                {"isin": isin}
            ).first()
            if row2 and row2.scrip_code:
                logger.info(f"[CE/PE] ✅ EQ scrip_code via ISIN '{isin}': "
                            f"{row2.scrip_code} for '{sym}'")
                return str(row2.scrip_code).strip(), str(row2.scrip_data or "").strip()
            else:
                logger.warning(f"[CE/PE] ISIN '{isin}' found for '{sym}' "
                               f"but no EQ scrip_code in scrip_master_cache")

        # Step 2: symbol_root exact match
        row = db.execute(
            text("""
                SELECT scrip_code, scrip_data FROM scrip_master_cache
                WHERE UPPER(symbol_root)=:sym AND exch='N' AND exch_type='C'
                  AND (series='EQ' OR series IS NULL)
                  AND scrip_code IS NOT NULL AND scrip_code != ''
                ORDER BY CASE WHEN series='EQ' THEN 0 ELSE 1 END
                LIMIT 1
            """),
            {"sym": sym}
        ).first()
        if row and row.scrip_code:
            logger.info(f"[CE/PE] ✅ EQ scrip_code via symbol_root '{sym}': {row.scrip_code}")
            return str(row.scrip_code).strip(), str(row.scrip_data or "").strip()

        # Step 3: name exact match
        row = db.execute(
            text("""
                SELECT scrip_code, scrip_data FROM scrip_master_cache
                WHERE UPPER(name)=:sym AND exch='N' AND exch_type='C'
                  AND (series='EQ' OR series IS NULL)
                  AND scrip_code IS NOT NULL AND scrip_code != ''
                LIMIT 1
            """),
            {"sym": sym}
        ).first()
        if row and row.scrip_code:
            logger.info(f"[CE/PE] ✅ EQ scrip_code via name '{sym}': {row.scrip_code}")
            return str(row.scrip_code).strip(), str(row.scrip_data or "").strip()

        logger.warning(f"[CE/PE] ⚠️ No EQ scrip_code for '{sym}' — "
                       f"period prices will be NSE 52W only")
        return None, None

    except Exception as e:
        logger.error(f"[CE/PE] _find_eq_scrip_code error for '{canonical}': {e}")
        return None, None
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# OHLC ranges — 5paisa primary, NSE 52W fallback
# ─────────────────────────────────────────────────────────────────────────────

_OHLC_EMPTY = {
    "spot": 0.0,
    "high_52w": 0.0, "low_52w": 0.0,
    "high_6m":  0.0, "low_6m":  0.0,
    "high_3m":  0.0, "low_3m":  0.0,
    "high_1m":  0.0, "low_1m":  0.0,
}


def _fetch_nse_quote(symbol: str) -> dict:
    """NSE quote — 52W high/low fallback only."""
    data = _nse_get(
        f"https://www.nseindia.com/api/quote-equity?symbol={symbol}", retries=2
    )
    if not data:
        logger.warning(f"[CE/PE] NSE quote failed for '{symbol}'")
        return {}
    price_info = data.get("priceInfo", {})
    whl = price_info.get("weekHighLow", {})
    result = {
        "last_price": _safe(price_info.get("lastPrice")),
        "high_52w":   _safe(whl.get("max")),
        "low_52w":    _safe(whl.get("min")),
    }
    logger.info(f"[CE/PE] NSE quote '{symbol}': spot={result['last_price']}, "
                f"52W H={result['high_52w']}, L={result['low_52w']}")
    return result


def _fetch_ohlc_ranges(symbol: str) -> dict:
    """
    5paisa primary: canonical → scrip_code → historical_data for all periods.
    NSE fallback: only 52W high/low from quote API.
    All values NaN-safe via _safe().
    """
    scrip_code, scrip_data = _find_eq_scrip_code(symbol)

    if scrip_code:
        logger.info(f"[CE/PE] Fetching 5paisa period prices for '{symbol}' "
                    f"(scrip={scrip_code})")
        try:
            periods = get_period_prices(scrip_code, symbol)
            result = {
                "spot":     0.0,
                "high_52w": _safe(periods.get("52w_high")),
                "low_52w":  _safe(periods.get("52w_low")),
                "high_6m":  _safe(periods.get("6m_high")),
                "low_6m":   _safe(periods.get("6m_low")),
                "high_3m":  _safe(periods.get("3m_high")),
                "low_3m":   _safe(periods.get("3m_low")),
                "high_1m":  _safe(periods.get("1m_high")),
                "low_1m":   _safe(periods.get("1m_low")),
            }
            logger.info(f"[CE/PE] Period prices '{symbol}': "
                        f"52W H={result['high_52w']} L={result['low_52w']} | "
                        f"6M H={result['high_6m']} L={result['low_6m']} | "
                        f"3M H={result['high_3m']} L={result['low_3m']} | "
                        f"1M H={result['high_1m']} L={result['low_1m']}")
            return result
        except Exception as e:
            logger.error(f"[CE/PE] 5paisa period fetch failed for '{symbol}' "
                         f"(scrip={scrip_code}): {e}")

    # Fallback: NSE 52W only
    logger.info(f"[CE/PE] No scrip_code for '{symbol}' — NSE 52W fallback")
    quote = _fetch_nse_quote(symbol)
    return {
        **_OHLC_EMPTY,
        "spot":     _safe(quote.get("last_price")),
        "high_52w": _safe(quote.get("high_52w")),
        "low_52w":  _safe(quote.get("low_52w")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5paisa REST: nearest expiry + ATM option tokens + premiums
# ─────────────────────────────────────────────────────────────────────────────

def _get_atm_option_info(client, symbol: str) -> dict | None:
    """
    Calls 5paisa get_expiry() then get_option_chain() to get:
      - nearest expiry date string
      - underlying LTP (from lastrate)
      - OTM CE token, strike, premium (CPLastRate)
      - OTM PE token, strike, premium

    Returns dict or None on any failure.
    Logs every step so you can trace exactly what's happening.
    """
    try:
        # ── get_expiry ────────────────────────────────────────────────────────
        exp_resp = client.get_expiry("N", symbol)
        if not exp_resp or exp_resp.get("Status") != 0:
            logger.warning(f"[CE/PE] [{symbol}] get_expiry failed: {exp_resp}")
            return None

        expiry_list   = exp_resp.get("Expiry", [])
        lastrate_list = exp_resp.get("lastrate", [])
        if not expiry_list or not lastrate_list:
            logger.warning(f"[CE/PE] [{symbol}] Empty expiry or lastrate")
            return None

        expiry_ts  = _parse_expiry_ms(expiry_list[0]["ExpiryDate"])
        expiry_raw = str(expiry_list[0]["ExpiryDate"])
        if not expiry_ts:
            logger.warning(f"[CE/PE] [{symbol}] Cannot parse expiry ts from: {expiry_raw}")
            return None

        # Convert /Date(ms+offset)/ → readable "DD-Mon-YYYY"
        try:
            expiry_readable = datetime.fromtimestamp(expiry_ts / 1000).strftime("%d-%b-%Y")
        except Exception:
            expiry_readable = expiry_raw

        ltp = _safe(lastrate_list[0].get("LTP", 0))
        logger.info(f"[CE/PE] [{symbol}] get_expiry OK: LTP={ltp}, expiry={expiry_readable}")

        # ── get_option_chain ──────────────────────────────────────────────────
        chain_resp = client.get_option_chain("N", symbol, expiry_ts)

        if isinstance(chain_resp, dict):
            if chain_resp.get("Status") != 0:
                logger.warning(f"[CE/PE] [{symbol}] get_option_chain error: "
                               f"{chain_resp.get('Message')}")
                return None
            all_options = chain_resp.get("Options") or chain_resp.get("Data", [])
        elif isinstance(chain_resp, list):
            all_options = chain_resp
        else:
            logger.warning(f"[CE/PE] [{symbol}] Unexpected chain type: {type(chain_resp)}")
            return None

        if not all_options:
            logger.warning(f"[CE/PE] [{symbol}] Empty option chain")
            return None

        logger.info(f"[CE/PE] [{symbol}] Chain received: {len(all_options)} options")

        def _cp(opt):
            return opt.get("CPType") or opt.get("OptionType", "")

        calls = sorted([o for o in all_options if _cp(o) in ("C", "CE")],
                       key=lambda x: float(x.get("StrikeRate", 0)))
        puts  = sorted([o for o in all_options if _cp(o) in ("P", "PE")],
                       key=lambda x: float(x.get("StrikeRate", 0)))

        if not calls:
            logger.warning(f"[CE/PE] [{symbol}] No calls found in chain")
            return None

        # ATM = strike closest to LTP
        atm_call   = min(calls, key=lambda x: abs(float(x.get("StrikeRate", 0)) - ltp))
        atm_put    = min(puts,  key=lambda x: abs(float(x.get("StrikeRate", 0)) - ltp)) if puts else None
        atm_strike = float(atm_call.get("StrikeRate", ltp))

        # OTM: one strike above ATM for CE, one below for PE
        otm_calls = [c for c in calls if float(c.get("StrikeRate", 0)) > atm_strike]
        otm_puts  = [p for p in puts  if float(p.get("StrikeRate", 0)) < atm_strike]
        ce_opt    = otm_calls[0] if otm_calls else atm_call
        pe_opt    = otm_puts[-1] if otm_puts  else atm_put

        def _token(opt):
            if opt is None:
                return None
            for k in ("Token", "ScripCode", "token", "scripCode"):
                v = opt.get(k)
                if v is not None:
                    return int(v)
            return None

        def _premium(opt):
            if opt is None:
                return 0.0
            for k in ("CPLastRate", "LastRate", "LTP", "lastrate"):
                v = opt.get(k)
                if v is not None:
                    return _safe(v)
            return 0.0

        info = {
            "ltp":          ltp,
            "expiry_raw":   expiry_readable,
            "expiry_ts":    expiry_ts,
            "call_token":   _token(ce_opt),
            "call_strike":  _safe(ce_opt.get("StrikeRate", 0)) if ce_opt else 0.0,
            "call_premium": _premium(ce_opt),
            "put_token":    _token(pe_opt),
            "put_strike":   _safe(pe_opt.get("StrikeRate", 0)) if pe_opt else 0.0,
            "put_premium":  _premium(pe_opt),
        }
        logger.info(f"[CE/PE] [{symbol}] CE: strike={info['call_strike']} "
                    f"token={info['call_token']} premium={info['call_premium']}")
        logger.info(f"[CE/PE] [{symbol}] PE: strike={info['put_strike']} "
                    f"token={info['put_token']} premium={info['put_premium']}")
        return info

    except Exception as e:
        logger.error(f"[CE/PE] [{symbol}] _get_atm_option_info error: {e}", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Greeks WebSocket — one connection, all tokens
# ─────────────────────────────────────────────────────────────────────────────

GREEKS_WS_URL = "wss://gateway.5paisa.com/openapi/greeks?access_token={token}"


def _fetch_iv_greeks_ws(access_token: str, token_map: dict,
                        timeout: int = 25) -> dict:
    """
    Single WebSocket connection to 5paisa Greeks feed.

    token_map: { "og<int>": (symbol_str, "call"|"put") }
    Returns:   { symbol_str: { "call_iv": float|None, "put_iv": float|None } }

    Logs every IV received and every token that times out.
    """
    if not token_map:
        return {}

    url         = GREEKS_WS_URL.format(token=access_token)
    instruments = list(token_map.keys())
    logger.info(f"[CE/PE] Greeks WS URL: {url[:60]}…")
    logger.info(f"[CE/PE] Greeks WS: subscribing {len(instruments)} tokens")

    results: dict = {}
    for _, (sym, side) in token_map.items():
        results.setdefault(sym, {"call_iv": None, "put_iv": None})

    received   = set()
    done_event = threading.Event()

    subscribe_msg = json.dumps({
        "Method":      "Subscribe",
        "Operation":   "optiongreek",
        "instruments": instruments,
    })

    def on_open(ws):
        logger.info("[CE/PE] Greeks WS connected — sending Subscribe")
        ws.send(subscribe_msg)

    def on_message(ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return
        ticks = data if isinstance(data, list) else [data]
        for tick in ticks:
            token_val = tick.get("Token")
            iv        = tick.get("IV")
            if token_val is None or iv is None:
                continue
            og_str = f"og{token_val}"
            if og_str in token_map:
                sym, side  = token_map[og_str]
                iv_raw     = _safe(iv)
                if iv_raw > 0:
                    # 5paisa Greeks WS returns IV as a decimal fraction (0.30 = 30%).
                    # Values < 5 are fractions; multiply by 100 to get percentage points.
                    iv_float = round(iv_raw * 100, 2) if iv_raw < 5 else round(iv_raw, 2)
                    results[sym][f"{side}_iv"] = iv_float
                    received.add(og_str)
                    logger.info(f"[CE/PE] IV ✅ {sym} {side}: raw={iv_raw:.4f} → {iv_float:.2f}%")
        if received >= set(instruments):
            logger.info("[CE/PE] All IVs received — closing Greeks WS")
            ws.close()
            done_event.set()

    def on_error(ws, error):
        logger.error(f"[CE/PE] Greeks WS error: {error}")
        done_event.set()

    def on_close(ws, code, msg):
        logger.info(f"[CE/PE] Greeks WS closed (code={code})")
        done_event.set()

    try:
        import websocket as _ws_lib
        ws  = _ws_lib.WebSocketApp(url,
                                   on_open=on_open, on_message=on_message,
                                   on_error=on_error, on_close=on_close)
        wst = threading.Thread(target=ws.run_forever, daemon=True)
        wst.start()

        timed_out = not done_event.wait(timeout=timeout)
        if timed_out:
            logger.warning(f"[CE/PE] Greeks WS timed out after {timeout}s")
            ws.close()

        missing = set(instruments) - received
        if missing:
            missed_labels = [
                f"{token_map[k][0]}({token_map[k][1]})" for k in missing
            ]
            logger.warning(f"[CE/PE] IV NOT received for: {missed_labels}")

    except ImportError:
        logger.error("[CE/PE] websocket-client not installed. "
                     "Run: pip install websocket-client")
    except Exception as e:
        logger.error(f"[CE/PE] Greeks WS exception: {e}", exc_info=True)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Black-Scholes IV fallback
# ─────────────────────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _bs_price(S, K, T, r, sigma, is_call: bool) -> float:
    try:
        if T <= 0 or sigma <= 0.001 or S <= 0 or K <= 0:
            return 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if is_call:
            return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        else:
            return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    except Exception:
        return 0.0


def _compute_iv_bs(spot: float, strike: float, premium: float,
                   expiry_raw, is_call: bool, r: float = 0.07) -> float | None:
    """
    Newton-Raphson Black-Scholes IV.
    Used only when Greeks WS returns no IV for a token.
    expiry_raw may be a /Date(ms)/ string or a date string.
    Returns IV as % (e.g. 35.2) or None.
    """
    if not all([spot > 0, strike > 0, premium > 0]):
        return None
    try:
        expiry_date = None
        if isinstance(expiry_raw, str):
            # Try /Date(ms)/ first
            ms = _parse_expiry_ms(expiry_raw)
            if ms:
                expiry_date = datetime.fromtimestamp(ms / 1000).date()
            else:
                for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        expiry_date = datetime.strptime(
                            expiry_raw.strip().upper(), fmt.upper()
                        ).date()
                        break
                    except Exception:
                        pass
        elif isinstance(expiry_raw, date):
            expiry_date = expiry_raw

        if expiry_date is None:
            logger.warning(f"[CE/PE] BS IV: could not parse expiry '{expiry_raw}'")
            return None

        T = max((expiry_date - date.today()).days, 1) / 365.0
        sigma = 0.30
        for _ in range(150):
            price = _bs_price(spot, strike, T, r, sigma, is_call)
            diff  = price - premium
            if abs(diff) < 0.001:
                break
            try:
                d1   = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (
                    sigma * math.sqrt(T)
                )
                vega = spot * _norm_pdf(d1) * math.sqrt(T)
            except Exception:
                vega = 0.0
            if vega < 1e-8:
                break
            sigma -= diff / vega
            sigma  = max(0.001, min(sigma, 20.0))

        iv_pct = round(sigma * 100, 2)
        return iv_pct if 0.5 < iv_pct < 500 else None
    except Exception as e:
        logger.error(f"[CE/PE] BS IV error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Load F&O-eligible holdings
# ─────────────────────────────────────────────────────────────────────────────

def _load_eligible_holdings(user_id: int) -> List[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT h.symbol, h.quantity, h.avg_buy_price, h.total_invested,
                       sm.canonical_symbol, sm.lot_size, sm.fno_available, sm.isin
                FROM holdings h
                LEFT JOIN user_stock_symbol_mapping usm
                    ON usm.user_id = h.user_id
                   AND UPPER(usm.symbol) = UPPER(h.symbol)
                JOIN stock_master_mapping sm ON sm.isin = usm.isin
                WHERE h.user_id = :uid
                  AND h.quantity > 0
                  AND h.segment = 'EQ'
                  AND sm.fno_available = 1
            """),
            {"uid": user_id}
        ).fetchall()

        results = []
        for r in rows:
            d = dict(r._mapping)
            lot = int(d.get("lot_size") or 0)
            # If lot_size missing in DB, try scrip_master_cache
            if lot <= 1:
                can = str(d.get("canonical_symbol") or "").strip().upper()
                if can:
                    try:
                        from services.scrip_master_db import is_db_populated, query_fno_info
                        if is_db_populated():
                            fno_ok, db_lot = query_fno_info(can)
                            if db_lot > 1:
                                d["lot_size"] = db_lot
                                db.execute(
                                    text("UPDATE stock_master_mapping "
                                         "SET lot_size=:lot WHERE canonical_symbol=:can"),
                                    {"lot": db_lot, "can": can}
                                )
                                logger.info(f"[CE/PE] Updated lot_size={db_lot} for '{can}'")
                    except Exception:
                        pass
            results.append(d)

        db.commit()
        logger.info(f"[CE/PE] {len(results)} F&O-eligible holdings for user {user_id}")
        return results
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Existing short CE/PE positions
# ─────────────────────────────────────────────────────────────────────────────

def _load_existing_short_options(user_id: int) -> Dict[str, dict]:
    """
    Returns { underlying_upper: { "CE": [pos, ...], "PE": [pos, ...] } }
    Each pos = { strike, expiry, qty }
    Stores ALL open short positions (not just one per type).
    Also includes positions computed from fno_transactions if snapshot is empty.
    """
    db = SessionLocal()
    mapping: Dict[str, dict] = defaultdict(lambda: {"CE": [], "PE": []})
    _today = date.today().isoformat()
    try:
        rows = db.execute(
            text("""
                SELECT underlying, instrument_type, strike_price, expiry_date, open_qty
                FROM fno_open_positions
                WHERE user_id = :uid
                  AND instrument_type IN ('CE','PE')
                  AND open_qty < 0
                  AND (expiry_date IS NULL OR expiry_date >= :today)
            """),
            {"uid": user_id, "today": _today}
        ).fetchall()

        # Fallback: compute from transactions if snapshot is empty
        if not rows:
            rows_txn = db.execute(
                text("""
                    SELECT underlying, instrument_type, expiry_date, strike_price,
                           SUM(CASE WHEN trade_type='BUY'  THEN  quantity ELSE 0 END) AS buy_qty,
                           SUM(CASE WHEN trade_type='SELL' THEN  quantity ELSE 0 END) AS sell_qty
                    FROM fno_transactions
                    WHERE user_id = :uid AND instrument_type IN ('CE','PE')
                      AND (expiry_date IS NULL OR expiry_date >= :today)
                    GROUP BY underlying, instrument_type, expiry_date, strike_price
                    HAVING (SUM(CASE WHEN trade_type='BUY' THEN quantity ELSE -quantity END)) < 0
                """),
                {"uid": user_id, "today": _today}
            ).fetchall()

            class _FR:
                pass
            rows = []
            for r in rows_txn:
                bq = float(r.buy_qty or 0)
                sq = float(r.sell_qty or 0)
                net = bq - sq
                if net >= 0:
                    continue
                fr = _FR()
                fr.underlying      = r.underlying
                fr.instrument_type = r.instrument_type
                fr.strike_price    = r.strike_price
                fr.expiry_date     = r.expiry_date
                fr.open_qty        = net
                rows.append(fr)

        for r in rows:
            underlying = str(r.underlying or "").strip().upper()
            itype      = str(r.instrument_type or "").strip().upper()
            if itype not in ("CE", "PE"):
                continue
            mapping[underlying][itype].append({
                "strike": float(r.strike_price or 0),
                "expiry": str(r.expiry_date or "")[:10],
                "qty":    float(r.open_qty or 0),
            })

        total = sum(len(v["CE"]) + len(v["PE"]) for v in mapping.values())
        logger.info(f"[CE/PE] Existing short positions: {total} across {len(mapping)} underlyings")
        return dict(mapping)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Signal logic
# ─────────────────────────────────────────────────────────────────────────────

def _compute_signal(pct_from_high: float | None,
                    pct_from_low:  float | None,
                    threshold: float = 10.0) -> str:
    if pct_from_high is not None and pct_from_high >= -threshold:
        return "SELL CE"
    if pct_from_low is not None and pct_from_low <= threshold:
        return "SELL PE"
    return "NEUTRAL"


def _fmt_existing(pos_list) -> str:
    """Format one or more existing option positions. pos_list is a list of dicts."""
    if not pos_list:
        return "—"
    # Support both old single-dict and new list formats
    if isinstance(pos_list, dict):
        pos_list = [pos_list]
    if not pos_list:
        return "—"
    parts = []
    for pos in pos_list:
        strike = float(pos.get("strike", 0) or 0)
        expiry = str(pos.get("expiry", "") or "")
        qty    = int(abs(float(pos.get("qty", 0) or 0)))
        # Indian format for strike
        s = str(int(strike))
        if len(s) > 3:
            result = s[-3:]
            s = s[:-3]
            while s:
                result = s[-2:] + "," + result
                s = s[:-2]
        else:
            result = s
        parts.append(f"{result} ({expiry} qty {qty})")
    return " | ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def get_ce_pe_screener(user_id: int) -> List[dict]:
    """
    Full screener. Steps:
      1. Load F&O-eligible holdings
      2. Batch 5paisa spot prices
      3. Per symbol: OHLC ranges via scrip_code → 5paisa historical_data
      4. Per symbol: 5paisa REST get_expiry + get_option_chain → tokens + premiums
      5. ONE Greeks WebSocket for all tokens → IV
      6. Black-Scholes IV fallback per symbol if WS gave nothing
      7. Signal + existing position check
      8. Build and return rows (all NaN-safe)
    """
    from services.engine_price_fetch import fetch_current_prices

    holdings = _load_eligible_holdings(user_id)
    if not holdings:
        logger.warning("[CE/PE] No F&O-eligible holdings — returning empty")
        return []

    logger.info(f"[CE/PE] === Starting screener for {len(holdings)} holdings ===")

    # ── Step 2: batch spot prices ────────────────────────────────────────────
    canonicals = [h["canonical_symbol"] for h in holdings if h.get("canonical_symbol")]
    spot_map: dict = {}
    try:
        spot_map = fetch_current_prices(canonicals)
        logger.info(f"[CE/PE] Spot prices fetched: {len(spot_map)}/{len(canonicals)}")
        for sym, px in spot_map.items():
            logger.info(f"[CE/PE]   {sym}: ₹{px}")
    except Exception as e:
        logger.error(f"[CE/PE] Spot price batch error: {e}")

    short_options = _load_existing_short_options(user_id)
    client        = _get_5paisa_client()
    access_token  = _get_access_token()

    # ── Steps 3 & 4: OHLC + option info per symbol ──────────────────────────
    cache_ohlc:    dict[str, dict] = {}
    cache_opt:     dict[str, dict] = {}   # canonical → option info dict
    token_map:     dict[str, tuple] = {}  # "og<int>" → (canonical, "call"|"put")

    for h in holdings:
        can = str(h.get("canonical_symbol") or "").strip().upper()
        if not can:
            continue

        if can not in cache_ohlc:
            cache_ohlc[can] = _fetch_ohlc_ranges(can)
            time.sleep(0.15)

        if can not in cache_opt:
            if client:
                info = _get_atm_option_info(client, can)
                cache_opt[can] = info or {}
                if info:
                    if info.get("call_token"):
                        token_map[f"og{info['call_token']}"] = (can, "call")
                    if info.get("put_token"):
                        token_map[f"og{info['put_token']}"]  = (can, "put")
                else:
                    logger.warning(f"[CE/PE] No option info for '{can}'")
                time.sleep(1.0)   # gentle on 5paisa REST
            else:
                cache_opt[can] = {}

    # ── Step 5: Greeks WebSocket — all tokens, one connection ────────────────
    iv_results: dict = {}
    if token_map and access_token:
        logger.info(f"[CE/PE] Opening Greeks WS for {len(token_map)} tokens")
        iv_results = _fetch_iv_greeks_ws(access_token, token_map, timeout=25)
    else:
        if not token_map:
            logger.warning("[CE/PE] No option tokens collected — IV via BS only")
        if not access_token:
            logger.warning("[CE/PE] No access_token — Greeks WS skipped, using BS IV")

    # ── Step 6 & 7: build result rows ────────────────────────────────────────
    results = []

    for h in holdings:
        can = str(h.get("canonical_symbol") or "").strip().upper()
        if not can:
            continue

        qty  = float(h.get("quantity", 0))
        avg  = _safe(h.get("avg_buy_price"))
        lot  = int(h.get("lot_size") or 0)
        ohlc = cache_ohlc.get(can, _OHLC_EMPTY.copy())

        # Spot: 5paisa primary, NSE fallback
        spot = _safe(spot_map.get(can) or spot_map.get(h.get("symbol", ""), 0))
        if spot <= 0:
            spot = _safe(ohlc.get("spot"))
            if spot > 0:
                logger.info(f"[CE/PE] Spot '{can}' from NSE fallback: ₹{spot}")
            else:
                logger.warning(f"[CE/PE] ⚠️ No spot for '{can}' — row skipped")
                continue

        high_52w = _safe(ohlc.get("high_52w"))
        low_52w  = _safe(ohlc.get("low_52w"))

        def _pct(s, ref):
            s, ref = _safe(s), _safe(ref)
            if ref > 0 and s > 0:
                return round((s - ref) / ref * 100, 2)
            return None

        pct_52w_high = _pct(spot, high_52w)
        pct_52w_low  = _pct(spot, low_52w)
        signal = (
            _compute_signal(pct_52w_high, pct_52w_low)
            if (pct_52w_high is not None and pct_52w_low is not None)
            else "—"
        )

        opt_info     = cache_opt.get(can, {})
        iv_data      = iv_results.get(can, {})
        call_strike  = _safe(opt_info.get("call_strike"))
        put_strike   = _safe(opt_info.get("put_strike"))
        call_premium = _safe(opt_info.get("call_premium"))
        put_premium  = _safe(opt_info.get("put_premium"))
        expiry_raw   = opt_info.get("expiry_raw", "—")

        # IV: Greeks WS first, Black-Scholes fallback
        call_iv = iv_data.get("call_iv")
        put_iv  = iv_data.get("put_iv")

        if call_iv is None and call_premium > 0 and call_strike > 0:
            call_iv = _compute_iv_bs(spot, call_strike, call_premium,
                                     expiry_raw, is_call=True)
            if call_iv:
                logger.info(f"[CE/PE] BS fallback IV '{can}' CE: {call_iv}%")

        if put_iv is None and put_premium > 0 and put_strike > 0:
            put_iv = _compute_iv_bs(spot, put_strike, put_premium,
                                    expiry_raw, is_call=False)
            if put_iv:
                logger.info(f"[CE/PE] BS fallback IV '{can}' PE: {put_iv}%")

        pos         = short_options.get(can, {})
        existing_ce = _fmt_existing(pos.get("CE", []))
        existing_pe = _fmt_existing(pos.get("PE", []))

        row = {
            "symbol":           h.get("symbol", ""),
            "canonical_symbol": can,
            "quantity":         int(qty),
            "lots_held":        int(qty // lot) if lot else 0,
            "lot_size":         lot if lot else None,
            "avg_buy_price":    round(avg, 2) if avg else None,
            "spot_price":       round(spot, 2),
            "unrealized_pnl":   round((_safe(spot) - avg) * qty, 2) if (spot and avg) else None,
            # 52W
            "high_52w":         _nz(high_52w),
            "low_52w":          _nz(low_52w),
            "pct_52w_high":     pct_52w_high,
            "pct_52w_low":      pct_52w_low,
            # 6M
            "high_6m":          _nz(ohlc.get("high_6m")),
            "low_6m":           _nz(ohlc.get("low_6m")),
            "pct_6m_high":      _pct(spot, ohlc.get("high_6m")),
            "pct_6m_low":       _pct(spot, ohlc.get("low_6m")),
            # 3M
            "high_3m":          _nz(ohlc.get("high_3m")),
            "low_3m":           _nz(ohlc.get("low_3m")),
            "pct_3m_high":      _pct(spot, ohlc.get("high_3m")),
            "pct_3m_low":       _pct(spot, ohlc.get("low_3m")),
            # 1M
            "high_1m":          _nz(ohlc.get("high_1m")),
            "low_1m":           _nz(ohlc.get("low_1m")),
            "pct_1m_high":      _pct(spot, ohlc.get("high_1m")),
            "pct_1m_low":       _pct(spot, ohlc.get("low_1m")),
            # Signal
            "signal":           signal,
            # Option chain
            "nearest_expiry":   expiry_raw if expiry_raw != "—" else None,
            "ce_strike":        _nz(call_strike),
            "ce_premium":       _nz(call_premium),
            "ce_iv":            round(call_iv, 2) if call_iv else None,
            "pe_strike":        _nz(put_strike),
            "pe_premium":       _nz(put_premium),
            "pe_iv":            round(put_iv, 2) if put_iv else None,
            # Existing shorts
            "existing_ce":      existing_ce,
            "existing_pe":      existing_pe,
        }
        results.append(row)
        logger.info(
            f"[CE/PE] ✅ {can}: spot=₹{spot} signal={signal} "
            f"CE={call_strike}@{call_premium}(IV={call_iv}%) "
            f"PE={put_strike}@{put_premium}(IV={put_iv}%)"
        )

    logger.info(f"[CE/PE] === Screener done: {len(results)} rows ===")
    return results





# ─────────────────────────────────────────────────────────────────────────────
# Step 1 helpers — holdings + futures synthetic exposure
# ─────────────────────────────────────────────────────────────────────────────

def _load_futures_exposure(user_id: int) -> Dict[str, dict]:
    """
    Load open FUT positions (from fno_open_positions OR computed from
    fno_transactions if the snapshot table is empty) and return a dict:
        { canonical_underlying_upper: {qty_shares, avg_entry, expiry} }
    qty_shares = open_qty * lot_size  (so we can add to equity qty).
    Positive = long futures, negative = short.
    """
    db = SessionLocal()
    try:
        # --- try snapshot table first (active contracts only) ---
        _today = date.today().isoformat()
        rows = db.execute(
            text("""
                SELECT underlying, instrument_type, expiry_date,
                       open_qty, avg_price, strike_price, broker
                FROM fno_open_positions
                WHERE user_id = :uid AND instrument_type = 'FUT'
                  AND ABS(open_qty) > 0
                  AND (expiry_date IS NULL OR expiry_date >= :today)
            """),
            {"uid": user_id, "today": _today}
        ).fetchall()

        # --- fallback: compute from transactions ---
        if not rows:
            rows_txn = db.execute(
                text("""
                    SELECT underlying, instrument_type, expiry_date,
                           strike_price, broker,
                           SUM(CASE WHEN trade_type='BUY'  THEN  quantity ELSE 0 END) AS buy_qty,
                           SUM(CASE WHEN trade_type='SELL' THEN  quantity ELSE 0 END) AS sell_qty,
                           SUM(CASE WHEN trade_type='BUY'  THEN quantity*price ELSE 0 END) AS buy_val,
                           SUM(CASE WHEN trade_type='SELL' THEN quantity*price ELSE 0 END) AS sell_val
                    FROM fno_transactions
                    WHERE user_id = :uid AND instrument_type = 'FUT'
                      AND (expiry_date IS NULL OR expiry_date >= :today)
                    GROUP BY underlying, instrument_type, expiry_date, strike_price, broker
                    HAVING ABS(SUM(CASE WHEN trade_type='BUY' THEN quantity ELSE -quantity END)) > 0.001
                """),
                {"uid": user_id, "today": _today}
            ).fetchall()
            # Reshape to match snapshot schema
            from datetime import date as _date
            today_str = _date.today().isoformat()
            rows = []
            for r in rows_txn:
                bq = float(r.buy_qty or 0)
                sq = float(r.sell_qty or 0)
                net = bq - sq
                if abs(net) < 0.001:
                    continue
                avg = (float(r.buy_val or 0) / bq) if net > 0 and bq > 0 else (
                    float(r.sell_val or 0) / sq if sq > 0 else 0.0
                )

                class _FakeRow:
                    pass
                fr = _FakeRow()
                fr.underlying    = r.underlying
                fr.instrument_type = r.instrument_type
                fr.expiry_date   = r.expiry_date
                fr.open_qty      = net
                fr.avg_price     = avg
                fr.strike_price  = r.strike_price
                fr.broker        = r.broker
                rows.append(fr)

        # --- aggregate by underlying ---
        result: Dict[str, dict] = {}
        for r in rows:
            can = str(r.underlying or "").strip().upper()
            if not can:
                continue
            # Resolve lot size for this underlying
            lot = _get_lot_size_for(can, db)
            # open_qty is already stored in SHARES (not lots) — do NOT multiply by lot again.
            # e.g. HDFCBANK FUT qty=550 means 550 shares = 1 contract of 550.
            # Multiplying by lot would give 550*550=302,500 which is wrong.
            qty_shares = float(r.open_qty or 0)
            # Number of contracts = qty_shares / lot_size
            contracts = round(qty_shares / lot, 2) if lot > 0 else qty_shares
            if can not in result:
                result[can] = {
                    "qty_shares":  qty_shares,
                    "contracts":   contracts,          # actual number of FUT contracts
                    "avg_entry":   float(r.avg_price or 0),
                    "expiry":      str(r.expiry_date or "")[:10],
                    "open_qty":    float(r.open_qty or 0),
                    "lot_size":    lot,
                }
            else:
                result[can]["qty_shares"] += qty_shares
                result[can]["contracts"]  += contracts
                result[can]["open_qty"]   += float(r.open_qty or 0)

        return result
    finally:
        db.close()


def _get_lot_size_for(canonical: str, db) -> int:
    """Quick lot-size lookup from stock_master_mapping."""
    try:
        row = db.execute(
            text("SELECT lot_size FROM stock_master_mapping "
                 "WHERE UPPER(canonical_symbol)=:can LIMIT 1"),
            {"can": canonical.upper()}
        ).first()
        if row and row.lot_size and int(row.lot_size) > 0:
            return int(row.lot_size)
    except Exception:
        pass
    return 1   # fallback


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — load ALL open option positions (CE + PE, long + short)
# ─────────────────────────────────────────────────────────────────────────────

def _load_all_option_positions(user_id: int) -> Dict[str, dict]:
    """
    Returns { canonical_upper: {
        sold_ce, sold_pe, bought_ce, bought_pe,
        open_expiry, days_to_expiry } }
    Uses snapshot table; falls back to fno_transactions computation.
    """
    db = SessionLocal()
    try:
        _today = date.today().isoformat()
        rows = db.execute(
            text("""
                SELECT underlying, instrument_type, strike_price,
                       expiry_date, open_qty, avg_price
                FROM fno_open_positions
                WHERE user_id = :uid
                  AND instrument_type IN ('CE','PE')
                  AND ABS(open_qty) > 0
                  AND (expiry_date IS NULL OR expiry_date >= :today)
            """),
            {"uid": user_id, "today": _today}
        ).fetchall()

        if not rows:
            rows_txn = db.execute(
                text("""
                    SELECT underlying, instrument_type, expiry_date,
                           strike_price,
                           SUM(CASE WHEN trade_type='BUY'  THEN  quantity ELSE 0 END) AS buy_qty,
                           SUM(CASE WHEN trade_type='SELL' THEN  quantity ELSE 0 END) AS sell_qty,
                           SUM(CASE WHEN trade_type='BUY'  THEN quantity*price ELSE 0 END) AS buy_val,
                           SUM(CASE WHEN trade_type='SELL' THEN quantity*price ELSE 0 END) AS sell_val
                    FROM fno_transactions
                    WHERE user_id = :uid AND instrument_type IN ('CE','PE')
                      AND (expiry_date IS NULL OR expiry_date >= :today)
                    GROUP BY underlying, instrument_type, expiry_date, strike_price
                    HAVING ABS(SUM(CASE WHEN trade_type='BUY' THEN quantity ELSE -quantity END)) > 0.001
                """),
                {"uid": user_id, "today": _today}
            ).fetchall()

            class _FakeRow:
                pass
            rows = []
            for r in rows_txn:
                bq = float(r.buy_qty or 0)
                sq = float(r.sell_qty or 0)
                net = bq - sq
                if abs(net) < 0.001:
                    continue
                avg = (float(r.buy_val or 0) / bq if bq > 0 else 0.0) if net > 0 else (
                    float(r.sell_val or 0) / sq if sq > 0 else 0.0
                )
                fr = _FakeRow()
                fr.underlying    = r.underlying
                fr.instrument_type = r.instrument_type
                fr.strike_price  = r.strike_price
                fr.expiry_date   = r.expiry_date
                fr.open_qty      = net
                fr.avg_price     = avg
                rows.append(fr)

        mapping: Dict[str, dict] = defaultdict(lambda: {
            "sold_ce": [], "sold_pe": [],
            "bought_ce": [], "bought_pe": [],
            "open_expiry": None, "days_to_expiry": 9999,
        })

        today = date.today()
        for r in rows:
            can   = str(r.underlying or "").strip().upper()
            itype = str(r.instrument_type or "").strip().upper()
            qty   = float(r.open_qty or 0)
            strike = float(r.strike_price or 0)
            expiry_str = str(r.expiry_date or "")[:10]
            avg   = float(r.avg_price or 0)

            try:
                exp_dt = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                dte    = (exp_dt - today).days
            except Exception:
                dte = 9999

            pos_info = {
                "strike": strike,
                "expiry": expiry_str,
                "qty":    qty,
                "avg":    avg,
                "dte":    dte,
            }

            if qty < 0:   # short (sold)
                if itype == "CE":
                    mapping[can]["sold_ce"].append(pos_info)
                elif itype == "PE":
                    mapping[can]["sold_pe"].append(pos_info)
            else:          # long (bought)
                if itype == "CE":
                    mapping[can]["bought_ce"].append(pos_info)
                elif itype == "PE":
                    mapping[can]["bought_pe"].append(pos_info)

            if dte < mapping[can]["days_to_expiry"]:
                mapping[can]["days_to_expiry"] = dte
                mapping[can]["open_expiry"]    = expiry_str

        return dict(mapping)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 — upcoming corp events
# ─────────────────────────────────────────────────────────────────────────────

def _load_corp_events(user_id: int, lookahead_days: int = 45) -> Dict[str, str]:
    """
    Returns { canonical_upper: "DIVIDEND 2025-05-15" } for events within
    `lookahead_days` calendar days from today.
    """
    db = SessionLocal()
    try:
        cutoff = (date.today() + timedelta(days=lookahead_days)).isoformat()
        rows = db.execute(
            text("""
                SELECT symbol, action_type, ex_date
                FROM corporate_actions
                WHERE user_id = :uid
                  AND ex_date >= :today
                  AND ex_date <= :cutoff
                  AND action_type IN ('DIVIDEND','RESULTS','BONUS','SPLIT','MERGER','DEMERGER')
                ORDER BY ex_date ASC
            """),
            {"uid": user_id, "today": date.today().isoformat(), "cutoff": cutoff}
        ).fetchall()
        result: Dict[str, str] = {}
        for r in rows:
            can = str(r.symbol or "").strip().upper()
            if can and can not in result:
                result[can] = f"{r.action_type} {str(r.ex_date)[:10]}"
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Two-expiry option fetch  (nearest + far month)
# ─────────────────────────────────────────────────────────────────────────────

def _get_two_expiry_option_info(client, symbol: str) -> dict | None:
    """
    Extends _get_atm_option_info to return BOTH nearest and far-month data.
    Returns dict with n_* and f_* prefixed keys, or None on failure.
    """
    try:
        exp_resp = client.get_expiry("N", symbol)
        if not exp_resp or exp_resp.get("Status") != 0:
            return None

        expiry_list   = exp_resp.get("Expiry", [])
        lastrate_list = exp_resp.get("lastrate", [])
        if not expiry_list or not lastrate_list:
            return None

        ltp = _safe(lastrate_list[0].get("LTP", 0))

        def _parse_exp(entry) -> tuple[int | None, str]:
            raw = entry.get("ExpiryDate", "")
            ts  = _parse_expiry_ms(str(raw))
            try:
                readable = datetime.fromtimestamp(ts / 1000).strftime("%d-%b-%Y") if ts else str(raw)
            except Exception:
                readable = str(raw)
            return ts, readable

        def _build_option_data(ts, readable) -> dict | None:
            if ts is None:
                return None
            chain_resp = client.get_option_chain("N", symbol, ts)
            if isinstance(chain_resp, dict):
                if chain_resp.get("Status") != 0:
                    return None
                all_options = chain_resp.get("Options") or chain_resp.get("Data", [])
            elif isinstance(chain_resp, list):
                all_options = chain_resp
            else:
                return None
            if not all_options:
                return None

            def _cp(opt):
                return opt.get("CPType") or opt.get("OptionType", "")

            calls = sorted([o for o in all_options if _cp(o) in ("C", "CE")],
                           key=lambda x: float(x.get("StrikeRate", 0)))
            puts  = sorted([o for o in all_options if _cp(o) in ("P", "PE")],
                           key=lambda x: float(x.get("StrikeRate", 0)))

            if not calls:
                return None

            atm_call   = min(calls, key=lambda x: abs(float(x.get("StrikeRate", 0)) - ltp))
            atm_put    = min(puts,  key=lambda x: abs(float(x.get("StrikeRate", 0)) - ltp)) if puts else None
            atm_strike = float(atm_call.get("StrikeRate", ltp))

            otm_calls = [c for c in calls if float(c.get("StrikeRate", 0)) > atm_strike]
            otm_puts  = [p for p in puts  if float(p.get("StrikeRate", 0)) < atm_strike]
            ce_opt    = otm_calls[0] if otm_calls else atm_call
            pe_opt    = otm_puts[-1] if otm_puts  else atm_put

            def _tok(opt):
                if opt is None: return None
                for k in ("Token", "ScripCode", "token", "scripCode"):
                    v = opt.get(k)
                    if v is not None: return int(v)
                return None

            def _prem(opt):
                if opt is None: return 0.0
                for k in ("CPLastRate", "LastRate", "LTP", "lastrate"):
                    v = opt.get(k)
                    if v is not None: return _safe(v)
                return 0.0

            return {
                "expiry_readable": readable,
                "expiry_ts":       ts,
                "ce_token":   _tok(ce_opt),
                "ce_strike":  _safe(ce_opt.get("StrikeRate", 0)) if ce_opt else 0.0,
                "ce_premium": _prem(ce_opt),
                "pe_token":   _tok(pe_opt),
                "pe_strike":  _safe(pe_opt.get("StrikeRate", 0)) if pe_opt else 0.0,
                "pe_premium": _prem(pe_opt),
            }

        # Nearest
        n_ts, n_readable = _parse_exp(expiry_list[0])
        near = _build_option_data(n_ts, n_readable)
        time.sleep(0.8)

        # Far (next month — index 1 if available)
        far = None
        if len(expiry_list) > 1:
            f_ts, f_readable = _parse_exp(expiry_list[1])
            far = _build_option_data(f_ts, f_readable)
            time.sleep(0.8)

        if near is None:
            return None

        return {"ltp": ltp, "near": near, "far": far}

    except Exception as e:
        logger.error(f"[AdvScr] _get_two_expiry_option_info error for {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Delta & Prob-OTM from IV
# ─────────────────────────────────────────────────────────────────────────────

def _delta_and_prob(spot: float, strike: float, iv_pct: float,
                    expiry_str: str, is_call: bool, r: float = 0.07) -> tuple[float | None, float | None]:
    """
    Black-Scholes delta and approximate probability of expiring OTM.
    Returns (delta, prob_otm_pct) or (None, None) on bad inputs.
    """
    try:
        if not all([spot > 0, strike > 0, iv_pct and iv_pct > 0]):
            return None, None
        sigma = iv_pct / 100.0
        exp_dt = None
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                exp_dt = datetime.strptime(expiry_str.strip(), fmt).date()
                break
            except Exception:
                pass
        if exp_dt is None:
            return None, None
        T = max((exp_dt - date.today()).days, 1) / 365.0
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        # delta
        def _ncdf(x): return 0.5 * math.erfc(-x / math.sqrt(2))
        delta = _ncdf(d1) if is_call else -_ncdf(-d1)
        # Prob OTM for seller: call seller wants spot < strike → prob = N(-d2);
        #                      put  seller wants spot > strike → prob = N(d2)
        prob_otm = _ncdf(-d2) * 100 if is_call else _ncdf(d2) * 100
        return round(delta, 4), round(prob_otm, 1)
    except Exception:
        return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — position signal
# ─────────────────────────────────────────────────────────────────────────────

_PROFIT_THRESHOLD  = 0.90   # bought option profit > 90% → SQUARE_OFF
_ROLLOVER_DTE      = 15     # days-to-expiry threshold for ROLLOVER
_CORRECTION_PCT    = 0.05   # sold option threatened if spot moved 5% against


def _position_signal(can: str, spot: float, opt_pos: dict | None,
                     fut_expiry: str | None = None) -> tuple[str, str]:
    """
    Returns (position_signal, reason_str).
    position_signal ∈ {SQUARE_OFF, FUT_ROLLOVER, OPT_ROLLOVER, CORRECTION, FRESH, HOLD}

    ROLLOVER is split into two cases:
      FUT_ROLLOVER — only FUT positions expiring soon (no sold options)
      OPT_ROLLOVER — sold CE/PE or bought CE/PE expiring soon
    """
    has_any_option = opt_pos and (
        opt_pos.get("sold_ce") or opt_pos.get("sold_pe") or
        opt_pos.get("bought_ce") or opt_pos.get("bought_pe")
    )

    if not has_any_option:
        # No option positions at all — check if FUT is near expiry
        if fut_expiry:
            try:
                exp_dt = datetime.strptime(fut_expiry[:10], "%Y-%m-%d").date()
                fut_dte = (exp_dt - date.today()).days
                if 0 <= fut_dte < _ROLLOVER_DTE:
                    return ("FUT_ROLLOVER",
                            f"FUT position expires in {fut_dte} days — roll to next month")
            except Exception:
                pass
        return "FRESH", "No existing option positions"

    # sold_ce/sold_pe/bought_ce/bought_pe are now LISTS (multiple strikes supported)
    sold_ce_list   = opt_pos.get("sold_ce")   or []
    sold_pe_list   = opt_pos.get("sold_pe")   or []
    bought_ce_list = opt_pos.get("bought_ce") or []
    bought_pe_list = opt_pos.get("bought_pe") or []
    dte            = opt_pos.get("days_to_expiry", 9999)

    # For backward compat: if a single dict slipped through, wrap it
    if isinstance(sold_ce_list, dict):   sold_ce_list   = [sold_ce_list]
    if isinstance(sold_pe_list, dict):   sold_pe_list   = [sold_pe_list]
    if isinstance(bought_ce_list, dict): bought_ce_list = [bought_ce_list]
    if isinstance(bought_pe_list, dict): bought_pe_list = [bought_pe_list]

    # Convenience: first item (highest qty / soonest) used for single-item logic
    sold_ce   = sold_ce_list[0]   if sold_ce_list   else None
    sold_pe   = sold_pe_list[0]   if sold_pe_list   else None
    bought_ce = bought_ce_list[0] if bought_ce_list else None
    bought_pe = bought_pe_list[0] if bought_pe_list else None

    # 1. SQUARE_OFF — any bought option deep ITM
    for pos_list, is_call in [(bought_ce_list, True), (bought_pe_list, False)]:
        for pos in pos_list:
            strike = pos.get("strike", 0)
            avg    = pos.get("avg", 0)
            if avg <= 0:
                continue
            intrinsic = max(spot - strike, 0) if is_call else max(strike - spot, 0)
            if intrinsic > 0 and (intrinsic - avg) / avg >= _PROFIT_THRESHOLD:
                itype = "CE" if is_call else "PE"
                return ("SQUARE_OFF",
                        f"Bought {itype} deep ITM: entry=₹{avg:.2f}, "
                        f"intrinsic=₹{intrinsic:.2f} (~{(intrinsic/avg-1)*100:.0f}% gain)")

    # 2. OPT_ROLLOVER — option position near expiry
    if dte < _ROLLOVER_DTE and dte >= 0:
        return ("OPT_ROLLOVER",
                f"Option position expires in {dte} days — roll to next month")

    # 3. CORRECTION — any sold option being threatened
    for pos in sold_ce_list:
        strike = pos.get("strike", 0)
        if strike > 0 and spot >= strike * (1 - _CORRECTION_PCT):
            return ("CORRECTION",
                    f"Sold CE strike={strike:.0f} threatened (spot ₹{spot:.2f}); "
                    f"consider buying PE hedge")
    for pos in sold_pe_list:
        strike = pos.get("strike", 0)
        if strike > 0 and spot <= strike * (1 + _CORRECTION_PCT):
            return ("CORRECTION",
                    f"Sold PE strike={strike:.0f} threatened (spot ₹{spot:.2f}); "
                    f"consider buying CE hedge")

    # 4. HOLD — existing sold position(s) healthy
    if sold_ce_list or sold_pe_list:
        parts = []
        for pos in sold_ce_list:
            parts.append(f"CE sold@{pos.get('strike',0):.0f}")
        for pos in sold_pe_list:
            parts.append(f"PE sold@{pos.get('strike',0):.0f}")
        return "HOLD", "Healthy sold position: " + ", ".join(parts)

    return "FRESH", "No sold option — evaluate fresh entry"


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — fresh signal (price vs OHLC levels)
# ─────────────────────────────────────────────────────────────────────────────

_FRESH_PCT = 5.0   # within 5% of period H/L triggers signal


def _fresh_signal(spot: float, ohlc: dict, corp_event: str | None,
                  fut_bias: bool = False) -> tuple[str, str]:
    """
    Returns (final_signal, reason).
    final_signal ∈ {SELL CE, SELL PE, NEUTRAL}
    """
    if corp_event:
        return "NEUTRAL", f"Corp event upcoming: {corp_event}"

    def _near_high(h):
        if h and h > 0 and spot > 0:
            return (h - spot) / h * 100 <= _FRESH_PCT
        return False

    def _near_low(l):
        if l and l > 0 and spot > 0:
            return (spot - l) / l * 100 <= _FRESH_PCT
        return False

    if fut_bias:
        return "SELL CE", "Long futures position → covered call bias"

    if _near_high(ohlc.get("high_1m")) or _near_high(ohlc.get("high_3m")):
        ref = "1M" if _near_high(ohlc.get("high_1m")) else "3M"
        return "SELL CE", f"Spot within 5% of {ref} high — near resistance"

    if _near_low(ohlc.get("low_1m")) or _near_low(ohlc.get("low_3m")):
        ref = "1M" if _near_low(ohlc.get("low_1m")) else "3M"
        return "SELL PE", f"Spot within 5% of {ref} low — near support"

    return "NEUTRAL", "Spot mid-range — no clear edge"


# ─────────────────────────────────────────────────────────────────────────────
# Suggested action text
# ─────────────────────────────────────────────────────────────────────────────

def _build_action_text(pos_sig: str, final_sig: str, can: str,
                       opt_pos: dict | None,
                       near: dict | None, far: dict | None,
                       reason: str) -> str:
    lines = []
    if pos_sig == "SQUARE_OFF":
        # bought_ce / bought_pe are now lists
        bce = (opt_pos or {}).get("bought_ce") or []
        bpe = (opt_pos or {}).get("bought_pe") or []
        if isinstance(bce, dict): bce = [bce]
        if isinstance(bpe, dict): bpe = [bpe]
        pos_type = "CE" if bce else "PE"
        pos_list = bce if bce else bpe
        pos = pos_list[0] if pos_list else {}
        lines.append(f"✅ CLOSE bought {pos_type}: strike={pos.get('strike',0):.0f}, "
                     f"entry avg=₹{pos.get('avg',0):.2f}  ({reason})")
        if final_sig in ("SELL CE", "SELL PE"):
            lines.append(f"➡ After closing, evaluate {final_sig} on {can}")
    elif pos_sig == "OPT_ROLLOVER":
        open_exp = (opt_pos or {}).get("open_expiry", "")
        lines.append(f"🔁 ROLL option position (expires {open_exp}) to next month")
        if far and far.get("expiry_readable"):
            lines.append(f"   Far-month expiry: {far['expiry_readable']}")
        if final_sig != "NEUTRAL":
            lines.append(f"   Direction for new position: {final_sig}")
    elif pos_sig == "FUT_ROLLOVER":
        lines.append(f"🔀 ROLL FUT position: {reason}")
        if far and far.get("expiry_readable"):
            lines.append(f"   Roll to far-month expiry: {far['expiry_readable']}")
        if final_sig != "NEUTRAL":
            lines.append(f"   Consider selling {final_sig} on the new FUT month")
    elif pos_sig == "CORRECTION":
        lines.append(f"⚠️ HEDGE: {reason}")
        if final_sig == "SELL CE" and near:
            lines.append(f"   Buy PE hedge: strike≈{near.get('pe_strike',0):.0f} "
                         f"premium≈₹{near.get('pe_premium',0):.2f}")
        elif final_sig == "SELL PE" and near:
            lines.append(f"   Buy CE hedge: strike≈{near.get('ce_strike',0):.0f} "
                         f"premium≈₹{near.get('ce_premium',0):.2f}")
    elif pos_sig in ("FRESH", "FUT_ROLLOVER") and final_sig != "NEUTRAL":
        side = "CE" if final_sig == "SELL CE" else "PE"
        lines.append(f"🆕 {final_sig} on {can}  ({reason})")
        if near:
            k = f"{side.lower()}_strike"
            p = f"{side.lower()}_premium"
            d = f"{side.lower()}_delta"
            delta_val = near.get(d)
            # ⭐ CRITICAL FIX: Add Delta to the suggestion text
            delta_str = f" Δ={delta_val:.2f}" if delta_val is not None else ""
            lines.append(f"   Nearest: strike={near.get(k,0):.0f}  "
                         f"premium≈₹{near.get(p,0):.2f}{delta_str}  "
                         f"expiry={near.get('expiry_readable','?')}")
        # ⭐ FALLBACK FIX: User knows why the table is empty
        else:
            lines.append("   ⚠️ Option chain data unavailable. Check if symbol is F&O-eligible or check backend logs.")
        if far:
            k = f"{side.lower()}_strike"
            p = f"{side.lower()}_premium"
            lines.append(f"   Far-month: strike={far.get(k,0):.0f}  "
                         f"premium≈₹{far.get(p,0):.2f}  "
                         f"expiry={far.get('expiry_readable','?')}")
    elif pos_sig == "HOLD":
        lines.append(f"✅ HOLD — {reason}")

    if not lines:
        lines.append(f"{pos_sig} → {final_sig}: {reason}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 helper — format one expiry block's IV + delta + prob columns
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_one_expiry(opt_data: dict | None, spot: float,
                       iv_results: dict, sym: str,
                       prefix: str) -> dict:
    """
    prefix = "n_" or "f_"
    Returns flat dict of {n_ce_strike, n_ce_premium, n_ce_iv, n_ce_delta, ...}
    """
    if not opt_data:
        return {}

    out = {
        f"{prefix}expiry":     opt_data.get("expiry_readable", "—"),
        f"{prefix}ce_strike":  _nz(opt_data.get("ce_strike")),
        f"{prefix}ce_premium": _nz(opt_data.get("ce_premium")),
        f"{prefix}ce_iv":      None,
        f"{prefix}ce_delta":   None,
        f"{prefix}ce_prob_otm":None,
        f"{prefix}pe_strike":  _nz(opt_data.get("pe_strike")),
        f"{prefix}pe_premium": _nz(opt_data.get("pe_premium")),
        f"{prefix}pe_iv":      None,
        f"{prefix}pe_delta":   None,
        f"{prefix}pe_prob_otm":None,
    }

    iv_data = iv_results.get(sym, {})

    # CE
    ce_iv = iv_data.get(f"{prefix}call_iv") or iv_data.get("call_iv")
    if ce_iv is None:
        ce_prem = _safe(opt_data.get("ce_premium"))
        ce_st   = _safe(opt_data.get("ce_strike"))
        if ce_prem > 0 and ce_st > 0:
            ce_iv = _compute_iv_bs(spot, ce_st, ce_prem,
                                   opt_data.get("expiry_readable", ""), is_call=True)
    if ce_iv:
        out[f"{prefix}ce_iv"] = round(ce_iv, 2)
        d, p = _delta_and_prob(spot, _safe(opt_data.get("ce_strike")),
                               ce_iv, opt_data.get("expiry_readable", ""), is_call=True)
        out[f"{prefix}ce_delta"]    = d
        out[f"{prefix}ce_prob_otm"] = p

    # PE
    pe_iv = iv_data.get(f"{prefix}put_iv") or iv_data.get("put_iv")
    if pe_iv is None:
        pe_prem = _safe(opt_data.get("pe_premium"))
        pe_st   = _safe(opt_data.get("pe_strike"))
        if pe_prem > 0 and pe_st > 0:
            pe_iv = _compute_iv_bs(spot, pe_st, pe_prem,
                                   opt_data.get("expiry_readable", ""), is_call=False)
    if pe_iv:
        out[f"{prefix}pe_iv"] = round(pe_iv, 2)
        d, p = _delta_and_prob(spot, _safe(opt_data.get("pe_strike")),
                               pe_iv, opt_data.get("expiry_readable", ""), is_call=False)
        out[f"{prefix}pe_delta"]    = d
        out[f"{prefix}pe_prob_otm"] = p

    return out


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — Advanced Options Screener  (8-step pipeline)
# ─────────────────────────────────────────────────────────────────────────────
def _load_all_fno_underlyings(user_id: int) -> dict:
    """
    Load ALL unique underlyings from fno_open_positions (active only).
    Returns: { CANONICAL_UPPER: {"lot_size": int} }
    """
    from database import SessionLocal
    from sqlalchemy import text
    import datetime

    db = SessionLocal()
    try:
        today_str = datetime.date.today().isoformat()
        rows = db.execute(
            text("""
                SELECT DISTINCT underlying
                FROM fno_open_positions
                WHERE user_id = :uid
                  AND ABS(open_qty) > 0.001
                  AND (expiry_date IS NULL OR expiry_date >= :today)
            """),
            {"uid": user_id, "today": today_str}
        ).fetchall()

        result = {}
        for r in rows:
            can = str(r.underlying or "").strip().upper()
            if not can:
                continue
            lot = 0
            try:
                lot_row = db.execute(
                    text("SELECT lot_size FROM stock_master_mapping "
                         "WHERE UPPER(canonical_symbol) = :can LIMIT 1"),
                    {"can": can}
                ).first()
                if lot_row and lot_row.lot_size:
                    lot = int(lot_row.lot_size)
            except Exception:
                pass
            result[can] = {"lot_size": lot or 1}
        return result
    finally:
        db.close()
        
def _get_two_expiry_option_info_batch(symbols: list, client, max_workers=4) -> dict:
    """Fetch option info for multiple symbols concurrently."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {executor.submit(_get_two_expiry_option_info, client, sym): sym for sym in symbols}
        for future in as_completed(future_to_symbol):
            sym = future_to_symbol[future]
            try:
                results[sym] = future.result()
            except Exception as e:
                logger.error(f"[CE/PE] Error fetching {sym}: {e}")
                results[sym] = None
    return results
        
def _fetch_ohlc_batch(symbols: list, max_workers=4) -> dict:
    """Fetch OHLC ranges for multiple symbols concurrently."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {executor.submit(_fetch_ohlc_ranges, sym): sym for sym in symbols}
        for future in as_completed(future_to_symbol):
            sym = future_to_symbol[future]
            try:
                results[sym] = future.result()
            except Exception as e:
                logger.error(f"[CE/PE] OHLC fetch error for {sym}: {e}")
                results[sym] = _OHLC_EMPTY.copy()
    return results

def get_advanced_options_screener(user_id: int) -> List[dict]:
    """
    Full 8-step advanced options screener for Section B.

    Step 1  Holdings + Futures lots (synthetic equity exposure)
    Step 2  Eligible stocks + total qty
    Step 3  Live spot price
    Step 4  1M / 3M / 52W OHLC
    Step 5  Existing position signals:
              SQUARE_OFF  — bought option deep ITM (profit > 90%)
              ROLLOVER    — open option expires in < 15 days
              CORRECTION  — sold option being threatened
              FRESH       — no existing sold position
              HOLD        — existing sold position healthy
    Step 6  Fresh signal: within 5% of 1M/3M high → SELL CE; low → SELL PE
    Step 7  Option selection: nearest + far month, strike, premium, Δ, Prob-OTM
    Step 8  Corp event alerts (upcoming dividends / results / splits)

    Returns list of row dicts (all NaN-safe).
    """
    from services.engine_price_fetch import fetch_current_prices

    logger.info(f"[AdvScr] === Starting advanced screener for user {user_id} ===")

#   # ── Step 1 & 2: Holdings + Futures + ALL F&O position underlyings ─────────
    equity_holdings = _load_eligible_holdings(user_id)
    futures_map     = _load_futures_exposure(user_id)
    fno_pos_underlyings = _load_all_fno_underlyings(user_id)   # ← NEW

    # Start with equity holdings
    all_underlyings: Dict[str, dict] = {}
    for h in equity_holdings:
        can = str(h.get("canonical_symbol") or "").strip().upper()
        if not can:
            continue
        all_underlyings[can] = {
            "symbol":        h.get("symbol", ""),
            "canonical":     can,
            "equity_qty":    float(h.get("quantity", 0)),
            "avg_buy_price": _safe(h.get("avg_buy_price")),
            "lot_size":      int(h.get("lot_size") or 0),
            "isin":          h.get("isin", ""),
        }

    # Add futures-only underlyings
    for can, finfo in futures_map.items():
        if can not in all_underlyings:
            all_underlyings[can] = {
                "symbol":        can,
                "canonical":     can,
                "equity_qty":    0.0,
                "avg_buy_price": 0.0,
                "lot_size":      finfo.get("lot_size", 1),
                "isin":          "",
            }

    if not all_underlyings:
        logger.warning("[AdvScr] No eligible underlyings found")
        return []

    logger.info(f"[AdvScr] {len(all_underlyings)} underlyings (equity + FUT)")

    for can, pos_info in fno_pos_underlyings.items():
        if can not in all_underlyings:
            lot = pos_info.get("lot_size", 1)
            all_underlyings[can] = {
                "symbol":        can,
                "canonical":     can,
                "equity_qty":    0.0,
                "avg_buy_price": 0.0,
                "lot_size":      lot,
                "isin":          "",
            }
        elif all_underlyings[can].get("lot_size", 0) <= 1:
            # Update lot_size if we have better info from positions
            lot = pos_info.get("lot_size", 1)
            if lot > 1:
                all_underlyings[can]["lot_size"] = lot

    logger.info(f"[AdvScr] {len(all_underlyings)} total underlyings "
                f"(equity={len(equity_holdings)}, FUT-only={len(futures_map)}, "
                f"F&O-pos={len(fno_pos_underlyings)})")

    # ── Step 3: Batch spot prices ────────────────────────────────────────────
    symbols_list = list(all_underlyings.keys())
    spot_map: Dict[str, float] = {}
    try:
        spot_map = fetch_current_prices(symbols_list)
        logger.info(f"[AdvScr] Spot prices: {len(spot_map)}/{len(symbols_list)}")
    except Exception as e:
        logger.error(f"[AdvScr] Spot fetch error: {e}")

    # ── Step 4: OHLC per symbol (parallel) ──────────────────────────────────
    ohlc_cache = _fetch_ohlc_batch(list(all_underlyings.keys()), max_workers=4)

    # ── Step 5 data: existing option positions ───────────────────────────────
    opt_positions = _load_all_option_positions(user_id)

    # ── Step 8 data: corp events ─────────────────────────────────────────────
    corp_events = _load_corp_events(user_id)

    # ── Step 7 data: option chain (5paisa) ───────────────────────────────────
    client       = _get_5paisa_client()
    access_token = _get_access_token()

    # Pre-build set of symbols confirmed in derivative master (scrip_master_cache)
    # so we skip API calls for HUDCO-type stocks not in F&O
    _fno_confirmed: set = set()
    try:
        from database import SessionLocal as _SL
        from sqlalchemy import text as _t
        _db = _SL()
        try:
            _rows = _db.execute(_t(
                "SELECT DISTINCT UPPER(symbol_root) AS sr FROM scrip_master_cache "
                "WHERE exch='N' AND exch_type='D' AND symbol_root IS NOT NULL AND symbol_root != ''"
            )).fetchall()
            _fno_confirmed = {r.sr for r in _rows if r.sr}
        finally:
            _db.close()
    except Exception:
        pass   # if DB query fails, we'll just try all symbols and let API return empty

    two_exp_cache: Dict[str, dict] = {}   # can → {ltp, near, far}
    token_map: Dict[str, tuple]    = {}   # "og<int>" → (can, "n_call"|"n_put"|"f_call"|"f_put")

    # Parallel fetch for symbols that are in derivative master
    symbols_to_fetch = [can for can in all_underlyings if not (_fno_confirmed and can not in _fno_confirmed)]
    if symbols_to_fetch and client:
        batch_results = _get_two_expiry_option_info_batch(symbols_to_fetch, client, max_workers=4)
        for can, info in batch_results.items():
            two_exp_cache[can] = info or {}
            if info:
                near = info.get("near") or {}
                far  = info.get("far")  or {}
                if near.get("ce_token"):
                    token_map[f"og{near['ce_token']}"] = (can, "n_call")
                if near.get("pe_token"):
                    token_map[f"og{near['pe_token']}"] = (can, "n_put")
                if far and far.get("ce_token"):
                    token_map[f"og{far['ce_token']}"] = (can, "f_call")
                if far and far.get("pe_token"):
                    token_map[f"og{far['pe_token']}"] = (can, "f_put")
    # For symbols not in derivative master, set empty
    for can in all_underlyings:
        if can not in two_exp_cache:
            two_exp_cache[can] = {}

    # Batch IV via Greeks WS — all tokens in one shot
    # We flatten the iv_results so _enrich_one_expiry can find n_call_iv / f_call_iv
    raw_iv: Dict[str, dict] = {}    # {can: {"n_call_iv":%, "n_put_iv":%, "f_call_iv":%, ...}}

    if token_map and access_token:
        # Build a token_map shaped for _fetch_iv_greeks_ws
        # (it expects {og_str: (sym, "call"|"put")})
        # We pass "n_call" → strip prefix; greeks WS doesn't need it,
        # but we DO need to demux the response.
        ws_token_map = {}
        for og, (can, label) in token_map.items():
            # Map label → call/put
            side = "call" if "call" in label else "put"
            ws_token_map[og] = (can + "|" + label, side)   # embed label in sym

        iv_raw_ws = _fetch_iv_greeks_ws(access_token, ws_token_map, timeout=30)
        # Demux back to per-symbol n_call_iv / n_put_iv / f_call_iv / f_put_iv
        for og, (compound, side) in ws_token_map.items():
            can, label = compound.split("|", 1)
            iv_val = (iv_raw_ws.get(compound) or {}).get(f"{side}_iv")
            if iv_val is not None:
                raw_iv.setdefault(can, {})[f"{label}_iv"] = iv_val

    # ── Build result rows ────────────────────────────────────────────────────
    results = []

    for can, base in all_underlyings.items():
        eq_qty  = base["equity_qty"]
        avg     = base["avg_buy_price"]
        lot     = base["lot_size"]

        # Futures contribution (qty_shares is now correct: open_qty in shares, no extra multiply)
        fut_info   = futures_map.get(can, {})
        fut_shares = _safe(fut_info.get("qty_shares", 0))
        fut_contr  = _safe(fut_info.get("contracts", 0))   # actual number of contracts
        total_qty  = eq_qty + (fut_shares if fut_shares > 0 else 0)
        # Spot
        spot = _safe(spot_map.get(can, 0))
        if spot <= 0:
            ohlc_spot = _safe(ohlc_cache.get(can, {}).get("spot"))
            if ohlc_spot > 0:
                spot = ohlc_spot
            else:
                logger.warning(f"[AdvScr] No spot for '{can}' — skipping")
                continue

        ohlc = ohlc_cache.get(can, _OHLC_EMPTY.copy())

        # Step 5 — position signal (pass FUT expiry so FUT_ROLLOVER works correctly)
        opt_pos = opt_positions.get(can)
        fut_expiry_str = fut_info.get("expiry") if fut_info else None
        pos_sig, pos_reason = _position_signal(can, spot, opt_pos, fut_expiry_str)

        # Step 6 — fresh signal (if FRESH; else carry pos_sig forward)
        corp_event = corp_events.get(can)
        has_long_fut = fut_shares > 0
        if pos_sig in ("FRESH", "FUT_ROLLOVER"):
            # FUT_ROLLOVER still needs a fresh direction signal for the action text
            final_sig, final_reason = _fresh_signal(spot, ohlc, corp_event, has_long_fut)
        elif pos_sig == "CORRECTION":
            # Correction signal: suggest the opposite hedge side
            final_sig = "SELL PE" if opt_pos and opt_pos.get("sold_ce") else "SELL CE"
            final_reason = pos_reason
        else:
            # HOLD / OPT_ROLLOVER / SQUARE_OFF — final signal still evaluates direction
            final_sig, final_reason = _fresh_signal(spot, ohlc, corp_event, has_long_fut)

        # Step 7 — option data
        two_info = two_exp_cache.get(can, {})
        near     = two_info.get("near")
        far      = two_info.get("far")

        near_cols = _enrich_one_expiry(near, spot, raw_iv, can, "n_")
        far_cols  = _enrich_one_expiry(far,  spot, raw_iv, can, "f_")

        # Unrealized P&L
        unrealized_pnl = round((spot - avg) * eq_qty, 2) if (spot and avg and eq_qty) else None

        # Existing position display strings — handles lists of multiple strikes
        def _pos_str_list(p_list):
            """Format a list of option positions as a combined string."""
            if not p_list:
                return "—"
            if isinstance(p_list, dict):
                p_list = [p_list]
            if not p_list:
                return "—"
            parts = []
            for p in p_list:
                strike = float(p.get("strike", 0) or 0)
                expiry = str(p.get("expiry", "") or "")
                qty    = abs(int(float(p.get("qty", 0) or 0)))
                # Indian format for strike
                s = str(int(strike))
                if len(s) > 3:
                    res = s[-3:]; s = s[:-3]
                    while s: res = s[-2:] + "," + res; s = s[:-2]
                else:
                    res = s
                parts.append(f"{res} exp:{expiry} qty:{qty}")
            return " | ".join(parts)

        existing_sold_ce   = _pos_str_list(opt_pos.get("sold_ce"))   if opt_pos else "—"
        existing_sold_pe   = _pos_str_list(opt_pos.get("sold_pe"))   if opt_pos else "—"
        existing_bought_ce = _pos_str_list(opt_pos.get("bought_ce")) if opt_pos else "—"
        existing_bought_pe = _pos_str_list(opt_pos.get("bought_pe")) if opt_pos else "—"
        dte = (opt_pos or {}).get("days_to_expiry", 9999)

        action_txt = _build_action_text(
            pos_sig, final_sig, can, opt_pos, near, far, final_reason
        )

        def _pct(s, ref):
            s, ref = _safe(s), _safe(ref)
            if ref > 0 and s > 0:
                return round((s - ref) / ref * 100, 2)
            return None

        row = {
            # Core
            "symbol":           base.get("symbol", can),
            "canonical_symbol": can,
            "equity_qty":       int(eq_qty),
            "fut_contracts":    int(fut_contr) if fut_contr else None,   # e.g. 1 contract
            "fut_qty_shares":   int(fut_shares) if fut_shares else None, # e.g. 550/700 shares
            "total_qty":        int(total_qty),
            "spot_price":       round(spot, 2),
            "avg_buy_price":    round(avg, 2) if avg else None,
            "unrealized_pnl":   unrealized_pnl,
            # OHLC
            "high_52w":         _nz(ohlc.get("high_52w")),
            "low_52w":          _nz(ohlc.get("low_52w")),
            "pct_52w_high":     _pct(spot, ohlc.get("high_52w")),
            "pct_52w_low":      _pct(spot, ohlc.get("low_52w")),
            "high_3m":          _nz(ohlc.get("high_3m")),
            "low_3m":           _nz(ohlc.get("low_3m")),
            "pct_3m_high":      _pct(spot, ohlc.get("high_3m")),
            "pct_3m_low":       _pct(spot, ohlc.get("low_3m")),
            "high_1m":          _nz(ohlc.get("high_1m")),
            "low_1m":           _nz(ohlc.get("low_1m")),
            "pct_1m_high":      _pct(spot, ohlc.get("high_1m")),
            "pct_1m_low":       _pct(spot, ohlc.get("low_1m")),
            # Signals
            "position_signal":  pos_sig,
            "signal_reason":    pos_reason,
            "final_signal":     final_sig,
            "suggested_action": action_txt,
            # Existing positions
            "existing_sold_ce":   existing_sold_ce,
            "existing_sold_pe":   existing_sold_pe,
            "existing_bought_ce": existing_bought_ce,
            "existing_bought_pe": existing_bought_pe,
            "days_to_open_expiry": dte if dte < 9999 else None,
            # Corp event
            "corp_event_alert": corp_event or "—",
            # Futures
            "fut_avg_entry": round(fut_info.get("avg_entry", 0), 2) if fut_info else None,
            "fut_expiry":    fut_info.get("expiry") if fut_info else None,
            "fut_open_qty":  round(fut_info.get("open_qty", 0), 2) if fut_info else None,
        }
        # Merge option chain columns
        row.update(near_cols)
        row.update(far_cols)

        results.append(row)
        logger.info(
            f"[AdvScr] ✅ {can}: spot=₹{spot} pos={pos_sig} final={final_sig} "
            f"n_CE={near_cols.get('n_ce_strike')}@{near_cols.get('n_ce_premium')} "
            f"n_PE={near_cols.get('n_pe_strike')}@{near_cols.get('n_pe_premium')}"
        )

    logger.info(f"[AdvScr] === Done: {len(results)} rows ===")
    return results

_MONTH_ABBR = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
    "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
    "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}
 
 
def _opt_compact(pos_list) -> str:
    """
    Convert a list of option position dicts to a compact display string.
    e.g.  [{"strike":2800,"expiry":"2025-05-29","qty":-50}]  →  "2,800 May25"
    Multiple strikes are joined with " | ".
    """
    if not pos_list:
        return "—"
    if isinstance(pos_list, dict):
        pos_list = [pos_list]
    parts: list[str] = []
    for p in pos_list or []:
        try:
            strike = int(float(p.get("strike", 0) or 0))
            exp = str(p.get("expiry", "") or "")
            mon = _MONTH_ABBR.get(exp[5:7], exp[5:7]) if len(exp) >= 7 else ""
            yr = exp[2:4] if len(exp) >= 4 else ""
            parts.append(f"{strike:,} {mon}{yr}")
        except Exception:
            pass
    return " | ".join(parts) if parts else "—"
 
 
def _col_label_for_group(username: str, broker: str, broker_count: dict) -> str:
    """
    Mirrors the col_label() logic in group_stock_master.py.
    If two or more users share the same broker, append a 3-letter broker tag.
    """
    br = (broker or "").lower()
    if broker_count.get(br, 0) > 1:
        return f"{username} ({(broker or '???')[:3].upper()})"
    return username
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────
 
def get_group_advanced_options_screener(group_id: int) -> List[dict]:
    """
    Advanced options screener aggregated across all members of a group.
 
    Returns one row per F&O-eligible underlying with group-aggregate columns
    PLUS per-member dynamic columns:
        {label}_eq_qty       int    — equity shares held
        {label}_fut_qty      int|None — FUT shares (+long / -short); None if no position
        {label}_sold_ce      str    — compact sold CE strikes, e.g. "2,800 May25"
        {label}_sold_pe      str    — compact sold PE strikes
        {label}_bought_ce    str    — compact bought CE strikes
        {label}_bought_pe    str    — compact bought PE strikes
 
    Column label logic mirrors group_stock_master.py — same broker-dedup rules.
    Market data (spot, OHLC, option chain, IV) is fetched once per unique
    underlying regardless of how many members hold it.
    """
    from services.engine_price_fetch import fetch_current_prices  # noqa: PLC0415
 
    # ── 1. Load group members ────────────────────────────────────────────────
    db = SessionLocal()
    try:
        members_rows = db.execute(
            text("""
                SELECT u.id, u.username, u.broker
                FROM group_members gm
                JOIN users u ON u.id = gm.user_id
                WHERE gm.group_id = :gid
                ORDER BY u.username
            """),
            {"gid": group_id}
        ).fetchall()
    finally:
        db.close()
 
    if not members_rows:
        logger.warning(f"[GrpAdv] group {group_id} has no members")
        return []
 
    # ── Column label logic (same as group_stock_master.py) ──────────────────
    broker_cnt: dict[str, int] = defaultdict(int)
    for m in members_rows:
        broker_cnt[(m.broker or "").lower()] += 1
 
    uid_label = {
        m.id: _col_label_for_group(m.username, m.broker or "", broker_cnt)
        for m in members_rows
    }
    member_labels_ordered: List[str] = [uid_label[m.id] for m in members_rows]
 
    # ── 2. Aggregate structure keyed by canonical symbol ─────────────────────
    # all_cans[can] = {
    #   symbol, lot_size, total_eq_qty,
    #   member_eq:         {label: float},
    #   member_fut:        {label: float},   ← shares (+ long, - short)
    #   member_fut_expiry: {label: str},
    #   member_sold_ce:    {label: [pos_dict, ...]},
    #   member_sold_pe:    {label: [pos_dict, ...]},
    #   member_bought_ce:  {label: [pos_dict, ...]},
    #   member_bought_pe:  {label: [pos_dict, ...]},
    # }
    all_cans: dict[str, dict] = {}
 
    def _ensure_can(can: str, symbol: str = "", lot: int = 1) -> dict:
        if can not in all_cans:
            all_cans[can] = {
                "symbol":           symbol or can,
                "lot_size":         lot,
                "total_eq_qty":     0.0,
                "member_eq":        {},
                "member_fut":       {},
                "member_fut_expiry": {},
                "member_sold_ce":   {},
                "member_sold_pe":   {},
                "member_bought_ce": {},
                "member_bought_pe": {},
            }
        else:
            # Upgrade symbol from bare canonical if we now have a real display name
            if symbol and all_cans[can]["symbol"] == can:
                all_cans[can]["symbol"] = symbol
            # Upgrade lot size if we learn a better value
            if lot > 1 and all_cans[can]["lot_size"] <= 1:
                all_cans[can]["lot_size"] = lot
        return all_cans[can]
 
    # ── 3. Per-member data loading ───────────────────────────────────────────
    for m in members_rows:
        uid = m.id
        lbl = uid_label[uid]
 
        # Equity holdings (fno_available=1 stocks)
        for h in _load_eligible_holdings(uid):
            can = str(h.get("canonical_symbol") or "").strip().upper()
            if not can:
                continue
            entry = _ensure_can(can, h.get("symbol", can), int(h.get("lot_size") or 1))
            qty = float(h.get("quantity", 0))
            entry["total_eq_qty"]    += qty
            entry["member_eq"][lbl]   = entry["member_eq"].get(lbl, 0.0) + qty
 
        # Futures exposure
        for can, finfo in _load_futures_exposure(uid).items():
            entry = _ensure_can(can, lot=finfo.get("lot_size", 1))
            qty_s = float(finfo.get("qty_shares", 0))
            entry["member_fut"][lbl]       = entry["member_fut"].get(lbl, 0.0) + qty_s
            entry["member_fut_expiry"][lbl] = finfo.get("expiry", "")
 
        # Underlyings that have open F&O positions but no equity holding
        for can in _load_all_fno_underlyings(uid):
            _ensure_can(can)
 
        # Option positions (sold_ce / sold_pe / bought_ce / bought_pe)
        for can, pos in _load_all_option_positions(uid).items():
            entry = _ensure_can(can)
            for side in ("sold_ce", "sold_pe", "bought_ce", "bought_pe"):
                pl = pos.get(side) or []
                if not pl:
                    continue
                if isinstance(pl, dict):
                    pl = [pl]
                key = f"member_{side}"
                if lbl not in entry[key]:
                    entry[key][lbl] = []
                entry[key][lbl].extend(pl)
 
    if not all_cans:
        return []
 
    logger.info(f"[GrpAdv] {len(all_cans)} unique underlyings across {len(members_rows)} members")
 
    # ── 4. Spot prices — one batch call ─────────────────────────────────────
    symbols_list = list(all_cans.keys())
    spot_map: dict[str, float] = {}
    try:
        spot_map = fetch_current_prices(symbols_list)
        logger.info(f"[GrpAdv] Spot prices: {len(spot_map)}/{len(symbols_list)}")
    except Exception as e:
        logger.error(f"[GrpAdv] Spot fetch error: {e}")
 
    # ── 5. OHLC — once per underlying ───────────────────────────────────────
    ohlc_cache: dict[str, dict] = {}
    for can in all_cans:
        ohlc_cache[can] = _fetch_ohlc_ranges(can)
        time.sleep(0.08)
 
    # ── 6. Option chain — once per underlying ───────────────────────────────
    client       = _get_5paisa_client()
    access_token = _get_access_token()
    two_exp_cache: dict[str, dict] = {}
    token_map:     dict[str, tuple] = {}
 
    # Pre-build set of confirmed F&O symbols from scrip_master_cache
    _fno_confirmed: set[str] = set()
    try:
        _db2 = SessionLocal()
        try:
            _rows = _db2.execute(text(
                "SELECT DISTINCT UPPER(symbol_root) AS sr FROM scrip_master_cache "
                "WHERE exch='N' AND exch_type='D' "
                "AND symbol_root IS NOT NULL AND symbol_root != ''"
            )).fetchall()
            _fno_confirmed = {r.sr for r in _rows if r.sr}
        finally:
            _db2.close()
    except Exception:
        pass
 
    for can in all_cans:
        if _fno_confirmed and can not in _fno_confirmed:
            logger.info(f"[GrpAdv] Skipping option chain for '{can}' — not in derivative master")
            two_exp_cache[can] = {}
            continue
        if client:
            info = _get_two_expiry_option_info(client, can)
            two_exp_cache[can] = info or {}
            if info:
                near = info.get("near") or {}
                far  = info.get("far")  or {}
                if near.get("ce_token"):
                    token_map[f"og{near['ce_token']}"] = (can, "n_call")
                if near.get("pe_token"):
                    token_map[f"og{near['pe_token']}"] = (can, "n_put")
                if far and far.get("ce_token"):
                    token_map[f"og{far['ce_token']}"] = (can, "f_call")
                if far and far.get("pe_token"):
                    token_map[f"og{far['pe_token']}"] = (can, "f_put")
        else:
            two_exp_cache[can] = {}
 
    # ── 7. Greeks WebSocket — all tokens in one connection ──────────────────
    raw_iv: dict[str, dict] = {}
    if token_map and access_token:
        ws_token_map: dict[str, tuple] = {}
        for og, (can, label_side) in token_map.items():
            side = "call" if "call" in label_side else "put"
            ws_token_map[og] = (can + "|" + label_side, side)
        iv_raw_ws = _fetch_iv_greeks_ws(access_token, ws_token_map, timeout=30)
        for og, (compound, side) in ws_token_map.items():
            can, label_side = compound.split("|", 1)
            iv_val = (iv_raw_ws.get(compound) or {}).get(f"{side}_iv")
            if iv_val is not None:
                raw_iv.setdefault(can, {})[f"{label_side}_iv"] = iv_val
 
    # ── 8. Corp events — union across all members ────────────────────────────
    corp_events: dict[str, str] = {}
    for m in members_rows:
        for can, ev in _load_corp_events(m.id).items():
            corp_events.setdefault(can, ev)
 
    # ── 9. Build result rows ─────────────────────────────────────────────────
    results: List[dict] = []
 
    for can, base in all_cans.items():
        total_eq   = base["total_eq_qty"]
        total_fut  = sum(base["member_fut"].values())
        total_qty  = total_eq + max(total_fut, 0)
        lot        = max(base["lot_size"], 1)
        lots       = int(total_qty // lot) if lot > 0 else 0
        pending    = int((lot - int(total_qty) % lot) % lot) if lot > 0 and total_qty > 0 else 0
 
        spot = _safe(spot_map.get(can, 0))
        if spot <= 0:
            spot = _safe(ohlc_cache.get(can, {}).get("spot"))
        if spot <= 0:
            logger.warning(f"[GrpAdv] No spot for '{can}' — skipping")
            continue
 
        ohlc        = ohlc_cache.get(can, _OHLC_EMPTY.copy())
        corp_event  = corp_events.get(can)
        final_sig, final_reason = _fresh_signal(spot, ohlc, corp_event, total_fut > 0)
 
        two_info  = two_exp_cache.get(can, {})
        near_cols = _enrich_one_expiry(two_info.get("near"), spot, raw_iv, can, "n_")
        far_cols  = _enrich_one_expiry(two_info.get("far"),  spot, raw_iv, can, "f_")
 
        def _pct(s, ref):
            s, ref = _safe(s), _safe(ref)
            return round((s - ref) / ref * 100, 2) if ref > 0 and s > 0 else None
 
        # ── Conflict detection ──────────────────────────────────────────────
        conflict_parts: list[str] = []
        has_sold_ce = bool(base["member_sold_ce"])
        has_sold_pe = bool(base["member_sold_pe"])
        if has_sold_ce and has_sold_pe:
            ce_m = list(base["member_sold_ce"].keys())
            pe_m = list(base["member_sold_pe"].keys())
            shared = set(ce_m) & set(pe_m)
            if shared:
                conflict_parts.append(f"⚡ Same-account straddle ({', '.join(shared)})")
            else:
                conflict_parts.append(
                    f"⚠️ Distributed straddle "
                    f"({', '.join(ce_m)} CE / {', '.join(pe_m)} PE)"
                )
        if total_fut > 0 and has_sold_ce:
            conflict_parts.append("📋 Covered call across accounts")
 
        # ── Lot distribution string ─────────────────────────────────────────
        lot_dist_parts: list[str] = []
        for lbl in member_labels_ordered:
            eq  = int(base["member_eq"].get(lbl, 0))
            fut = int(base["member_fut"].get(lbl, 0))
            if eq or fut:
                fut_str = (f" {'+' if fut > 0 else ''}{fut:,}F") if fut else ""
                lot_dist_parts.append(f"{lbl}: {eq:,}{fut_str}")
        lot_dist = " / ".join(lot_dist_parts) if lot_dist_parts else "—"
 
        row: dict = {
            # ── Core ──────────────────────────────────────────────────────
            "symbol":           base["symbol"],
            "canonical_symbol": can,
            "total_eq_qty":     int(total_eq),
            "total_fut_shares": int(total_fut) if total_fut else None,
            "total_qty":        int(total_qty),
            "lot_size":         lot,
            "lots":             lots,
            "pending_qty":      pending,
            "lot_distribution": lot_dist,
            "spot_price":       round(spot, 2),
            # ── Signals ────────────────────────────────────────────────────
            "final_signal":     final_sig,
            "signal_reason":    final_reason,
            # ── OHLC ───────────────────────────────────────────────────────
            "high_52w":         _nz(ohlc.get("high_52w")),
            "low_52w":          _nz(ohlc.get("low_52w")),
            "pct_52w_high":     _pct(spot, ohlc.get("high_52w")),
            "pct_52w_low":      _pct(spot, ohlc.get("low_52w")),
            "high_3m":          _nz(ohlc.get("high_3m")),
            "low_3m":           _nz(ohlc.get("low_3m")),
            "pct_3m_high":      _pct(spot, ohlc.get("high_3m")),
            "pct_3m_low":       _pct(spot, ohlc.get("low_3m")),
            "high_1m":          _nz(ohlc.get("high_1m")),
            "low_1m":           _nz(ohlc.get("low_1m")),
            "pct_1m_high":      _pct(spot, ohlc.get("high_1m")),
            "pct_1m_low":       _pct(spot, ohlc.get("low_1m")),
            # ── Alerts ─────────────────────────────────────────────────────
            "corp_event_alert": corp_event or "—",
            "conflict_alert":   " | ".join(conflict_parts) if conflict_parts else "—",
        }
 
        # ── Per-member dynamic columns ────────────────────────────────────
        for lbl in member_labels_ordered:
            row[f"{lbl}_eq_qty"]    = int(base["member_eq"].get(lbl, 0))
            fut_v = base["member_fut"].get(lbl, 0)
            row[f"{lbl}_fut_qty"]   = int(fut_v) if fut_v else None
            row[f"{lbl}_sold_ce"]   = _opt_compact(base["member_sold_ce"].get(lbl))
            row[f"{lbl}_sold_pe"]   = _opt_compact(base["member_sold_pe"].get(lbl))
            row[f"{lbl}_bought_ce"] = _opt_compact(base["member_bought_ce"].get(lbl))
            row[f"{lbl}_bought_pe"] = _opt_compact(base["member_bought_pe"].get(lbl))
 
        row.update(near_cols)
        row.update(far_cols)
        results.append(row)
 
        logger.info(
            f"[GrpAdv] ✅ {can}: spot=₹{spot} sig={final_sig} "
            f"total_qty={int(total_qty)} lots={lots} pending={pending}"
        )
 
    logger.info(f"[GrpAdv] === Done: {len(results)} rows for group {group_id} ===")
    return results