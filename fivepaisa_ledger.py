"""
5paisa Ledger Report Parser
============================
File: LedgerReport_<ClientID>_<n>.xls  (XLSX internally)
Sheet: LedgerReport

Header rows (0-10):
  Row 4:  Report Name / date range
  Row 7:  Total Debit
  Row 8:  Total Credit
  Row 9:  Closing Balance
  Row 10: Opening Balance

Data starts at Row 12 (column headers), Row 13 onwards = transactions
Columns: Date | Segment | Particular | Description | Debit | Credit | Balance

Particular types:
  Funds Added             → deposit from bank
  Funds Withdrawn         → withdrawal to bank
  Bill - Cash             → equity settlement bill
  Bill - FNO              → F&O settlement bill
  Charges - Margin Plus   → margin interest
  MTF Interest            → MTF loan interest
  MTF Funding             → MTF loan disbursed
  MTF Funding Repayment   → MTF loan repaid
  DP AMC Charges          → DP annual charges
  DP txn Charges          → DP transaction charges
  Net Banking Charges     → net banking fee
  Charges - Delayed Payment → delayed payment charge
  Brokerage Reversal      → brokerage refund

Returns a rich dict with pre-computed insights.
"""
import io
import re
import pandas as pd
from collections import defaultdict


def _sf(val, default=0.0) -> float:
    try:
        v = str(val).strip()
        return float(v) if v not in ("", "nan", "None") else default
    except Exception:
        return default


