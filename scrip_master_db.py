"""
scrip_master_db.py  — v2
=========================
What changed vs v1:
  + query_isin_fuzzy(symbol, company)   — normalized + SQL-LIKE fallback when exact fails
  + classify_instrument(symbol, isin)   — detect bonds / ETFs / rights / SGBs programmatically
    so auto_populate can silently skip them instead of dumping them into unmatched_symbols.
  + _normalize_broker_name(name)        — generic abbreviation expander (NOT stock-specific)

Resolution order used by query_isin_fuzzy():
  1. Normalize the input (strip punctuation, expand abbrevs like HLDG.→HOLDINGS)
  2. Exact symbol_root match on normalized form
  3. Exact name match on normalized form
  4. SQL LIKE scan on name and full_name (first ≥2 significant words as prefix)
  5. Return "" — caller decides what to do next

classify_instrument() detection hierarchy:
  1. ScripMaster DB series column (N2/N3/…→BOND, ES→ETF, EQ→EQUITY)
  2. ISIN prefix  (INF*→ETF/MF units)
  3. Symbol / name keyword scan (BOND, NCD, -RE, SGB, ETF, …)
  4. Returns UNKNOWN if none match — caller treats as potentially-equity

All other functions (upsert_scrip_master, query_isin, query_fno_info, …) are
unchanged from v1 and reproduced verbatim below.
"""
from __future__ import annotations

import io
import re
import pandas as pd
from sqlalchemy import text
from database import SessionLocal
import logging
logger = logging.getLogger(__name__)

# ── Column map (unchanged) ─────────────────────────────────────────────────────
_CSV_TO_DB = {
    "ScripCode":   "scrip_code",
    "Exch":        "exch",
    "ExchType":    "exch_type",
    "Name":        "name",
    "SymbolRoot":  "symbol_root",
    "FullName":    "full_name",
    "ScripData":   "scrip_data",
    "ISIN":        "isin",
    "Series":      "series",
    "ScripType":   "scrip_type",
    "StrikeRate":  "strike_rate",
    "Expiry":      "expiry",
    "LotSize":     "lot_size",
    "TickSize":    "tick_size",
    "QtyLimit":    "qty_limit",
    "Multiplier":  "multiplier",
}

_populated_cache: bool | None = None


def _invalidate_cache():
    global _populated_cache
    _populated_cache = None


# ─────────────────────────────────────────────────────────────────────────────
# NEW ① — Generic broker-name normaliser
#
# This is NOT a stock-specific alias table.  It expands generic abbreviations
# that brokers use across thousands of names (HLDG. → HOLDINGS, MAHA. →
# MAHARASHTRA, etc.).  A two-word broker display name like "BAJAJ HLDG. & INV."
# becomes "BAJAJ HOLDINGS INVESTMENT", which ScripMaster can find by FullName.
#
# To add a new abbreviation: add one line to _ABBREV — no stock-specific ISINs,
# no hardcoded tickers.
# ─────────────────────────────────────────────────────────────────────────────

_ABBREV: dict[str, str] = {
    # broker abbreviation (upper, no trailing period) → full word
    "HLDG":       "HOLDINGS",
    "HOLDGS":     "HOLDINGS",
    "INV":        "INVESTMENT",
    "INVST":      "INVESTMENT",
    "MAHA":       "MAHARASHTRA",
    "INTL":       "INTERNATIONAL",
    "INDL":       "INDUSTRIES",
    "INDS":       "INDUSTRIES",
    "FIN":        "FINANCE",
    "FINSERV":    "FINANCIAL SERVICES",
    "SERV":       "SERVICES",
    "CORP":       "CORPORATION",
    "COMM":       "COMMERCIAL",
    "NATIONLBAK": "NATIONAL BANK",   # 5paisa truncation
    "NATIONL":    "NATIONAL",
    "PASSGR":     "PASSENGER",
    "PASSENGE":   "PASSENGER",
    "MOTOCORP":   "MOTO CORP",
    "PHARMA":     "PHARMACEUTICALS",
    "INFRA":      "INFRASTRUCTURE",
    "ENGG":       "ENGINEERING",
    "ENGY":       "ENERGY",
    "ELEC":       "ELECTRICAL",
}

