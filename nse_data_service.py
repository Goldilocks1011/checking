# # backend/services/nse_data_service.py
# """
# NSE Data Service — v2
# ======================
# All symbol/ISIN resolution goes through NSE website only (no ScripMaster CSV).

# Key improvements vs v1:
#   - Single persistent session reused for all calls (not recreated per symbol)
#   - Smart broker abbreviation expansion before NSE search
#   - Multi-strategy resolution: exact symbol → cleaned → search by name → expanded name
#   - Retry with exponential backoff on 429/503
#   - Quote API returns symbol + ISIN + company name in one call
#   - F&O derivative master cached for 6 hours, loaded once
# """
# from __future__ import annotations

# import re
# import time
# import logging
# from datetime import datetime
# from typing import Optional
# import requests

# logger = logging.getLogger(__name__)

# # ─────────────────────────────────────────────────────────────────────────────
# # NSE session — ONE session reused across all calls in the process lifetime
# # ─────────────────────────────────────────────────────────────────────────────

# NSE_HEADERS = {
#     "User-Agent": (
#         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
#         "AppleWebKit/537.36 (KHTML, like Gecko) "
#         "Chrome/124.0.0.0 Safari/537.36"
#     ),
#     "Accept": "application/json, text/plain, */*",
#     "Accept-Language": "en-US,en;q=0.9",
#     "Referer": "https://www.nseindia.com/",
#     "X-Requested-With": "XMLHttpRequest",
# }

# _nse_session: Optional[requests.Session] = None
# _nse_session_born: float = 0.0
# _SESSION_MAX_AGE = 1800   # recreate session every 30 minutes


# def _get_session(force_new: bool = False) -> requests.Session:
#     """Return a warm NSE session, recreating it if stale."""
#     global _nse_session, _nse_session_born
#     now = time.time()
#     if force_new or _nse_session is None or (now - _nse_session_born) > _SESSION_MAX_AGE:
#         s = requests.Session()
#         s.headers.update(NSE_HEADERS)
#         try:
#             s.get("https://www.nseindia.com/", timeout=12)
#             time.sleep(0.6)
#         except Exception:
#             pass
#         _nse_session = s
#         _nse_session_born = now
#     return _nse_session


# def _nse_get(url: str, retries: int = 3, pause: float = 1.0) -> Optional[dict]:
#     """
#     GET an NSE API URL, retrying on rate-limit / server errors.
#     Returns parsed JSON dict/list or None.
#     """
#     for attempt in range(retries):
#         try:
#             sess = _get_session()
#             resp = sess.get(url, timeout=12)
#             if resp.status_code == 200:
#                 return resp.json()
#             if resp.status_code in (429, 503, 403):
#                 # Session may have gone stale — recreate and retry
#                 logger.warning(f"[NSE] HTTP {resp.status_code} for {url}, recreating session")
#                 _get_session(force_new=True)
#                 time.sleep(pause * (attempt + 1))
#                 continue
#             logger.debug(f"[NSE] HTTP {resp.status_code} for {url}")
#             return None
#         except requests.exceptions.Timeout:
#             logger.warning(f"[NSE] Timeout on attempt {attempt+1}: {url}")
#             time.sleep(pause)
#         except Exception as e:
#             logger.error(f"[NSE] Error: {e} for {url}")
#             time.sleep(pause)
#     return None


# # ─────────────────────────────────────────────────────────────────────────────
# # Broker abbreviation expansion table
# # Covers truncated names that 5paisa / IIFL print in their trade files
# # ─────────────────────────────────────────────────────────────────────────────

