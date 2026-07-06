"""
Zerodha Ledger Parser — v3
===========================
File: ledger-TL7712.xlsx  /  ledgerTL7712_6.xlsx

Unique Zerodha markers (used to distinguish from IIFL which shares
Particulars/Debit/Credit column names):
  - "Posting Date"   (IIFL uses plain "Date")
  - "Net Balance"    (IIFL uses "Balance")
  - "Voucher Type"   (IIFL uses "Voucher")
  - "Cost Center"

Header detection now requires "Posting Date" to be present — this
prevents the Zerodha parser from accidentally accepting IIFL ledger files.
"""
import io
import re
import pandas as pd


def _sf(val, default=0.0) -> float:
    try:
        v = str(val).strip().replace(",", "")
        return float(v) if v not in ("", "nan", "None") else default
    except Exception:
        return default


def parse(file) -> dict:
    file_bytes = file.read() if hasattr(file, "read") else file

    df_raw = None
    for engine in ("openpyxl", "xlrd"):
        try:
            df_raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0,
                                   header=None, engine=engine)
            break
        except Exception:
            continue
    if df_raw is None:
        raise ValueError("Cannot open Zerodha ledger file")

    # Date range
    date_range = ""
    for i in range(15):
        for v in df_raw.iloc[i]:
            s = str(v)
            m = re.search(r'(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})', s)
            if m:
                date_range = f"{m.group(1)} to {m.group(2)}"

    # Find header row — MUST contain "Posting Date" (unique to Zerodha)
    # This prevents accidentally parsing IIFL files which have "Particulars"
    # but NOT "Posting Date".
    hdr = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row if str(v).strip() not in ("nan", "")]
        if "Particulars" in vals and "Debit" in vals and "Credit" in vals:
            if "Posting Date" in vals:   # ← Zerodha-unique check
                hdr = i
                break
    if hdr is None:
        raise ValueError(
            "Header row not found or file is not a Zerodha ledger "
            "(missing 'Posting Date' column — IIFL/5paisa files are not accepted here)"
        )

    df_raw.columns = list(df_raw.iloc[hdr])
    df = df_raw.iloc[hdr + 1:].reset_index(drop=True)

    # Rename Zerodha-specific column names to standard names
    df = df.rename(columns={
        "Posting Date": "Date",
        "Net Balance":  "Balance",
    })

    # Opening balance from first row that says "Opening Balance"
    opening_bal = 0.0
    ob = df[df["Particulars"].astype(str).str.lower().str.contains("opening", na=False)]
    if not ob.empty:
        v = ob.iloc[0].get("Balance", ob.iloc[0].get("Credit", 0))
        opening_bal = _sf(v)
        df = df[~df["Particulars"].astype(str).str.lower().str.contains("opening", na=False)]

    df = df[df["Particulars"].notna()]
    df = df[~df["Particulars"].astype(str).str.strip().isin(["nan", "", "Particulars"])]
    df = df[~df["Particulars"].astype(str).str.lower().str.startswith(
        ("sebi", "copyright", "cin", "email")
    )]

    df["Debit"]   = df["Debit"].apply(_sf)
    df["Credit"]  = df["Credit"].apply(_sf)
    df["Balance"] = df["Balance"].apply(_sf)
    df["Date"]    = pd.to_datetime(df["Date"], errors="coerce")
    df            = df.dropna(subset=["Date"])
    df["Month"]   = df["Date"].dt.to_period("M").astype(str)

    part = df["Particulars"].astype(str).str.strip()

    funds_added     = df[part.str.contains("Payin|pay in|fund credit|bank|payout of", case=False, na=False) &
                         (df["Credit"] > 0)]["Credit"].sum()
    funds_withdrawn = df[part.str.contains("Payout of|withdrawal", case=False, na=False)]["Debit"].sum()

    eq_cr  = df[part.str.contains("settlement.*equity|nseeq|bseeq|Net settlement for Equity", case=False, na=False)]["Credit"].sum()
    eq_dr  = df[part.str.contains("settlement.*equity|nseeq|bseeq|Net settlement for Equity", case=False, na=False)]["Debit"].sum()
    fno_cr = df[part.str.contains("fno|nsefno|bsefno", case=False, na=False)]["Credit"].sum()
    fno_dr = df[part.str.contains("fno|nsefno|bsefno", case=False, na=False)]["Debit"].sum()

    dp_txn   = df[part.str.contains("DP Charges", case=False, na=False)]["Debit"].sum()
    dp_amc   = df[part.str.contains("AMC for Demat", case=False, na=False)]["Debit"].sum()
    delayed  = df[part.str.contains("Delayed payment", case=False, na=False)]["Debit"].sum()
    total_chg = dp_txn + dp_amc + delayed

    closing_bal = df["Balance"].iloc[-1] if not df.empty else 0.0

    monthly = df.groupby("Month").agg(
        total_credit=("Credit", "sum"),
        total_debit=("Debit", "sum"),
        txn_count=("Particulars", "count"),
    ).reset_index()
    top_credit_months = monthly.nlargest(3, "total_credit")[["Month", "total_credit"]].to_dict("records")

    txns = df[["Date", "Particulars", "Debit", "Credit", "Balance"]].copy()
    txns["Date"] = txns["Date"].dt.strftime("%Y-%m-%d")

    return {
        "broker":           "Zerodha",
        "date_range":       date_range,
        "opening_balance":  opening_bal,
        "closing_balance":  closing_bal,
        "total_debit":      df["Debit"].sum(),
        "total_credit":     df["Credit"].sum(),
        "funds_added":      funds_added,
        "funds_withdrawn":  funds_withdrawn,
        "net_invested":     funds_added - funds_withdrawn,
        "bank_sources":     {},
        "eq_debit":         eq_dr,
        "eq_credit":        eq_cr,
        "fno_debit":        fno_dr,
        "fno_credit":       fno_cr,
        "eq_net":           eq_cr - eq_dr,
        "fno_net":          fno_cr - fno_dr,
        "dp_amc":           dp_amc,
        "dp_txn":           dp_txn,
        "margin_interest":  0.0,
        "mtf_interest":     0.0,
        "net_banking":      0.0,
        "delayed_payment":  delayed,
        "brokerage_reversal": 0.0,
        "mtf_funding":      0.0,
        "mtf_repayment":    0.0,
        "total_charges":    total_chg,
        "monthly":          monthly.to_dict("records"),
        "top_credit_months": top_credit_months,
        "top_txn_months":   [],
        "transactions":     txns.to_dict("records"),
        "total_rows":       len(txns),
    }