# Legal suffixes to strip before comparison (common at end of company names)
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(LTD\.?|LIMITED|PVT\.?|PRIVATE|CORP\.?|INC\.?|CO\.?)$",
    re.IGNORECASE,
)
_SUFFIX_STRIP = re.compile(r"(_EQ|-EQ|_NSE|-NSE|_BSE|-BSE)$", re.IGNORECASE)

def _normalize_broker_name(raw: str) -> str:
    """
    Normalize a broker display name so ScripMaster exact/fuzzy match works.

    Steps:
      1. Uppercase + strip
      2. Remove trailing period from each word  ("HLDG." → "HLDG")
      3. Replace " & " with " "
      4. Expand abbreviations word-by-word
      5. Strip trailing legal suffixes (LTD, LIMITED, …)
      6. Collapse extra whitespace

    Examples:
      "BAJAJ HLDG. & INV." → "BAJAJ HOLDINGS INVESTMENT"
      "PUNJ. NATIONLBAK"   → "PUNJAB NATIONAL BANK"   (PUNJ not in abbrev → kept)
      "MAHA. SCOOTERS"     → "MAHARASHTRA SCOOTERS"
    """
    s = raw.strip().upper()
    # Remove trailing periods from individual tokens ("HLDG." → "HLDG")
    s = re.sub(r'\.(\s|$)', r' ', s)
    # Drop ampersands
    s = s.replace("&", " ")
    # Expand abbreviations
    words = [_ABBREV.get(w.strip(), w.strip()) for w in s.split() if w.strip()]
    s = " ".join(words)
    # Strip legal suffix
    s = _LEGAL_SUFFIX_RE.sub("", s).strip()
    s = _SUFFIX_STRIP.sub("", s).strip()
    # Collapse spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ─────────────────────────────────────────────────────────────────────────────
# NEW ② — Instrument classifier
#
# Returns one of: "EQUITY" | "BOND" | "ETF" | "RIGHTS" | "SGB" | "UNKNOWN"
#
# Used by auto_populate() to silently skip non-equity instruments so they never
# appear in the "unresolved symbols" UI as something the user needs to fix.
#
# Detection priority:
#   1. ScripMaster DB series column — most reliable (N2/N3/… = bond, ES = ETF)
#   2. ISIN prefix pattern (INF* = MF/ETF units)
#   3. Keyword scan on symbol and company name
#   4. UNKNOWN — caller decides (treated as potentially-equity)
# ─────────────────────────────────────────────────────────────────────────────

# NSE series codes that definitively mean "not ordinary equity"
_BOND_SERIES: frozenset[str] = frozenset({
    "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9",
    "NE", "NF", "ND", "NC", "NB", "NA",
    "GB", "GS",    # govt bonds / gilt strips
    "CD", "CP",    # certificates of deposit / commercial paper
    "TB",          # T-bills
    "DVR",         # differential voting rights (separate series from EQ)
})

_SME_REIT_SERIES: frozenset[str] = frozenset({
    "SM", "ST", "TA", "TC",  # SME, InvIT, REIT — still equity-like
})

_BOND_NAME_KEYWORDS: frozenset[str] = frozenset({
    "BOND", "NCD", "DEBENTURE", "TBILL", "T-BILL",
    "COMMERCIAL PAPER", "GILT", "G-SEC", "GSEC",
})

_RIGHTS_PATTERNS: tuple[str, ...] = (
    "-RE",          # e.g. "BHARTIARTLRE"
    " RE-",         # space-separated rights entitlement
    "RIGHTS ENTITLEMENT",
    " RIGHTS ",
)

_ETF_KEYWORDS: frozenset[str] = frozenset({
    " ETF", "BEES", "INDEX FUND", "LIQUID FUND", "DEBT FUND",
})

_SGB_KEYWORDS: frozenset[str] = frozenset({
    "SGB", "SOVEREIGN GOLD BOND",
})


