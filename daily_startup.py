"""
daily_startup.py
================
Run once each morning before market open.  Runs INDEPENDENTLY — no uvicorn needed.

  python daily_startup.py
  python daily_startup.py --check    (diagnostics only, no actions)

Steps:
  1. Setup logging
  2. Refresh 5paisa token  (token_data.json)
  3. Download & UPSERT ScripMaster directly via service (no API call)
  4. Refresh corporate actions for all canonical symbols
  5. Run dividend-adjustment detection for all users  (NEW)

Key fixes vs previous version:
  • ScripMaster is now downloaded DIRECTLY by calling the service function,
    not by hitting the FastAPI endpoint.  This means uvicorn does NOT need
    to be running.
  • ScripMaster uses ON DUPLICATE KEY UPDATE so rows are UPDATED not re-inserted.
  • Logging is set up at the top of __main__ so all logger.* calls work.
  • Path setup is robust whether run from project root or from backend/.
  • Dividend detection runs for every user and logs a summary.
"""

from __future__ import annotations

import sys
import os
import time
import json
import logging

# ─── Path setup ──────────────────────────────────────────────────────────────
# Support three common working directories:
#   project root  (where v4/ lives)        →  python daily_startup.py
#   backend/      (flat layout)             →  python daily_startup.py
#   anywhere else with PYTHONPATH set

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(_HERE, "backend")

# Insert paths so imports like "from backend.database import …" resolve correctly.
# If running from within backend/ already, _BACKEND won't exist — that's fine.
if os.path.isdir(_BACKEND):
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
else:
    # We're already inside backend/ or flat layout
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

# ─── Config ──────────────────────────────────────────────────────────────────
# API_BASE_URL is kept for optional HTTP-based steps but is NOT required.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8001/api/v1")
SCRIP_MASTER_TIMEOUT = int(os.getenv("SCRIP_MASTER_TIMEOUT", "300"))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Refresh 5paisa token
# ─────────────────────────────────────────────────────────────────────────────