# _ABBREV: dict[str, str] = {
#     # Punctuation / common shortenings
#     r"\bHldg\.?\b":     "Holdings",
#     r"\bHldgs\.?\b":    "Holdings",
#     r"\bInd\.?\b":      "Industries",
#     r"\bIndl\.?\b":     "Industries",
#     r"\bInds\.?\b":     "Industries",
#     r"\bLtd\.?\b":      "Limited",
#     r"\bPvt\.?\b":      "Private",
#     r"\bMfg\.?\b":      "Manufacturing",
#     r"\bMfrs\.?\b":     "Manufacturers",
#     r"\bIntl\.?\b":     "International",
#     r"\bNatl\.?\b":     "National",
#     r"\bNationlBak\b":  "National Bank",
#     r"\bNtn\.?\b":      "National",
#     r"\bSer\.?\b":      "Services",
#     r"\bServ\.?\b":     "Services",
#     r"\bFin\.?\b":      "Finance",
#     r"\bEngg\.?\b":     "Engineering",
#     r"\bEntrp\.?\b":    "Enterprises",
#     r"\bEntpr\.?\b":    "Enterprises",
#     r"\bInfra\.?\b":    "Infrastructure",
#     r"\bInv\.?\b":      "Investment",
#     r"\bInvt\.?\b":     "Investment",
#     r"\b&\s+Inv\.?\b":  "and Investment",
#     r"\b&\s+Inve\b":    "and Investment",
#     # State abbreviations
#     r"\bMaha\.?\b":     "Maharashtra",
#     r"\bScooters\b":    "Scooters",   # keep as-is but try full name too
#     r"\bPunj\.?\b":     "Punjab",
#     r"\bRaj\.?\b":      "Rajasthan",
#     r"\bGuj\.?\b":      "Gujarat",
#     r"\bAndh\.?\b":     "Andhra",
#     r"\bKar\.?\b":      "Karnataka",
#     # Specific known broker display names → NSE ticker
#     # These are direct overrides: broker_display_name_upper → NSE_ticker
# }

# # Direct override table: broker abbreviated display name (upper) → NSE canonical ticker
# # Add more as you encounter them; these bypass all NSE API calls.
# _DIRECT_MAP: dict[str, str] = {
#     "BHARATIWIN":       "BHARTIARTL",   # Bharti Airtel - Win? Actually this is a different class
#     "MAHA. SCOOTERS":   "MAHASCOOTER",
#     "BAJAJ HLDG. & INV.": "BAJAJHLDNG",
#     "BAJAJ HLDG. & INV": "BAJAJHLDNG",
#     "BAJAJ HLDG & INV": "BAJAJHLDNG",
#     "PUNJ. NATIONLBAK": "PNB",
#     "SBI":              "SBIN",
#     "LIC":              "LICI",
#     "NEXUS REIT":       "NEXUSSELECT",
#     "ELLENBARRIE INDL.":"ELLENBARRIE",
#     "ELLENBARRIE IND":  "ELLENBARRIE",
#     "IIFCBOND16":       "",   # bond — no NSE equity listing, leave unmatched
#     "IDFCBOND12":       "",   # bond — no NSE equity listing, leave unmatched
# }


# def _expand_abbrev(name: str) -> str:
#     """Expand broker abbreviations in a company name."""
#     result = name
#     for pat, replacement in _ABBREV.items():
#         result = re.sub(pat, replacement, result, flags=re.IGNORECASE)
#     return result.strip()


# def _clean_variants(raw: str) -> list[str]:
#     """
#     Generate search variants for a raw broker symbol / company name.
#     Returns a deduplicated list, most-specific first.
#     """
#     raw = raw.strip()
#     variants: list[str] = []

#     # 1. Cleaned: remove trailing dots, extra spaces
#     clean = re.sub(r'\.+$', '', raw).strip()
#     variants.append(clean)

#     # 2. Strip common legal suffixes for tighter search
#     stripped = re.sub(
#         r'\s+(Ltd\.?|Limited|Pvt\.?|Private|Corp\.?|Industries|Holdings?|'
#         r'Finance|Financial|Bank|Intl\.?|Services|Serv\.?|Ind\.?|Indl\.?)$',
#         '', clean, flags=re.IGNORECASE
#     ).strip()
#     if stripped and stripped != clean:
#         variants.append(stripped)

