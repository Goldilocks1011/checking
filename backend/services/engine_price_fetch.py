"""
engine_price_fetch.py  — v7  (DB-only, no CSV path)
=====================================================
KEY CHANGES vs v6:
  ① SCRIP_MASTER_PATH completely removed.
  ② _load_scrip_master(), _build_indexes(), reload_scrip_master() removed.
  ③ All CSV index globals (_eq_name_index, _eq_data_index, etc.) removed.
  ④ _find_eq_scrip_code() is now DB-only.
  ⑤ _get_scrip_data_for_code() is now DB-only.
  ⑥ get_fno_info() is now DB-only (no CSV fallback step).
  ⑦ debug_lookup() queries scrip_master_cache directly.
  scrip_master_cache table (populated via Upload & Manage) is the single source.
"""
from __future__ import annotations

import re
import time
import pandas as pd
from datetime import datetime
import yfinance as yfinance

import logging
logger = logging.getLogger(__name__)

# ── Module-level caches ────────────────────────────────────────────────────────
_client = None

_MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
           "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}


def _normalise(symbol: str) -> str:
    s = str(symbol).strip().upper()
    for suffix in ("_EQ", "_NSE", "_BSE"):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    return s


def reload_scrip_master() -> bool:
    """No-op — DB (scrip_master_cache) is always the source."""
    return True


# ─────────────────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────────────────

