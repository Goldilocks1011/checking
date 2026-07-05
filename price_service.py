"""
price_service.py — v4 (DB-only, no CSV)
=========================================
KEY CHANGES vs v3:
  ① SCRIP_MASTER_PATH and all CSV-related globals removed.
  ② _load_scrip_master(), _find_eq_scrip_code() (CSV), _symroot_index etc. removed.
  ③ fetch_current_prices() uses _find_scrip_code_from_db() only.
  ④ _get_scrip_data_for_code() queries DB only.
  scrip_master_cache table is the single source of truth.
"""

import time
import threading
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from database import SessionLocal
from sqlalchemy import text
import logging
logger = logging.getLogger(__name__)
# Thread-safe lock for the period cache
_period_cache_lock = threading.Lock()

_client = None
# In-memory cache for period prices: key = scrip_code, value = dict
_period_cache = {}

__all__ = ['_get_client', '_find_scrip_code_from_db', '_get_scrip_data_for_code']
# ─────────────────────────────────────────────────────────────────────────────
# DB-based ScripCode lookup
# ─────────────────────────────────────────────────────────────────────────────

def _find_scrip_code_from_db(symbol: str) -> tuple[str | None, str | None]:
    sym = str(symbol).strip().upper()
    if not sym:
        return None, None
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT scrip_code, scrip_data FROM scrip_master_cache
                WHERE (UPPER(symbol_root) = :sym OR UPPER(name) = :sym)
                  AND exch = 'N' AND exch_type = 'C'
                  AND scrip_code IS NOT NULL AND scrip_code != ''
                ORDER BY CASE WHEN UPPER(symbol_root) = :sym THEN 0 ELSE 1 END
                LIMIT 1
            """),
            {"sym": sym}
        ).first()
        if row and row.scrip_code:
            return str(row.scrip_code).strip(), str(row.scrip_data or "").strip()
        return None, None
    except Exception as e:
        logger.error(f"[PriceService] DB ScripCode lookup error for {symbol}: {e}", exc_info=True)
        return None, None
    finally:
        db.close()


def _get_scrip_data_for_code(scrip_code: str) -> str:
    """Look up scrip_data for a numeric code from DB."""
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT scrip_data FROM scrip_master_cache
                WHERE scrip_code = :code AND scrip_data IS NOT NULL AND scrip_data != ''
                LIMIT 1
            """),
            {"code": str(scrip_code).strip()}
        ).first()
        return str(row.scrip_data).strip() if row and row.scrip_data else str(scrip_code)
    except Exception:
        return str(scrip_code)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Canonical resolution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_canonical(symbol: str) -> str:
    sym = str(symbol).strip().upper()
    if not sym:
        return symbol

    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT sm.canonical_symbol
                FROM user_stock_symbol_mapping usm
                JOIN stock_master_mapping sm ON sm.isin = usm.isin
                WHERE UPPER(usm.symbol) = :sym
                  AND sm.canonical_symbol IS NOT NULL AND sm.canonical_symbol != ''
                LIMIT 1
            """),
            {"sym": sym}
        ).fetchone()
        if row and row.canonical_symbol:
            return row.canonical_symbol.strip().upper()

        row2 = db.execute(
            text("""
                SELECT canonical_symbol FROM stock_master_mapping
                WHERE UPPER(canonical_symbol) = :sym
                LIMIT 1
            """),
            {"sym": sym}
        ).fetchone()
        if row2 and row2.canonical_symbol:
            return row2.canonical_symbol.strip().upper()
    except Exception as e:
        logger.error(f"[PriceService] _resolve_canonical DB error for {symbol}: {e}", exc_info=True)
    finally:
        db.close()

    try:
        from services.isin_resolver import resolve_isin, isin_to_canonical
        isin = resolve_isin(sym)
        if isin:
            can = isin_to_canonical(isin)
            if can:
                return can.upper()
    except Exception:
        pass
    return sym


# ─────────────────────────────────────────────────────────────────────────────
# 5paisa client (lazy init)
# ─────────────────────────────────────────────────────────────────────────────

def _get_client(force_refresh=False):
    global _client
    if force_refresh:
        _client = None
    if _client is not None:
        return _client
    from auth_manager import get_client
    _client = get_client()
    logger.info("[PriceService] 5paisa client created")
    return _client


# ─────────────────────────────────────────────────────────────────────────────
# PERIOD HIGH/LOW FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def fetch_period_high_low(scrip_code: str, days: int,
                          exchange: str = 'N') -> tuple[float, float]:
    """
    Fetch (High, Low) for given scrip_code over last `days` calendar days.
    Uses 5paisa historical_data with 1d interval.
    """
    client = _get_client()
    if client is None:
        return 0.0, 0.0

    end_date = datetime.now().strftime('%Y-%m-%d')

    def _try_fetch(exch: str, day_window: int) -> tuple[float, float]:
        start_date = (datetime.now() - timedelta(days=day_window + 10)).strftime('%Y-%m-%d')
        for attempt in range(3):
            try:
                df = client.historical_data(exch, 'C', int(scrip_code), '1d', start_date, end_date)
                if df is None or (hasattr(df, 'empty') and df.empty):
                    if attempt < 2:
                        time.sleep(1.0)
                        continue
                    return 0.0, 0.0
                if not hasattr(df, 'columns') or 'High' not in df.columns or 'Low' not in df.columns:
                    return 0.0, 0.0
                if 'Datetime' in df.columns:
                    df['Datetime'] = pd.to_datetime(df['Datetime'], errors='coerce')
                    df = df.sort_values('Datetime')
                    cutoff = datetime.now() - timedelta(days=day_window)
                    df = df[df['Datetime'] >= cutoff]
                if df.empty:
                    return 0.0, 0.0
                import math
                high = float(df['High'].max())
                low  = float(df['Low'].min())
                if math.isnan(high) or math.isnan(low) or high <= 0:
                    return 0.0, 0.0
                return high, low
            except Exception as e:
                logger.error(f"[PriceService] Period fetch attempt {attempt+1} error for "
                      f"scrip {scrip_code}, {day_window}d, exch={exch}: {e}", exc_info=True)
                if attempt < 2:
                    time.sleep(1.0)
        return 0.0, 0.0

    high, low = _try_fetch(exchange, days)
    if high > 0:
        return high, low
    if exchange == 'N':
        high, low = _try_fetch('B', days)
        if high > 0:
            return high, low
    if days >= 365:
        high, low = _try_fetch('N', 300)
        if high > 0:
            return high, low
        high, low = _try_fetch('B', 300)
        if high > 0:
            return high, low
    return 0.0, 0.0


def get_period_prices(scrip_code: str, symbol_name: str = "") -> dict:
    """
    Returns dict with 1M, 3M, 6M, 52W high/low.
    Caches results per scrip_code. Fetches 4 periods concurrently.
    """
    with _period_cache_lock:
        if scrip_code in _period_cache:
            return _period_cache[scrip_code]

    logger.info(f"[PriceService] Fetching period prices for scrip {scrip_code} ({symbol_name})…")

    period_specs = [
        ("1m",  30),
        ("3m",  90),
        ("6m",  180),
        ("52w", 365),
    ]

    period_results: dict[str, tuple[float, float]] = {}

    def _fetch_period(label: str, days: int) -> tuple[str, float, float]:
        h, l = fetch_period_high_low(scrip_code, days)
        return label, h, l

    try:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ohlc") as executor:
            futures = {executor.submit(_fetch_period, lbl, d): lbl for lbl, d in period_specs}
            for future in as_completed(futures, timeout=30):
                try:
                    lbl, h, l = future.result()
                    period_results[lbl] = (h, l)
                except Exception as e:
                    lbl = futures[future]
                    logger.error(f"[PriceService] Period fetch error {lbl} scrip={scrip_code}: {e}", exc_info=True)
                    period_results[lbl] = (0.0, 0.0)
    except Exception as e:
        logger.error(f"[PriceService] Parallel period fetch failed ({e}), falling back to sequential", exc_info=True)
        for lbl, days in period_specs:
            if lbl not in period_results:
                period_results[lbl] = fetch_period_high_low(scrip_code, days)

    result = {
        '1m_high':  period_results.get("1m",  (0.0, 0.0))[0],
        '1m_low':   period_results.get("1m",  (0.0, 0.0))[1],
        '3m_high':  period_results.get("3m",  (0.0, 0.0))[0],
        '3m_low':   period_results.get("3m",  (0.0, 0.0))[1],
        '6m_high':  period_results.get("6m",  (0.0, 0.0))[0],
        '6m_low':   period_results.get("6m",  (0.0, 0.0))[1],
        '52w_high': period_results.get("52w", (0.0, 0.0))[0],
        '52w_low':  period_results.get("52w", (0.0, 0.0))[1],
    }

    with _period_cache_lock:
        _period_cache[scrip_code] = result

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Live market feed
# ─────────────────────────────────────────────────────────────────────────────

def _batch_market_feed(req_list: list, original_symbols: set) -> dict[str, float]:
    client = _get_client()
    results = {}
    BATCH = 50
    for i in range(0, len(req_list), BATCH):
        batch = req_list[i: i + BATCH]
        try:
            resp = client.fetch_market_feed_scrip(batch)
            items = []
            if isinstance(resp, list):
                items = resp
            elif isinstance(resp, dict):
                items = resp.get("Data", resp.get("data", []))
            logger.info(f"[PriceService] batch {i // BATCH}: {len(items)} items received")
            for item in items:
                if not isinstance(item, dict):
                    continue
                ltp = float(item.get("LastRate", 0))
                if ltp <= 0:
                    continue
                sd = str(item.get("ScripData", "")).strip()
                if sd and sd in original_symbols:
                    results[sd] = ltp
                    continue
                sym = str(item.get("Symbol", "")).strip()
                if sym and sym in original_symbols:
                    results[sym] = ltp
        except Exception as e:
            logger.error(f"[PriceService] market feed error: {e}", exc_info=True)
        time.sleep(0.35)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PUBLIC FUNCTION — fetch live spot prices
# ─────────────────────────────────────────────────────────────────────────────

def fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    """DB-only scrip lookup; no CSV."""
    req_list = []
    sd_to_sym = {}
    original_symbols_set = set(symbols)

    for sym in symbols:
        canonical = _resolve_canonical(sym)
        scrip_code = None
        scrip_data = None

        for lookup_sym in ([canonical] if canonical != sym else []) + [sym]:
            scrip_code, scrip_data = _find_scrip_code_from_db(lookup_sym)
            if scrip_code:
                break

        if scrip_code:
            sd = scrip_data or _get_scrip_data_for_code(scrip_code)
            sd_to_sym[sd] = sym
            sd_to_sym[canonical] = sym
            original_symbols_set.add(canonical)
            original_symbols_set.add(sym)
            req_list.append({"Exch": "N", "ExchType": "C", "ScripData": sd})
        else:
            logger.warning(f"[PriceService] ScripCode not found for '{sym}' (canonical: '{canonical}')")

    if not req_list:
        return {}

    feed = _batch_market_feed(req_list, original_symbols_set)
    result = {}
    for key, ltp in feed.items():
        orig = sd_to_sym.get(key)
        if not orig and key in symbols:
            orig = key
        if orig:
            result[orig] = ltp
    return result