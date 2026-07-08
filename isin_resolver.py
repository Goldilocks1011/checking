"""
isin_resolver.py — v4 (DB-only, no CSV)
=========================================
KEY CHANGES vs v3:
  ① SCRIP_MASTER_PATH completely removed.
  ② _load_csv() and all CSV in-memory dicts removed.
  ③ resolve_isin() queries scrip_master_db.query_isin() only.
  ④ isin_to_canonical() queries scrip_master_cache DB directly.
  ⑤ build_canonical_bridge() no longer merges CSV data.
  scrip_master_cache table is the single source of truth.
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def resolve_isin(symbol: str) -> str:
    """
    DB-only ISIN resolution. Returns ISIN string or ''.

    Resolution order:
      1. scrip_master_cache DB via query_isin()
      2. Return '' — caller adds symbol to unmatched_symbols

    NO prefix/first-word guessing, NO CSV fallback.
    """
    if not symbol:
        return ""
    s = str(symbol).strip().upper()
    if not s or s in ("NAN", "NONE", ""):
        return ""
    try:
        from backend.services.scrip_master_db import is_db_populated, query_isin

        if is_db_populated():
            isin = query_isin(s)
            if isin:
                return isin
    except Exception as e:
        logger.error(f"[ISIN] DB lookup error for '{symbol}': {e}", exc_info=True)
    return ""


def resolve_isin_from_isin(isin: str) -> str:
    """
    Validate / pass through a broker-supplied ISIN.
    Trusts ISINs starting with 'IN' (Indian securities).
    """
    if not isin:
        return ""
    s = str(isin).strip().upper()
    return s if s.startswith("IN") else ""


def isin_to_canonical(isin: str) -> str:
    """Given ISIN, return canonical NSE SymbolRoot. Returns '' if unknown."""
    if not isin:
        return ""
    s = str(isin).strip().upper()
    try:
        from backend.services.scrip_master_db import is_db_populated
        from backend.database import SessionLocal
        from sqlalchemy import text

        if is_db_populated():
            db = SessionLocal()
            try:
                row = db.execute(
                    text("""
                        SELECT symbol_root FROM scrip_master_cache
                        WHERE isin = :isin AND exch='N' AND exch_type='C'
                          AND symbol_root IS NOT NULL AND symbol_root != ''
                        LIMIT 1
                    """),
                    {"isin": s},
                ).first()
                if row and row.symbol_root:
                    return row.symbol_root.strip().upper()
            finally:
                db.close()
    except Exception as e:
        logger.error(f"[ISIN] isin_to_canonical DB error: {e}", exc_info=True)
    return ""


def build_isin_map_for_symbols(symbols: list[str]) -> dict[str, str]:
    """Batch resolve {symbol → isin}."""
    return {s: resolve_isin(s) for s in symbols}


def build_canonical_bridge(all_txns_df) -> dict[str, str]:
    """
    Build {isin → canonical_ticker} for tax harvest engine.
    Sources transaction ISINs from the DataFrame; canonical comes from DB.
    """
    bridge: dict[str, str] = {}
    if "isin" in all_txns_df.columns and "symbol" in all_txns_df.columns:
        for _, row in all_txns_df.iterrows():
            isin = str(row.get("isin", "")).strip().upper()
            sym = str(row.get("symbol", "")).strip().upper()
            if isin and isin not in ("NAN", "NONE", "") and isin not in bridge:
                # Try to get the canonical from DB; fall back to transaction symbol
                can = isin_to_canonical(isin) or sym
                bridge[isin] = can
    return bridge


def enrich_transactions_with_isin(db, user_id: int):
    """Post-insert: fill empty ISINs in transactions from scrip_master_cache."""
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT id, symbol FROM transactions "
            "WHERE user_id=:uid AND (isin IS NULL OR isin='')"
        ),
        {"uid": user_id},
    ).fetchall()
    updated = 0
    for row in rows:
        isin = resolve_isin(str(row.symbol))
        if isin:
            db.execute(
                text("UPDATE transactions SET isin=:isin WHERE id=:id"),
                {"isin": isin, "id": row.id},
            )
            updated += 1
    if updated:
        db.commit()
        logger.info(f"[ISIN] Enriched {updated} transactions for user {user_id}")