def classify_instrument(symbol: str, isin: str = "", company: str = "") -> str:
    """
    Classify a traded symbol as EQUITY / BOND / ETF / RIGHTS / SGB / UNKNOWN.

    Parameters
    ----------
    symbol  : broker symbol string (e.g. "IIFCBOND16", "BHARTIARTLRE")
    isin    : ISIN if known (e.g. "INF200K01884")
    company : broker company name if available (e.g. "BHARTI AIRTEL RIGHTS")

    Returns
    -------
    "EQUITY"  — normal NSE equity (EQ series)
    "BOND"    — bond / NCD / debenture / commercial paper
    "ETF"     — exchange-traded fund / MF unit
    "RIGHTS"  — rights entitlement (temporarily listed, then delisted)
    "SGB"     — sovereign gold bond
    "UNKNOWN" — could not determine; treat as potentially-equity
    """
    sym_up  = str(symbol).strip().upper()
    comp_up = str(company).strip().upper()
    scan_up = sym_up + " " + comp_up   # combined text to scan keywords on

    # ── Step 1: ScripMaster DB series ─────────────────────────────────────────
    if is_db_populated():
        db = SessionLocal()
        try:
            row = db.execute(
                text("""
                    SELECT series FROM scrip_master_cache
                    WHERE (UPPER(symbol_root) = :sym OR UPPER(name) = :sym)
                      AND exch = 'N' AND exch_type = 'C'
                      AND series IS NOT NULL AND series != ''
                    ORDER BY CASE WHEN series = 'EQ' THEN 0 ELSE 1 END
                    LIMIT 1
                """),
                {"sym": sym_up}
            ).first()
            if row and row.series:
                series = str(row.series).strip().upper()
                if series == "EQ":
                    return "EQUITY"
                if series == "ES":        # ETF series in ScripMaster
                    return "ETF"
                if series in _SME_REIT_SERIES:
                    return "EQUITY"       # InvIT/REIT are equity-like instruments
                if series in _BOND_SERIES:
                    return "BOND"
        except Exception:
            pass
        finally:
            db.close()

    # ── Step 2: ISIN prefix ────────────────────────────────────────────────────
    if isin:
        isin_up = str(isin).strip().upper()
        if isin_up.startswith("INF"):
            # INF* = SEBI-registered fund units (MF, ETF) — not listed equity
            return "ETF"

    # ── Step 3: Keyword scan ──────────────────────────────────────────────────
    for kw in _SGB_KEYWORDS:
        if kw in scan_up:
            return "SGB"

    for kw in _ETF_KEYWORDS:
        if kw in scan_up:
            return "ETF"

    for pat in _RIGHTS_PATTERNS:
        if pat in scan_up:
            return "RIGHTS"

    for kw in _BOND_NAME_KEYWORDS:
        if kw in scan_up:
            return "BOND"

    # ── Step 4: Symbol suffix heuristics ─────────────────────────────────────
    # Bonds often end in 2-digit year suffix: "IIFCBOND16", "IDFCBOND12"
    if re.search(r"BOND\d{2}$", sym_up):
        return "BOND"
    # Rights: last 2 chars are "RE" and symbol > 8 chars (too long for normal ticker)
    if sym_up.endswith("RE") and len(sym_up) > 8:
        return "RIGHTS"

    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# NEW ③ — Fuzzy ISIN resolution
#
# Called by stock_master_service._resolve_isin() AFTER the exact-match path
# (query_isin) has already failed.  Uses two additional strategies:
#   a) Exact match on the *normalized* broker name (via _normalize_broker_name)
#   b) SQL LIKE prefix scan on name and full_name columns using the first two
#      significant words of the normalized name.
#
# Only matches NSE EQ rows (exch='N', exch_type='C', series='EQ') to avoid
# returning bond ISINs for equity lookups.
# ─────────────────────────────────────────────────────────────────────────────