#     # 3. Abbreviation-expanded version
#     expanded = _expand_abbrev(raw)
#     if expanded not in variants:
#         variants.append(expanded)
#     expanded_stripped = re.sub(
#         r'\s+(Ltd\.?|Limited|Pvt\.?|Private|Corp\.?|Industries|Holdings?|'
#         r'Finance|Financial|Bank|Intl\.?|Services|Serv\.?|Ind\.?|Indl\.?)$',
#         '', expanded, flags=re.IGNORECASE
#     ).strip()
#     if expanded_stripped and expanded_stripped not in variants:
#         variants.append(expanded_stripped)

#     # 4. Replace '&' → 'and' and vice versa
#     for v in list(variants):
#         if '&' in v:
#             variants.append(v.replace('&', 'and'))
#         if ' and ' in v.lower():
#             variants.append(re.sub(r'\band\b', '&', v, flags=re.IGNORECASE))

#     # Deduplicate preserving order, skip empties
#     seen: set[str] = set()
#     result: list[str] = []
#     for v in variants:
#         key = v.upper()
#         if key and key not in seen:
#             seen.add(key)
#             result.append(v)
#     return result


# # ─────────────────────────────────────────────────────────────────────────────
# # Core resolution functions
# # ─────────────────────────────────────────────────────────────────────────────

# def fetch_quote_full(symbol: str) -> dict:
#     """
#     Fetch NSE quote for a symbol.
#     Returns {"symbol": str, "isin": str, "company_name": str} or empty dict.
#     """
#     sym = str(symbol).strip().upper()
#     if not sym:
#         return {}
#     data = _nse_get(f"https://www.nseindia.com/api/quote-equity?symbol={sym}")
#     if not data:
#         return {}
#     info = data.get("info", {})
#     isin = str(info.get("isin", "") or "").strip().upper()
#     comp = str(info.get("companyName", "") or info.get("longName", "") or "").strip()
#     nse_sym = str(info.get("symbol", sym) or sym).strip().upper()
#     if isin:
#         return {"symbol": nse_sym, "isin": isin, "company_name": comp}
#     return {}


# def fetch_isin_from_nse(symbol: str) -> Optional[str]:
#     """
#     Get ISIN for an NSE symbol via quote API.
#     Returns ISIN string or None.
#     """
#     result = fetch_quote_full(symbol)
#     return result.get("isin") or None


# def search_nse_symbol(query: str) -> list[dict]:
#     """
#     Search NSE autocomplete for a query string.
#     Returns list of {"symbol": str, "name": str, ...}
#     """
#     if not query or len(query.strip()) < 2:
#         return []
#     q = query.strip()
#     data = _nse_get(f"https://www.nseindia.com/api/search/autocomplete?q={q}", pause=0.5)
#     if not data:
#         return []
#     raw = data.get("symbols", [])
#     # Normalise keys — NSE returns either {"symbol","name"} or {"symbol_info","symbol_name"}
#     result = []
#     for item in raw:
#         sym  = str(item.get("symbol", item.get("symbol_info", "")) or "").strip().upper()
#         name = str(item.get("name",   item.get("symbol_name",  "")) or "").strip()
#         stype = str(item.get("symbol_type", item.get("asset_type", "")) or "").strip().upper()
#         if sym:
#             result.append({"symbol": sym, "name": name, "symbol_type": stype})
#     return result


# def _search_best_equity(query: str) -> Optional[str]:
#     """
#     Search NSE autocomplete and return the best matching EQUITY symbol.
#     Filters out debt instruments (bonds, REITs, InvITs, ETFs) when possible.
#     Returns NSE ticker string or None.
#     """
#     results = search_nse_symbol(query)
#     if not results:
#         return None

#     # Prefer equity instruments; filter out obvious non-equity
#     equity_results = [
#         r for r in results
#         if r["symbol_type"] in ("", "EQUITY", "EQ")
#         or r["symbol_type"] not in ("DEBT", "BOND", "ETF", "REIT", "InvIT", "GB", "CD", "NCD")
#     ]
#     candidates = equity_results if equity_results else results

#     # Return the first (best-match) result's symbol
#     return candidates[0]["symbol"] if candidates else None


