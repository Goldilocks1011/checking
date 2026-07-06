"""
5paisa Equity Transaction Report Parser — v3 (Fixed)
=====================================================
Correctly handles ALL Remarks types from 5paisa files:

  Remarks              → trade_type     price=0 ok?  Notes
  ─────────────────────────────────────────────────────────
  Buy Trade            → BUY            No           Normal market buy
  Sell Trade           → SELL           No           Normal market sell
  Auction              → BUY            No           Circuit-breaker auction buy
  OffMarket-In         → TRANSFER_IN    Yes          Inter-demat transfer received
  OffMarket-Out        → TRANSFER_OUT   Yes          Inter-demat transfer sent (no P&L)
  OFFMarket            → TRANSFER_IN    Yes          Variant spelling
  Bonus - N:M          → BONUS          Yes ✓        Free shares — price=0 is CORRECT
  Demerger-In-N:M      → DEMERGER_IN    Yes ✓        Spinoff shares — price=0 is CORRECT
  Merger-Out -N:M      → MERGER_OUT     Yes          Merger exit — no taxable P&L
  BI-Manual-           → BONUS          Yes          Manual credit (treat like bonus)

ROOT CAUSE OF BUG: Old parser had `if qty <= 0 or price <= 0: continue`
which silently dropped ALL corporate actions (Bonus/Demerger) and caused
Jio Financial Serv., ITC Hotels, Wipro bonus etc. to never appear in UI.
"""
import pandas as pd
import io
import re


def _classify(remark: str) -> str:
    r = str(remark).strip().lower()
    if "offmarket-out" in r or "offmarket-out" in r:
        return "TRANSFER_OUT"
    if "offmarket-in" in r or r == "offmarket":
        return "TRANSFER_IN"
    if "auction" in r:
        return "AUCTION"
    if "bonus" in r or "bi-manual" in r:
        return "BONUS"
    if "demerger-in" in r:
        return "DEMERGER_IN"
    if "merger-out" in r:
        return "MERGER_OUT"
    if "buy" in r:
        return "BUY"
    if "sell" in r:
        return "SELL"
    return "UNKNOWN"


def parse(file, broker: str = "5paisa") -> list[dict]:
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "5paisa_file.xls")

    df_raw = None
    for engine in ("openpyxl", "xlrd"):
        try:
            df_raw = pd.read_excel(
                io.BytesIO(file_bytes), sheet_name=0, header=None, engine=engine
            )
            break
        except Exception:
            continue
    if df_raw is None:
        raise ValueError("Could not open 5paisa file")

    # Find header row
    hdr = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "Transaction Date" in vals and "Company Name" in vals:
            hdr = i
            break
    if hdr is None:
        raise ValueError("Header row not found in 5paisa file")

    df_raw.columns = df_raw.iloc[hdr]
    df = df_raw.iloc[hdr + 1:].reset_index(drop=True)
    df.columns = ["Transaction Date", "Company Name", "Exchange",
                  "Type", "Quantity", "Price", "Tax/Charges", "Brokerage", "Remarks"]
    df["Transaction Date"] = pd.to_datetime(df["Transaction Date"], errors="coerce")
    df = df.dropna(subset=["Transaction Date"])
    df["Remarks"] = df["Remarks"].fillna("").astype(str).str.strip()

    def sf(val, default=0.0):
        try:
            v = str(val).strip()
            return float(v) if v not in ("", "nan", "None") else default
        except Exception:
            return default

    results = []
    for _, row in df.iterrows():
        exch_raw = str(row.get("Exchange", "")).strip()
        if not exch_raw or exch_raw.lower() in ("nan", "exchange"):
            continue

        exup = exch_raw.upper()
        # === NEW FIX ===
        if "FNO" in exup or "FO" in exup:
            continue
        # ===============

        clean_exch = "NSE" if "NSE" in exup else ("BSE" if "BSE" in exup else exup)
        company = str(row["Company Name"]).strip()
        if not company or company.lower() in ("nan", "company name"):
            continue

        try:
            trade_date = row["Transaction Date"].strftime("%Y-%m-%d")
        except Exception:
            continue

        remark   = row["Remarks"]
        txn_class = _classify(remark)

        qty       = sf(row["Quantity"])
        price     = sf(row["Price"])
        brokerage = sf(row["Brokerage"])
        tax       = sf(row["Tax/Charges"])
        exchange  = str(row.get("Exchange", "NSE")).strip()
        if exchange.lower() == "nan":
            exchange = "NSE"

        if qty <= 0:
            continue                          # truly empty row

        # ── Map to final trade_type ────────────────────────────────────────
        # AUCTION is a special market buy — store as BUY
        if txn_class == "AUCTION":
            txn_class = "BUY"

        # For normal BUY/SELL: price must be > 0
        # For corporate actions (BONUS, DEMERGER_IN, TRANSFER_IN, TRANSFER_OUT,
        # MERGER_OUT): price CAN be 0 — do NOT skip them
        if txn_class in ("BUY", "SELL") and price <= 0:
            continue

        # UNKNOWN rows — skip
        if txn_class == "UNKNOWN":
            continue

        results.append({
            "symbol":       company,
            "company_name": company,
            "exchange":     exchange,
            "isin":         "",
            "segment":      "EQ",
            "trade_date":   trade_date,
            "quantity":     qty,
            "price":        price,
            "trade_type":   txn_class,
            "brokerage":    brokerage,
            "tax_charges":  tax,
            "broker":       broker,
            "source_file":  fname,
            "remarks":      remark,
        })

    return results