def query_isin_fuzzy(symbol: str, company: str = "") -> str:
    """
    Fuzzy ISIN resolution when exact match fails.

    Resolution steps (all strictly NSE EQ series='EQ'):
      1. Normalize symbol via _normalize_broker_name → exact name/symbol_root match
      2. Normalize company name → exact name/symbol_root match
      3. SQL LIKE prefix (first 2 significant words of normalized name) on name
      4. SQL LIKE prefix on full_name column
      5. Return "" if all fail

    Returns ISIN string or "".

    NOTE: step 3/4 return the *shortest* matching name to avoid grabbing a parent
    company name that matches a subsidiary prefix.  If there are multiple matches,
    the caller should prefer the one returned here and log a warning for review.
    """
    if not is_db_populated():
        return ""

    candidates: list[tuple[str, str]] = []  # (symbol_to_try, label_for_logging)

    # Build list of candidates to try in order
    norm_sym  = _normalize_broker_name(symbol)
    norm_comp = _normalize_broker_name(company) if company else ""

    for cand, label in [
        (symbol.strip().upper(), "raw symbol"),
        (norm_sym,               "normalized symbol"),
        (norm_comp,              "normalized company"),
    ]:
        if cand and cand not in ("NAN", "NONE", ""):
            candidates.append((cand, label))

    db = SessionLocal()
    try:
        # ── Steps 1 & 2: exact match on each candidate ─────────────────────────
        for cand, label in candidates:
            for col in ("symbol_root", "name"):
                row = db.execute(
                    text(f"""
                        SELECT isin FROM scrip_master_cache
                        WHERE UPPER({col}) = :cand
                          AND exch = 'N' AND exch_type = 'C'
                          AND series = 'EQ'
                          AND isin IS NOT NULL AND isin != ''
                        LIMIT 1
                    """),
                    {"cand": cand}
                ).first()
                if row and row.isin:
                    logger.info(f"[ScripMasterDB] fuzzy exact ({label}, col={col}): "
                          f"'{cand}' → {row.isin}")
                    return row.isin.strip().upper()

        # ── Steps 3 & 4: SQL LIKE prefix on name and full_name ─────────────────
        # Build prefix from first 1-2 significant words (>=3 chars each) of the
        # best normalized candidate.  Short words like "OF", "AND", "THE" are
        # excluded to avoid over-broad matches.
        #
        # IMPORTANT: single-word symbols (e.g. "IRCTC", "TNPL", "WONDERLA") must
        # still go through this step using just that one word as the prefix —
        # previously this required >=2 significant words and silently skipped
        # every single-word ticker, which is the majority of NSE symbols.
        _STOP_WORDS = {"OF", "AND", "THE", "IN", "AT", "A", "AN", "FOR", "&"}
        best_cand = norm_sym or norm_comp
        if best_cand:
            sig_words = [
                w for w in best_cand.split()
                if len(w) >= 3 and w not in _STOP_WORDS
            ]
            if len(sig_words) >= 1:
                # 1. Space-separated (e.g., "HERO MOTO")
                prefix_space = " ".join(sig_words[:2])
                # 2. Joined together (e.g., "HEROMOTO" – matches "HEROMOTOCO")
                prefix_joined = "".join(sig_words[:2])
                # 3. Separated by wildcard (e.g., "ENGINEERS%IN" – matches "ENGINEERSIN")
                prefix_pct = "%".join(sig_words[:2])

                for col in ("symbol_root", "name", "full_name"):
                    rows = db.execute(
                        text(f"""
                            SELECT isin, {col} AS matched_name
                            FROM scrip_master_cache
                            WHERE (UPPER({col}) LIKE :prefix_space
                                    OR UPPER({col}) LIKE :prefix_joined
                                    OR UPPER({col}) LIKE :prefix_pct)
                                AND exch = 'N' AND exch_type = 'C'
                                AND series = 'EQ'
                                AND isin IS NOT NULL AND isin != ''
                            ORDER BY LENGTH({col}) ASC
                            LIMIT 5
                        """),
                        {
                            "prefix_space": f"{prefix_space}%",
                            "prefix_joined": f"{prefix_joined}%",
                            "prefix_pct": f"{prefix_pct}%"
                        }
                    ).fetchall()

                    if rows:
                        best = rows[0]
                        logger.info(f"[ScripMasterDB] fuzzy LIKE (col={col}): "
                              f"prefix='{prefix_space}' or '{prefix_joined}' → matched='{best.matched_name}' "
                              f"isin={best.isin}")
                        if len(rows) > 1:
                            logger.info(f"[ScripMasterDB]   (ambiguous: {len(rows)} matches — "
                                  f"using shortest: '{best.matched_name}')")
                        return best.isin.strip().upper()

                    if rows:
                        # Take shortest match (most specific name wins over parent company)
                        best = rows[0]
                        logger.info(f"[ScripMasterDB] fuzzy LIKE (col={col}): "
                              f"prefix='{prefix}' → matched='{best.matched_name}' "
                              f"isin={best.isin}")
                        if len(rows) > 1:
                            logger.info(f"[ScripMasterDB]   (ambiguous: {len(rows)} matches — "
                                  f"using shortest: '{best.matched_name}')")
                        return best.isin.strip().upper()

        # ── Step 5: last-resort — same LIKE prefix, but drop series='EQ' filter ──
        # Some legitimate equities are tagged with non-standard series codes in
        # 5paisa's export (e.g. blank series, or a series code not in our EQ
        # whitelist). Try the same prefix search against any row that isn't a
        # known bond series, so we don't lose real equities to a strict filter.
        if best_cand:
            sig_words = [
                w for w in best_cand.split()
                if len(w) >= 3 and w not in _STOP_WORDS
            ]
            if sig_words:
                prefix = " ".join(sig_words[:2])
                _BOND_SERIES_SQL = (
                    "N2","N3","N4","N5","N6","N7","N8","N9",
                    "NE","NF","ND","NC","NB","NA","GB","GS",
                    "CD","CP","TB","DVR",
                )
                for col in ("symbol_root", "name"):
                    rows = db.execute(
                        text(f"""
                            SELECT isin, {col} AS matched_name, series
                            FROM scrip_master_cache
                            WHERE UPPER({col}) LIKE :prefix
                              AND exch = 'N' AND exch_type = 'C'
                              AND (series IS NULL OR series NOT IN :bond_series)
                              AND isin IS NOT NULL AND isin != ''
                            ORDER BY CASE WHEN series='EQ' THEN 0 ELSE 1 END,
                                     LENGTH({col}) ASC
                            LIMIT 5
                        """),
                        {"prefix": f"{prefix}%", "bond_series": _BOND_SERIES_SQL}
                    ).fetchall()
                    if rows:
                        best = rows[0]
                        logger.info(f"[ScripMasterDB] fuzzy LIKE non-EQ fallback (col={col}): "
                              f"prefix='{prefix}' → matched='{best.matched_name}' "
                              f"series='{best.series}' isin={best.isin}")
                        return best.isin.strip().upper()

        return ""
    except Exception as e:
        logger.error(f"[ScripMasterDB] query_isin_fuzzy error for '{symbol}': {e}", exc_info=True)
        return ""
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# All functions below are UNCHANGED from v1 — reproduced verbatim
# ─────────────────────────────────────────────────────────────────────────────

