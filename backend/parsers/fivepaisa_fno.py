"""
5paisa F&O Transaction Report Parser
=====================================
File: Trade_Report_FNO_<ClientID>.xls  (XLSX internally)

Contract format:
  SYMBOL~O:DD-MMM-YY:TYPE:STRIKE   → Option  (CE / PE)
  SYMBOL~F:DD-MMM-YY               → Future

Columns (row 8 is header):
  Transaction Date | Contract | Expiry Date | Option Type | Strike Price |
  Type (Buy/Sell) | Quantity | Price | Tax/Charges | Brokerage | Remarks
"""
import io
import re
import pandas as pd
from datetime import datetime


def _sf(val, default=0.0) -> float:
    try:
        v = str(val).strip()
        return float(v) if v not in ("", "nan", "None") else default
    except Exception:
        return default


def _parse_expiry(date_str: str) -> str:
    """Convert DD-MMM-YY → YYYY-MM-DD."""
    for fmt in ("%d-%b-%y", "%d/%b/%y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return str(date_str).strip()


def _parse_contract(contract: str) -> dict:
    """
    Parse 5paisa contract string:
      BSE~O:28-APR-26:PE:2500  → underlying=BSE, type=PE, expiry=2026-04-28, strike=2500
      UPL~F:30-MAR-26          → underlying=UPL, type=FUT, expiry=2026-03-30, strike=0
    """
    parts = str(contract).strip().split("~")
    underlying = parts[0].strip()
    if len(parts) < 2:
        return {"underlying": underlying, "instrument_type": "FUT",
                "expiry_date": "", "strike_price": 0}

    right = parts[1]
    if right.startswith("F:"):
        expiry_str = right[2:]
        return {
            "underlying":      underlying,
            "instrument_type": "FUT",
            "expiry_date":     _parse_expiry(expiry_str),
            "strike_price":    0,
        }
    elif right.startswith("O:"):
        seg = right[2:].split(":")
        expiry_str  = seg[0] if len(seg) > 0 else ""
        opt_type    = seg[1].upper().strip() if len(seg) > 1 else "CE"
        strike      = _sf(seg[2]) if len(seg) > 2 else 0
        return {
            "underlying":      underlying,
            "instrument_type": opt_type,    # CE or PE
            "expiry_date":     _parse_expiry(expiry_str),
            "strike_price":    strike,
        }
    return {"underlying": underlying, "instrument_type": "FUT",
            "expiry_date": "", "strike_price": 0}


def parse(file, broker: str = "5paisa") -> list[dict]:
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "5paisa_fno.xls")

    df_raw = None
    for engine in ("openpyxl", "xlrd"):
        try:
            df_raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0,
                                   header=None, engine=engine)
            break
        except Exception:
            continue
    if df_raw is None:
        raise ValueError("Cannot open 5paisa F&O file")

    # Find header row containing "Transaction Date" and "Contract"
    hdr = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "Transaction Date" in vals and "Contract" in vals:
            hdr = i
            break
    if hdr is None:
        raise ValueError("Header row not found in 5paisa F&O file")

    df_raw.columns = list(df_raw.iloc[hdr])
    df = df_raw.iloc[hdr + 1:].reset_index(drop=True)

    # Rename columns to known names
    col_map = {
        "Transaction Date": "trade_date",
        "Contract": "contract",
        "Expiry Date": "expiry_date_raw",
        "Option Type": "option_type",
        "Strike Price": "strike_price",
        "Type": "trade_type_raw",
        "Quantity": "quantity",
        "Price": "price",
        "Tax/Charges": "tax_charges",
        "Brokerage": "brokerage",
        "Remarks": "remarks",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"])

    results = []
    for _, row in df.iterrows():
        contract = str(row.get("contract", "")).strip()
        if not contract or contract.lower() in ("nan", "contract"):
            continue

        parsed = _parse_contract(contract)
        qty   = _sf(row.get("quantity", 0))
        price = _sf(row.get("price",    0))
        if qty <= 0:
            continue

        raw_type  = str(row.get("trade_type_raw", "")).strip().lower()
        trade_type = "BUY" if raw_type == "buy" else "SELL"

        try:
            trade_date = row["trade_date"].strftime("%Y-%m-%d")
        except Exception:
            continue

        results.append({
            "symbol":          contract,
            "underlying":      parsed["underlying"],
            "exchange":        "NSE",
            "instrument_type": parsed["instrument_type"],
            "expiry_date":     parsed["expiry_date"],
            "strike_price":    parsed["strike_price"],
            "trade_date":      trade_date,
            "trade_type":      trade_type,
            "quantity":        qty,
            "price":           price,
            "brokerage":       _sf(row.get("brokerage",   0)),
            "tax_charges":     _sf(row.get("tax_charges", 0)),
            "broker":          broker,
            "source_file":     fname,
            "remarks":         str(row.get("remarks", "")).strip(),
        })

    return results