def step1_token() -> None:
    logger.info("\n[1/5] Refreshing 5paisa token…")
    try:
        # Try both flat and nested import paths
        try:
            from backend.auth_manager import login_and_save
        except ImportError:
            from auth_manager import login_and_save  # type: ignore

        client = login_and_save()
        if client:
            logger.info("     OK  Token saved to token_data.json")
        else:
            logger.warning(
                "     WARN  login_and_save() returned None — check credentials"
            )
    except Exception as e:
        logger.error("     FAIL  Token refresh: %s", e, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Download & UPSERT ScripMaster (DIRECT — no uvicorn needed)
# ─────────────────────────────────────────────────────────────────────────────


def step2_scrip_master() -> None:
    logger.info("\n[2/5] Downloading ScripMaster (direct, no uvicorn needed)…")

    try:
        # Try to import the function from the correct module
        from backend.services.stock_master_service import (
            download_and_upsert_scrip_master,
        )

        download_fn = download_and_upsert_scrip_master
    except ImportError:
        try:
            from backend.services.stock_master_service import (
                download_and_upsert_scrip_master,
            )

            download_fn = download_and_upsert_scrip_master
        except ImportError:
            download_fn = None

    if download_fn is not None:
        logger.info("     Using service function (direct DB upsert)…")
        result = download_fn()
        inserted = result.get("inserted", 0)
        updated = result.get("updated", 0)
        errors = result.get("errors", 0)
        size = result.get("download_size", "?")
        logger.info(
            "     OK  inserted=%d  updated=%d  errors=%d  size=%s",
            inserted,
            updated,
            errors,
            size,
        )
        if errors > 0:
            logger.warning("     WARN  %d rows had errors (usually non-fatal)", errors)
        _verify_scrip_master_db()
        return

    # ── If direct import fails, only then fall back to HTTP ──
    logger.warning("     Service function not found — falling back to HTTP endpoint")
    _step2_via_api()


def _step2_via_api() -> None:
    """HTTP fallback — calls our own FastAPI endpoint (requires uvicorn running)."""
    import requests as _req

    url = f"{API_BASE_URL}/stock-master/download-from-5paisa"
    for attempt in range(1, 4):
        try:
            logger.info(
                "     HTTP %s (attempt %d, timeout=%ds)…",
                url,
                attempt,
                SCRIP_MASTER_TIMEOUT,
            )
            resp = _req.post(url, timeout=SCRIP_MASTER_TIMEOUT)
            if resp.status_code == 200:
                result = resp.json()
                logger.info(
                    "     OK  %s | inserted=%d | errors=%d | size=%s",
                    result.get("message", "Done"),
                    result.get("inserted", 0),
                    result.get("errors", 0),
                    result.get("download_size", "?"),
                )
                _verify_scrip_master_db()
                return
            else:
                logger.error("     FAIL HTTP %d: %s", resp.status_code, resp.text[:200])
        except _req.exceptions.ConnectionError:
            logger.error("     FAIL  Cannot connect to %s — is uvicorn running?", url)
        except _req.exceptions.Timeout:
            logger.error(
                "     FAIL  Timeout (%ds) — try: export SCRIP_MASTER_TIMEOUT=600",
                SCRIP_MASTER_TIMEOUT,
            )
        except Exception as e:
            logger.error("     FAIL  attempt %d: %s", attempt, e, exc_info=True)

        if attempt < 3:
            logger.info("     Retrying in 15 s…")
            time.sleep(15)

    logger.error("     FAIL  ScripMaster update failed after 3 attempts")


def _verify_scrip_master_db() -> None:
    """Log row counts from scrip_master_cache for a quick sanity check."""
    try:
        try:
            from backend.database import SessionLocal
        except ImportError:
            from backend.database import SessionLocal  # type: ignore

        from sqlalchemy import text

        db = SessionLocal()
        try:
            total = (
                db.execute(text("SELECT COUNT(*) FROM scrip_master_cache")).scalar()
                or 0
            )
            nse_eq = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM scrip_master_cache WHERE exch='N' AND exch_type='C'"
                    )
                ).scalar()
                or 0
            )
            nse_fno = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM scrip_master_cache WHERE exch='N' AND exch_type='D'"
                    )
                ).scalar()
                or 0
            )
            with_isin = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM scrip_master_cache "
                        "WHERE isin IS NOT NULL AND isin != '' AND exch='N' AND exch_type='C'"
                    )
                ).scalar()
                or 0
            )
            logger.info(
                "     DB  total=%d | NSE_EQ=%d | NSE_FNO=%d | with_isin=%d",
                total,
                nse_eq,
                nse_fno,
                with_isin,
            )
            if total < 50_000:
                logger.warning("     WARN  Row count seems low — check upsert logs")
        finally:
            db.close()
    except Exception as e:
        logger.error("     DB verify failed: %s", e, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Corporate actions for all canonical symbols
# ─────────────────────────────────────────────────────────────────────────────


def step3_corp_actions() -> None:
    logger.info("\n[3/5] Refreshing corporate actions…")

    try:
        from sqlalchemy import text

        try:
            from backend.database import SessionLocal
        except ImportError:
            from backend.database import SessionLocal  # type: ignore

        try:
            from backend.services.corp_actions_service import (
                fetch_nse_corp_actions,
                _normalise_action_type,
                _extract_details,
                _norm_date,
            )
        except ImportError:
            from corp_actions_service import (  # type: ignore
                fetch_nse_corp_actions,
                _normalise_action_type,
                _extract_details,
                _norm_date,
            )

    except Exception as e:
        logger.error("     FAIL  Import error: %s", e, exc_info=True)
        return

    db = SessionLocal()
    try:
        sym_rows = db.execute(
            text(
                "SELECT DISTINCT canonical_symbol FROM stock_master_mapping "
                "WHERE canonical_symbol IS NOT NULL AND canonical_symbol != ''"
            )
        ).fetchall()
        symbols = [
            r.canonical_symbol.strip().upper() for r in sym_rows if r.canonical_symbol
        ]

        if not symbols:
            logger.info("     No symbols in stock_master_mapping — skipping")
            return

        logger.info("     Fetching NSE corp actions for %d symbols…", len(symbols))

        # Get an NSE session (try service function first, fall back to local)
        try:
            try:
                from backend.services.nse_data_service import _get_session
            except ImportError:
                from backend.services.nse_data_service import _get_session  # type: ignore
            session = _get_session()
        except Exception:
            # Fallback: build session ourselves (same as corp_actions_service._nse_session)
            import requests as _req

            session = _req.Session()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.nseindia.com/",
                }
            )
            try:
                session.get("https://www.nseindia.com/", timeout=10)
                time.sleep(1)
            except Exception:
                pass

        fetched: dict[str, list] = {}
        failed = 0
        for i, sym in enumerate(symbols, 1):
            try:
                fetched[sym] = fetch_nse_corp_actions(sym, session) or []
            except Exception as e:
                logger.warning("     WARN  %s: %s", sym, e)
                fetched[sym] = []
                failed += 1
            time.sleep(0.4)
            if i % 25 == 0:
                logger.info("     … %d/%d symbols fetched", i, len(symbols))

        logger.info(
            "     NSE fetch done — ok=%d  errors=%d", len(symbols) - failed, failed
        )

        user_rows = db.execute(text("SELECT id FROM users")).fetchall()
        user_ids = [r.id for r in user_rows]
        if not user_ids:
            logger.info("     No users found — skipping DB write")
            return

        upserted = 0
        for uid in user_ids:
            for sym, actions in fetched.items():
                for r in actions:
                    purpose = str(r.get("purpose") or r.get("subject") or "").strip()
                    if not purpose:
                        continue
                    action_type = _normalise_action_type(purpose)
                    ex_date = _norm_date(
                        r.get("exDate") or r.get("exdate") or r.get("ex_date") or ""
                    )
                    if not ex_date:
                        continue
                    rec_date = _norm_date(
                        r.get("recordDate") or r.get("record_date") or ""
                    )
                    details = _extract_details(
                        purpose, action_type, str(r.get("faceVal") or "")
                    )
                    comp = str(r.get("comp") or r.get("companyName") or sym)
                    isin = str(r.get("isin") or "")
                    try:
                        db.execute(
                            text("""
                            INSERT INTO corporate_actions
                              (user_id, symbol, isin, company_name, action_type,
                               ex_date, record_date, action_details, source, is_verified, notes)
                            VALUES
                              (:uid, :sym, :isin, :comp, :act,
                               :ex,  :rec,  :det, 'nse_api', 1, :purp)
                            ON DUPLICATE KEY UPDATE
                              isin           = COALESCE(NULLIF(VALUES(isin),''), isin),
                              company_name   = COALESCE(NULLIF(VALUES(company_name),''), company_name),
                              record_date    = COALESCE(NULLIF(VALUES(record_date),''), record_date),
                              action_details = VALUES(action_details),
                              updated_at     = NOW()
                        """),
                            {
                                "uid": uid,
                                "sym": sym,
                                "isin": isin,
                                "comp": comp,
                                "act": action_type,
                                "ex": ex_date,
                                "rec": rec_date,
                                "det": json.dumps(details),
                                "purp": purpose,
                            },
                        )
                        upserted += 1
                    except Exception:
                        db.rollback()

        db.commit()
        logger.info(
            "     OK  %d symbols × %d users → %d rows upserted",
            len(symbols),
            len(user_ids),
            upserted,
        )

    except Exception as e:
        db.rollback()
        logger.error("     FAIL  Corp actions: %s", e, exc_info=True)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Dividend adjustment detection for all users  (NEW)