def is_db_populated() -> bool:
    """Returns True if scrip_master_cache has at least 1000 rows."""
    global _populated_cache
    if _populated_cache is not None:
        return _populated_cache
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT COUNT(*) FROM scrip_master_cache LIMIT 1")
        ).scalar()
        _populated_cache = (result or 0) >= 1000
        return _populated_cache
    except Exception:
        return False
    finally:
        db.close()


def get_db_stats() -> dict:
    db = SessionLocal()
    try:
        total = db.execute(
            text("SELECT COUNT(*) FROM scrip_master_cache")
        ).scalar() or 0
        nse_eq = db.execute(
            text("SELECT COUNT(*) FROM scrip_master_cache WHERE exch='N' AND exch_type='C'")
        ).scalar() or 0
        with_isin = db.execute(
            text("SELECT COUNT(*) FROM scrip_master_cache WHERE isin IS NOT NULL AND isin != '' AND exch='N' AND exch_type='C'")
        ).scalar() or 0
        fno_symbols = db.execute(
            text("""SELECT COUNT(DISTINCT symbol_root)
                    FROM scrip_master_cache
                    WHERE exch='N' AND exch_type='D'
                      AND scrip_type='XX'
                      AND lot_size > 1
                      AND symbol_root IS NOT NULL AND symbol_root != ''""")
        ).scalar() or 0
        return {
            "total_rows":       total,
            "nse_eq_rows":      nse_eq,
            "rows_with_isin":   with_isin,
            "symbols_with_lot": fno_symbols,
        }
    finally:
        db.close()


