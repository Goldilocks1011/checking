"""
Symbol Resolver — v3 (DB-only, no CSV)
========================================
KEY CHANGES vs v2:
  ① Source 1 (_load_scrip_master() CSV call) removed from _build_cache().
  ② _build_cache() now queries scrip_master_cache DB for symbol_root entries
     in addition to stock_master_mapping (which was already there).
  ③ No CSV path, no pandas CSV read anywhere.
  scrip_master_cache table is the single source of truth.

Resolution order in get_canonical() is unchanged:
  1. Exact match in cache
  2. Suffix-stripped exact match
  3. ISIN lookup via isin_resolver (DB-only)
  4. Return symbol as-is (safe fallback)
"""
from __future__ import annotations

import re
import logging

from sqlalchemy import text
from database import SessionLocal

logger = logging.getLogger(__name__)

# ── Module-level cache ────────────────────────────────────────────────────────
_cache: dict[str, str] | None = None
_nse_cache: dict[str, str] = {}  
_SUFFIX_STRIP = re.compile(r"(_EQ|-EQ|_NSE|-NSE|_BSE|-BSE)$", re.IGNORECASE)
try:
    from services.nse_data_service import search_nse_symbol, fetch_quote_full
except ImportError:
    search_nse_symbol = None
    fetch_quote_full = None
    
# Strip common noisy suffix words from company names
_STRIP = re.compile(
    r"\s+(LTD\.?|LIMITED|PVT\.?|CORP\.?|INDUSTRIES|HOLDINGS?|FINANCE|FINANCIAL|"
    r"BANK|INTL\.?|INTERNATIONAL|SERV\.?|SERVICES|INDL\.?|INDUSTRIAL|FIN\.?)$",
    re.IGNORECASE,
)
_RE_SUFFIX = re.compile(r"(_RE|-RE)$", re.IGNORECASE)

# ─────────────────────────────────────────────────────────────────────────────
# Cache builder  (DB-only)
# ─────────────────────────────────────────────────────────────────────────────

def _build_cache() -> dict[str, str]:
    cache: dict[str, str] = {}

    # Source 1: scrip_master_cache — symbol_root / name / full_name for NSE EQ rows
    try:
        from services.scrip_master_db import is_db_populated
        if is_db_populated():
            db = SessionLocal()
            try:
                rows = db.execute(text("""
                    SELECT symbol_root, name, full_name
                    FROM scrip_master_cache
                    WHERE exch='N' AND exch_type='C'
                      AND symbol_root IS NOT NULL AND symbol_root != ''
                    LIMIT 100000
                """)).fetchall()
                for r in rows:
                    sr   = str(r.symbol_root or "").strip().upper()
                    name = str(r.name        or "").strip().upper()
                    full = str(r.full_name   or "").strip().upper()
                    if not sr or sr in ("NAN", ""):
                        continue
                    for key in (sr, name, full):
                        if key and key not in ("NAN", "") and key not in cache:
                            cache[key] = sr
                    stripped = _STRIP.sub("", name).strip()
                    if stripped and stripped not in cache:
                        cache[stripped] = sr
            finally:
                db.close()
    except Exception as e:
        logger.info(f"[SymbolResolver] scrip_master_cache DB load skipped: {e}")

    # Source 2: stock_master_mapping canonical symbols
    try:
        db = SessionLocal()
        rows = db.execute(
            text("SELECT standard_name, canonical_symbol FROM stock_master_mapping "
                 "WHERE canonical_symbol IS NOT NULL AND canonical_symbol != ''")
        ).fetchall()
        db.close()

        for r in rows:
            can = str(r.canonical_symbol or "").strip().upper()
            std = str(r.standard_name    or "").strip().upper()
            if can and can not in ("NAN", "—", ""):
                cache[std] = can
                cache[can] = can   # self-mapping
                stripped = _STRIP.sub("", std).strip()
                if stripped:
                    cache[stripped] = can
    except Exception as e:
        logger.info(f"[SymbolResolver] stock_master_mapping DB load skipped: {e}")

    logger.info(f"[SymbolResolver] Cache built — {len(cache):,} entries")
    return cache


def reload_cache():
    """Force a full cache rebuild (call after ScripMaster upload / auto-populate)."""
    global _cache
    _cache = None
    logger.info("[SymbolResolver] Cache cleared — will rebuild on next call")


def _ensure_cache() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = _build_cache()
    return _cache


# ─────────────────────────────────────────────────────────────────────────────
# Public resolution function  (safe — no prefix scan)
# ─────────────────────────────────────────────────────────────────────────────

def _try_nse_resolve(symbol: str) -> str | None:
    """
    Query NSE autocomplete + quote to find canonical symbol.
    Caches in memory and in symbol_normalisation table for future.
    Returns canonical symbol or None.
    """
    if search_nse_symbol is None:
        return None
    sym = symbol.strip().upper()
    if not sym:
        return None
    # Check in‑memory NSE cache
    if sym in _nse_cache:
        return _nse_cache[sym]
    # Check DB cache (symbol_normalisation table)
    try:
        from database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        row = db.execute(
            text("SELECT canonical_symbol FROM symbol_normalisation WHERE raw_symbol = :sym"),
            {"sym": sym}
        ).first()
        db.close()
        if row and row.canonical_symbol:
            _nse_cache[sym] = row.canonical_symbol
            return row.canonical_symbol
    except Exception:
        pass
    # Hit NSE API
    try:
        results = search_nse_symbol(sym)
        if not results:
            return None
        # Prefer equity symbols
        candidates = [
            r for r in results
            if r.get("symbol_type", "").upper() in ("", "EQUITY", "EQ")
        ] or results
        # Take first match and fetch its canonical symbol via quote
        best = candidates[0]
        ticker = best.get("symbol", "").strip().upper()
        if ticker:
            # Optionally verify with quote
            quote = fetch_quote_full(ticker) if fetch_quote_full else {}
            canon = quote.get("symbol", ticker).strip().upper()
            if canon:
                # Cache in memory
                _nse_cache[sym] = canon
                # Persist in DB (symbol_normalisation)
                try:
                    db = SessionLocal()
                    db.execute(
                        text("""
                            INSERT INTO symbol_normalisation (raw_symbol, canonical_symbol, source)
                            VALUES (:raw, :canon, 'nse')
                            ON DUPLICATE KEY UPDATE canonical_symbol = :canon, source = 'nse'
                        """),
                        {"raw": sym, "canon": canon}
                    )
                    db.commit()
                    db.close()
                except Exception:
                    pass
                return canon
    except Exception:
        pass
    return None

