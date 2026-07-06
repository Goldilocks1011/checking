# """
# stock_master_service.py — v3
# =============================
# Key fixes vs v2:
#   1. _BROKER_ALIASES table: maps 5paisa display names → NSE canonical tickers
#      Covers all known truncated/abbreviated names that ScripMaster exact-match misses.
#   2. _SKIP_SYMBOLS set: bonds, rights entitlements, de-listed instruments that
#      have no NSE equity ISIN — auto_populate silently skips them (no unmatched entry).
#   3. _resolve_isin tries alias → canonical before ScripMaster DB/CSV/NSE API.
#   4. Better NSE ticker stored in user_stock_symbol_mapping.symbol (canonical ticker,
#      not broker display name) so JOIN on holdings always works.

# Resolution priority (unchanged):
#   ISIN:     ScripMaster DB → ScripMaster CSV → NSE API
#   F&O info: ScripMaster DB → ScripMaster CSV (engine_price_fetch) → NSE API
# """
# from __future__ import annotations

# from collections import defaultdict
# from sqlalchemy import text
# from database import SessionLocal
# from services.nse_data_service import fetch_isin_from_nse, get_fno_info_from_nse, search_nse_symbol


# # ─────────────────────────────────────────────────────────────────────────────
# # 5paisa broker display name → (NSE canonical ticker, ISIN)
# # Add new entries whenever auto_populate leaves something unresolved.
# # ISIN is definitive; canonical ticker is what NSE uses.
# # ─────────────────────────────────────────────────────────────────────────────
# _BROKER_ALIASES: dict[str, tuple[str, str]] = {
#     # 5paisa display name (UPPER)  →  (NSE ticker, ISIN)
#     "MAHA. SCOOTERS":              ("MAHSCOOTER",   "INE064A01026"),
#     "MAHA.SCOOTERS":               ("MAHSCOOTER",   "INE064A01026"),
#     "MAHARASHTRA SCOOTERS":        ("MAHSCOOTER",   "INE064A01026"),

#     "BAJAJ HLDG. & INV.":          ("BAJAJHLDNG",   "INE118J01026"),
#     "BAJAJ HLDG. & INV":           ("BAJAJHLDNG",   "INE118J01026"),
#     "BAJAJ HLDG & INV":            ("BAJAJHLDNG",   "INE118J01026"),
#     "BAJAJ HLDG.& INV.":           ("BAJAJHLDNG",   "INE118J01026"),
#     "BAJAJ HOLDINGS & INVESTMENT": ("BAJAJHLDNG",   "INE118J01026"),

#     "PUNJ. NATIONLBAK":            ("PNB",          "INE160A01022"),
#     "PUNJ.NATIONLBAK":             ("PNB",          "INE160A01022"),
#     "PUNJAB NATIONAL BANK":        ("PNB",          "INE160A01022"),
#     "PUNJ NATIONAL BANK":          ("PNB",          "INE160A01022"),

#     "SBI":                         ("SBIN",         "INE062A01020"),
#     "STATE BANK OF INDIA":         ("SBIN",         "INE062A01020"),

#     "LIC":                         ("LICI",         "INE0J1Y01017"),
#     "LIFE INSURANCE CORP":         ("LICI",         "INE0J1Y01017"),
#     "LIFE INSURANCE CORPORATION":  ("LICI",         "INE0J1Y01017"),

#     "NEXUS REIT":                  ("NEXUSSELECT",  "INE0J4401026"),
#     "NEXUS SELECT TRUST":          ("NEXUSSELECT",  "INE0J4401026"),

#     "HDFC BANK":                   ("HDFCBANK",     "INE040A01034"),
#     "HDFC":                        ("HDFCBANK",     "INE040A01034"),  # post-merger HDFC → HDFCBANK

#     "ICICI BANK":                  ("ICICIBANK",    "INE090A01021"),  # equity ISIN, NOT bond
#     "ICICI BANK LTD":              ("ICICIBANK",    "INE090A01021"),

#     "TATA MOTORS":                 ("TATAMOTORS",   "INE155A01022"),  # ordinary shares
#     "TATA MOTORS LTD":             ("TATAMOTORS",   "INE155A01022"),

#     "TATA MOTORS PASSENGE":        ("TATAMOTORS",   "INE155A01022"),  # 5paisa truncates
#     "TATA MOTORS PASSENGER":       ("TATAMOTORS",   "INE155A01022"),

#     "WIPRO":                       ("WIPRO",        "INE075A01022"),
#     "WIPRO LTD":                   ("WIPRO",        "INE075A01022"),

#     "BSE":                         ("BSE",          "INE118H01025"),
#     "BSE LTD":                     ("BSE",          "INE118H01025"),
#     "BOMBAY STOCK EXCHANGE":       ("BSE",          "INE118H01025"),

#     "ITC":                         ("ITC",          "INE154A01025"),
#     "ITC LTD":                     ("ITC",          "INE154A01025"),

#     "ITC HOTELS":                  ("ITCHOTELS",    "INE379A01028"),

#     "ENGINEERS INDIA":             ("ENGINEERSIN",  "INE510A01028"),
#     "ENGINEERS INDIA LTD":         ("ENGINEERSIN",  "INE510A01028"),
#     "ENGINEERS INDIA LIMITED":     ("ENGINEERSIN",  "INE510A01028"),

#     "GUJARAT PIPAVAV PORT":        ("GPPL",         "INE517F01014"),
#     "GUJARAT PIPAVAV":             ("GPPL",         "INE517F01014"),

#     "HOUSING & URBAN DEV.":        ("HUDCO",        "INE031A01017"),
#     "HOUSING & URBAN DEV":         ("HUDCO",        "INE031A01017"),
#     "HOUSING AND URBAN DEVELOPMENT": ("HUDCO",      "INE031A01017"),

#     "NOIDA TOLL BRIDGE":           ("NOIDATOLL",    "INE781B01015"),
#     "NOIDA TOLL BRIDGE CO":        ("NOIDATOLL",    "INE781B01015"),

