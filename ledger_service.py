"""
ledger_service.py — v3
======================
Bug fixes vs v1/v2:
  1. _detect_broker: Added space-variant IIFL patterns ("equity currency" with space)
     was missing → filename "Equity Currency Ledger of HUFSARJU.xls" fell through to
     'auto', Zerodha parser then mistakenly accepted it (shares Particulars/Debit/Credit).
  2. ProcessedFile duplicate check now filters file_type='LEDGER'.
  3. Broker mismatch detection returns broker_warning key.
  4. Period summary marks all_duplicate=1 when 0 new rows inserted.
  5. get_ledger_entries: sorted ASC by date for correct running balance display.
"""

import io
import re
import hashlib
from sqlalchemy import text
from backend.database import SessionLocal
from backend.models import ProcessedFile


def _safe_float(val, default=0.0) -> float:
    try:
        s = str(val).strip()
        s = re.sub(r"\s*(Dr|Cr)\s*$", "", s, flags=re.IGNORECASE)
        s = s.replace(",", "")
        return float(s) if s not in ("", "nan", "None") else default
    except Exception:
        return default


# ─── Broker auto-detection ────────────────────────────────────────────────────
def _detect_broker(filename: str) -> str:
    fn = filename.lower()

    # 5paisa: LedgerReport_<ClientID>_<n>.xls
    if fn.startswith("ledgerreport_") or "ledgerreport" in fn:
        return "5paisa"

    # IIFL: various naming patterns (underscore OR space variants)
    #   "Equity_Currency_Ledger_of_GOLDIHUF_1month.xls"   ← underscore
    #   "Equity_Currency Ledger of HUFSARJU.xls"           ← mixed
    #   "Equity Currency Ledger of HUFSARJU.xls"           ← spaces only  ← BUG WAS HERE
    #   "EquityLedger_HUFSARJU.xls"
    if (
        fn.startswith("equity_currency_ledger")  # underscore variant
        or fn.startswith("equity_curr")  # partial underscore match
        or fn.startswith("equity currency")  # space variant  ← NEW FIX
        or ("equity" in fn and "currency" in fn and "ledger" in fn)  # any order
        or (
            "equity" in fn
            and "ledger" in fn
            and "iifl" not in fn
            and not fn.startswith("ledger")
        )
    ):  # catch-all for equity ledger
        return "IIFL"

    # Zerodha: ledger-TL7712.xlsx  /  ledgerTL7712_6.xlsx  /  ledger_<id>.xlsx
    if (
        fn.startswith("ledger-")
        or fn.startswith("ledgertl")
        or fn.startswith("ledger_")
    ):
        return "Zerodha"

    return "unknown"


_BROKER_CANONICAL: dict[str, str] = {
    "5paisa": "5paisa",
    "iifl": "IIFL",
    "zerodha": "Zerodha",
    "multiple": "",
}


def _canonical_broker(raw: str) -> str:
    return _BROKER_CANONICAL.get(str(raw).strip().lower(), raw)


def _normalise_txn(txn: dict) -> dict:
    return {
        "date": str(txn.get("Date", "")),
        "segment": str(txn.get("Segment", "")),
        "particular": str(
            txn.get("Particular") or txn.get("Particulars") or ""
        ).strip(),
        "description": str(txn.get("Description") or txn.get("Voucher") or "").strip(),
        "debit": _safe_float(txn.get("Debit", 0)),
        "credit": _safe_float(txn.get("Credit", 0)),
        "balance": float(txn.get("Balance", 0) or 0),
    }


def _row_hash(user_id: int, t: dict) -> str:
    raw = f"{user_id}|{t['date']}|{t['particular']}|{t['debit']:.2f}|{t['credit']:.2f}"
    return hashlib.md5(raw.encode()).hexdigest()[:32]