# # ─────────────────────────────────────────────────────────────────────────────
# # Main entry point: resolve any broker symbol/name → (nse_ticker, isin, company)
# # ─────────────────────────────────────────────────────────────────────────────

# def resolve_symbol_to_isin(
#     raw_symbol: str,
#     company_name: str = "",
#     delay: float = 0.4,
# ) -> dict:
#     """
#     Resolve a broker symbol or company name to NSE ticker + ISIN.

#     Resolution pipeline (stops at first success):
#       1. Direct override map (_DIRECT_MAP) — instant, no API call
#       2. Direct NSE quote on raw_symbol (works when broker uses NSE ticker directly)
#       3. NSE quote on each cleaned/expanded variant of raw_symbol
#       4. NSE autocomplete search on each variant of raw_symbol
#       5. NSE quote on company_name variants
#       6. NSE autocomplete search on company_name variants

#     Returns:
#         {"symbol": nse_ticker, "isin": isin, "company_name": company, "source": ...}
#         or {} if unresolvable.

#     All successful results are cached in `symbol_normalisation` table by the caller.
#     """
#     raw = str(raw_symbol or "").strip()
#     comp = str(company_name or "").strip()
#     if not raw:
#         return {}

#     raw_upper = raw.upper()

#     # ── 1. Direct override ────────────────────────────────────────────────────
#     if raw_upper in _DIRECT_MAP:
#         nse_ticker = _DIRECT_MAP[raw_upper]
#         if not nse_ticker:
#             # Explicitly marked as unresolvable (e.g. bonds)
#             return {}
#         result = fetch_quote_full(nse_ticker)
#         if result:
#             result["source"] = "direct_map"
#             return result
#         # Ticker override but quote failed — still return the ticker
#         return {"symbol": nse_ticker, "isin": "", "company_name": raw, "source": "direct_map_no_quote"}

#     # Also check company name against direct map
#     comp_upper = comp.upper()
#     for key, ticker in _DIRECT_MAP.items():
#         if comp_upper and key in comp_upper:
#             if not ticker:
#                 return {}
#             result = fetch_quote_full(ticker)
#             if result:
#                 result["source"] = "direct_map_company"
#                 return result

#     # ── 2 & 3. Direct quote on symbol variants ────────────────────────────────
#     sym_variants = _clean_variants(raw)
#     for variant in sym_variants:
#         time.sleep(delay)
#         result = fetch_quote_full(variant)
#         if result:
#             result["source"] = f"quote:{variant}"
#             return result

#     # ── 4. Autocomplete search on symbol variants ─────────────────────────────
#     for variant in sym_variants:
#         time.sleep(delay)
#         best = _search_best_equity(variant)
#         if best:
#             time.sleep(delay)
#             result = fetch_quote_full(best)
#             if result:
#                 result["source"] = f"search:{variant}→{best}"
#                 return result

#     # ── 5 & 6. Try company name if different from symbol ─────────────────────
#     if comp and comp.upper() != raw_upper:
#         comp_variants = _clean_variants(comp)
#         for variant in comp_variants:
#             time.sleep(delay)
#             result = fetch_quote_full(variant)
#             if result:
#                 result["source"] = f"company_quote:{variant}"
#                 return result
#         for variant in comp_variants:
#             time.sleep(delay)
#             best = _search_best_equity(variant)
#             if best:
#                 time.sleep(delay)
#                 result = fetch_quote_full(best)
#                 if result:
#                     result["source"] = f"company_search:{variant}→{best}"
#                     return result

#     return {}


# # ─────────────────────────────────────────────────────────────────────────────
# # F&O Derivative Master (cached)
# # ─────────────────────────────────────────────────────────────────────────────

# _derivative_master: Optional[dict] = None
# _derivative_master_time: Optional[datetime] = None


# def _load_derivative_master() -> dict:
#     global _derivative_master, _derivative_master_time
#     now = datetime.now()
#     if (
#         _derivative_master is not None
#         and _derivative_master_time is not None
#         and (now - _derivative_master_time).seconds < 6 * 3600
#     ):
#         return _derivative_master

#     master: dict = {}