#     "ELLENBARRIE INDL.":           ("ELLENBARRIE",  "INE731A01020"),
#     "ELLENBARRIE INDL":            ("ELLENBARRIE",  "INE731A01020"),
#     "ELLENBARRIE INDUSTRIES":      ("ELLENBARRIE",  "INE731A01020"),

#     "GLOBAL HEALTH":               ("MEDANTA",      "INE474Q01031"),

#     "JIO FINANCIAL SERV.":         ("JIOFIN",       "INE758E01017"),
#     "JIO FINANCIAL SERVICES":      ("JIOFIN",       "INE758E01017"),

#     "BEML LAND ASSETS":            ("BEMLLAND",     "INE0N7W01012"),

#     "THE UNITED NILGIRI":          ("UNITEDTEA",    "INE458F01011"),
#     "UNITED NILGIRI TEA":          ("UNITEDTEA",    "INE458F01011"),

#     "WONDERLA HOLIDAYS":           ("WONDERLA",     "INE066O01014"),

#     "ADITYA BIRLA FASHION":        ("ABFRL",        "IN9647O01019"),
#     "ADITYA BIRLA FASHION & RETAIL": ("ABFRL",      "IN9647O01019"),

#     "BAJAJ HOUSING FIN.":          ("BAJAJHFL",     "INE377Y01014"),
#     "BAJAJ HOUSING FINANCE":       ("BAJAJHFL",     "INE377Y01014"),

#     "SATIA INDUSTRIES":            ("SATIA",        "INE170E01023"),

#     "SYNGENE INTL.":               ("SYNGENE",      "INE398R01022"),
#     "SYNGENE INTERNATIONAL":       ("SYNGENE",      "INE398R01022"),

#     "SHEELA FOAM":                 ("SFL",          "INE916U01025"),

#     "KOTAK MAHINDRA BANK":         ("KOTAKBANK",    "INE237A01036"),
#     "KOTAK BANK":                  ("KOTAKBANK",    "INE237A01036"),

#     "UPL":                         ("UPL",          "INE628A01036"),
#     "UPL LTD":                     ("UPL",          "INE628A01036"),

#     "HEMISPHERE PROPERTIE":        ("HEMIPROP",     "INE0AJG01018"),
#     "HEMISPHERE PROPERTIES":       ("HEMIPROP",     "INE0AJG01018"),

#     "NMDC STEEL":                  ("NMDCSTEEL",    "INE0NNS01018"),

#     "BAJAJ AUTO":                  ("BAJAJ-AUTO",   "INE917I01010"),
#     "BAJAJ AUTO LTD":              ("BAJAJ-AUTO",   "INE917I01010"),

#     "BANK OF BARODA":              ("BANKBARODA",   "INE028A01039"),
#     "BANK OF BARODA LTD":          ("BANKBARODA",   "INE028A01039"),

#     "CANARA BANK":                 ("CANBK",        "INE476A01022"),

#     "HERO MOTOCORP":               ("HEROMOTOCO",   "INE158A01026"),
#     "HERO MOTO CORP":              ("HEROMOTOCO",   "INE158A01026"),

#     "ICICI PRUDENTIAL AMC":        ("ICICI PRUDENTIAL AMC", ""),  # no single ISIN shortcut, let ScripMaster resolve
#     "ICICI PRUDENTIAL ASSET":      ("ICICIPRAMC",   "INE860H01022"),
# }

# # ─────────────────────────────────────────────────────────────────────────────
# # Instruments to silently skip — bonds, rights entitlements, de-listed
# # Do NOT add to unmatched_symbols; they clutter the UI.
# # ─────────────────────────────────────────────────────────────────────────────
# _SKIP_SYMBOLS: set[str] = {
#     # Bonds
#     "IIFCBOND16", "IDFCBOND12", "DEWAN HOUSING", "DEWANHOUSING",
#     "IIFCBOND", "IDFCBOND",
#     # Rights entitlements (de-listed after allotment)
#     "BHARATIWIN",       # Bharti Airtel rights entitlement — no equity ISIN
#     "BHARTIARTLRE",
#     # Add more as you encounter them:
#     # "XXXXBOND12", "YYYY-RE",
# }


# # ─────────────────────────────────────────────────────────────────────────────
# # ISIN resolution — alias table first, then ScripMaster DB, CSV, NSE API
# # ─────────────────────────────────────────────────────────────────────────────

# def _alias_lookup(sym: str) -> tuple[str, str] | None:
#     """
#     Check _BROKER_ALIASES for this symbol (uppercased, stripped).
#     Returns (canonical_ticker, isin) or None.
#     """
#     s = sym.strip().upper()
#     # Direct match
#     if s in _BROKER_ALIASES:
#         return _BROKER_ALIASES[s]
#     # Try without trailing punctuation (e.g. "MAHA. SCOOTERS." → "MAHA. SCOOTERS")
#     s2 = s.rstrip(".")
#     if s2 in _BROKER_ALIASES:
#         return _BROKER_ALIASES[s2]
#     return None


# def _resolve_isin(raw_sym: str, company: str = "") -> tuple[str, str]:
#     """
#     5-step ISIN resolution. Returns (isin, canonical_ticker).

#     Step 0: Broker alias table (instant, most accurate for truncated names)
#     Step 1: ScripMaster DB — symbol_root / name exact match + series='EQ' filter
#     Step 2: ScripMaster CSV in-memory (legacy)
#     Step 3: Company name via ScripMaster
#     Step 4: Live NSE API (slow, last resort)
#     """
#     s = str(raw_sym or "").strip().upper()
#     if not s:
#         return "", ""

#     # ── Step 0: Alias table ───────────────────────────────────────────────────
#     alias = _alias_lookup(s)
#     if alias:
#         canonical, isin = alias
#         if isin:
#             return isin, canonical
#         # alias has canonical but no hardcoded ISIN — resolve ISIN via DB/CSV
#         # using the canonical ticker (more likely to match ScripMaster SymbolRoot)
#         s = canonical.upper()  # fall through with canonical

