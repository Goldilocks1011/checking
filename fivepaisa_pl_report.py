"""
5paisa P&L Report Parser
========================
File: last_1_year_F_O_and_equity_PL_report.xls  (XLSX internally)

Parses TWO sheets:

  Equity sheet  — columns (0-indexed):
    0: Company Name
    1: Quantity
    2: Buy Value
    3: Sell Value
    4: Short Term P&L
    5: Long Term P&L
    6: Intraday P&L
    7: Total P&L

  FNO sheet  — columns:
    0: Contract Name  (format: SYMBOL:EXPIRY:TYPE:STRIKE)
       TYPE: CE=Call, PE=Put, XX=Futures
    1: Quantity
    2: Buy Value
    3: Sell Value
    4: Brought Forward P&L
    5: Intraday P&L
    6: Total P&L

Returns dict:
  {
    "report_date_range": "2025-04-01 to 2026-03-28",
    "equity":  [list of dicts],
    "fno":     [list of dicts],
    "eq_totals":  {qty, buy_value, sell_value, short_term, long_term, intraday, total},
    "fno_totals": {qty, buy_value, sell_value, brought_fwd, intraday, total},
  }
"""
import io
import re
import pandas as pd


def _sf(val, default=0.0) -> float:
    try:
        v = str(val).strip()
        return float(v) if v not in ("", "nan", "None") else default
    except Exception:
        return default


def _parse_date_range(df_raw) -> str:
    """Extract report date range from header rows."""
    for i in range(8):
        for v in df_raw.iloc[i]:
            s = str(v)
            m = re.search(r'from\s+(\S+)\s+[Tt]o\s+(\S+)', s)
            if m:
                return f"{m.group(1)} to {m.group(2)}"
    return ""


def _find_data_rows(df_raw) -> int:
    """Return index of first data row (row after the sub-header row 9)."""
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row]
        # Sub-header row contains 'Short Term' or 'Brought Forward'
        if any(x in vals for x in ("Short Term", "Brought Forward")):
            return i + 1   # data starts next row
    return 10  # fallback


def parse_equity(df_raw) -> tuple[list[dict], dict]:
    data_start = _find_data_rows(df_raw)
    rows = []
    totals = {"qty": 0, "buy_value": 0, "sell_value": 0,
              "short_term": 0, "long_term": 0, "intraday": 0, "total": 0}

    for _, row in df_raw.iloc[data_start:].iterrows():
        name = str(row.iloc[0]).strip()
        if not name or name.lower() in ("nan", "none", ""):
            continue
        if name.lower().startswith("grand total"):
            # Capture totals row
            totals = {
                "qty":        _sf(row.iloc[1]),
                "buy_value":  _sf(row.iloc[2]),
                "sell_value": _sf(row.iloc[3]),
                "short_term": _sf(row.iloc[4]),
                "long_term":  _sf(row.iloc[5]),
                "intraday":   _sf(row.iloc[6]),
                "total":      _sf(row.iloc[7]),
            }
            break
        if name.lower().startswith("sebi") or name.lower().startswith("copyright"):
            break

        rows.append({
            "company":    name,
            "qty":        _sf(row.iloc[1]),
            "buy_value":  _sf(row.iloc[2]),
            "sell_value": _sf(row.iloc[3]),
            "short_term_pnl": _sf(row.iloc[4]),
            "long_term_pnl":  _sf(row.iloc[5]),
            "intraday_pnl":   _sf(row.iloc[6]),
            "total_pnl":      _sf(row.iloc[7]),
        })

    return rows, totals


def _parse_contract(name: str) -> dict:
    """
    Parse contract string like 'ADANIENT:2026-01-27:PE:2100'
    Returns dict with symbol, expiry, option_type, strike, instrument_type.
    """
    parts = name.split(":")
    if len(parts) == 4:
        sym, expiry, otype, strike = parts
        otype = otype.strip().upper()
        if otype == "XX":
            instrument = "FUT"
            otype_label = "Futures"
        elif otype == "CE":
            instrument = "CE"
            otype_label = "Call"
        elif otype == "PE":
            instrument = "PE"
            otype_label = "Put"
        else:
            instrument = otype
            otype_label = otype
        return {
            "symbol":          sym.strip(),
            "expiry":          expiry.strip(),
            "option_type":     otype_label,
            "instrument_type": instrument,
            "strike":          _sf(strike),
        }
    return {"symbol": name, "expiry": "", "option_type": "", "instrument_type": "", "strike": 0}


def parse_fno(df_raw) -> tuple[list[dict], dict]:
    data_start = _find_data_rows(df_raw)
    rows = []
    totals = {"qty": 0, "buy_value": 0, "sell_value": 0,
              "brought_fwd": 0, "intraday": 0, "total": 0}

    for _, row in df_raw.iloc[data_start:].iterrows():
        name = str(row.iloc[0]).strip()
        if not name or name.lower() in ("nan", "none", ""):
            continue
        if name.lower().startswith("grand total"):
            totals = {
                "qty":         _sf(row.iloc[1]),
                "buy_value":   _sf(row.iloc[2]),
                "sell_value":  _sf(row.iloc[3]),
                "brought_fwd": _sf(row.iloc[4]),
                "intraday":    _sf(row.iloc[5]),
                "total":       _sf(row.iloc[6]),
            }
            break
        if name.lower().startswith("sebi") or name.lower().startswith("copyright"):
            break

        contract = _parse_contract(name)
        rows.append({
            **contract,
            "contract_name":  name,
            "qty":            _sf(row.iloc[1]),
            "buy_value":      _sf(row.iloc[2]),
            "sell_value":     _sf(row.iloc[3]),
            "brought_fwd_pnl": _sf(row.iloc[4]),
            "intraday_pnl":   _sf(row.iloc[5]),
            "total_pnl":      _sf(row.iloc[6]),
        })

    return rows, totals


def parse(file) -> dict:
    """Main entry point. Accepts a file-like object or bytes."""
    file_bytes = file.read() if hasattr(file, "read") else file

    df_eq  = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Equity",
                           engine="openpyxl", header=None)
    df_fno = pd.read_excel(io.BytesIO(file_bytes), sheet_name="FNO",
                           engine="openpyxl", header=None)

    date_range  = _parse_date_range(df_eq)
    eq_rows,  eq_totals  = parse_equity(df_eq)
    fno_rows, fno_totals = parse_fno(df_fno)

    return {
        "report_date_range": date_range,
        "equity":     eq_rows,
        "fno":        fno_rows,
        "eq_totals":  eq_totals,
        "fno_totals": fno_totals,
    }