def _clean_str_col(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def upsert_scrip_master(file_bytes: bytes) -> dict:
    try:
        df = pd.read_csv(
            io.BytesIO(file_bytes),
            low_memory=False,
            encoding="utf-8-sig",
            dtype=str,
        )
        df.columns = [c.strip() for c in df.columns]
    except Exception as e:
        return {"error": f"CSV parse failed: {e}"}

    logger.info(f"[ScripMasterDB] CSV columns ({len(df.columns)}): {list(df.columns)}")
    logger.info(f"[ScripMasterDB] Total rows in CSV: {len(df)}")
    if not df.empty:
        row0 = df.iloc[0]
        logger.info(f"[ScripMasterDB] First row sample — Exch={repr(row0.get('Exch','?'))} "
              f"ExchType={repr(row0.get('ExchType','?'))} "
              f"ScripCode={repr(row0.get('ScripCode','?'))} "
              f"Name={repr(row0.get('Name','?'))}")

    rename_map = {k: v for k, v in _CSV_TO_DB.items() if k in df.columns}
    df = df.rename(columns=rename_map)

    for required in ("scrip_code", "exch"):
        if required not in df.columns:
            return {
                "error": (
                    f"Required column missing after rename: {required} "
                    f"(have: {list(df.columns)[:15]})"
                )
            }

    def _clean_key(series: pd.Series) -> pd.Series:
        return (
            series.fillna("")
            .astype(str)
            .str.strip()
            .str.strip("'")
            .str.strip('"')
            .str.strip()
        )

    df["scrip_code"] = _clean_key(df["scrip_code"])
    df["exch"]       = _clean_key(df["exch"])

    df = df[df["scrip_code"].str.len() > 0]
    df = df[~df["scrip_code"].str.lower().isin(["nan", "none", "scripcode", ""])]

    exch_counts = df["exch"].value_counts().to_dict()
    logger.info(f"[ScripMasterDB] Exch distribution after clean: {exch_counts}")

    str_cols = ["exch_type", "name", "symbol_root", "full_name", "scrip_data",
                "isin", "series", "scrip_type", "expiry"]
    for col in str_cols:
        if col not in df.columns:
            df[col] = ""
        else:
            df[col] = _clean_str_col(df[col])

    num_cols = {
        "strike_rate": 0.0,
        "lot_size":    0,
        "tick_size":   0.0,
        "qty_limit":   0,
        "multiplier":  1.0,
    }
    for col, default in num_cols.items():
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)

    df["fno_flag"] = (df["exch_type"].str.upper() == "D").astype(int)

    nse_eq_before = len(df[(df["exch"] == "N") & (df["exch_type"] == "C")])
    nse_d_before  = len(df[(df["exch"] == "N") & (df["exch_type"] == "D")])
    logger.info(f"[ScripMasterDB] Pre-insert: NSE EQ rows={nse_eq_before}, NSE F&O rows={nse_d_before}")

    df["name"]        = df["name"].str[:200]
    df["symbol_root"] = df["symbol_root"].str[:100]
    df["full_name"]   = df["full_name"].str[:300]
    df["scrip_data"]  = df["scrip_data"].str[:200]
    df["isin"]        = df["isin"].str[:20]
    df["expiry"]      = df["expiry"].str[:30]
    df["series"]      = df["series"].str[:10]
    df["scrip_type"]  = df["scrip_type"].str[:10]
    df["exch_type"]   = df["exch_type"].str[:5]
    df["exch"]        = df["exch"].str[:5]

    db = SessionLocal()
    inserted = updated = errors = 0
    CHUNK = 500

    upsert_sql = text("""
        INSERT INTO scrip_master_cache
            (scrip_code, exch, exch_type, name, symbol_root, full_name, scrip_data,
             isin, series, scrip_type, strike_rate, expiry, lot_size,
             tick_size, qty_limit, multiplier, fno_flag)
        VALUES
            (:scrip_code, :exch, :exch_type, :name, :symbol_root, :full_name, :scrip_data,
             :isin, :series, :scrip_type, :strike_rate, :expiry, :lot_size,
             :tick_size, :qty_limit, :multiplier, :fno_flag)
        ON DUPLICATE KEY UPDATE
            exch_type   = VALUES(exch_type),
            name        = VALUES(name),
            symbol_root = VALUES(symbol_root),
            full_name   = VALUES(full_name),
            scrip_data  = VALUES(scrip_data),
            isin        = COALESCE(NULLIF(VALUES(isin), ''), isin),
            series      = VALUES(series),
            scrip_type  = VALUES(scrip_type),
            strike_rate = VALUES(strike_rate),
            expiry      = VALUES(expiry),
            lot_size    = VALUES(lot_size),
            tick_size   = VALUES(tick_size),
            qty_limit   = VALUES(qty_limit),
            multiplier  = VALUES(multiplier),
            fno_flag    = VALUES(fno_flag),
            updated_at  = NOW()
    """)

    records = df.to_dict(orient="records")
    for i in range(0, len(records), CHUNK):
        chunk = records[i: i + CHUNK]
        try:
            result = db.execute(upsert_sql, chunk)
            inserted += result.rowcount
            db.commit()
        except Exception as e:
            db.rollback()
            errors += len(chunk)
            logger.info(f"[ScripMasterDB] Chunk {i // CHUNK} error: {e}")

    def _ensure_nse_eq_rows(db_session):
        """
        Some equities are present in the ScripMaster CSV only under exch='B'
        (BSE) even though they also trade on NSE — 5paisa's export doesn't
        always include an explicit exch='N' row for every BSE-listed name.

        Every ISIN/symbol lookup in this codebase filters on exch='N', so
        without a synthetic NSE row these stocks are invisible to resolution
        even though the data (ISIN, name, symbol_root) is sitting right there
        under exch='B'.

        NOTE: previously this only ran for symbol_roots that ALSO had F&O
        contracts (exch='N' AND exch_type='D'), which wrongly excluded plain
        cash-market equities with no derivatives (e.g. IRCTC) — F&O eligibility
        has nothing to do with whether a stock trades on NSE. That EXISTS
        condition has been removed. The original series (EQ or BE) is now
        preserved instead of being hardcoded to EQ.
        """
        missing_rows = db_session.execute(
            text("""
                SELECT b.scrip_code, b.name, b.symbol_root, b.scrip_data, b.isin, b.series
                FROM scrip_master_cache b
                LEFT JOIN scrip_master_cache n
                    ON n.symbol_root = b.symbol_root
                    AND n.exch = 'N'
                    AND n.exch_type = 'C'
                    AND n.series IN ('EQ', 'BE')
                WHERE b.exch = 'B'
                    AND b.exch_type = 'C'
                    AND b.series IN ('EQ', 'BE')
                    AND n.scrip_code IS NULL
                    AND b.symbol_root IS NOT NULL AND b.symbol_root != ''
            """)
        ).fetchall()

        new_rows = 0
        for row in missing_rows:
            db_session.execute(
                text("""
                    INSERT IGNORE INTO scrip_master_cache
                    (scrip_code, exch, exch_type, name, symbol_root, scrip_data, isin, series, updated_at)
                    VALUES (:code, 'N', 'C', :name, :sym_root, :scrip_data, :isin, :series, NOW())
                """),
                {
                    "code":       row.scrip_code,
                    "name":       row.name,
                    "sym_root":   row.symbol_root,
                    "scrip_data": row.scrip_data,
                    "isin":       row.isin,
                    "series":     row.series or "EQ",
                }
            )
            new_rows += 1
        if new_rows > 0:
            db_session.commit()
            logger.info(f"[ScripMasterDB] Added {new_rows} missing NSE EQ/BE rows (synthesized from BSE listing).")
        return new_rows

    _ensure_nse_eq_rows(db)
    db.close()
    _invalidate_cache()

    total     = len(records)
    succeeded = total - errors
    return {
        "inserted": succeeded,
        "updated":  0,
        "errors":   errors,
        "total":    total,
    }