#     # Also check company name against alias table
#     if company:
#         alias2 = _alias_lookup(company)
#         if alias2:
#             canonical, isin = alias2
#             if isin:
#                 return isin, canonical
#             s_try = canonical.upper()
#         else:
#             s_try = s
#     else:
#         s_try = s

#     # ── Step 1: ScripMaster DB ────────────────────────────────────────────────
#     try:
#         from services.scrip_master_db import is_db_populated, query_isin
#         if is_db_populated():
#             isin = query_isin(s_try)
#             if isin:
#                 canonical = _ticker_from_isin(isin) or s_try
#                 return isin, canonical
#             # Also try canonical form
#             from services.symbol_resolver import get_canonical
#             can = get_canonical(s_try)
#             if can and can != s_try:
#                 isin = query_isin(can)
#                 if isin:
#                     return isin, can
#     except Exception as e:
#         logger.info(f"[StockMaster] DB ISIN lookup error: {e}")

#     # ── Step 2: ScripMaster CSV in-memory ─────────────────────────────────────
#     try:
#         from services.isin_resolver import resolve_isin as csv_resolve, isin_to_canonical
#         isin = csv_resolve(s_try)
#         if isin:
#             can = isin_to_canonical(isin) or s_try
#             return isin, can
#         from services.symbol_resolver import get_canonical
#         can = get_canonical(s_try)
#         if can and can != s_try:
#             isin = csv_resolve(can)
#             if isin:
#                 return isin, can
#     except Exception as e:
#         logger.info(f"[StockMaster] CSV ISIN lookup error: {e}")

#     # ── Step 3: Company name via ScripMaster ──────────────────────────────────
#     if company and len(company.strip()) > 4:
#         co_up = company.strip().upper()
#         try:
#             from services.scrip_master_db import is_db_populated, query_isin
#             if is_db_populated():
#                 isin = query_isin(co_up)
#                 if isin:
#                     canonical = _ticker_from_isin(isin) or co_up
#                     return isin, canonical
#         except Exception:
#             pass
#         try:
#             from services.isin_resolver import resolve_isin as csv_resolve, isin_to_canonical
#             isin = csv_resolve(co_up)
#             if isin:
#                 can = isin_to_canonical(isin) or co_up
#                 return isin, can
#         except Exception:
#             pass

#     # ── Step 4: NSE API (last resort) ─────────────────────────────────────────
#     try:
#         from services.symbol_resolver import get_canonical
#         can = get_canonical(s_try)
#         isin = fetch_isin_from_nse(can)
#         if not isin and can != s_try:
#             isin = fetch_isin_from_nse(s_try)
#         if not isin and company and len(company.strip()) > 6:
#             results = search_nse_symbol(company)
#             if results:
#                 hit_sym = results[0].get("symbol", "")
#                 if hit_sym:
#                     isin = fetch_isin_from_nse(hit_sym)
#                     can = hit_sym
#         if isin:
#             return isin, can
#     except Exception as e:
#         logger.info(f"[StockMaster] NSE API ISIN lookup error: {e}")

#     return "", ""


# def _ticker_from_isin(isin: str) -> str:
#     """Given ISIN, return canonical NSE ticker from scrip_master_cache."""
#     if not isin:
#         return ""
#     try:
#         from services.scrip_master_db import is_db_populated
#         from database import SessionLocal
#         from sqlalchemy import text as _text
#         if is_db_populated():
#             db = SessionLocal()
#             try:
#                 row = db.execute(
#                     _text("""SELECT symbol_root FROM scrip_master_cache
#                               WHERE isin = :isin AND exch='N' AND exch_type='C'
#                                 AND series = 'EQ'
#                                 AND symbol_root != ''
#                               LIMIT 1"""),
#                     {"isin": isin}
#                 ).first()
#                 if row and row.symbol_root:
#                     return row.symbol_root.strip().upper()
#             finally:
#                 db.close()
#     except Exception:
#         pass
#     try:
#         from services.isin_resolver import isin_to_canonical
#         return isin_to_canonical(isin)
#     except Exception:
#         pass
#     return ""


# def _resolve_fno(raw_sym: str) -> tuple[bool, int]:
#     """
#     F&O availability + lot size resolution.
#     Step 1: ScripMaster DB → Step 2: CSV → Step 3: NSE API
#     """
#     s = str(raw_sym or "").strip().upper()

#     # ── Step 1: DB ────────────────────────────────────────────────────────────
#     try:
#         from services.scrip_master_db import is_db_populated, query_fno_info
#         if is_db_populated():
#             fno, lot = query_fno_info(s)
#             if fno and lot > 1:
#                 return True, lot
#             from services.symbol_resolver import get_canonical
#             can = get_canonical(s)
#             if can and can != s:
#                 fno, lot = query_fno_info(can)
#                 if fno and lot > 1:
#                     return True, lot
#     except Exception as e:
#         logger.info(f"[StockMaster] DB F&O lookup error: {e}")

#     # ── Step 2: ScripMaster CSV (engine_price_fetch) ──────────────────────────
#     try:
#         from services.engine_price_fetch import get_fno_info
#         fno, lot = get_fno_info(s)
#         if fno and lot > 1:
#             return True, lot
#         from services.symbol_resolver import get_canonical
#         can = get_canonical(s)
#         if can and can != s:
#             fno, lot = get_fno_info(can)
#             if fno and lot > 1:
#                 return True, lot
#     except Exception as e:
#         logger.info(f"[StockMaster] CSV F&O lookup error: {e}")

#     # ── Step 3: NSE API ───────────────────────────────────────────────────────
#     try:
#         return get_fno_info_from_nse(s)
#     except Exception as e:
#         logger.info(f"[StockMaster] NSE F&O lookup error: {e}")
#         return False, 0


