"""
market_status.py
================
Determines NSE market open/closed status.

Strategy (no hardcoding):
  1. PRIMARY  — NSE /api/marketStatus
               Returns live status + reason directly from exchange.
               If market is closed due to Muharram, Eid, or any other reason,
               NSE says so in marketStatusMessage.  No guessing needed.

  2. SECONDARY — NSE /api/holiday-master?type=trading
               Full CM-segment holiday list for the year.
               Used to pre-check before trading hours so we don't need
               to hit marketStatus on every page load.

  Both endpoints require a session with cookies from the NSE homepage.
  A single warm-up GET to nseindia.com sets the required cookies.
  The session is module-level and reused across calls.

  3. FALLBACK — time-only logic (weekday + 9:15–15:30 IST).
               Used only when both NSE APIs are unreachable.
               Does NOT hardcode any holiday dates.

Cache policy:
  • marketStatus    — cached 5 minutes (changes during pre-open / open / close)
  • holiday list    — cached 24 hours (changes at most once per day)
  • NSE session     — reused for the process lifetime; recreated on 403/connection error
"""

import datetime
import logging
import threading
import time as _time
from typing import Tuple

import requests

logger = logging.getLogger(__name__)

# ── IST timezone ──────────────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    try:
        import pytz
        _IST = pytz.timezone("Asia/Kolkata")
    except ImportError:
        _IST = None  # server must be running in IST already


def _now_ist() -> datetime.datetime:
    if _IST:
        return datetime.datetime.now(tz=_IST)
    return datetime.datetime.now()


# ── NSE session (module-level, thread-safe lock) ──────────────────────────────
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}

_session: requests.Session | None = None
_session_lock = threading.Lock()
_session_created_at: float = 0.0
_SESSION_TTL = 3600  # recreate session every hour


def _get_session(force_new: bool = False) -> requests.Session:
    """Return a warmed-up NSE session, recreating it if stale or forced."""
    global _session, _session_created_at

    with _session_lock:
        age = _time.monotonic() - _session_created_at
        if _session is None or force_new or age > _SESSION_TTL:
            s = requests.Session()
            s.headers.update(_NSE_HEADERS)
            try:
                s.get("https://www.nseindia.com/", timeout=10)
                _time.sleep(0.5)
            except Exception as e:
                logger.warning("[MarketStatus] NSE homepage warm-up failed: %s", e)
            _session = s
            _session_created_at = _time.monotonic()

        return _session


# ── Cache: live market status ─────────────────────────────────────────────────
_status_cache: dict | None = None          # {"is_open": bool, "text": str, "fetched_at": datetime}
_STATUS_CACHE_TTL = 300                    # 5 minutes


# ── Cache: holiday list ───────────────────────────────────────────────────────
_holidays: set[datetime.date] = set()
_holidays_fetched_at: datetime.datetime | None = None
_HOLIDAY_CACHE_TTL = 86400                 # 24 hours


