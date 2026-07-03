"""
Zerodha Tradebook Parser — v2 (Fixed)
=======================================
Zerodha tradebook is mostly clean (only BUY/SELL rows).

Fixes applied:
  - Handles edge case where Trade Type column has extra spaces / mixed case
  - Handles Auction rows (marked as 'buy' in Zerodha, which is correct)
  - Handles Bonus/Demerger rows if present (Zerodha sometimes includes them
    in extended P&L reports with Trade Type = 'bonus' or 'demerger')
  - Quantity 0 rows (order splits) are correctly skipped
  - Price 0 rows for corporate actions (bonus/demerger) are NOT skipped
    (previously `price <= 0: continue` would drop them)
"""
import pandas as pd
import io


def _map_type(raw: str) -> str:
    r = raw.strip().lower()
    if r in ("buy",):
        return "BUY"
    if r in ("sell",):
        return "SELL"
    if "bonus" in r:
        return "BONUS"
    if "demerger" in r or "split" in r:
        return "DEMERGER_IN"
    if "transfer" in r and "in" in r:
        return "TRANSFER_IN"
    if "transfer" in r and "out" in r:
        return "TRANSFER_OUT"
    return "UNKNOWN"


def parse(file, broker: str = "Zerodha") -> list[dict]:
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "zerodha_file.xlsx")

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
        raise ValueError("Could not open Zerodha file")

    # Find header row
    hdr = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "Symbol" in vals and "Trade Type" in vals:
            hdr = i
            break
    if hdr is None:
        raise ValueError("Header row not found in Zerodha file")

    df = df_raw.iloc[hdr:].reset_index(drop=True)
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    df = df.dropna(how="all")

    results = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol or symbol.lower() == "nan":
            continue

        raw_type = str(row.get("Trade Type", "")).strip()
        trade_type = _map_type(raw_type)

        if trade_type == "UNKNOWN":
            continue

        try:
            trade_date = pd.to_datetime(row.get("Trade Date")).strftime("%Y-%m-%d")
        except Exception:
            continue

        try:
            qty = float(row.get("Quantity", 0))
        except Exception:
            qty = 0
        if qty <= 0:
            continue

        try:
            price = float(row.get("Price", 0))
        except Exception:
            price = 0

        # For normal BUY/SELL, skip zero-price rows
        # For corporate actions (BONUS, DEMERGER_IN), price=0 is valid
        if trade_type in ("BUY", "SELL") and price <= 0:
            continue

        isin     = str(row.get("ISIN", "")).strip()
        exchange = str(row.get("Exchange", "NSE")).strip()
        segment  = str(row.get("Segment", "EQ")).strip()
        if exchange.lower() == "nan":
            exchange = "NSE"
        if segment.lower() == "nan":
            segment = "EQ"

        results.append({
            "symbol":       symbol,
            "company_name": symbol,
            "exchange":     exchange,
            "isin":         isin if isin.lower() != "nan" else "",
            "segment":      segment,
            "trade_date":   trade_date,
            "quantity":     qty,
            "price":        price,
            "trade_type":   trade_type,
            "brokerage":    0.0,
            "tax_charges":  0.0,
            "broker":       broker,
            "source_file":  fname,
            "remarks":      raw_type,
        })

    return results