# # ─────────────────────────────────────────────────────────────────────────────
# # auto_populate
# # ─────────────────────────────────────────────────────────────────────────────

# def auto_populate(user_id: int) -> dict:
#     from services.symbol_resolver import get_canonical

#     db = SessionLocal()
#     try:
#         rows = db.execute(
#             text("""
#                 SELECT symbol, MAX(company_name) as company_name, broker
#                 FROM transactions
#                 WHERE user_id = :uid AND segment = 'EQ'
#                 GROUP BY symbol, broker
#             """),
#             {"uid": user_id}
#         ).fetchall()

#         added = updated = unmatched = fno_set = skipped = 0

#         for r in rows:
#             raw_sym = str(r.symbol or "").strip()
#             company = str(r.company_name or raw_sym).strip()
#             broker  = str(r.broker or "").strip()

#             if not raw_sym or raw_sym.upper() in ("NAN", "NONE", ""):
#                 continue

#             # ── Skip known bonds / rights entitlements ────────────────────────
#             if raw_sym.upper() in _SKIP_SYMBOLS:
#                 logger.info(f"[AutoPopulate] Skipping non-equity instrument: {raw_sym}")
#                 skipped += 1
#                 continue

#             # ── Canonical normalisation (safe — no prefix guessing) ───────────
#             canonical_sym = get_canonical(raw_sym)

#             # ── ISIN resolution ───────────────────────────────────────────────
#             isin, resolved_canonical = _resolve_isin(canonical_sym, company)
#             if not isin and canonical_sym != raw_sym:
#                 isin, resolved_canonical = _resolve_isin(raw_sym, company)

#             # Use resolved canonical if available, else fall back
#             if resolved_canonical:
#                 canonical_sym = resolved_canonical

#             if not isin:
#                 # Mark as unmatched
#                 exists = db.execute(
#                     text("SELECT id FROM unmatched_symbols WHERE user_id=:uid AND broker=:br AND raw_symbol=:sym"),
#                     {"uid": user_id, "br": broker, "sym": raw_sym}
#                 ).first()
#                 if not exists:
#                     db.execute(
#                         text("INSERT INTO unmatched_symbols (user_id, broker, raw_symbol, company_name) VALUES (:uid,:br,:sym,:co)"),
#                         {"uid": user_id, "br": broker, "sym": raw_sym, "co": company}
#                     )
#                     unmatched += 1
#                 continue

#             # ── Ensure stock_master_mapping has this ISIN ─────────────────────
#             existing_master = db.execute(
#                 text("SELECT isin, fno_available, lot_size FROM stock_master_mapping WHERE isin=:isin"),
#                 {"isin": isin}
#             ).first()

#             fno_avail, lot_sz = _resolve_fno(canonical_sym)

#             if not existing_master:
#                 db.execute(
#                     text("""INSERT INTO stock_master_mapping
#                             (isin, standard_name, canonical_symbol, fno_available, lot_size)
#                             VALUES (:isin, :std, :can, :fno, :lot)"""),
#                     {"isin": isin, "std": company, "can": canonical_sym,
#                      "fno": 1 if fno_avail else 0, "lot": lot_sz}
#                 )
#                 added += 1
#             else:
#                 old_lot = int(existing_master.lot_size or 0)
#                 old_fno = bool(existing_master.fno_available)
#                 # Always update canonical_symbol if we now have a better one
#                 db.execute(
#                     text("""UPDATE stock_master_mapping
#                             SET canonical_symbol = COALESCE(NULLIF(canonical_symbol,''), :can),
#                                 updated_at = NOW()
#                             WHERE isin = :isin"""),
#                     {"can": canonical_sym, "isin": isin}
#                 )
#                 if (fno_avail and lot_sz > 1) and (not old_fno or old_lot <= 1):
#                     db.execute(
#                         text("""UPDATE stock_master_mapping
#                                 SET fno_available=:fno, lot_size=:lot, updated_at=NOW()
#                                 WHERE isin=:isin"""),
#                         {"fno": 1, "lot": lot_sz, "isin": isin}
#                     )
#                     fno_set += 1

#             # ── Upsert per-user-broker symbol mapping ─────────────────────────
#             # Store the CANONICAL ticker (not broker display name) so the
#             # holdings JOIN always works regardless of how 5paisa spelled it.
#             # We also keep raw_sym accessible via the symbol_normalisation table.
#             db.execute(
#                 text("""INSERT INTO user_stock_symbol_mapping (user_id, isin, broker, symbol)
#                        VALUES (:uid, :isin, :br, :sym)
#                        ON DUPLICATE KEY UPDATE symbol = VALUES(symbol)"""),
#                 {"uid": user_id, "isin": isin, "br": broker, "sym": raw_sym}
#                 # NOTE: keep raw_sym here so the holdings JOIN (UPPER match) finds it
#             )
#             updated += 1

#             # ── Cache normalisation ───────────────────────────────────────────
#             db.execute(
#                 text("INSERT IGNORE INTO symbol_normalisation (raw_symbol, canonical_symbol) VALUES (:raw, :can)"),
#                 {"raw": raw_sym.upper(), "can": canonical_sym}
#             )
#             if company.upper() != raw_sym.upper():
#                 db.execute(
#                     text("INSERT IGNORE INTO symbol_normalisation (raw_symbol, canonical_symbol) VALUES (:raw, :can)"),
#                     {"raw": company.upper(), "can": canonical_sym}
#                 )

#             # ── Mark previously unmatched as resolved ─────────────────────────
#             db.execute(
#                 text("""UPDATE unmatched_symbols SET resolved=1, resolved_isin=:isin
#                         WHERE user_id=:uid AND raw_symbol=:sym"""),
#                 {"isin": isin, "uid": user_id, "sym": raw_sym}
#             )

