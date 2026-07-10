# """
# Zerodha Holdings Parser
# ========================
# Parses Zerodha Console holdings export (Excel/CSV).
# Zerodha holdings columns typically:
#   Instrument | ISIN | Qty. | Avg. cost | LTP | Cur. val | P&L | Net chg. | Day chg.

# Also handles variant column names across different export formats.
# """
# import pandas as pd
# import io


# # All possible column name variants for each field (lowercased for matching)
# _SYMBOL_COLS = ["instrument", "symbol", "tradingsymbol", "trading symbol", "scrip", "stock"]
# _ISIN_COLS = ["isin"]
# _QTY_COLS = ["qty.", "qty", "quantity", "holdings qty", "net qty", "total qty"]
# _AVG_COLS = ["avg. cost", "avg cost", "average price", "avg. price", "avg price",
#              "buy avg.", "buy avg", "buy average", "avg_cost"]


# def _find_col(df_columns: list[str], candidates: list[str]) -> str | None:
#     """Find a column name from a list of candidates (case-insensitive)."""
#     col_lower = {c.strip().lower(): c for c in df_columns}
#     for cand in candidates:
#         if cand in col_lower:
#             return col_lower[cand]
#     return None


# def parse(file, broker: str = "Zerodha") -> list[dict]:
#     file_bytes = file.read() if hasattr(file, "read") else file
#     fname = getattr(file, "name", "zerodha_holdings.xlsx")

#     # Try reading as Excel first, then CSV
#     df_raw = None
#     for engine in ("openpyxl", "xlrd"):
#         try:
#             df_raw = pd.read_excel(
#                 io.BytesIO(file_bytes), sheet_name=0, header=None, engine=engine
#             )
#             break
#         except Exception:
#             continue

#     if df_raw is None:
#         try:
#             df_raw = pd.read_csv(io.BytesIO(file_bytes), header=None)
#         except Exception:
#             raise ValueError("Could not open Zerodha holdings file")

#     # Find header row by looking for key columns
#     hdr = None
#     for i, row in df_raw.iterrows():
#         vals = [str(v).strip().lower() for v in row]
#         has_symbol = any(c in vals for c in _SYMBOL_COLS)
#         has_qty = any(c in vals for c in _QTY_COLS)
#         if has_symbol and has_qty:
#             hdr = i
#             break

#     if hdr is None:
#         raise ValueError("Header row not found in Zerodha holdings file")

#     df = df_raw.iloc[hdr:].reset_index(drop=True)
#     df.columns = [str(c).strip() for c in df.iloc[0]]
#     df = df.iloc[1:].reset_index(drop=True)
#     df = df.dropna(how="all")

#     cols = list(df.columns)
#     sym_col = _find_col(cols, _SYMBOL_COLS)
#     isin_col = _find_col(cols, _ISIN_COLS)
#     qty_col = _find_col(cols, _QTY_COLS)
#     avg_col = _find_col(cols, _AVG_COLS)

#     if not sym_col or not qty_col:
#         raise ValueError(f"Required columns not found. Found: {cols}")

#     results = []
#     for _, row in df.iterrows():
#         symbol = str(row.get(sym_col, "")).strip()
#         if not symbol or symbol.lower() in ("nan", "none", ""):
#             continue

#         try:
#             qty = float(str(row.get(qty_col, 0)).replace(",", "").strip())
#         except (ValueError, TypeError):
#             qty = 0.0
#         if qty <= 0:
#             continue

#         isin = ""
#         if isin_col:
#             isin = str(row.get(isin_col, "")).strip()
#             if isin.lower() in ("nan", "none"):
#                 isin = ""

#         avg_price = 0.0
#         if avg_col:
#             try:
#                 avg_price = float(str(row.get(avg_col, 0)).replace(",", "").strip())
#             except (ValueError, TypeError):
#                 avg_price = 0.0

#         results.append({
#             "symbol": symbol,
#             "isin": isin.upper() if isin else "",
#             "quantity": qty,
#             "avg_buy_price": round(avg_price, 4),
#             "broker": broker,
#         })

#     return results

"""
Zerodha Holdings Parser
========================
Parses Zerodha Holdings report (CSV/Excel).

Expected columns (case-insensitive):
  - Symbol / Tradingsymbol
  - ISIN
  - Quantity / Qty
  - Average Price / Avg Cost / Entry Price
  - Last Price / LTP / Current Price
  - Investment Value / Total Invested
  - Current Value

Returns list of holdings dicts with normalized keys.
"""
import pandas as pd
import io


def parse(file, broker: str = "Zerodha") -> list[dict]:
    """
    Parse Zerodha holdings file.
    Returns: [{"symbol", "isin", "quantity", "avg_cost", "market_price", "market_value", "as_of_date"}, ...]
    """
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "zerodha_holdings.csv")

    # Fallback to Excel
    try:
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None)
    except Exception:
        raise ValueError("Could not parse Zerodha holdings file as CSV or Excel")

    # ⭐ CRITICAL FIX: Find the actual header row
    header_idx = None
    for i in range(min(30, len(df))):
        row_vals = [str(v).strip().lower() for v in df.iloc[i].values]
        # Look for rows containing both a symbol/isin and a quantity field
        if (any('symbol' in v for v in row_vals) or any('isin' in v for v in row_vals)) and \
           any('qty' in v or 'quantity' in v for v in row_vals):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Header row not found (needs 'Symbol' and 'Qty' columns)")

    # Set the header row, and slice the dataframe from the next row
    df.columns = [str(v).strip() for v in df.iloc[header_idx]]
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    df = df.dropna(how='all')

    # ... then continue with your column normalization and mapping ...
    df.columns = df.columns.str.strip().str.lower()

    # Map possible column names to standard names
    col_map = {
        "tradingsymbol": "symbol",
        "symbol": "symbol",
        "isin": "isin",
        "qty": "quantity",
        "quantity": "quantity",
        "quantity available": "quantity",  
        "average price": "avg_cost",
        "avg cost": "avg_cost",
        "entry price": "avg_cost",
        "avg price": "avg_cost",
        "last price": "market_price",
        "ltp": "market_price",
        "current price": "market_price",
        "previous closing price": "market_price", 
        "investment value": "market_value",
        "total invested": "market_value",
        "current value": "market_value",
    }

    # Rename columns based on mapping
    df = df.rename(columns=col_map)

    required_cols = ["symbol", "quantity"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    results = []
    for _, row in df.iterrows():
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol or symbol == "NAN":
            continue

        try:
            qty = float(row.get("quantity", 0) or 0)
        except Exception:
            qty = 0

        if qty <= 0:
            continue

        try:
            avg_cost = float(row.get("avg_cost", 0) or 0)
        except Exception:
            avg_cost = 0

        try:
            market_price = float(row.get("market_price", 0) or 0)
        except Exception:
            market_price = 0

        try:
            market_value = float(row.get("market_value", 0) or 0)
        except Exception:
            market_value = market_price * qty if market_price else 0

        isin = str(row.get("isin", "")).strip().upper()
        if isin in ("NAN", ""):
            isin = ""

        results.append({
            "symbol": symbol,
            "isin": isin,
            "quantity": qty,
            "avg_cost": avg_cost,
            "market_price": market_price,
            "market_value": market_value,
            "as_of_date": "",  # Zerodha doesn't always include date; set to blank
            "broker": broker,
            "source_file": fname,
        })

    if not results:
        raise ValueError("No holdings found in the file")

    return results