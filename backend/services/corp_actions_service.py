import io, json, re, time, requests
from datetime import datetime
from sqlalchemy import text
from database import SessionLocal
from models import CorporateAction
from typing import Optional

# ---------- NSE fetch helpers (same as old corporate_actions.py) ----------
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

def _nse_session():
    """Return a requests session with cookies set."""
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=10)
        time.sleep(1)
    except:
        pass
    return s

def _normalise_action_type(purpose: str) -> str:
    for pat, atype in [(re.compile(r'split|sub.?division', re.I), "SPLIT"),
                       (re.compile(r'bonus', re.I), "BONUS"),
                       (re.compile(r'dividend', re.I), "DIVIDEND"),
                       (re.compile(r'buy.?back', re.I), "BUYBACK"),
                       (re.compile(r'merger|amalgam', re.I), "MERGER"),
                       (re.compile(r'demerger|spin.?off', re.I), "DEMERGER"),
                       (re.compile(r'rights?\s+issue', re.I), "RIGHTS"),
                       (re.compile(r'transfer', re.I), "TRANSFER")]:
        if pat.search(purpose):
            return atype
    return "OTHER"

def _extract_details(purpose: str, action_type: str, face_value: str = "") -> dict:
    details = {"raw": purpose}
    if action_type == "SPLIT":
        m = re.search(r'from\s+rs\.?\s*([\d.]+).*?to\s+re?s?\.?\s*([\d.]+)', purpose, re.I)
        if m:
            details["old_fv"], details["new_fv"] = float(m.group(1)), float(m.group(2))
            if details["old_fv"] > 0 and details["new_fv"] > 0:
                ratio = details["old_fv"] / details["new_fv"]
                details["ratio"] = f"{int(ratio)}:1"
    elif action_type == "BONUS":
        m = re.search(r'(\d+)\s*[:/]\s*(\d+)', purpose)
        if m:
            details["ratio"] = f"{m.group(1)}:{m.group(2)}"
    elif action_type == "DIVIDEND":
        m = re.search(r'rs\.?\s*([\d.]+)\s*per\s*share', purpose, re.I)
        if m:
            details["amount_per_share"] = float(m.group(1))
    elif action_type in ("DEMERGER", "MERGER"):
        m = re.search(r'(\d+)\s*[:/]\s*(\d+)', purpose)
        if m:
            details["ratio"] = f"{m.group(1)}:{m.group(2)}"
    return details

def _norm_date(d: str) -> str:
    if not d or d.lower() in ("-", "n.a.", ""):
        return ""
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).strftime("%Y-%m-%d")
        except:
            continue
    return d[:10]

def fetch_nse_corp_actions(symbol: str, session=None) -> list[dict]:
    """Fetch corporate actions from NSE API for a symbol."""
    if session is None:
        session = _nse_session()
    url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={symbol.upper()}"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", [])
    except:
        pass
    return []

# ---------- Service functions ----------
def seed_from_transactions(user_id: int) -> dict:
    """Seed corporate_actions from BONUS/DEMERGER rows in transactions."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""SELECT symbol, company_name, isin, trade_type, trade_date, quantity, price, remarks
                   FROM transactions
                   WHERE user_id = :uid AND trade_type IN ('BONUS','DEMERGER_IN','MERGER_OUT','TRANSFER_IN','TRANSFER_OUT')
                   ORDER BY trade_date"""),
            {"uid": user_id}
        ).fetchall()

        inserted, skipped = 0, 0
        for r in rows:
            atype = r.trade_type
            action = {"BONUS":"BONUS","DEMERGER_IN":"DEMERGER","MERGER_OUT":"MERGER","TRANSFER_IN":"TRANSFER","TRANSFER_OUT":"TRANSFER"}.get(atype)
            details = {"qty": float(r.quantity), "price": float(r.price), "source_type": atype}
            remarks = str(r.remarks or "")
            m = re.search(r'(\d+)\s*[:/]\s*(\d+)', remarks)
            if m and action == "BONUS":
                details["ratio"] = f"{m.group(1)}:{m.group(2)}"

            # check exists
            ex = db.execute(text("SELECT id FROM corporate_actions WHERE user_id=:u AND symbol=:s AND action_type=:a AND ex_date=:e"),
                            {"u": user_id, "s": r.symbol, "a": action, "e": r.trade_date}).first()
            if ex:
                skipped += 1
                continue

            db.execute(text("""INSERT INTO corporate_actions
                               (user_id, symbol, isin, company_name, action_type, ex_date, record_date,
                                action_details, source, is_verified, notes)
                               VALUES (:uid,:sym,:isin,:comp,:act,:ex,:ex,:det,'transaction_file',1,:rem)"""),
                       {"uid": user_id, "sym": r.symbol, "isin": r.isin or "", "comp": r.company_name or r.symbol,
                        "act": action, "ex": r.trade_date, "det": json.dumps(details), "rem": remarks})
            inserted += 1
        db.commit()
        return {"inserted": inserted, "skipped": skipped}
    finally:
        db.close()