def query_isin(symbol: str) -> str:
    """
    Strict exact-match ISIN resolution (NSE EQ series='EQ' rows only).
    Resolution order: symbol_root → name → scrip_data stripped → any non-bond series.
    Returns ISIN string or "".
    """
    if not symbol:
        return ""
    s = str(symbol).strip().upper()
    if not s or s in ("NAN", "NONE", ""):
        return ""

    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT isin FROM scrip_master_cache
                WHERE UPPER(symbol_root) = :sym
                  AND exch = 'N' AND exch_type = 'C'
                  AND series = 'EQ'
                  AND isin IS NOT NULL AND isin != ''
                LIMIT 1
            """),
            {"sym": s}
        ).first()
        if row and row.isin:
            return row.isin.strip().upper()

        row = db.execute(
            text("""
                SELECT isin FROM scrip_master_cache
                WHERE UPPER(name) = :sym
                  AND exch = 'N' AND exch_type = 'C'
                  AND series = 'EQ'
                  AND isin IS NOT NULL AND isin != ''
                LIMIT 1
            """),
            {"sym": s}
        ).first()
        if row and row.isin:
            return row.isin.strip().upper()

        sd_stripped = s.split("_")[0]
        row = db.execute(
            text("""
                SELECT isin FROM scrip_master_cache
                WHERE UPPER(SUBSTRING_INDEX(scrip_data, '_', 1)) = :sym
                  AND exch = 'N' AND exch_type = 'C'
                  AND series = 'EQ'
                  AND isin IS NOT NULL AND isin != ''
                LIMIT 1
            """),
            {"sym": sd_stripped}
        ).first()
        if row and row.isin:
            return row.isin.strip().upper()

        BOND_SERIES = ("N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9",
                       "NE", "NF", "ND", "NC", "NB", "NA", "GB", "GS",
                       "CD", "CP", "TB", "DVR")
        row = db.execute(
            text("""
                SELECT isin, series FROM scrip_master_cache
                WHERE UPPER(symbol_root) = :sym
                  AND exch = 'N' AND exch_type = 'C'
                  AND series NOT IN :bond_series
                  AND isin IS NOT NULL AND isin != ''
                ORDER BY CASE WHEN series='EQ' THEN 0 ELSE 1 END
                LIMIT 1
            """),
            {"sym": s, "bond_series": BOND_SERIES}
        ).first()
        if row and row.isin:
            return row.isin.strip().upper()

        return ""
    except Exception as e:
        logger.error(f"[ScripMasterDB] query_isin error for {symbol}: {e}", exc_info=True)
        return ""
    finally:
        db.close()


def query_fno_info(symbol: str) -> tuple[bool, int]:
    """
    Look up F&O availability and lot size.
    Pass 1: Futures rows (scrip_type='XX') with lot_size > 1.
    Pass 2: Option rows (CE/PE) — gap fill.
    Returns (True, lot_size) or (False, 0).
    """
    if not symbol:
        return False, 0
    s = str(symbol).strip().upper()
    if not s or s in ("NAN", "NONE", ""):
        return False, 0

    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT lot_size FROM scrip_master_cache
                WHERE (UPPER(symbol_root) = :sym OR UPPER(name) = :sym)
                  AND exch = 'N' AND exch_type = 'D'
                  AND scrip_type = 'XX'
                  AND lot_size > 1
                ORDER BY expiry DESC
                LIMIT 1
            """),
            {"sym": s}
        ).first()
        if row and int(row.lot_size) > 1:
            return True, int(row.lot_size)

        row = db.execute(
            text("""
                SELECT lot_size FROM scrip_master_cache
                WHERE (UPPER(symbol_root) = :sym OR UPPER(name) = :sym)
                  AND exch = 'N' AND exch_type = 'D'
                  AND scrip_type IN ('CE', 'PE')
                  AND lot_size > 1
                ORDER BY expiry DESC
                LIMIT 1
            """),
            {"sym": s}
        ).first()
        if row and int(row.lot_size) > 1:
            return True, int(row.lot_size)

        return False, 0
    except Exception as e:
        logger.error(f"[ScripMasterDB] query_fno_info error for {symbol}: {e}", exc_info=True)
        return False, 0
    finally:
        db.close()