#         db.commit()
#         return {
#             "added": added, "updated": updated,
#             "unmatched": unmatched, "fno_enriched": fno_set,
#             "skipped_non_equity": skipped
#         }
#     except Exception as e:
#         db.rollback()
#         raise e
#     finally:
#         db.close()


# # ─────────────────────────────────────────────────────────────────────────────
# # update_custom_name  (unchanged)
# # ─────────────────────────────────────────────────────────────────────────────

# def update_custom_name(isin: str, new_name: str) -> bool:
#     db = SessionLocal()
#     try:
#         if new_name.strip():
#             db.execute(
#                 text("UPDATE stock_master_mapping SET user_custom_name=:name, updated_at=NOW() WHERE isin=:isin"),
#                 {"name": new_name.strip(), "isin": isin}
#             )
#         else:
#             db.execute(
#                 text("UPDATE stock_master_mapping SET user_custom_name=NULL, updated_at=NOW() WHERE isin=:isin"),
#                 {"isin": isin}
#             )
#         db.commit()
#         return True
#     except Exception:
#         db.rollback()
#         return False
#     finally:
#         db.close()


# # ─────────────────────────────────────────────────────────────────────────────
# # get_user_stock_grid  (unchanged logic, added alias-based canonical display)
# # ─────────────────────────────────────────────────────────────────────────────

# def get_user_stock_grid(user_id: int) -> list[dict]:
#     db = SessionLocal()
#     try:
#         user_row = db.execute(
#             text("SELECT username FROM users WHERE id=:uid"), {"uid": user_id}
#         ).first()
#         username = user_row.username if user_row else "user"

#         rows = db.execute(
#             text("""
#                 SELECT
#                     h.symbol                                          AS raw_symbol,
#                     COALESCE(usm.isin, CONCAT('UNRESOLVED:', h.symbol)) AS display_isin,
#                     COALESCE(usm.isin, '')                            AS isin,
#                     usm.broker                                        AS mapped_broker,
#                     usm.symbol                                        AS mapped_symbol,
#                     h.quantity,
#                     h.company_name
#                 FROM holdings h
#                 LEFT JOIN user_stock_symbol_mapping usm
#                     ON usm.user_id = h.user_id
#                 AND UPPER(usm.symbol) = UPPER(h.symbol)
#                 WHERE h.user_id = :uid
#                   AND h.segment  = 'EQ'
#                 ORDER BY h.symbol
#             """),
#             {"uid": user_id}
#         ).fetchall()

#         if not rows:
#             return []

#         grouped: dict = defaultdict(lambda: {
#             "brokers": {},
#             "total_qty": 0.0,
#             "company_name": "",
#             "isin": "",
#             "is_unresolved": False,
#         })

#         for r in rows:
#             key  = r.display_isin
#             info = grouped[key]
#             info["company_name"] = r.company_name or r.raw_symbol
#             info["isin"]         = r.isin if r.isin else ""
#             if not r.isin:
#                 info["is_unresolved"] = True

#             broker = r.mapped_broker or "Unknown"
#             symbol = r.mapped_symbol or r.raw_symbol
#             qty    = float(r.quantity) if r.quantity else 0.0

#             if broker not in info["brokers"]:
#                 info["brokers"][broker] = {"symbol": symbol, "qty": 0.0}
#             info["brokers"][broker]["qty"] += qty
#             info["total_qty"] += qty

#         master_lookup: dict = {}
#         isins_to_fetch = [k for k, v in grouped.items() if v["isin"]]
#         if isins_to_fetch:
#             master_rows = db.execute(
#                 text("SELECT * FROM stock_master_mapping WHERE isin IN :isins"),
#                 {"isins": tuple(isins_to_fetch)}
#             ).fetchall()
#             for mr in master_rows:
#                 master_lookup[mr.isin] = mr

#         grid = []
#         for key, info in grouped.items():
#             isin   = info["isin"]
#             master = master_lookup.get(isin) if isin else None

#             standard_name = master.standard_name    if master else (info["company_name"] or key)
#             custom_name   = getattr(master, "user_custom_name", "") or ""
#             canonical     = master.canonical_symbol  if master else ""
#             fno_available = bool(master.fno_available) if master else False
#             lot_size      = int(master.lot_size or 0)  if master else 0
#             total_qty     = info["total_qty"]

#             row = {
#                 "isin":              isin if isin else "",
#                 "standard_name":     standard_name,
#                 "user_custom_name":  custom_name,
#                 "canonical_symbol":  canonical,
#                 "fno_available":     fno_available,
#                 "lot_size":          lot_size,
#                 "total_qty":         round(total_qty, 2),
#                 "resolved":          "No" if info["is_unresolved"] else "Yes",
#                 "updated_at":        str(master.updated_at) if master and hasattr(master, "updated_at") else "",
#             }

#             for broker, bdata in info["brokers"].items():
#                 row[f"{username}_{broker}_symbol"] = bdata["symbol"]
#                 row[f"{username}_{broker}_qty"]    = round(bdata["qty"], 2)

#             if fno_available and lot_size > 0 and total_qty > 0:
#                 row["pending_qty"] = (lot_size - total_qty % lot_size) % lot_size
#             else:
#                 row["pending_qty"] = 0

#             grid.append(row)

#         return grid
#     finally:
#         db.close()