def get_canonical(symbol: str) -> str:
    if not symbol:
        return ""
    sym = str(symbol).strip().upper()
    if not sym or sym in ("NAN", "NONE", ""):
        return sym
    cache = _ensure_cache()
    # 1. Exact
    if sym in cache:
        return cache[sym]
    # 2. Strip legal suffix (existing)
    stripped = _STRIP.sub("", sym).strip()
    if stripped and stripped != sym and stripped in cache:
        cache[sym] = cache[stripped]
        return cache[stripped]
    # 3. Strip broker suffix (_RE / -RE) and retry
    base = _RE_SUFFIX.sub("", sym).strip()
    base = _SUFFIX_STRIP.sub("", base).strip()
    if base and base != sym:
        if base in cache:
            cache[sym] = cache[base]
            return cache[base]
        # Also try stripped version of base
        base_stripped = _STRIP.sub("", base).strip()
        if base_stripped and base_stripped in cache:
            cache[sym] = cache[base_stripped]
            return cache[base_stripped]
        # Fall through to further steps with base? We'll continue with original sym below.
   
    # 4. ISIN lookup (existing + now also try on base and base_stripped)
    try:
        from services.isin_resolver import resolve_isin, isin_to_canonical
        
        # Try original symbol
        isin = resolve_isin(sym)
        if isin:
            can = isin_to_canonical(isin)
            if can:
                cache[sym] = can
                return can
        # Try legal-suffix stripped version
        if stripped and stripped != sym:
            isin = resolve_isin(stripped)
            if isin:
                can = isin_to_canonical(isin)
                if can:
                    cache[sym] = can
                    return can
        # Try base (without _RE/-RE)
        if base and base != sym:
            isin = resolve_isin(base)
            if isin:
                can = isin_to_canonical(isin)
                if can:
                    cache[sym] = can
                    return can
            # Try base_stripped (without legal suffix and without _RE/-RE)
            if base_stripped and base_stripped != sym:
                isin = resolve_isin(base_stripped)
                if isin:
                    can = isin_to_canonical(isin)
                    if can:
                        cache[sym] = can
                        return can
    except Exception:
        pass
    # 5. NSE fallback (new)
    nse_canon = _try_nse_resolve(sym)
    if nse_canon:
        cache[sym] = nse_canon
        return nse_canon
    # If we stripped _RE, also try NSE on the base
    if base and base != sym:
        nse_canon = _try_nse_resolve(base)
        if nse_canon:
            cache[sym] = nse_canon
            return nse_canon
    # 6. Final fallback
    clean = stripped if stripped else sym
    cache[sym] = clean
    return clean

# ── Backward-compatible aliases ───────────────────────────────────────────────
_normalise = get_canonical
normalise  = get_canonical


# ─────────────────────────────────────────────────────────────────────────────
# Batch helper
# ─────────────────────────────────────────────────────────────────────────────

def get_canonical_batch(symbols) -> dict[str, str]:
    """Resolve a list/Series of symbols → {raw: canonical} dict."""
    return {str(s): get_canonical(str(s)) for s in symbols}


# ─────────────────────────────────────────────────────────────────────────────
# Debug helper
# ─────────────────────────────────────────────────────────────────────────────

def debug_resolve(symbol: str) -> None:
    """
    logger.info step-by-step resolution trace.
    Usage: from services.symbol_resolver import debug_resolve; debug_resolve("BAJAJ AUTO")
    """
    sym   = str(symbol).strip().upper()
    cache = _ensure_cache()

    logger.info(f"\n=== Resolution trace for '{symbol}' ===")

    if sym in cache:
        logger.info(f"  Step 1 EXACT → '{cache[sym]}'")
        return

    stripped = _STRIP.sub("", sym).strip()
    if stripped and stripped != sym and stripped in cache:
        logger.info(f"  Step 2 STRIPPED ('{stripped}') → '{cache[stripped]}'")
        return

    try:
        from services.isin_resolver import resolve_isin, isin_to_canonical
        isin = resolve_isin(sym)
        logger.info(f"  Step 3 ISIN lookup → '{isin}'")
        if isin:
            can = isin_to_canonical(isin)
            logger.info(f"  Step 3 ISIN→canonical → '{can}'")
            return
    except Exception as e:
        logger.info(f"  Step 3 ISIN lookup failed: {e}")

    logger.info(f"  Step 4 FALLBACK → '{stripped or sym}' (unresolved)")


# ─────────────────────────────────────────────────────────────────────────────
# Warm-up at import time
# ─────────────────────────────────────────────────────────────────────────────
try:
    _cache = _build_cache()
except Exception as _e:
    logger.info(f"[SymbolResolver] Warm-up failed (will retry on first call): {_e}")
    _cache = None