#     # Primary: derivative-master futures list
#     data = _nse_get("https://www.nseindia.com/api/derivative-master?type=futures", retries=2)
#     if data:
#         for item in (data.get("data") or []):
#             sym = str(item.get("symbol", "") or "").strip().upper()
#             ls  = item.get("lotSize", 0)
#             if sym and ls:
#                 master[sym] = {"fno": True, "lot_size": int(ls)}

#     # Fallback: securities in F&O index
#     if not master:
#         data2 = _nse_get(
#             "https://www.nseindia.com/api/equity-stockIndices"
#             "?index=SECURITIES%20IN%20F%26O",
#             retries=2,
#         )
#         if data2:
#             for item in (data2.get("data") or []):
#                 sym = str(item.get("symbol", "") or "").strip().upper()
#                 if sym:
#                     master[sym] = {"fno": True, "lot_size": 500}

#     # Hard-coded minimal fallback
#     if not master:
#         for sym in [
#             "NIFTY", "BANKNIFTY", "FINNIFTY", "RELIANCE", "TCS", "HDFCBANK",
#             "ICICIBANK", "INFY", "SBIN", "KOTAKBANK", "ITC", "TRENT", "BSE",
#             "BAJAJ-AUTO", "BAJAJFINSV", "BAJAJHLDNG", "LICI",
#         ]:
#             master[sym] = {"fno": True, "lot_size": 500}

#     _derivative_master = master
#     _derivative_master_time = now
#     logger.info(f"[NSE] Derivative master loaded: {len(master)} symbols")
#     return master


# def get_fno_info_from_nse(symbol: str) -> tuple[bool, int]:
#     """
#     Returns (fno_available, lot_size) for a canonical NSE symbol.
#     Uses the in-memory derivative master cache (refreshed every 6 h).
#     """
#     sym = str(symbol or "").strip().upper()
#     if not sym:
#         return False, 0
#     master = _load_derivative_master()
#     info = master.get(sym)
#     if info:
#         return True, info["lot_size"]
#     return False, 0


# def clear_derivative_cache() -> None:
#     global _derivative_master, _derivative_master_time
#     _derivative_master = None
#     _derivative_master_time = None


# # ─────────────────────────────────────────────────────────────────────────────
# # Warm up session at import time (non-blocking best-effort)
# # ─────────────────────────────────────────────────────────────────────────────
# try:
#     _get_session()
# except Exception:
#     pass

"""
nse_data_service.py — v2
=========================
Fixes vs v1:
  - Added _nse_get() helper (was called by ce_pe_service.py but missing here)
  - Added force_new param to _get_session()
  - get_fno_info_from_nse() now uses scrip_master_cache DB first so lot
    sizes are correct instead of returning 500 for everything
"""

from __future__ import annotations

import time
import requests
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# ── Session management ────────────────────────────────────────────────────────
_session: requests.Session | None = None
_session_created_at: datetime | None = None
_SESSION_TTL_SECONDS = 600  # re-warm every 10 minutes


def _get_session(force_new: bool = False) -> requests.Session:
    """
    Return a warmed NSE session.
    force_new=True: always create a fresh session (use after rate-limit errors).
    Auto-refreshes if session is older than SESSION_TTL_SECONDS.
    """
    global _session, _session_created_at

    now = datetime.now()
    expired = (
        _session is None
        or _session_created_at is None
        or (now - _session_created_at).total_seconds() > _SESSION_TTL_SECONDS
    )

    if force_new or expired:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        try:
            s.get("https://www.nseindia.com/", timeout=10)
            time.sleep(0.5)
        except Exception:
            pass
        _session = s
        _session_created_at = now

    return _session