"""
stock_master_service.py — v4
=============================
KEY CHANGES vs v3:

  ① _BROKER_ALIASES dict REMOVED entirely.
    Replaced by a 5-step resolution pipeline in _resolve_isin() that uses
    ScripMaster DB fuzzy matching instead of hardcoded stock-specific ISINs.
    Rationale: ISINs change after mergers; NSE tickers get renamed; lot sizes
    change quarterly.  A hardcoded dict silently gives wrong answers after any
    of these events with no warning.

  ② _SKIP_SYMBOLS set REMOVED.
    Replaced by classify_instrument() from scrip_master_db, which detects
    bonds / rights entitlements / ETFs / SGBs programmatically from ScripMaster
    series codes and keyword patterns.  A new instrument type (e.g. a new bond)
    is auto-detected without requiring a code deployment.

  ③ Resolution pipeline in _resolve_isin() is now:
      Step 1  ScripMaster DB — exact symbol_root / name match  (query_isin)
      Step 2  ScripMaster DB — normalized+fuzzy match          (query_isin_fuzzy)
      Step 3  ScripMaster CSV in-memory (legacy fallback for machines with file)
      Step 4  NSE API                                          (fetch_isin_from_nse)
      Step 5  → unmatched_symbols (only for genuine equity; bonds skipped silently)

  ④ auto_populate() calls classify_instrument() before resolution.
    Instruments classified as BOND / ETF / RIGHTS / SGB are silently skipped
    and never appear in the "unresolved symbols" UI.

  ⑤ _ABBREV dict in scrip_master_db.py handles generic abbreviation expansion
    (HLDG. → HOLDINGS, MAHA. → MAHARASHTRA, etc.) — those are language-level
    normalizations, NOT stock-specific hardcoding.
"""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import text
from database import SessionLocal
from services.nse_data_service import fetch_isin_from_nse, get_fno_info_from_nse, search_nse_symbol

import logging
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ISIN resolution — 5-step pipeline (no hardcoded aliases)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_isin(raw_sym: str, company: str = "") -> tuple[str, str]:
    """
    Resolve raw broker symbol → (isin, canonical_ticker).

    Resolution pipeline:
      Step 1  ScripMaster DB exact match (symbol_root / name / scrip_data)
              via query_isin()
      Step 2  ScripMaster DB normalized+fuzzy match (strips abbreviations,
              LIKE prefix scan) via query_isin_fuzzy()
      Step 3  ScripMaster CSV in-memory (legacy; only present on the dev machine)
      Step 4  NSE symbol search API (slow, last resort)
      Step 5  Return ("", "") — caller adds to unmatched_symbols

    Returns (isin, canonical_ticker).
    Both strings are empty when resolution fails.
    """
    s = str(raw_sym or "").strip().upper()
    if not s:
        return "", ""

    # Shared helper: given an ISIN, return its canonical NSE ticker
    def _ticker(isin: str) -> str:
        return _ticker_from_isin(isin) or s

    # ── Step 1: ScripMaster DB — exact match ──────────────────────────────────
    try:
        from services.scrip_master_db import is_db_populated, query_isin
        if is_db_populated():
            # Try raw symbol first
            isin = query_isin(s)
            if not isin:
                # Try canonical form of raw symbol
                from services.symbol_resolver import get_canonical
                can = get_canonical(s)
                if can and can != s:
                    isin = query_isin(can)
            # Also try company name as a symbol (works for e.g. "RELIANCE INDUSTRIES")
            if not isin and company and len(company.strip()) > 4:
                isin = query_isin(company.strip().upper())
            if isin:
                return isin, _ticker(isin)
    except Exception as e:
        logger.error(f"[StockMaster] Step1 DB exact error: {e}", exc_info=True)

    # ── Step 2: ScripMaster DB — normalized + fuzzy match ─────────────────────
    # This handles broker-truncated / abbreviated names like:
    #   "BAJAJ HLDG. & INV." — normalized to "BAJAJ HOLDINGS INVESTMENT"
    #   "PUNJ. NATIONLBAK"   — normalized to "PUNJAB NATIONAL BANK"
    # No stock-specific ISINs hardcoded; works purely via ScripMaster data.
    try:
        from services.scrip_master_db import is_db_populated, query_isin_fuzzy
        if is_db_populated():
            isin = query_isin_fuzzy(s, company)
            if not isin and company:
                # Try with company as primary, symbol as secondary
                isin = query_isin_fuzzy(company, s)
            if isin:
                return isin, _ticker(isin)
    except Exception as e:
        logger.error(f"[StockMaster] Step2 fuzzy error: {e}", exc_info=True)

    # ── Step 3: ScripMaster CSV in-memory (legacy fallback) ───────────────────
    try:
        from services.isin_resolver import resolve_isin as csv_resolve, isin_to_canonical
        from services.symbol_resolver import get_canonical
        can = get_canonical(s)
        for try_sym in ([can, s] if can != s else [s]):
            isin = csv_resolve(try_sym)
            if isin:
                canonical = isin_to_canonical(isin) or try_sym
                return isin, canonical
        if company and len(company.strip()) > 4:
            isin = csv_resolve(company.strip().upper())
            if isin:
                canonical = isin_to_canonical(isin) or company
                return isin, canonical
    except Exception as e:
        logger.error(f"[StockMaster] Step3 CSV error: {e}", exc_info=True)

    # ── Step 4: NSE API (slow — called last to avoid rate limits) ─────────────
    try:
        from services.symbol_resolver import get_canonical
        can = get_canonical(s)
        isin = fetch_isin_from_nse(can)
        if not isin and can != s:
            isin = fetch_isin_from_nse(s)
        # Try NSE symbol search with company name as free text
        if not isin and company and len(company.strip()) > 6:
            results = search_nse_symbol(company)
            if results:
                hit_sym = results[0].get("symbol", "")
                if hit_sym:
                    isin = fetch_isin_from_nse(hit_sym)
                    can  = hit_sym
        if isin:
            return isin, can
    except Exception as e:
        logger.error(f"[StockMaster] Step4 NSE API error: {e}", exc_info=True)

    # ── Step 5: resolution failed ─────────────────────────────────────────────
    return "", ""