# ─────────────────────────────────────────────────────────────────────────────


def step4_dividend_detection() -> None:
    """
    Runs detect_pending_adjustments() for every user.
    By the time users log in, PENDING records are already in the DB so
    the UI notification appears instantly without a spinner.
    """
    logger.info("\n[4/5] Running dividend adjustment detection for all users…")

    try:
        from sqlalchemy import text

        try:
            from backend.database import SessionLocal
        except ImportError:
            from backend.database import SessionLocal  # type: ignore

        try:
            from backend.services.fno_dividend_adjustment_service import (
                detect_pending_adjustments,
            )
        except ImportError:
            try:
                from backend.services.fno_dividend_adjustment_service import detect_pending_adjustments  # type: ignore
            except ImportError:
                from fno_dividend_adjustment_service import detect_pending_adjustments  # type: ignore

    except Exception as e:
        logger.error("     FAIL  Import error: %s", e, exc_info=True)
        return

    try:
        from sqlalchemy import text

        try:
            from backend.database import SessionLocal
        except ImportError:
            from backend.database import SessionLocal  # type: ignore

        db = SessionLocal()
        try:
            user_rows = db.execute(text("SELECT id FROM users")).fetchall()
            user_ids = [r.id for r in user_rows]
        finally:
            db.close()
    except Exception as e:
        logger.error("     FAIL  Could not fetch user list: %s", e, exc_info=True)
        return

    if not user_ids:
        logger.info("     No users found — skipping")
        return

    total_pending = 0
    imminent = []  # adjustments with ex_date within 3 days

    import datetime

    today = datetime.date.today()

    for uid in user_ids:
        try:
            pending = detect_pending_adjustments(uid)
            n = len(pending)
            if n:
                total_pending += n
                logger.info("     user_id=%d  →  %d pending adjustment(s)", uid, n)
                for adj in pending:
                    try:
                        ex_dt = datetime.date.fromisoformat(
                            str(adj.get("ex_date", ""))[:10]
                        )
                        if 0 <= (ex_dt - today).days <= 3:
                            imminent.append((uid, adj))
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("     user_id=%d  detect error: %s", uid, e)

    if total_pending == 0:
        logger.info("     OK  No pending dividend adjustments found across all users")
    else:
        logger.info("     OK  Total pending adjustments: %d", total_pending)

    if imminent:
        logger.warning("     ⚠️  IMMINENT ADJUSTMENTS (ex_date within 3 days):")
        for uid, adj in imminent:
            logger.warning(
                "         user_id=%-4d  %s %s  ex_date=%s  div=₹%.2f  "
                "old_strike=%.0f → new_strike=%.0f",
                uid,
                adj.get("underlying", "?"),
                adj.get("instrument_type", "?"),
                adj.get("ex_date", "?"),
                adj.get("dividend_amount", 0),
                adj.get("old_strike", 0),
                adj.get("new_strike", 0),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Clear spot-price cache  (NEW)
# ─────────────────────────────────────────────────────────────────────────────


def step5_clear_caches() -> None:
    """Clear module-level caches that should reset each trading day."""
    logger.info("\n[5/5] Clearing daily caches…")
    try:
        try:
            from backend.services.fno_dividend_adjustment_service import (
                clear_spot_cache,
            )
        except ImportError:
            try:
                from backend.services.fno_dividend_adjustment_service import clear_spot_cache  # type: ignore
            except ImportError:
                from fno_dividend_adjustment_service import clear_spot_cache  # type: ignore
        clear_spot_cache()
        logger.info("     OK  Spot price cache cleared")
    except Exception as e:
        logger.warning("     WARN  Could not clear spot cache: %s", e)

    try:
        # Clear market-status holiday cache so fresh holidays are fetched
        try:
            from backend.services import market_status as _ms
        except ImportError:
            try:
                import market_status as _ms  # type: ignore
            except ImportError:
                _ms = None
        if _ms and hasattr(_ms, "_holiday_cache"):
            _ms._holiday_cache = None
            _ms._holiday_cache_time = None
            logger.info("     OK  Market-status holiday cache cleared")
    except Exception as e:
        logger.warning("     WARN  Could not clear market-status cache: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics — python daily_startup.py --check
# ─────────────────────────────────────────────────────────────────────────────


def run_diagnostics() -> None:
    """Print DB state and unresolved symbols.  No writes."""
    logger.info("\n=== Diagnostic Check ===")

    try:
        from sqlalchemy import text

        try:
            from backend.database import SessionLocal
        except ImportError:
            from backend.database import SessionLocal  # type: ignore
    except Exception as e:
        logger.error("Cannot import DB: %s", e)
        return

    db = SessionLocal()
    try:
        tables = [
            "scrip_master_cache",
            "stock_master_mapping",
            "unmatched_symbols",
            "transactions",
            "holdings",
            "fno_transactions",
            "fno_synthetic_transactions",
            "corporate_actions",
            "fno_dividend_adjustments",
        ]
        logger.info("\nTable row counts:")
        for t in tables:
            try:
                n = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                logger.info("  %-40s %8d", t, n)
            except Exception:
                logger.info("  %-40s  (missing)", t)

        rows = db.execute(
            text(
                "SELECT exch, exch_type, COUNT(*) as cnt FROM scrip_master_cache "
                "GROUP BY exch, exch_type ORDER BY cnt DESC"
            )
        ).fetchall()
        if rows:
            logger.info("\nScrip master breakdown:")
            for r in rows:
                logger.info(
                    "  exch=%-3s exch_type=%-5s  %8d rows", r.exch, r.exch_type, r.cnt
                )

        # Dividend adjustments summary
        adj_rows = db.execute(
            text(
                "SELECT status, COUNT(*) as cnt FROM fno_dividend_adjustments GROUP BY status"
            )
        ).fetchall()
        if adj_rows:
            logger.info("\nDividend adjustments:")
            for r in adj_rows:
                logger.info("  status=%-15s  %d", r.status, r.cnt)

        unresolved = db.execute(
            text(
                "SELECT raw_symbol, broker, qty FROM unmatched_symbols "
                "WHERE resolved=0 ORDER BY qty DESC LIMIT 30"
            )
        ).fetchall()
        if unresolved:
            logger.info("\nUnresolved symbols (%d shown):", len(unresolved))
            for r in unresolved:
                logger.info("  %-30s %-10s %-8s", r.raw_symbol, r.broker, str(r.qty))
        else:
            logger.info("\nNo unresolved symbols — all good")

    except Exception as e:
        logger.error("Diagnostic error: %s", e, exc_info=True)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import datetime as _dt

    # ── Setup logging FIRST so every logger.* call below works ───────────────
    # Try to use the project's logging_config if available; else basic config.
    try:
        try:
            from backend.logging_config import setup_logging
        except ImportError:
            from backend.logging_config import setup_logging  # type: ignore
        setup_logging()
    except Exception:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    if "--check" in sys.argv:
        run_diagnostics()
        sys.exit(0)

    logger.info("=" * 60)
    logger.info(
        "daily_startup.py  %s", _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    logger.info("=" * 60)

    step5_clear_caches()  # clear stale caches first
    step1_token()
    step2_scrip_master()
    step3_corp_actions()
    step4_dividend_detection()

    logger.info("\n=== Done ===\n")