def _nse_get(url: str, retries: int = 2, pause: float = 0.6) -> dict | list | None:
    """
    GET a JSON endpoint from NSE with retry logic.
    Returns parsed JSON or None on failure.
    Used by ce_pe_service.py for option chain + historical data.
    """
    for attempt in range(retries + 1):
        try:
            sess = _get_session()
            resp = sess.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 503, 403):
                logger.warning(
                    f"[NSE] {resp.status_code} on {url} — refreshing session (attempt {attempt+1})"
                )
                _get_session(force_new=True)
                time.sleep(pause * (attempt + 1))
                continue
            logger.warning(f"[NSE] HTTP {resp.status_code} for {url}")
            return None
        except Exception as e:
            logger.error(f"[NSE] Request error: {e}")
            if attempt < retries:
                time.sleep(pause)
    return None


# ── Derivative Master (F&O lot sizes) ────────────────────────────────────────
_derivative_master: dict | None = None
_derivative_master_time: datetime | None = None


def _load_derivative_master() -> dict:
    global _derivative_master, _derivative_master_time
    now = datetime.now()
    if (
        _derivative_master is not None
        and _derivative_master_time
        and (now - _derivative_master_time).total_seconds() < 6 * 3600
    ):
        return _derivative_master

    master: dict = {}

    # Primary: derivative-master
    try:
        data = _nse_get(
            "https://www.nseindia.com/api/derivative-master?type=futures",
            retries=2,
            pause=1.0,
        )
        if data and isinstance(data, dict):
            for item in data.get("data", []):
                sym = item.get("symbol", "").strip().upper()
                ls = item.get("lotSize", 0)
                if sym and ls and int(ls) > 1:
                    master[sym] = {"fno": True, "lot_size": int(ls)}
        elif data and isinstance(data, list):
            for item in data:
                sym = item.get("symbol", "").strip().upper()
                ls = item.get("lotSize", 0)
                if sym and ls and int(ls) > 1:
                    master[sym] = {"fno": True, "lot_size": int(ls)}
    except Exception as e:
        logger.error(f"[NSE] Derivative master error: {e}")

    # Fallback: equity F&O index list (no lot sizes, but confirms F&O availability)
    if not master:
        try:
            data = _nse_get(
                "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O",
                retries=1,
                pause=1.0,
            )
            if data and isinstance(data, dict):
                for item in data.get("data", []):
                    sym = item.get("symbol", "").strip().upper()
                    if sym:
                        master[sym] = {
                            "fno": True,
                            "lot_size": 0,
                        }  # lot_size unknown from this endpoint
        except Exception:
            pass

    _derivative_master = master
    _derivative_master_time = now
    return master


def get_fno_info_from_nse(symbol: str) -> tuple[bool, int]:
    """
    Returns (fno_available, lot_size).
    Priority:
      1. scrip_master_cache DB  (accurate lot sizes)
      2. NSE derivative master  (current, but sometimes unreliable)
      3. Return (False, 0)
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False, 0

    # ── 1. DB (ScripMaster cache) ─────────────────────────────────────────────
    try:
        from backend.services.scrip_master_db import is_db_populated, query_fno_info

        if is_db_populated():
            fno, lot = query_fno_info(sym)
            if fno and lot > 1:
                return True, lot
    except Exception:
        pass

    # ── 2. NSE derivative master ──────────────────────────────────────────────
    master = _load_derivative_master()
    info = master.get(sym)
    if info:
        lot = info["lot_size"]
        return True, lot if lot > 1 else 500  # 500 only when NSE gives 0

    return False, 0


def clear_derivative_cache():
    global _derivative_master, _derivative_master_time
    _derivative_master = None
    _derivative_master_time = None


# ── ISIN & Quote ──────────────────────────────────────────────────────────────


def fetch_isin_from_nse(symbol: str) -> str | None:
    """Get ISIN for an NSE symbol via quote API."""
    if not symbol:
        return None
    sym = str(symbol).strip().upper()
    data = _nse_get(f"https://www.nseindia.com/api/quote-equity?symbol={sym}")
    if data:
        isin = data.get("info", {}).get("isin", "")
        if isin:
            return isin.strip().upper()
    return None


def search_nse_symbol(query: str) -> list[dict]:
    """Search NSE for matching symbols."""
    data = _nse_get(f"https://www.nseindia.com/api/search/autocomplete?q={query}")
    if data and isinstance(data, dict):
        return data.get("symbols", [])
    return []