# ─────────────────────────────────────────────────────────────────────────────
# Helper: parse a date string in various formats NSE uses
# ─────────────────────────────────────────────────────────────────────────────
def _parse_date(s: str) -> datetime.date | None:
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY: NSE /api/marketStatus
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_market_status_from_nse() -> dict | None:
    """
    Calls NSE /api/marketStatus.
    Returns dict with keys: is_open (bool), text (str), reason (str|None).
    Returns None if the API call fails.

    NSE response example:
    {
      "marketState": [
        {
          "market": "Capital Market",
          "marketStatus": "Closed",
          "marketStatusMessage": "Market is Closed",
          "tradeDate": "26-Jun-2025",
          ...
        }
      ]
    }
    """
    for attempt in range(2):
        try:
            session = _get_session(force_new=(attempt > 0))
            resp = session.get(
                "https://www.nseindia.com/api/marketStatus",
                timeout=8,
            )
            if resp.status_code == 403:
                logger.warning("[MarketStatus] 403 on marketStatus — forcing new session")
                _get_session(force_new=True)
                continue
            if resp.status_code != 200:
                logger.warning("[MarketStatus] marketStatus HTTP %d", resp.status_code)
                return None

            data = resp.json()
            states = data.get("marketState", [])

            # Look for Capital Market segment
            cm = next(
                (s for s in states
                 if "capital" in str(s.get("market", "")).lower()
                 or "equity" in str(s.get("market", "")).lower()),
                None,
            )
            if cm is None and states:
                cm = states[0]   # fallback to first entry
            if cm is None:
                return None

            status_str = str(cm.get("marketStatus", "")).strip().lower()
            msg        = str(cm.get("marketStatusMessage", "")).strip()
            is_open    = status_str == "open"

            # Build a clean human-readable label
            if is_open:
                text = "🟢 Market Open"
            else:
                # Use NSE's own message to give the real reason
                # e.g. "Market is Closed" / "Market is Closed for Muharram"
                reason = msg if msg and msg.lower() != "market is closed" else ""
                if reason:
                    text = f"🔴 Market Closed – {reason}"
                else:
                    text = "🔴 Market Closed"

            return {"is_open": is_open, "text": text}

        except Exception as e:
            logger.warning("[MarketStatus] marketStatus fetch error (attempt %d): %s", attempt + 1, e)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# SECONDARY: NSE /api/holiday-master  (full year holiday list)
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_holiday_list() -> set[datetime.date]:
    """
    Fetches the CM-segment holiday list for the current year from NSE.
    Returns a set of holiday dates.  Empty set on failure.

    NSE response:
    {
      "CM": [
        {"tradingDate": "26-Jun-2025", "weekDay": "Thursday", "description": "Muharram"},
        ...
      ],
      "FO": [...],
      ...
    }
    """
    holidays: set[datetime.date] = set()
    for attempt in range(2):
        try:
            session = _get_session(force_new=(attempt > 0))
            resp = session.get(
                "https://www.nseindia.com/api/holiday-master?type=trading",
                timeout=10,
            )
            if resp.status_code == 403:
                _get_session(force_new=True)
                continue
            if resp.status_code != 200:
                logger.warning("[MarketStatus] holiday-master HTTP %d", resp.status_code)
                return holidays

            data = resp.json()

            # Use CM (Capital Market) segment; fall back to any segment
            rows = data.get("CM") or data.get("FO") or []
            if not rows:
                # Try iterating all keys
                for v in data.values():
                    if isinstance(v, list) and v:
                        rows = v
                        break

            for item in rows:
                if not isinstance(item, dict):
                    continue
                date_str = (
                    item.get("tradingDate")
                    or item.get("tradeDate")
                    or item.get("date", "")
                )
                d = _parse_date(str(date_str))
                if d:
                    holidays.add(d)

            if holidays:
                logger.info("[MarketStatus] Loaded %d NSE holidays from API", len(holidays))
                return holidays

        except Exception as e:
            logger.warning("[MarketStatus] holiday-master fetch error (attempt %d): %s", attempt + 1, e)

    return holidays


