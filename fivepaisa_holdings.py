# """
# 5paisa Holdings Parser
# ========================
# Parses 5paisa backoffice holdings export (Excel/CSV).
# 5paisa holdings columns typically:
#   Company Name | ISIN | Quantity | Avg Rate | Current Rate | Current Value | Profit/Loss

# Also handles variant column names across different export formats.
# """
# import pandas as pd
# import io


# # All possible column name variants for each field (lowercased for matching)
# _SYMBOL_COLS = ["company name", "scrip name", "scrip", "symbol", "stock name",
#                 "stock", "name", "instrument"]
# _ISIN_COLS = ["isin", "isin no", "isin code"]
# _QTY_COLS = ["quantity", "qty", "qty.", "net qty", "holdings qty", "total qty",
#              "free qty", "available qty"]
# _AVG_COLS = ["avg rate", "avg. rate", "average rate", "avg cost", "avg. cost",
#              "average price", "buy avg", "buy average", "purchase price",
#              "avg price", "avg. price"]


# def _find_col(df_columns: list[str], candidates: list[str]) -> str | None:
#     """Find a column name from a list of candidates (case-insensitive)."""
#     col_lower = {c.strip().lower(): c for c in df_columns}
#     for cand in candidates:
#         if cand in col_lower:
#             return col_lower[cand]
#     return None


# def parse(file, broker: str = "5paisa") -> list[dict]:
#     file_bytes = file.read() if hasattr(file, "read") else file
#     fname = getattr(file, "name", "5paisa_holdings.xls")

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
#             raise ValueError("Could not open 5paisa holdings file")

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
#         raise ValueError("Header row not found in 5paisa holdings file")

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
#         # Skip summary/total rows
#         if "total" in symbol.lower() or "grand" in symbol.lower():
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
5paisa Holdings Parser
======================
Parses 5paisa Portfolio / Holdings statement (CSV/Excel).

5paisa format typically includes:
  - Company Name / Symbol / Scrip Name
  - ISIN / Scripcode
  - Quantity / Qty
  - Average Price / Cost Price / Entry Price
  - LTP / Last Price / Market Price
  - Market Value / Current Value / Investment Value

Returns list of holdings dicts with normalized keys.
"""
import pandas as pd
import io
import math

def parse(file, broker: str = "5paisa") -> list[dict]:
    """
    Parse 5paisa holdings file.
    Returns: [{"symbol", "isin", "quantity", "avg_cost", "market_price", "market_value", "as_of_date"}, ...]
    """
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "5paisa_holdings.csv")

    # Try CSV first
    try:
        df = pd.read_csv(io.BytesIO(file_bytes), dtype=str)
    except Exception:
        # Fallback to Excel
        try:
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None)
        except Exception:
            raise ValueError("Could not parse 5paisa holdings file")

    # ⭐ CRITICAL FIX: Find the actual header row
    header_idx = None
    for i in range(min(30, len(df))):
        row_vals = [str(v).strip().lower() for v in df.iloc[i].values]
        # Look for rows containing both a company/symbol and a quantity field
        if (any('company' in v or 'symbol' in v for v in row_vals)) and \
           any('qty' in v or 'quantity' in v for v in row_vals):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Header row not found (needs 'Company/Symbol' and 'Qty' columns)")

    # Set the header row, and slice the dataframe from the next row
    df.columns = [str(v).strip() for v in df.iloc[header_idx]]
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    df = df.dropna(how='all')

    # ... then continue with your column normalization and mapping ...
    df.columns = df.columns.str.strip().str.lower()
    df = df.loc[:, df.columns.notna()]
    col_map = {
        "company": "symbol", 
        "company name": "symbol",
        "symbol": "symbol",
        "scripname": "symbol",
        "script name": "symbol",
        "isin": "isin",
        "scripcode": "isin",
        "qty": "quantity",
        "quantity": "quantity",
        "avg.price": "avg_cost",
        "avg price": "avg_cost",
        "average price": "avg_cost",
        "cost price": "avg_cost",
        "entry price": "avg_cost",
        "ltp": "market_price",
        "last price": "market_price",
        "market price": "market_price",
        "current price": "market_price",
        "current market value": "market_price",
        "market value": "market_value",
        "current value": "market_value",
        "investment value": "market_value",
    }

    df = df.rename(columns=col_map)

    results = []
    for _, row in df.iterrows():
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol or symbol in ("NAN", ""):
            continue

        # ✅ FIX: Handle Quantity. float(nan) returns nan, which is NOT <= 0!
        try:
            qty = float(row.get("quantity", 0))
        except (ValueError, TypeError):
            qty = 0.0
        
        # MUST explicitly check math.isnan() to catch the footer rows
        if math.isnan(qty) or qty <= 0:
            continue

        # ✅ FIX: Handle Avg Cost safely - force nan to 0.0
        try:
            avg_cost = float(row.get("avg_cost", 0))
        except (ValueError, TypeError):
            avg_cost = 0.0
        if math.isnan(avg_cost):
            avg_cost = 0.0

        # ✅ FIX: Handle Market Price safely - force nan to 0.0
        try:
            market_price = float(row.get("market_price", 0))
        except (ValueError, TypeError):
            market_price = 0.0
        if math.isnan(market_price):
            market_price = 0.0

        # ✅ FIX: Handle Market Value safely - force nan to 0.0
        try:
            market_value = float(row.get("market_value", 0))
        except (ValueError, TypeError):
            market_value = 0.0
        if math.isnan(market_value):
            market_value = 0.0

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
            "as_of_date": "",
            "broker": broker,
            "source_file": fname,
        })
    
    if not results:
        raise ValueError("No holdings found in the file")

    return results