def _ensure_ledger_columns(db):
    for ddl in [
        "ALTER TABLE ledger_entries ADD COLUMN broker VARCHAR(20) DEFAULT ''",
        "ALTER TABLE ledger_entries ADD COLUMN row_hash VARCHAR(32)",
        "ALTER TABLE ledger_entries ADD UNIQUE KEY uq_ledger_user_hash (user_id, row_hash)",
    ]:
        try:
            db.execute(text(ddl))
            db.commit()
        except Exception:
            db.rollback()

    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS ledger_period_summaries (
                id                INT AUTO_INCREMENT PRIMARY KEY,
                user_id           INT NOT NULL,
                broker            VARCHAR(20),
                source_file       VARCHAR(200),
                period_start      VARCHAR(10),
                period_end        VARCHAR(10),
                opening_balance   FLOAT DEFAULT 0,
                closing_balance   FLOAT DEFAULT 0,
                funds_added       FLOAT DEFAULT 0,
                funds_withdrawn   FLOAT DEFAULT 0,
                total_charges     FLOAT DEFAULT 0,
                eq_net            FLOAT DEFAULT 0,
                fno_net           FLOAT DEFAULT 0,
                total_inserted    INT DEFAULT 0,
                total_skipped     INT DEFAULT 0,
                all_duplicate     TINYINT(1) DEFAULT 0,
                uploaded_at       DATETIME DEFAULT NOW()
            )
        """))
        db.commit()
    except Exception:
        db.rollback()

    try:
        db.execute(
            text(
                "ALTER TABLE ledger_period_summaries ADD COLUMN all_duplicate TINYINT(1) DEFAULT 0"
            )
        )
        db.commit()
    except Exception:
        db.rollback()


# ─── Main entry point ────────────────────────────────────────────────────────
def process_ledger_file(
    uploaded_file,
    user_id: int,
    filename: str,
    broker: str = "auto",
    user_broker: str = "",
) -> dict:
    db = SessionLocal()
    try:
        _ensure_ledger_columns(db)

        # ── Auto-detect broker from filename ───────────────────────────────
        if not broker or broker in ("auto", "unknown"):
            broker = _detect_broker(filename)

        actual_broker = broker

        # ── Broker mismatch warning ────────────────────────────────────────
        broker_warning = ""
        if user_broker and user_broker.lower() != "multiple":
            expected = _canonical_broker(user_broker)
            detected = broker if broker != "unknown" else ""
            if detected and expected and detected.lower() != expected.lower():
                broker_warning = (
                    f"⚠️ This looks like a **{detected}** ledger, "
                    f"but user is registered as **{expected}**. "
                    f"Proceeding — please verify the file is correct."
                )

        # ── Duplicate file check ───────────────────────────────────────────
        existing = (
            db.query(ProcessedFile)
            .filter_by(user_id=user_id, filename=filename, file_type="LEDGER")
            .first()
        )
        if existing:
            return {
                "status": "skipped",
                "message": (
                    f"'{filename}' was already processed "
                    f"({existing.records_added} rows). "
                    "Delete it first to re-import."
                ),
                "broker_warning": broker_warning,
            }

        # ── Parse ──────────────────────────────────────────────────────────
        file_bytes = (
            uploaded_file.read() if hasattr(uploaded_file, "read") else uploaded_file
        )
        buf = io.BytesIO(file_bytes)

        parsed = None

        if broker == "5paisa":
            from backend.parsers.fivepaisa_ledger import parse as p5

            parsed = p5(buf)
        elif broker == "IIFL":
            from backend.parsers.iifl_ledger import parse as piifl

            parsed = piifl(buf)
        elif broker == "Zerodha":
            from backend.parsers.zerodha_ledger import parse as pzrd

            parsed = pzrd(buf)
        else:
            # Auto-fallback: try each parser with strict validation
            # IMPORTANT: Zerodha is tried LAST because its header check
            # (Particulars+Debit+Credit) also matches IIFL files.
            for _broker, _mod_name in [
                ("5paisa", "parsers.fivepaisa_ledger"),
                ("IIFL", "parsers.iifl_ledger"),
                ("Zerodha", "parsers.zerodha_ledger"),
            ]:
                try:
                    import importlib

                    mod = importlib.import_module(_mod_name)
                    _result = mod.parse(io.BytesIO(file_bytes))
                    txns = _result.get("transactions", [])
                    if txns:
                        parsed = _result
                        actual_broker = _broker
                        break
                except Exception:
                    pass

        if not parsed:
            return {
                "status": "error",
                "message": (
                    "Could not parse ledger file. "
                    "Use the broker dropdown to select the correct broker manually."
                ),
                "broker_warning": broker_warning,
            }

        txns_raw = parsed.get("transactions", [])
        if not txns_raw:
            return {
                "status": "error",
                "message": "No ledger entries found in file.",
                "broker_warning": broker_warning,
            }

        # ── Dedup + insert ─────────────────────────────────────────────────
        rows_existing = db.execute(
            text("SELECT row_hash FROM ledger_entries WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchall()
        existing_hashes = {r[0] for r in rows_existing}

        inserted = 0
        skipped = 0
        for raw_txn in txns_raw:
            t = _normalise_txn(raw_txn)
            rh = _row_hash(user_id, t)
            if rh in existing_hashes:
                skipped += 1
                continue
            db.execute(
                text("""
                INSERT INTO ledger_entries
                  (user_id, date, segment, particular, description,
                   debit, credit, balance, source_file, broker, row_hash)
                VALUES
                  (:uid, :dt, :seg, :part, :desc,
                   :debit, :credit, :bal, :src, :broker, :rh)
            """),
                {
                    "uid": user_id,
                    "dt": t["date"],
                    "seg": t["segment"],
                    "part": t["particular"],
                    "desc": t["description"],
                    "debit": t["debit"],
                    "credit": t["credit"],
                    "bal": t["balance"],
                    "src": filename,
                    "broker": actual_broker,
                    "rh": rh,
                },
            )
            existing_hashes.add(rh)
            inserted += 1

        db.add(
            ProcessedFile(
                user_id=user_id,
                filename=filename,
                records_added=inserted,
                file_type="LEDGER",
            )
        )

        all_dup = 1 if (inserted == 0 and skipped > 0) else 0
        db.execute(
            text("""
            INSERT INTO ledger_period_summaries
              (user_id, broker, source_file, period_start, period_end,
               opening_balance, closing_balance, funds_added, funds_withdrawn,
               total_charges, eq_net, fno_net,
               total_inserted, total_skipped, all_duplicate)
            VALUES
              (:uid, :broker, :src, :ps, :pe,
               :ob, :cb, :fa, :fw, :tc, :eq, :fno,
               :ins, :skip, :dup)
        """),
            {
                "uid": user_id,
                "broker": actual_broker,
                "src": filename,
                "ps": _date_from_range(parsed.get("date_range", ""), "start"),
                "pe": _date_from_range(parsed.get("date_range", ""), "end"),
                "ob": float(parsed.get("opening_balance", 0) or 0),
                "cb": float(parsed.get("closing_balance", 0) or 0),
                "fa": _safe_float(parsed.get("funds_added", 0)),
                "fw": _safe_float(parsed.get("funds_withdrawn", 0)),
                "tc": _safe_float(parsed.get("total_charges", 0)),
                "eq": _safe_float(parsed.get("eq_net", 0)),
                "fno": _safe_float(parsed.get("fno_net", 0)),
                "ins": inserted,
                "skip": skipped,
                "dup": all_dup,
            },
        )

        db.commit()

        dr = parsed.get("date_range", "")
        if all_dup:
            msg = (
                f"ℹ️ '{filename}' — All {skipped} rows already in DB. "
                f"Period: {dr or 'unknown'}"
            )
            status = "info"
        else:
            parts = [f"✅ '{filename}' — {inserted} entries imported"]
            if skipped:
                parts.append(f"{skipped} duplicates skipped")
            if dr:
                parts.append(f"Period: {dr}")
            msg = " | ".join(parts)
            status = "success"

        return {
            "status": status,
            "message": msg,
            "inserted": inserted,
            "skipped": skipped,
            "broker": actual_broker,
            "broker_warning": broker_warning,
            "all_duplicate": bool(all_dup),
            "summary": {
                "date_range": dr,
                "opening_balance": float(parsed.get("opening_balance", 0) or 0),
                "closing_balance": float(parsed.get("closing_balance", 0) or 0),
                "funds_added": _safe_float(parsed.get("funds_added", 0)),
                "funds_withdrawn": _safe_float(parsed.get("funds_withdrawn", 0)),
                "total_charges": _safe_float(parsed.get("total_charges", 0)),
                "eq_net": _safe_float(parsed.get("eq_net", 0)),
                "fno_net": _safe_float(parsed.get("fno_net", 0)),
            },
        }

    except Exception as e:
        db.rollback()
        return {
            "status": "error",
            "message": f"Ledger error: {e}",
            "broker_warning": "",
        }
    finally:
        db.close()


def _date_from_range(date_range: str, which: str) -> str:
    try:
        parts = re.split(r"\s+to\s+", date_range.strip(), flags=re.IGNORECASE)
        if len(parts) == 2:
            raw = parts[0].strip() if which == "start" else parts[1].strip()
            # DD/MM/YYYY
            m = re.match(r"(\d{2})/(\d{2})/(\d{4})", raw)
            if m:
                return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            # DD/MM/YY
            m2 = re.match(r"(\d{2})/(\d{2})/(\d{2})$", raw)
            if m2:
                y = int(m2.group(3))
                full_year = 2000 + y if y < 50 else 1900 + y
                return f"{full_year}-{m2.group(2)}-{m2.group(1)}"
            return raw[:10]
    except Exception:
        pass
    return ""


def get_ledger_entries(user_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT id, date, segment, particular, description,
                       debit, credit, balance, source_file, broker
                FROM ledger_entries
                WHERE user_id = :uid
                ORDER BY date ASC, id ASC
            """),
            {"uid": user_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


def get_ledger_periods(user_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        _ensure_ledger_columns(db)
        rows = db.execute(
            text("""
                SELECT id, broker, source_file, period_start, period_end,
                       opening_balance, closing_balance, funds_added,
                       funds_withdrawn, total_charges, eq_net, fno_net,
                       total_inserted, total_skipped, all_duplicate, uploaded_at
                FROM ledger_period_summaries
                WHERE user_id = :uid
                ORDER BY period_start ASC, uploaded_at ASC
            """),
            {"uid": user_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()