def _ticker_from_isin(isin: str) -> str:
    """Given ISIN, return canonical NSE ticker from scrip_master_cache."""
    if not isin:
        return ""
    try:
        from services.scrip_master_db import is_db_populated
        from database import SessionLocal
        from sqlalchemy import text as _text
        if is_db_populated():
            db = SessionLocal()
            try:
                row = db.execute(
                    _text("""SELECT symbol_root FROM scrip_master_cache
                              WHERE isin = :isin AND exch='N' AND exch_type='C'
                                AND series = 'EQ'
                                AND symbol_root != ''
                              LIMIT 1"""),
                    {"isin": isin}
                ).first()
                if row and row.symbol_root:
                    return row.symbol_root.strip().upper()
            finally:
                db.close()
    except Exception:
        pass
    try:
        from services.isin_resolver import isin_to_canonical
        return isin_to_canonical(isin)
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# F&O info resolution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_fno(raw_sym: str) -> tuple[bool, int]:
    """
    F&O availability + lot size.
    Step 1: ScripMaster DB → Step 2: CSV → Step 3: NSE API.
    """
    s = str(raw_sym or "").strip().upper()

    try:
        from services.scrip_master_db import is_db_populated, query_fno_info
        if is_db_populated():
            fno, lot = query_fno_info(s)
            if fno and lot > 1:
                return True, lot
            from services.symbol_resolver import get_canonical
            can = get_canonical(s)
            if can and can != s:
                fno, lot = query_fno_info(can)
                if fno and lot > 1:
                    return True, lot
    except Exception as e:
        logger.error(f"[StockMaster] DB F&O lookup error: {e}", exc_info=True)

    try:
        from services.engine_price_fetch import get_fno_info
        fno, lot = get_fno_info(s)
        if fno and lot > 1:
            return True, lot
        from services.symbol_resolver import get_canonical
        can = get_canonical(s)
        if can and can != s:
            fno, lot = get_fno_info(can)
            if fno and lot > 1:
                return True, lot
    except Exception as e:
        logger.error(f"[StockMaster] CSV F&O lookup error: {e}", exc_info=True)

    try:
        return get_fno_info_from_nse(s)
    except Exception as e:
        logger.error(f"[StockMaster] NSE F&O lookup error: {e}", exc_info=True)
        return False, 0


# ─────────────────────────────────────────────────────────────────────────────
# auto_populate
# ─────────────────────────────────────────────────────────────────────────────