def parse(file) -> dict:
    file_bytes = file.read() if hasattr(file, "read") else file
    df_raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0,
                           engine="openpyxl", header=None)

    # ── Extract summary rows ──────────────────────────────────────────────
    total_debit    = 0.0
    total_credit   = 0.0
    closing_bal    = 0.0
    opening_bal    = 0.0
    date_range     = ""

    for i in range(12):
        row = df_raw.iloc[i]
        vals = [str(v).strip() for v in row if str(v).strip() not in ("nan","None","")]
        if not vals:
            continue
        label = vals[0].lower()
        if "report name" in label or "ledger report" in label:
            m = re.search(r'(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})', vals[0])
            if m:
                date_range = f"{m.group(1)} to {m.group(2)}"
        if "total debit"    in label and len(vals) > 1: total_debit  = _sf(vals[1])
        if "total credit"   in label and len(vals) > 1: total_credit = _sf(vals[1])
        if "closing balance" in label and len(vals) > 1: closing_bal  = _sf(vals[1])
        if "opening balance" in label and len(vals) > 1: opening_bal  = _sf(vals[1])

    # ── Read transaction data ─────────────────────────────────────────────
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0,
                       engine="openpyxl", header=None, skiprows=12)
    df = df.iloc[:, :7]   # keep only first 7 columns, drop extra empty ones
    df.columns = ["Date", "Segment", "Particular", "Description", "Debit", "Credit", "Balance"]
    df = df[df["Particular"].notna()]
    df = df[df["Particular"].astype(str).str.strip() != "Particular"]  # drop header row

    # Remove footer rows
    df = df[~df["Particular"].astype(str).str.lower().str.startswith("sebi")]
    df = df[~df["Particular"].astype(str).str.lower().str.startswith("copyright")]
    df = df[~df["Particular"].astype(str).str.lower().str.startswith("cin")]
    df = df[~df["Particular"].astype(str).str.lower().str.startswith("email")]
    df = df[~df["Particular"].astype(str).str.lower().str.startswith("statement")]

    df["Debit"]  = df["Debit"].apply(_sf)
    df["Credit"] = df["Credit"].apply(_sf)
    df["Balance"]= df["Balance"].apply(_sf)
    df["Date"]   = pd.to_datetime(df["Date"], errors="coerce", dayfirst=False)
    df = df.dropna(subset=["Date"])
    df["Month"]  = df["Date"].dt.to_period("M").astype(str)

    part = df["Particular"].astype(str).str.strip()

    # ── Fund movements ────────────────────────────────────────────────────
    funds_added      = df[part == "Funds Added"]["Credit"].sum()
    funds_withdrawn  = df[part == "Funds Withdrawn"]["Debit"].sum()
    net_invested     = funds_added - funds_withdrawn

    # ── Trading activity ──────────────────────────────────────────────────
    eq_bills  = df[part == "Bill - Cash"]
    fno_bills = df[part == "Bill - FNO"]

    eq_debit   = eq_bills["Debit"].sum()
    eq_credit  = eq_bills["Credit"].sum()
    fno_debit  = fno_bills["Debit"].sum()
    fno_credit = fno_bills["Credit"].sum()

    # ── Charges breakdown ─────────────────────────────────────────────────
    margin_interest  = df[part == "Charges - Margin Plus"]["Debit"].sum()
    mtf_interest     = df[part == "MTF Interest"]["Debit"].sum()
    dp_amc           = df[part == "DP AMC Charges"]["Debit"].sum()
    dp_txn           = df[part == "DP txn Charges"]["Debit"].sum()
    net_banking      = df[part == "Net Banking Charges"]["Debit"].sum()
    delayed_payment  = df[part == "Charges - Delayed Payment"]["Debit"].sum()
    brok_reversal    = df[part == "Brokerage Reversal"]["Credit"].sum()
    mtf_funding      = df[part == "MTF Funding"]["Credit"].sum()
    mtf_repayment    = df[part == "MTF Funding Repayment"]["Debit"].sum()

    total_charges = (margin_interest + mtf_interest + dp_amc +
                     dp_txn + net_banking + delayed_payment - brok_reversal)

    # ── Monthly activity ──────────────────────────────────────────────────
    monthly = df.groupby("Month").agg(
        total_credit=("Credit", "sum"),
        total_debit=("Debit", "sum"),
        txn_count=("Particular", "count"),
    ).reset_index()

    top_credit_months = monthly.nlargest(3, "total_credit")[["Month","total_credit"]].to_dict("records")
    top_txn_months    = monthly.nlargest(3, "txn_count")[["Month","txn_count"]].to_dict("records")

    # ── Funds Added sources (bank name from description) ──────────────────
    funds_df = df[part == "Funds Added"].copy()
    bank_totals = defaultdict(float)
    for _, row in funds_df.iterrows():
        desc = str(row["Description"])
        # extract bank name e.g. "HDFC BANK", "AXIS BANK", "ICICI BANK"
        m = re.search(r'from\s+([A-Z ]+BANK[A-Z ]*)', desc, re.IGNORECASE)
        bank = m.group(1).strip().title() if m else "Unknown"
        bank_totals[bank] += row["Credit"]

    # ── All transactions (for raw table) ─────────────────────────────────
    txns = df[["Date","Segment","Particular","Description","Debit","Credit","Balance"]].copy()
    txns["Date"] = txns["Date"].dt.strftime("%Y-%m-%d")

    return {
        "broker": "5paisa",
        "date_range":       date_range,
        "opening_balance":  opening_bal,
        "closing_balance":  closing_bal,
        "total_debit":      total_debit,
        "total_credit":     total_credit,

        # Fund movements
        "funds_added":      funds_added,
        "funds_withdrawn":  funds_withdrawn,
        "net_invested":     net_invested,
        "bank_sources":     dict(bank_totals),

        # Trading
        "eq_debit":         eq_debit,
        "eq_credit":        eq_credit,
        "fno_debit":        fno_debit,
        "fno_credit":       fno_credit,
        "eq_net":           eq_credit - eq_debit,
        "fno_net":          fno_credit - fno_debit,

        # Charges
        "margin_interest":  margin_interest,
        "mtf_interest":     mtf_interest,
        "dp_amc":           dp_amc,
        "dp_txn":           dp_txn,
        "net_banking":      net_banking,
        "delayed_payment":  delayed_payment,
        "brokerage_reversal": brok_reversal,
        "mtf_funding":      mtf_funding,
        "mtf_repayment":    mtf_repayment,
        "total_charges":    total_charges,

        # Monthly
        "monthly":          monthly.to_dict("records"),
        "top_credit_months": top_credit_months,
        "top_txn_months":   top_txn_months,

        # Raw
        "transactions":     txns.to_dict("records"),
        "total_rows":       len(txns),
    }