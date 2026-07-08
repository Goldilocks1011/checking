"""
wishlist_service.py
===================
Manages the Wishlist Bucket — a per-user / per-group list of symbols
the trader wants to monitor and run option strategies on.

Steps covered (per the module spec):
  Step 1  · Load base data:  auto-populate from holdings + open F&O positions
  Step 2  · User modifies:   ADD / REMOVE symbols manually
            is_auto_added = 1  →  synced from holdings / F&O
            is_auto_added = 0  →  manually added by user

Table created automatically on first import.
"""

from __future__ import annotations

from sqlalchemy import text
from backend.database import SessionLocal
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Table bootstrap (idempotent)
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_wishlist_table() -> None:
    db = SessionLocal()
    try:
        # Main table
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS wishlists (
                id               INT          NOT NULL AUTO_INCREMENT,
                user_id          INT          NULL,
                group_id         INT          NULL,
                symbol           VARCHAR(100) NOT NULL,
                canonical_symbol VARCHAR(100) DEFAULT '',
                is_auto_added    TINYINT      NOT NULL DEFAULT 1
                    COMMENT '1=synced from holdings, 0=manually added',
                notes            TEXT,
                added_at         DATETIME     DEFAULT NOW(),
                updated_at       DATETIME     DEFAULT NOW() ON UPDATE NOW(),
                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
        db.commit()

        # Unique index: one row per (user, symbol)
        try:
            db.execute(
                text(
                    "ALTER TABLE wishlists "
                    "ADD UNIQUE KEY uq_wl_user_sym (user_id, symbol)"
                )
            )
            db.commit()
        except Exception:
            db.rollback()

        # Unique index: one row per (group, symbol)
        try:
            db.execute(
                text(
                    "ALTER TABLE wishlists "
                    "ADD UNIQUE KEY uq_wl_grp_sym (group_id, symbol)"
                )
            )
            db.commit()
        except Exception:
            db.rollback()

        logger.info("[Wishlist] Table ready")
    except Exception as e:
        logger.error(f"[Wishlist] Table init error: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


_ensure_wishlist_table()


# ─────────────────────────────────────────────────────────────────────────────
# Single-user CRUD
# ─────────────────────────────────────────────────────────────────────────────


def get_wishlist(user_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT id, symbol, canonical_symbol, is_auto_added, notes,
                       added_at, updated_at
                FROM wishlists
                WHERE user_id = :uid
                ORDER BY is_auto_added DESC, symbol ASC
            """),
            {"uid": user_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


def add_to_wishlist(
    user_id: int,
    symbol: str,
    canonical: str = "",
    is_auto: bool = False,
    notes: str = "",
) -> dict:
    """
    Insert or update a symbol in the user's wishlist.
    Existing rows are not overwritten (canonical / notes kept if already set).
    """
    sym = symbol.strip().upper()
    can = canonical.strip().upper() or sym
    if not sym:
        return {"status": "error", "message": "Symbol is empty"}

    db = SessionLocal()
    try:
        db.execute(
            text("""
                INSERT INTO wishlists
                    (user_id, symbol, canonical_symbol, is_auto_added, notes)
                VALUES
                    (:uid, :sym, :can, :auto, :notes)
                ON DUPLICATE KEY UPDATE
                    canonical_symbol = IF(
                        canonical_symbol = '' OR canonical_symbol IS NULL,
                        VALUES(canonical_symbol),
                        canonical_symbol
                    ),
                    updated_at = NOW()
            """),
            {
                "uid": user_id,
                "sym": sym,
                "can": can,
                "auto": 1 if is_auto else 0,
                "notes": notes,
            },
        )
        db.commit()
        return {"status": "added", "symbol": sym}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


def remove_from_wishlist(user_id: int, symbol: str) -> dict:
    sym = symbol.strip().upper()
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM wishlists WHERE user_id = :uid AND symbol = :sym"),
            {"uid": user_id, "sym": sym},
        )
        db.commit()
        if result.rowcount:
            return {"status": "removed", "symbol": sym}
        return {"status": "not_found", "symbol": sym}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


def sync_from_holdings(user_id: int) -> dict:
    """
    Auto-populate the wishlist with every symbol the user currently holds
    (equity holdings with qty > 0) PLUS every underlying in open F&O positions.

    Sets is_auto_added = 1 for inserted rows.
    Does NOT remove symbols the user manually added.
    """
    db = SessionLocal()
    try:
        # ── Equity holdings ────────────────────────────────────────────────────
        eq_rows = db.execute(
            text("""
                SELECT
                    UPPER(h.symbol)                                         AS symbol,
                    UPPER(COALESCE(sm.canonical_symbol, h.symbol, ''))      AS canonical
                FROM holdings h
                LEFT JOIN user_stock_symbol_mapping usm
                    ON  usm.user_id     = h.user_id
                    AND UPPER(usm.symbol) = UPPER(h.symbol)
                LEFT JOIN stock_master_mapping sm ON sm.isin = usm.isin
                WHERE h.user_id = :uid
                  AND h.quantity > 0
                  AND h.segment  = 'EQ'
            """),
            {"uid": user_id},
        ).fetchall()

        # ── Open F&O underlyings ───────────────────────────────────────────────
        # ── Open F&O underlyings ───────────────────────────────────────────────
        fno_rows = db.execute(
            text("""
                SELECT DISTINCT
                    UPPER(underlying) AS symbol,
                    UPPER(underlying) AS canonical
                FROM fno_open_positions
                WHERE user_id   = :uid
                AND ABS(open_qty) > 0.001
            """),
            {"uid": user_id},
        ).fetchall()

        # ── Merge symbol sets ──────────────────────────────────────────────────
        all_syms: dict[str, str] = {}  # symbol → canonical
        for r in list(eq_rows) + list(fno_rows):
            sym = (r.symbol or "").strip().upper()
            can = (r.canonical or sym).strip().upper()
            if sym:
                all_syms.setdefault(sym, can)  # first canonical wins

        # ── Upsert each symbol ─────────────────────────────────────────────────
        added = 0
        for sym, can in all_syms.items():
            existing = db.execute(
                text("SELECT id FROM wishlists WHERE user_id=:uid AND symbol=:sym"),
                {"uid": user_id, "sym": sym},
            ).first()
            if not existing:
                db.execute(
                    text("""
                        INSERT IGNORE INTO wishlists
                            (user_id, symbol, canonical_symbol, is_auto_added)
                        VALUES
                            (:uid, :sym, :can, 1)
                    """),
                    {"uid": user_id, "sym": sym, "can": can},
                )
                added += 1

        db.commit()
        return {
            "status": "synced",
            "total": len(all_syms),
            "added": added,
            "eq": len(eq_rows),
            "fno": len(fno_rows),
        }
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


def clear_auto_added(user_id: int) -> dict:
    """Remove all auto-synced symbols; keep manually added ones."""
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM wishlists WHERE user_id=:uid AND is_auto_added=1"),
            {"uid": user_id},
        )
        db.commit()
        return {"status": "cleared", "removed": result.rowcount}
    finally:
        db.close()


def clear_all(user_id: int) -> dict:
    """Remove ALL symbols from this user's wishlist."""
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM wishlists WHERE user_id=:uid"), {"uid": user_id}
        )
        db.commit()
        return {"status": "cleared", "removed": result.rowcount}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Group CRUD
# ─────────────────────────────────────────────────────────────────────────────


def get_group_wishlist(group_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT id, symbol, canonical_symbol, is_auto_added, notes,
                       added_at, updated_at
                FROM wishlists
                WHERE group_id = :gid
                ORDER BY is_auto_added DESC, symbol ASC
            """),
            {"gid": group_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


def add_to_group_wishlist(
    group_id: int,
    symbol: str,
    canonical: str = "",
    is_auto: bool = False,
    notes: str = "",
) -> dict:
    sym = symbol.strip().upper()
    can = canonical.strip().upper() or sym
    if not sym:
        return {"status": "error", "message": "Symbol is empty"}

    db = SessionLocal()
    try:
        db.execute(
            text("""
                INSERT INTO wishlists
                    (group_id, symbol, canonical_symbol, is_auto_added, notes)
                VALUES
                    (:gid, :sym, :can, :auto, :notes)
                ON DUPLICATE KEY UPDATE
                    canonical_symbol = IF(
                        canonical_symbol = '' OR canonical_symbol IS NULL,
                        VALUES(canonical_symbol),
                        canonical_symbol
                    ),
                    updated_at = NOW()
            """),
            {
                "gid": group_id,
                "sym": sym,
                "can": can,
                "auto": 1 if is_auto else 0,
                "notes": notes,
            },
        )
        db.commit()
        return {"status": "added", "symbol": sym}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


def remove_from_group_wishlist(group_id: int, symbol: str) -> dict:
    sym = symbol.strip().upper()
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM wishlists WHERE group_id=:gid AND symbol=:sym"),
            {"gid": group_id, "sym": sym},
        )
        db.commit()
        if result.rowcount:
            return {"status": "removed", "symbol": sym}
        return {"status": "not_found", "symbol": sym}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


def sync_group_from_holdings(group_id: int) -> dict:
    """
    Auto-populate group wishlist from every member's holdings + F&O positions.
    """
    db = SessionLocal()
    try:
        members = db.execute(
            text("SELECT user_id FROM group_members WHERE group_id=:gid"),
            {"gid": group_id},
        ).fetchall()
        if not members:
            return {"status": "synced", "total": 0, "added": 0}

        uid_list = ",".join(str(m.user_id) for m in members)

        eq_rows = db.execute(text(f"""
                SELECT
                    UPPER(h.symbol)                                         AS symbol,
                    UPPER(COALESCE(sm.canonical_symbol, h.symbol, ''))      AS canonical
                FROM holdings h
                LEFT JOIN user_stock_symbol_mapping usm
                    ON  usm.user_id       = h.user_id
                    AND UPPER(usm.symbol) = UPPER(h.symbol)
                LEFT JOIN stock_master_mapping sm ON sm.isin = usm.isin
                WHERE h.user_id IN ({uid_list})
                  AND h.quantity > 0
                  AND h.segment  = 'EQ'
            """)).fetchall()

        fno_rows = db.execute(text(f"""
                SELECT DISTINCT
                    UPPER(underlying) AS symbol,
                    UPPER(underlying) AS canonical
                FROM fno_open_positions
                WHERE user_id IN ({uid_list})
                  AND ABS(open_qty) > 0.001

            """)).fetchall()

        all_syms: dict[str, str] = {}
        for r in list(eq_rows) + list(fno_rows):
            sym = (r.symbol or "").strip().upper()
            can = (r.canonical or sym).strip().upper()
            if sym:
                all_syms.setdefault(sym, can)

        added = 0
        for sym, can in all_syms.items():
            existing = db.execute(
                text("SELECT id FROM wishlists WHERE group_id=:gid AND symbol=:sym"),
                {"gid": group_id, "sym": sym},
            ).first()
            if not existing:
                db.execute(
                    text("""
                        INSERT IGNORE INTO wishlists
                            (group_id, symbol, canonical_symbol, is_auto_added)
                        VALUES
                            (:gid, :sym, :can, 1)
                    """),
                    {"gid": group_id, "sym": sym, "can": can},
                )
                added += 1

        db.commit()
        return {
            "status": "synced",
            "total": len(all_syms),
            "added": added,
            "eq": len(eq_rows),
            "fno": len(fno_rows),
        }
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


def clear_group_auto_added(group_id: int) -> dict:
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM wishlists WHERE group_id=:gid AND is_auto_added=1"),
            {"gid": group_id},
        )
        db.commit()
        return {"status": "cleared", "removed": result.rowcount}
    finally:
        db.close()


def clear_group_all(group_id: int) -> dict:
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM wishlists WHERE group_id=:gid"), {"gid": group_id}
        )
        db.commit()
        return {"status": "cleared", "removed": result.rowcount}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Sell Call Condition Checks (4 conditions for Wishlist recommendations)
# ─────────────────────────────────────────────────────────────────────────────


def _check_profit_condition_wishlist(
    held: bool, avg_buy_price: float, spot: float
) -> dict:
    """
    CONDITION 1: Profit Check (for Wishlist)
    If you DON'T hold the stock, returns NEUTRAL (can't check profit on non-existent position).
    If you DO hold, checks if in profit.

    Returns: {"status": "✅ PASS" | "⚠️ FAIL" | "⏸️ NEUTRAL", "message": str, ...}
    """
    if not held:
        return {
            "status": "⏸️ NEUTRAL",
            "message": "You don't hold this stock (would need to buy first)",
            "profit_pct": 0.0,
        }

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
            "message": f"Your holding is in profit (+{profit_pct:.1f}%)",
            "profit_pct": round(profit_pct, 2),
        }
    else:
        return {
            "status": "⚠️ FAIL",
            "message": f"Your holding is in loss ({profit_pct:.1f}%). Avoid selling CE.",
            "profit_pct": round(profit_pct, 2),
        }


def _check_market_condition_wishlist(spot: float, high_52w: float) -> dict:
    """
    CONDITION 2: Market Condition (for Wishlist)
    Checks if stock is near 52W high (within 5%).
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
            "message": f"Near 52W high ({100 - pct_to_high:.1f}%). Good for selling CE.",
            "pct_to_52w_high": round(pct_to_high, 2),
        }
    else:
        return {
            "status": "⚠️ FAIL",
            "message": f"Stock {pct_to_high:.1f}% below 52W high. Still room to rise.",
            "pct_to_52w_high": round(pct_to_high, 2),
        }


def _check_seasonal_condition_wishlist(symbol: str) -> dict:
    """
    CONDITION 3: Seasonal Pattern Check (for Wishlist)
    Checks if current month is BEST or WORST seasonally.
    """
    try:
        from backend.services.analysis_service import get_seasonal_pattern

        db = SessionLocal()
        try:
            row = db.execute(
                text("""
                    SELECT scrip_code FROM scrip_master_cache
                    WHERE UPPER(symbol_root) = :sym
                    AND scrip_code IS NOT NULL
                    LIMIT 1
                """),
                {"sym": symbol.upper()},
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
                "message": f"Currently in {best_month} (BEST seasonal month). WAIT for rally.",
                "season_rank": "best",
            }
        elif rank == "worst":
            return {
                "status": "✅ READY",
                "message": f"Currently in {worst_month} (WORST seasonal month). IMMEDIATELY sell CE.",
                "season_rank": "worst",
            }
        else:
            return {
                "status": "✅ READY",
                "message": f"Neutral seasonal month. Proceed.",
                "season_rank": "neutral",
            }

    except Exception as e:
        logger.debug(f"[Wishlist Seasonal] Error for {symbol}: {e}")
        return {
            "status": "✅ READY",
            "message": "Seasonal check skipped. Proceed.",
            "season_rank": "neutral",
        }


def _check_lot_size_condition_wishlist(symbol: str, eq_qty: float = 0) -> dict:
    """
    CONDITION 4: Lot Size Check (for Wishlist)
    Verifies F&O lot exists AND user has enough shares.
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
                    "message": "No F&O contracts available for this stock",
                    "lot_exists": False,
                    "lot_size": 0,
                    "qty_available": int(eq_qty),
                }

            lot_size = int(rows[0].lot_size)
            qty_available = int(eq_qty)

            # Check lot completeness
            if qty_available >= lot_size:
                complete_lots = qty_available // lot_size
                return {
                    "status": "✅ PASS",
                    "message": f"F&O available ({lot_size} lot). You have {complete_lots} complete lot(s).",
                    "lot_exists": True,
                    "lot_size": lot_size,
                    "qty_available": qty_available,
                }
            else:
                return {
                    "status": "⚠️ FAIL",
                    "message": f"You have {qty_available} shares, need {lot_size} for 1 lot.",
                    "lot_exists": True,
                    "lot_size": lot_size,
                    "qty_available": qty_available,
                }
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[Wishlist LotSize] Error for {symbol}: {e}")
        return {
            "status": "⚠️ FAIL",
            "message": "Cannot verify F&O availability",
            "lot_exists": False,
            "lot_size": 0,
            "qty_available": int(eq_qty),
        }


def _evaluate_sell_call_conditions_wishlist(
    symbol: str,
    held: bool,
    avg_buy_price: float,
    spot: float,
    high_52w: float,
    eq_qty: float = 0,
) -> dict:
    """
    MAIN EVALUATION LOGIC (for Wishlist)
    Combines all 4 conditions into a SELL_CE verdict.

    Returns: {
        "can_sell_ce": True/False,
        "verdict": "✅ READY TO SELL CE" | "⚠️ AVOID SELLING CE" | "⏸️ WAIT FOR TRIGGER",
        "confidence_score": 1-5,
        "reason": str,
        "conditions": {...},
    }
    """
    # Evaluate each condition
    profit_check = _check_profit_condition_wishlist(held, avg_buy_price, spot)
    market_check = _check_market_condition_wishlist(spot, high_52w)
    seasonal_check = _check_seasonal_condition_wishlist(symbol)
    lot_check = _check_lot_size_condition_wishlist(symbol, eq_qty)

    # Count passes and fails
    all_checks = [profit_check, market_check, seasonal_check, lot_check]
    pass_count = sum(1 for c in all_checks if c["status"].startswith("✅"))
    fail_count = sum(1 for c in all_checks if c["status"].startswith("⚠️"))
    wait_count = sum(1 for c in all_checks if c["status"].startswith("⏸️"))

    # Decision logic
    if not held:
        # If you don't hold the stock, you can't sell CE
        verdict = "⚠️ AVOID SELLING CE"
        reason = "You don't hold this stock. Buy first, then sell CE."
        confidence = 1
        can_sell_ce = False

    elif lot_check["status"].startswith("⚠️"):
        # If lot doesn't exist or insufficient shares
        verdict = "⚠️ AVOID SELLING CE"
        reason = lot_check["message"]
        confidence = 1
        can_sell_ce = False

    elif profit_check["status"].startswith("⚠️"):
        # If in loss, AVOID
        verdict = "⚠️ AVOID SELLING CE"
        reason = profit_check["message"]
        confidence = 1
        can_sell_ce = False

    elif wait_count > 0:  # seasonal is WAIT
        # If WAIT condition, WAIT overall
        verdict = "⏸️ WAIT FOR TRIGGER"
        reason = seasonal_check["message"] + " Then sell CE."
        confidence = 2
        can_sell_ce = False

    elif pass_count == 4:
        # All 4 conditions pass
        verdict = "✅ READY TO SELL CE"
        reason = "All conditions favorable: profitable, market at ATH, good season, lot available. Sell CE now."
        confidence = 5
        can_sell_ce = True

    elif pass_count >= 2:
        # At least 2 conditions pass
        verdict = "✅ READY TO SELL CE"
        failed_reasons = [
            c["message"] for c in all_checks if c["status"].startswith("⚠️")
        ]
        reason = "Most conditions favorable. " + "; ".join(failed_reasons[:1])
        confidence = 3
        can_sell_ce = True

    else:
        # Multiple unfavorable conditions
        verdict = "⚠️ AVOID SELLING CE"
        failed_reasons = [
            c["message"] for c in all_checks if c["status"].startswith("⚠️")
        ]
        reason = "; ".join(failed_reasons[:2])
        confidence = 1
        can_sell_ce = False

    return {
        "can_sell_ce": can_sell_ce,
        "verdict": verdict,
        "reason": reason,
        "confidence_score": confidence,
        "conditions": {
            "profit": profit_check,
            "market": market_check,
            "seasonal": seasonal_check,
            "lot_size": lot_check,
        },
    }