def sync_nse_for_user(user_id: int) -> dict:
    """Fetch NSE corporate actions for all stocks held (from stock_master or transactions) and upsert."""
    db = SessionLocal()
    try:
        # Collect unique symbols from stock_master (we can also use transactions)
        syms = db.execute(
            text("SELECT canonical_symbol FROM stock_master_mapping WHERE canonical_symbol IS NOT NULL AND canonical_symbol != '' UNION SELECT symbol FROM transactions WHERE user_id=:uid GROUP BY symbol"),
            {"uid": user_id}
        ).fetchall()
        symbols = sorted(set(s.strip().upper() for s in [r[0] for r in syms] if s))
        if not symbols:
            return {"fetched": 0, "inserted": 0, "errors": 0}

        session = _nse_session()
        fetched, inserted, errors = 0, 0, 0
        for sym in symbols:
            rows = fetch_nse_corp_actions(sym, session)
            if rows is None:
                errors += 1
                time.sleep(0.5)
                continue
            for r in rows:
                purpose = str(r.get("purpose") or r.get("subject") or "").strip()
                if not purpose:
                    continue
                action_type = _normalise_action_type(purpose)
                ex_date = _norm_date(r.get("exDate") or r.get("exdate") or r.get("ex_date") or "")
                rec_date = _norm_date(r.get("recordDate") or r.get("record_date") or "")
                if not ex_date:
                    continue
                fv = str(r.get("faceVal") or "")
                details = _extract_details(purpose, action_type, fv)
                comp = str(r.get("comp") or r.get("companyName") or sym)
                isin = str(r.get("isin") or "")
                # Upsert
                result = db.execute(text("""INSERT INTO corporate_actions
                                             (user_id, symbol, isin, company_name, action_type, ex_date, record_date,
                                              action_details, source, is_verified, notes)
                                             VALUES (:uid,:sym,:isin,:comp,:act,:ex,:rec,:det,'nse_api',1,:purp)
                                             ON DUPLICATE KEY UPDATE isin=COALESCE(NULLIF(VALUES(isin),''),isin),
                                                     company_name=COALESCE(NULLIF(VALUES(company_name),''),company_name),
                                                     record_date=COALESCE(NULLIF(VALUES(record_date),''),record_date),
                                                     action_details=VALUES(action_details), updated_at=NOW()"""),
                                    {"uid": user_id, "sym": sym, "isin": isin, "comp": comp,
                                     "act": action_type, "ex": ex_date, "rec": rec_date,
                                     "det": json.dumps(details), "purp": purpose})
                if result.rowcount:
                    inserted += 1
            fetched += 1
            time.sleep(0.4)
        db.commit()
        return {"fetched": fetched, "inserted": inserted, "errors": errors}
    finally:
        db.close()


def get_corporate_actions(user_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT * FROM corporate_actions WHERE user_id=:uid ORDER BY ex_date DESC"),
                          {"uid": user_id}).fetchall()
        result = []
        for r in rows:
            d = dict(r._mapping)
            try:
                d["action_details"] = json.loads(d.get("action_details", "{}"))
            except:
                d["action_details"] = {}
            result.append(d)
        return result
    finally:
        db.close()


def add_manual_corp_action(user_id: int, data: dict) -> dict:
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO corporate_actions
                           (user_id, symbol, isin, company_name, action_type, ex_date, record_date,
                            action_details, source, is_verified, notes)
                           VALUES (:uid,:sym,:isin,:comp,:act,:ex,:rec,:det,'manual',1,:notes)"""),
                   {"uid": user_id, "sym": data["symbol"].upper(), "isin": data.get("isin",""),
                    "comp": data.get("company_name",""), "act": data["action_type"],
                    "ex": data["ex_date"], "rec": data.get("record_date", data["ex_date"]),
                    "det": json.dumps(data.get("action_details", {})), "notes": data.get("notes","")})
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()