def auto_populate(user_id: int) -> dict:
    from services.symbol_resolver import get_canonical
    from services.scrip_master_db import classify_instrument

    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT symbol, MAX(company_name) as company_name, broker
                FROM transactions
                WHERE user_id = :uid AND segment = 'EQ'
                GROUP BY symbol, broker
            """),
            {"uid": user_id}
        ).fetchall()

        added = updated = unmatched = fno_set = skipped = 0

        for r in rows:
            raw_sym = str(r.symbol or "").strip()
            company = str(r.company_name or raw_sym).strip()
            broker  = str(r.broker or "").strip()

            if not raw_sym or raw_sym.upper() in ("NAN", "NONE", ""):
                continue

            # Classify instrument
            instrument_class = classify_instrument(raw_sym, company=company)
            if instrument_class in ("BOND", "ETF", "RIGHTS", "SGB"):
                logger.info(f"[AutoPopulate] Skipping {instrument_class}: {raw_sym}")
                skipped += 1
                continue

            # Canonical normalisation
            canonical_sym = get_canonical(raw_sym)

            # ISIN resolution (5-step pipeline)
            isin, resolved_canonical = _resolve_isin(canonical_sym, company)
            if not isin and canonical_sym != raw_sym:
                isin, resolved_canonical = _resolve_isin(raw_sym, company)

            if resolved_canonical:
                canonical_sym = resolved_canonical

            if not isin:
                # Final classification check before adding to unmatched
                instrument_class_retry = classify_instrument(raw_sym, company=company)
                if instrument_class_retry in ("BOND", "ETF", "RIGHTS", "SGB"):
                    logger.info(f"[AutoPopulate] Skipping {instrument_class_retry} (post-resolution): {raw_sym}")
                    skipped += 1
                    continue

                # Add to unmatched_symbols
                exists = db.execute(
                    text("SELECT id FROM unmatched_symbols WHERE user_id=:uid AND broker=:br AND raw_symbol=:sym"),
                    {"uid": user_id, "br": broker, "sym": raw_sym}
                ).first()
                if not exists:
                    db.execute(
                        text("INSERT INTO unmatched_symbols (user_id, broker, raw_symbol, company_name) VALUES (:uid,:br,:sym,:co)"),
                        {"uid": user_id, "br": broker, "sym": raw_sym, "co": company}
                    )
                    unmatched += 1
                continue

            # Ensure stock_master_mapping has this ISIN
            existing_master = db.execute(
                text("SELECT isin, fno_available, lot_size FROM stock_master_mapping WHERE isin=:isin"),
                {"isin": isin}
            ).first()

            fno_avail, lot_sz = _resolve_fno(canonical_sym)

            if not existing_master:
                db.execute(
                    text("""INSERT INTO stock_master_mapping
                            (isin, standard_name, canonical_symbol, fno_available, lot_size)
                            VALUES (:isin, :std, :can, :fno, :lot)"""),
                    {"isin": isin, "std": company, "can": canonical_sym,
                     "fno": 1 if fno_avail else 0, "lot": lot_sz}
                )
                added += 1
            else:
                old_lot = int(existing_master.lot_size or 0)
                old_fno = bool(existing_master.fno_available)
                db.execute(
                    text("""UPDATE stock_master_mapping
                            SET canonical_symbol = COALESCE(NULLIF(canonical_symbol,''), :can),
                                updated_at = NOW()
                            WHERE isin = :isin"""),
                    {"can": canonical_sym, "isin": isin}
                )
                if (fno_avail and lot_sz > 1) and (not old_fno or old_lot <= 1):
                    db.execute(
                        text("""UPDATE stock_master_mapping
                                SET fno_available=:fno, lot_size=:lot, updated_at=NOW()
                                WHERE isin=:isin"""),
                        {"fno": 1, "lot": lot_sz, "isin": isin}
                    )
                    fno_set += 1

            # Upsert per-user-broker symbol mapping
            db.execute(
                text("""INSERT INTO user_stock_symbol_mapping (user_id, isin, broker, symbol)
                       VALUES (:uid, :isin, :br, :sym)
                       ON DUPLICATE KEY UPDATE symbol = VALUES(symbol)"""),
                {"uid": user_id, "isin": isin, "br": broker, "sym": raw_sym}
            )
            updated += 1

            # Cache normalisation
            db.execute(
                text("INSERT IGNORE INTO symbol_normalisation (raw_symbol, canonical_symbol) VALUES (:raw, :can)"),
                {"raw": raw_sym.upper(), "can": canonical_sym}
            )
            if company.upper() != raw_sym.upper():
                db.execute(
                    text("INSERT IGNORE INTO symbol_normalisation (raw_symbol, canonical_symbol) VALUES (:raw, :can)"),
                    {"raw": company.upper(), "can": canonical_sym}
                )

            # Mark previously unmatched as resolved
            db.execute(
                text("""UPDATE unmatched_symbols SET resolved=1, resolved_isin=:isin
                        WHERE user_id=:uid AND raw_symbol=:sym"""),
                {"isin": isin, "uid": user_id, "sym": raw_sym}
            )

        # ── COMMIT AND RETURN AFTER THE LOOP ──
        db.commit()
        return {
            "added":               added,
            "updated":             updated,
            "unmatched":           unmatched,
            "fno_enriched":        fno_set,
            "skipped_non_equity":  skipped,
        }

    except Exception as e:
        db.rollback()
        logger.error(f"[AutoPopulate] Failed: {e}", exc_info=True)
        raise e
    finally:
        db.close()
# ─────────────────────────────────────────────────────────────────────────────
# update_custom_name  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def update_custom_name(isin: str, new_name: str) -> bool:
    db = SessionLocal()
    try:
        if new_name.strip():
            db.execute(
                text("UPDATE stock_master_mapping SET user_custom_name=:name, updated_at=NOW() WHERE isin=:isin"),
                {"name": new_name.strip(), "isin": isin}
            )
        else:
            db.execute(
                text("UPDATE stock_master_mapping SET user_custom_name=NULL, updated_at=NOW() WHERE isin=:isin"),
                {"isin": isin}
            )
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
     
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# get_user_stock_grid  (unchanged logic)
# ─────────────────────────────────────────────────────────────────────────────

def get_user_stock_grid(user_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        user_row = db.execute(
            text("SELECT username FROM users WHERE id=:uid"), {"uid": user_id}
        ).first()
        username = user_row.username if user_row else "user"

        rows = db.execute(
            text("""
                SELECT
                    h.symbol                                          AS raw_symbol,
                    COALESCE(usm.isin, CONCAT('UNRESOLVED:', h.symbol)) AS display_isin,
                    COALESCE(usm.isin, '')                            AS isin,
                    usm.broker                                        AS mapped_broker,
                    usm.symbol                                        AS mapped_symbol,
                    h.quantity,
                    h.company_name
                FROM holdings h
                LEFT JOIN user_stock_symbol_mapping usm
                    ON usm.user_id = h.user_id
                AND UPPER(usm.symbol) = UPPER(h.symbol)
                WHERE h.user_id = :uid
                  AND h.segment  = 'EQ'
                ORDER BY h.symbol
            """),
            {"uid": user_id}
        ).fetchall()

        if not rows:
            return []

        grouped: dict = defaultdict(lambda: {
            "brokers": {},
            "total_qty": 0.0,
            "company_name": "",
            "isin": "",
            "is_unresolved": False,
        })

        for r in rows:
            key  = r.display_isin
            info = grouped[key]
            info["company_name"] = r.company_name or r.raw_symbol
            info["isin"]         = r.isin if r.isin else ""
            if not r.isin:
                info["is_unresolved"] = True

            broker = r.mapped_broker or "Unknown"
            symbol = r.mapped_symbol or r.raw_symbol
            qty    = float(r.quantity) if r.quantity else 0.0

            if broker not in info["brokers"]:
                info["brokers"][broker] = {"symbol": symbol, "qty": 0.0}
            info["brokers"][broker]["qty"] += qty
            info["total_qty"] += qty

        master_lookup: dict = {}
        isins_to_fetch = [k for k, v in grouped.items() if v["isin"]]
        if isins_to_fetch:
            master_rows = db.execute(
                text("SELECT * FROM stock_master_mapping WHERE isin IN :isins"),
                {"isins": tuple(isins_to_fetch)}
            ).fetchall()
            for mr in master_rows:
                master_lookup[mr.isin] = mr

        grid = []
        for key, info in grouped.items():
            isin   = info["isin"]
            master = master_lookup.get(isin) if isin else None

            standard_name = master.standard_name    if master else (info["company_name"] or key)
            custom_name   = getattr(master, "user_custom_name", "") or ""
            canonical     = master.canonical_symbol  if master else ""
            fno_available = bool(master.fno_available) if master else False
            lot_size      = int(master.lot_size or 0)  if master else 0
            total_qty     = info["total_qty"]

            row = {
                "isin":             isin if isin else "",
                "standard_name":    standard_name,
                "user_custom_name": custom_name,
                "canonical_symbol": canonical,
                "fno_available":    fno_available,
                "lot_size":         lot_size,
                "total_qty":        round(total_qty, 2),
                "resolved":         "No" if info["is_unresolved"] else "Yes",
                "updated_at":       str(master.updated_at) if master and hasattr(master, "updated_at") else "",
            }

            for broker, bdata in info["brokers"].items():
                row[f"{username}_{broker}_symbol"] = bdata["symbol"]
                row[f"{username}_{broker}_qty"]    = round(bdata["qty"], 2)

            if fno_available and lot_size > 0 and total_qty > 0:
                row["pending_qty"] = (lot_size - total_qty % lot_size) % lot_size
            else:
                row["pending_qty"] = 0

            grid.append(row)

        return grid
    finally:
        db.close()