def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from auth_manager import get_client
        _client = get_client()
        return _client
    except Exception as e:
        logger.error(f"[5paisa auth error] {e}", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# F&O AVAILABILITY + LOT SIZE  — DB-only
# ─────────────────────────────────────────────────────────────────────────────

def get_fno_info(symbol: str) -> tuple[bool, int]:
    """
    Returns (has_fno, lot_size).
    DB-only: queries scrip_master_cache.
    Returns (False, 0) if not found — user should upload ScripMaster.
    """
    s = _normalise(symbol)
    try:
        from services.scrip_master_db import is_db_populated, query_fno_info
        if is_db_populated():
            fno, lot = query_fno_info(s)
            if fno and lot > 1:
                return True, lot
    except Exception as e:
        logger.error(f"[get_fno_info] DB error: {e}", exc_info=True)
    65(
        f"[ScripMaster] WARNING: cannot determine F&O info for '{symbol}'. "
        f"Upload ScripMaster via Upload & Manage tab. Returning (False, 0)."
    )
    return False, 0


# ─────────────────────────────────────────────────────────────────────────────
# SCRIP CODE LOOKUP — EQUITY  (DB-only)
# ─────────────────────────────────────────────────────────────────────────────

def _find_eq_scrip_code(symbol: str) -> str | None:
    sym = symbol.strip().upper()
    try:
        from services.scrip_master_db import is_db_populated
        from database import SessionLocal
        from sqlalchemy import text
        if is_db_populated():
            db = SessionLocal()
            try:
                row = db.execute(text("""
                    SELECT scrip_code FROM scrip_master_cache
                    WHERE (UPPER(symbol_root)=:sym OR UPPER(name)=:sym)
                      AND exch='N' AND exch_type='C'
                      AND scrip_code IS NOT NULL AND scrip_code != ''
                    ORDER BY CASE WHEN UPPER(symbol_root)=:sym THEN 0 ELSE 1 END
                    LIMIT 1
                """), {"sym": sym}).first()
                if row and row.scrip_code:
                    return str(row.scrip_code).strip()
            finally:
                db.close()
    except Exception as e:
        logger.error(f"[_find_eq_scrip_code] DB error: {e}", exc_info=True)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# SCRIP CODE LOOKUP — F&O  (DB-only)
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_NUM_TO_ABBR = {
    1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
    7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC",
}

def _find_fno_scrip_code_db(
    underlying: str,
    instrument_type: str,
    expiry_date: str,
    strike: float = 0,
    exchange: str = "N",
) -> str | None:
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        itype     = {"FUT":"XX","CE":"CE","PE":"PE"}.get(instrument_type.upper(), instrument_type.upper())
        exp_dt    = datetime.strptime(expiry_date[:10], "%Y-%m-%d")
        exch_code = "N" if exchange.upper() in ("NSE","N") else "B"
        yr_str    = str(exp_dt.year)
        mon_abbr  = _MONTH_NUM_TO_ABBR[exp_dt.month]
        mon_2d    = f"{exp_dt.month:02d}"

        row = db.execute(text("""
            SELECT scrip_code, scrip_data FROM scrip_master_cache
            WHERE exch=:exch AND exch_type='D' AND scrip_type=:stype
              AND (UPPER(symbol_root)=:sym OR UPPER(name)=:sym)
              AND ABS(strike_rate - :strike) < 1
              AND expiry LIKE :yr_pat
              AND (UPPER(expiry) LIKE :mon_abbr_pat OR expiry LIKE :mon_2d_pat)
              AND scrip_code IS NOT NULL AND scrip_code != ''
            ORDER BY expiry DESC LIMIT 1
        """), {
            "exch": exch_code, "stype": itype,
            "sym": underlying.upper(), "strike": float(strike),
            "yr_pat": f"%{yr_str}%",
            "mon_abbr_pat": f"%{mon_abbr}%",
            "mon_2d_pat": f"%-{mon_2d}-%",
        }).first()

        return str(row.scrip_code).strip() if row and row.scrip_code else None
    except Exception as e:
        logger.error(f"[FNO ScripDB] lookup error {underlying} {instrument_type} {expiry_date}: {e}", exc_info=True)

        return None
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# SCRIP DATA helper  (DB-only)
# ─────────────────────────────────────────────────────────────────────────────

def _get_scrip_data_for_code(scrip_code: str) -> str:
    code = str(scrip_code).strip()
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(text("""
                SELECT scrip_data FROM scrip_master_cache
                WHERE scrip_code=:code AND scrip_data IS NOT NULL
                  AND scrip_data != '' LIMIT 1
            """), {"code": code}).first()
            if row and row.scrip_data:
                return str(row.scrip_data).strip()
        finally:
            db.close()
    except Exception:
        pass
    return code


# ─────────────────────────────────────────────────────────────────────────────
# MARKET FEED
# ─────────────────────────────────────────────────────────────────────────────

def _batch_market_feed(req_list: list[dict]) -> dict[str, dict]:
    client = _get_client()
    if client is None:
        logger.warning("[5paisa] No client — check auth_manager / token")
        return {}

    results: dict[str, dict] = {}
    BATCH = 50
    for i in range(0, len(req_list), BATCH):
        batch = req_list[i: i + BATCH]
        try:
            resp = client.fetch_market_feed_scrip(batch)
            items: list = []
            if isinstance(resp, list): items = resp
            elif isinstance(resp, dict): items = resp.get("Data", resp.get("data", []))
            elif hasattr(resp, 'status_code') and resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data.get("Data", data.get("data", [])) if isinstance(data, dict) else data
                except Exception: pass

            for item in items:
                if not isinstance(item, dict): continue
                sd  = str(item.get("ScripData", item.get("Scripdata", "")))
                sc  = str(item.get("ScripCode", ""))
                ltp = float(item.get("LastRate", item.get("LTP", 0)) or 0)
                prev = float(item.get("PreviousClose", item.get("PrevClose",
                              item.get("CloseRate", 0))) or 0)
                if sd: results[sd] = {"ltp": ltp, "prev": prev}
                if sc: results[sc] = {"ltp": ltp, "prev": prev}
        except Exception as e:
            logger.error(f"[5paisa market feed] batch {i//BATCH} error: {e}", exc_info=True)

        time.sleep(0.25)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL CANONICALISER
# ─────────────────────────────────────────────────────────────────────────────

def _canonical(sym: str) -> str:
    try:
        from services.symbol_resolver import get_canonical
        return get_canonical(sym)
    except Exception:
        return _normalise(sym)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    result:    dict[str, float] = {}
    req_list:  list[dict]       = []
    sd_to_sym: dict[str, str]   = {}

    for sym in symbols:
        can  = _canonical(sym)
        code = _find_eq_scrip_code(can)
        if code is None:
            code = _find_eq_scrip_code(sym)
        if code:
            sd = _get_scrip_data_for_code(code)
            sd_to_sym[sd]   = sym
            sd_to_sym[code] = sym
            req_list.append({"Exch": "N", "ExchType": "C", "ScripData": sd})
        else:
            logger.info(f"[ScripMaster EQ] Not found: {sym} (can={can})")

    if req_list:
        feed = _batch_market_feed(req_list)
        for sd, data in feed.items():
            orig = sd_to_sym.get(sd) or sd_to_sym.get(sd.split("_")[0], sd)
            ltp  = data.get("ltp", 0)
            if ltp > 0:
                result[orig] = ltp

    missing = [s for s in symbols if s not in result]
    if missing:
        result.update(_yfinance_fallback(missing))
    return result


def fetch_prices_with_change(symbols: list[str]) -> dict[str, dict]:
    result:    dict[str, dict] = {}
    req_list:  list[dict]      = []
    sd_to_sym: dict[str, str]  = {}

    for sym in symbols:
        can  = _canonical(sym)
        code = _find_eq_scrip_code(can) or _find_eq_scrip_code(sym)
        if code:
            sd = _get_scrip_data_for_code(code)
            sd_to_sym[sd]   = sym
            sd_to_sym[code] = sym
            req_list.append({"Exch": "N", "ExchType": "C", "ScripData": sd})
        else:
            logger.info(f"[ScripMaster EQ] Not found for %change: {sym}")

    if req_list:
        feed = _batch_market_feed(req_list)
        for sd, data in feed.items():
            orig = sd_to_sym.get(sd) or sd_to_sym.get(sd.split("_")[0], sd)
            ltp  = data.get("ltp",  0)
            prev = data.get("prev", 0)
            if ltp > 0:
                pct = round((ltp - prev) / prev * 100, 2) if prev > 0 else 0.0
                result[orig] = {"price": ltp, "pct_change": pct}

    missing = [s for s in symbols if s not in result]
    if missing:
        result.update(_yfinance_fallback_with_change(missing))
    return result


def fetch_fno_prices(op_df: pd.DataFrame) -> dict[tuple, float]:
    result:     dict[tuple, float]     = {}
    req_list:   list[dict]             = []
    sd_to_keys: dict[str, list[tuple]] = {}

    for _, row in op_df.iterrows():
        und    = str(row["underlying"])
        itype  = str(row["instrument_type"]).upper()
        expiry = str(row.get("expiry_date", "") or "")[:10]
        strike = float(row.get("strike_price", 0) or 0)

        can_und = _canonical(und)
        can_key = (can_und,             itype, expiry, strike)
        raw_key = (und.strip().upper(), itype, expiry, strike)

        raw_exch = str(row.get("exchange", "") or "").strip().upper()
        feed_exch_code = "B" if raw_exch in ("BSE","B") else "N"

        code = _find_fno_scrip_code_db(can_und, itype, expiry, strike, exchange=feed_exch_code)
        if code:
            sd = _get_scrip_data_for_code(code)
            for lookup in (sd, code):
                entry = sd_to_keys.setdefault(lookup, [])
                if can_key not in entry: entry.append(can_key)
                if raw_key != can_key and raw_key not in entry: entry.append(raw_key)
            req_list.append({"Exch": feed_exch_code, "ExchType": "D", "ScripData": sd})
        else:
            logger.info(f"[ScripMaster FNO] Not found: {und} {itype} {strike} {expiry}")

    if req_list:
        feed = _batch_market_feed(req_list)
        for sd, data in feed.items():
            ltp = data.get("ltp", 0)
            if ltp <= 0: continue
            keys = sd_to_keys.get(sd) or sd_to_keys.get(sd.split("_")[0]) or []
            for key in keys:
                result[key] = ltp

    for _, row in op_df.iterrows():
        und    = str(row["underlying"])
        itype  = str(row["instrument_type"]).upper()
        expiry = str(row.get("expiry_date", ""))[:10]
        strike = float(row.get("strike_price", 0) or 0)
        can_key = (_canonical(und),           itype, expiry, strike)
        raw_key = (und.strip().upper(), itype, expiry, strike)
        if can_key not in result and raw_key not in result and itype == "FUT":
            spot = fetch_current_prices([und])
            if und in spot:
                result[can_key] = spot[und]
                result[raw_key] = spot[und]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# YFINANCE FALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

def _yfinance_fallback(symbols: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    try:
        import yfinance as yf
    except ImportError:
        return result
    for sym in symbols:
        for suffix in (".NS", ".BO", ""):
            try:
                t   = yf.Ticker(sym.upper() + suffix)
                ltp = float(getattr(t.fast_info, "last_price", 0) or 0)
                if ltp > 0:
                    result[sym] = ltp
                    break
            except Exception:
                continue
    return result


def _yfinance_fallback_with_change(symbols: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    try:
        import yfinance as yf
    except ImportError:
        return result
    for sym in symbols:
        try:
            t    = yf.Ticker(sym.upper() + ".NS")
            info = t.fast_info
            ltp  = float(getattr(info, "last_price",     0) or 0)
            prev = float(getattr(info, "previous_close", 0) or 0)
            if ltp > 0:
                pct = round((ltp - prev) / prev * 100, 2) if prev > 0 else 0.0
                result[sym] = {"price": ltp, "pct_change": pct}
        except Exception:
            pass
    return result


# ─────────────────────────────────────────────────────────────────────────────
# DEBUG HELPER  (DB-only)
# ─────────────────────────────────────────────────────────────────────────────

def debug_lookup(symbol: str) -> None:
    from database import SessionLocal
    from sqlalchemy import text
    sym = symbol.strip().upper()
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT exch, exch_type, scrip_code, name, symbol_root,
                   scrip_type, strike_rate, expiry, scrip_data, series
            FROM scrip_master_cache
            WHERE UPPER(name) LIKE :sym OR UPPER(symbol_root) LIKE :sym
            LIMIT 20
        """), {"sym": f"%{sym}%"}).fetchall()
        logger.info(f"\n=== scrip_master_cache rows matching '{symbol}' ===")
        for r in rows:
            logger.info(dict(r._mapping))
    finally:
        db.close()