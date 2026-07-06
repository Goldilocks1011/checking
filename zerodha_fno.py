"""
Zerodha F&O Tradebook Parser — v3
===================================
Handles the actual Zerodha F&O tradebook format (sheet name: 'F&O').

Header row cols (0-indexed):
  0=None  1=Symbol  2=ISIN  3=Trade Date  4=Exchange  5=Segment
  6=Series  7=Trade Type  8=Auction  9=Quantity  10=Price
  11=Trade ID  12=Order ID  13=Order Execution Time  14=Expiry Date (header=None)

Symbol format examples:
  IDEA26APR10CE          → underlying=IDEA,       expiry from col14, strike=10,    type=CE
  BSE26APR3000CE         → underlying=BSE,         expiry from col14, strike=3000,  type=CE
  BAJAJ-AUTO26APR10000CE → underlying=BAJAJ-AUTO,  expiry from col14, strike=10000, type=CE
  TRENT26MAY4000PE       → underlying=TRENT,       expiry from col14, strike=4000,  type=PE
  CANBK26MAYFUT          → underlying=CANBK,       expiry from col14, type=FUT

KEY FIX v3: expiry date read directly from the last column (col 14),
            NOT calculated from last-Thursday. This is always exact.
"""
import io
import re
import pandas as pd
from datetime import datetime

_MONTHS = r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)'
_ZRD_RE = re.compile(rf'\d{{2}}{_MONTHS}(?:FUT|(\d+)(CE|PE))$', re.IGNORECASE)


def _parse_symbol(sym: str) -> dict:
    """
    Parse Zerodha F&O symbol into (underlying, instrument_type, strike_price).
    Expiry is determined separately from the dedicated expiry column.
    """
    sym = str(sym).strip()
    m = _ZRD_RE.search(sym)
    if not m:
        return {"underlying": sym, "instrument_type": "FUT", "strike_price": 0}

    underlying = sym[: m.start()]

    if m.group(3):          # option: DDMMMSTRIKECE/PE
        inst_type = m.group(3).upper()
        try:
            strike = float(m.group(2))
        except Exception:
            strike = 0.0
    else:                   # future: DDMMMFUT
        inst_type = "FUT"
        strike = 0.0

    return {
        "underlying":      underlying,
        "instrument_type": inst_type,
        "strike_price":    strike,
    }


def _sf(val, default=0.0) -> float:
    try:
        v = str(val).strip()
        return float(v) if v not in ("", "nan", "None", "False", "True") else default
    except Exception:
        return default


def _parse_date(val) -> str:
    """Parse various date/datetime values to YYYY-MM-DD string."""
    if val is None:
        return ""
    s = str(val).strip()
    # pandas Timestamp or datetime object
    if hasattr(val, 'strftime'):
        return val.strftime("%Y-%m-%d")
    # ISO with time: "2026-04-02T11:06:03"
    if "T" in s:
        s = s.split("T")[0]
    # Already YYYY-MM-DD
    if re.match(r'\d{4}-\d{2}-\d{2}', s):
        return s[:10]
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return s[:10] if len(s) >= 10 else s


def parse(file, broker: str = "Zerodha", scrip_master_path: str = None) -> tuple[list[dict], list[dict]]:
    """
    Parse Zerodha F&O tradebook.

    Returns:
        (fno_transactions, open_positions)
        open_positions is always [] — Zerodha tradebook files don't include
        a positions sheet. Net open positions are computed on-the-fly by
        fno_positions.py's fallback (_compute_from_transactions).
    """
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "zerodha_fno.xlsx")

    try:
        xl = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    except Exception as e:
        raise ValueError(f"Cannot open Zerodha F&O file: {e}")

    # Find the F&O sheet
    fo_sheet = None
    for sh in xl.sheet_names:
        su = sh.strip().upper().replace(" ", "").replace("&", "")
        if su in ("FO", "FNO", "F&O"):
            fo_sheet = sh
            break
    if fo_sheet is None:
        # broader search
        for sh in xl.sheet_names:
            if any(k in sh.lower() for k in ("f&o", "fno", "fo", "tradebook", "futures")):
                fo_sheet = sh
                break
    if fo_sheet is None:
        fo_sheet = xl.sheet_names[0]  # last resort

    df_raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=fo_sheet,
                           header=None, engine="openpyxl")

    # Find header row (contains 'Symbol' and 'Trade Date')
    hdr_idx = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row if str(v).strip() not in ("nan", "None", "")]
        if "Symbol" in vals and "Trade Date" in vals:
            hdr_idx = i
            break

    if hdr_idx is None:
        raise ValueError("Header row not found in Zerodha F&O tradebook")

    header_row = list(df_raw.iloc[hdr_idx])

    # Build col_name → index map; handle duplicate None headers
    col_map: dict[str, int] = {}
    for idx, val in enumerate(header_row):
        s = str(val).strip() if val is not None else ""
        if s and s not in ("nan", "None"):
            col_map[s] = idx

    # Find the expiry date column: it's the last column whose header is None/blank
    # In actual Zerodha files this is always col 14.
    expiry_col_idx = None
    for idx in range(len(header_row) - 1, -1, -1):
        v = header_row[idx]
        if v is None or str(v).strip() in ("nan", "None", ""):
            expiry_col_idx = idx
            break
    if expiry_col_idx is None or expiry_col_idx == 0:
        expiry_col_idx = 14   # hard fallback

    def _getcol(name: str, fallback: int, row: list):
        idx = col_map.get(name, fallback)
        return row[idx] if idx < len(row) else None

    results = []
    for i in range(hdr_idx + 1, len(df_raw)):
        row = list(df_raw.iloc[i])

        # Skip empty rows
        if not any(v is not None and str(v).strip() not in ("", "nan", "None") for v in row):
            continue

        sym = str(_getcol("Symbol", 1, row) or "").strip()
        if not sym or sym.lower() in ("nan", "symbol", ""):
            continue

        # Segment filter: skip equity rows that ended up in this sheet
        segment = str(_getcol("Segment", 5, row) or "").strip().upper()
        if segment and segment in ("EQ", "EQUITY"):
            continue

        trade_date_raw = _getcol("Trade Date", 3, row)
        trade_date = _parse_date(trade_date_raw)
        if not trade_date:
            continue

        raw_type = str(_getcol("Trade Type", 7, row) or "").strip().lower()
        if raw_type not in ("buy", "sell"):
            continue
        trade_type = raw_type.upper()

        qty = _sf(_getcol("Quantity", 9, row))
        if qty <= 0:
            continue

        price = _sf(_getcol("Price", 10, row))
        # price CAN be 0 for options that expired worthless — allow it

        # Read expiry directly from the dedicated column
        expiry_raw = row[expiry_col_idx] if expiry_col_idx < len(row) else None
        expiry_date = _parse_date(expiry_raw) if expiry_raw else ""

        parsed = _parse_symbol(sym)

        results.append({
            "symbol":          sym,
            "underlying":      parsed["underlying"],
            "exchange":        "NSE",
            "instrument_type": parsed["instrument_type"],
            "expiry_date":     expiry_date,
            "strike_price":    parsed["strike_price"],
            "trade_date":      trade_date,
            "trade_type":      trade_type,
            "quantity":        qty,
            "price":           price,
            "brokerage":       0.0,
            "tax_charges":     0.0,
            "broker":          broker,
            "source_file":     fname,
            "remarks":         "",
        })

    return results, []  # no positions sheet in tradebook format