def _get_holidays() -> set[datetime.date]:
    """Return cached holiday set, refreshing every 24 hours."""
    global _holidays, _holidays_fetched_at
    now = _now_ist()
    if (
        _holidays_fetched_at is None
        or (now - _holidays_fetched_at).total_seconds() > _HOLIDAY_CACHE_TTL
    ):
        fetched = _fetch_holiday_list()
        if fetched:                         # only update if we got real data
            _holidays = fetched
        _holidays_fetched_at = now
    return _holidays


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK: time-only logic (no hardcoded holidays)
# ─────────────────────────────────────────────────────────────────────────────
def _time_based_status() -> Tuple[bool, str]:
    """
    Pure time-based fallback used only when NSE APIs are unreachable.
    Checks weekday, cached holidays (if any), and trading hours.
    """
    now     = _now_ist()
    today   = now.date()
    weekday = today.weekday()

    if weekday == 5:
        return False, "🔴 Market Closed – Saturday"
    if weekday == 6:
        return False, "🔴 Market Closed – Sunday"

    # Use whatever holidays we have cached (may be empty — that's acceptable)
    if today in _holidays:
        return False, "🔴 Market Closed – Holiday"

    open_t  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if now < open_t:
        return False, "🔴 Market Not Open Yet (opens 9:15 AM IST)"
    elif now > close_t:
        return False, "🔴 Market Closed (closed 3:30 PM IST)"
    else:
        return True, "🟢 Market Open"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def get_market_status() -> Tuple[bool, str]:
    """
    Returns (is_open, status_text) with a human‑readable reason.
    Uses:
      1. Cached live status (< 5 min)
      2. Holiday list (always checked first)
      3. NSE /api/marketStatus (if reachable)
      4. Pure time‑based fallback
    """
    global _status_cache

    now = _now_ist()
    today = now.date()

    # ── 1. Return cached status if fresh ─────────────────────────────────────
    if _status_cache is not None:
        age = (now - _status_cache["fetched_at"]).total_seconds()
        if age < _STATUS_CACHE_TTL:
            return _status_cache["is_open"], _status_cache["text"]

    # ── 2. Always check the holiday list (cached) ───────────────────────────
    holidays = _get_holidays()
    is_holiday = today in holidays

    # ── 3. Weekend check (before API call) ──────────────────────────────────
    weekday = today.weekday()
    if weekday >= 5:
        result = (False, "🔴 Market Closed – Weekend")
        _status_cache = {"is_open": False, "text": result[1], "fetched_at": now}
        return result

    # ── 4. If we know it's a holiday, return that immediately ──────────────
    #     (no need to call API – saves bandwidth and avoids 403 errors)
    if is_holiday:
        result = (False, "🔴 Market Closed – Holiday")
        _status_cache = {"is_open": False, "text": result[1], "fetched_at": now}
        return result

    # ── 5. Outside trading hours (8:00–16:30 IST) ──────────────────────────
    hour = now.hour
    if hour < 8 or hour >= 17:
        # We already know it's not a holiday, so use time‑based reason
        open_t  = now.replace(hour=9, minute=15, second=0, microsecond=0)
        close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now < open_t:
            result = (False, "🔴 Market Not Open Yet (opens 9:15 AM IST)")
        else:
            result = (False, "🔴 Market Closed (closed 3:30 PM IST)")
        _status_cache = {"is_open": False, "text": result[1], "fetched_at": now}
        return result

    # ── 6. During trading window – try NSE API ──────────────────────────────
    nse_result = _fetch_market_status_from_nse()
    if nse_result is not None:
        # If NSE says it's closed, enrich the text with the holiday reason
        # if the API didn't already provide one.
        if not nse_result["is_open"]:
            # Check if the API gave a specific reason (e.g., "Muharram")
            if " – " in nse_result["text"]:
                # Already has a reason – keep it
                text = nse_result["text"]
            else:
                # Generic "Market Closed" – add holiday info if applicable
                # (we already know it's not a holiday, but just in case)
                if is_holiday:
                    text = "🔴 Market Closed – Holiday"
                else:
                    text = nse_result["text"]  # keep as is
            nse_result["text"] = text

        _status_cache = {
            "is_open":    nse_result["is_open"],
            "text":       nse_result["text"],
            "fetched_at": now,
        }
        return nse_result["is_open"], nse_result["text"]

    # ── 7. Fallback – NSE API unreachable ────────────────────────────────────
    logger.warning("[MarketStatus] NSE marketStatus unreachable – using fallback logic")
    # Use time‑based logic, but we already know it's not a holiday (checked above)
    # so the fallback will correctly say "Closed – ..." based on time.
    result = _time_based_status()  # this will also check holidays again, but fine
    _status_cache = {"is_open": result[0], "text": result[1], "fetched_at": now}
    return result

def get_market_status_badge() -> str:
    """Returns a markdown bold badge string for Streamlit sidebar."""
    _, text = get_market_status()
    return f"**{text}**"


def is_market_holiday(d: datetime.date | None = None) -> bool:
    """Returns True if `d` (default: today IST) is an NSE holiday or weekend."""
    if d is None:
        d = _now_ist().date()
    if d.weekday() >= 5:
        return True
    return d in _get_holidays()