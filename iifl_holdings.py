# """
# IIFL Holdings Parser
# ========================
# Parses IIFL backoffice holdings export (Excel/CSV).
# IIFL holdings columns typically:
#   Code | Name | ISIN | Quantity | Avg Price | Current Price | Value | P&L

# Also handles variant column names across different export formats.
# """
# import pandas as pd
# import io


# # All possible column name variants for each field (lowercased for matching)
# _SYMBOL_COLS = ["name", "code", "symbol", "scrip", "stock name", "instrument",
#                 "scrip name", "company name"]
# _ISIN_COLS = ["isin", "isin no", "isin code"]
# _QTY_COLS = ["quantity", "qty", "qty.", "net qty", "holdings qty", "total qty",
#              "free qty", "available qty", "bal qty"]
# _AVG_COLS = ["avg price", "avg. price", "average price", "avg cost", "avg. cost",
#              "buy avg", "buy average", "purchase price", "avg rate", "avg. rate",
#              "weighted avg price"]


# def _find_col(df_columns: list[str], candidates: list[str]) -> str | None:
#     """Find a column name from a list of candidates (case-insensitive)."""
#     col_lower = {c.strip().lower(): c for c in df_columns}
#     for cand in candidates:
#         if cand in col_lower:
#             return col_lower[cand]
#     return None


# def parse(file, broker: str = "IIFL") -> list[dict]:
#     file_bytes = file.read() if hasattr(file, "read") else file
#     fname = getattr(file, "name", "iifl_holdings.xls")

#     # Try reading as Excel first, then CSV
#     df_raw = None
#     for engine in ("xlrd", "openpyxl"):
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
#             raise ValueError("Could not open IIFL holdings file")

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
#         raise ValueError("Header row not found in IIFL holdings file")

#     df = df_raw.iloc[hdr:].reset_index(drop=True)
#     # Build unique column names (IIFL sometimes has duplicate headers)
#     raw_cols = list(df.iloc[0])
#     seen, cols = {}, []
#     for c in raw_cols:
#         label = str(c).strip() if str(c) not in ("nan", "None") else "_blank_"
#         seen[label] = seen.get(label, -1) + 1
#         cols.append(f"{label}_{seen[label]}" if seen[label] > 0 else label)
#     df.columns = cols
#     df = df.iloc[1:].reset_index(drop=True)
#     df = df.dropna(how="all")

#     all_cols = list(df.columns)
#     sym_col = _find_col(all_cols, _SYMBOL_COLS)
#     isin_col = _find_col(all_cols, _ISIN_COLS)
#     qty_col = _find_col(all_cols, _QTY_COLS)
#     avg_col = _find_col(all_cols, _AVG_COLS)

#     if not sym_col or not qty_col:
#         raise ValueError(f"Required columns not found. Found: {all_cols}")

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
IIFL Holdings Parser (Auto-detects Standard IIFL vs CDSL CAS Format)
======================================================================
Handles both the standard IIFL Portfolio Summary and the IIFL-generated
CDSL Consolidated Account Statement (sheet CDSLHoldingMaster).

Robustly handles:
  - .xls (legacy Excel) and .xlsx (modern Excel) files
  - CDSL format with dynamic column detection
  - Standard IIFL Portfolio Summary format
  - CSV fallback
"""
import pandas as pd
import io


def parse(file, broker: str = "IIFL") -> list[dict]:
    """Parse IIFL holdings file in any supported format."""
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "iifl_holdings.xlsx")

    # ─────────────────────────────────────────────────────────────────────────
    # ATTEMPT 1: CDSL CAS Format Detection
    # ─────────────────────────────────────────────────────────────────────────
    try:
        # Try both xlrd (for .xls) and openpyxl (for .xlsx)
        for engine in ["xlrd", "openpyxl"]:
            try:
                xl = pd.ExcelFile(io.BytesIO(file_bytes), engine=engine)
                sheet_names = xl.sheet_names
                
                # Look for CDSL or Holdings sheet
                cdsl_sheet = None
                for sname in sheet_names:
                    sname_upper = sname.upper()
                    if "CDSL" in sname_upper or ("HOLDING" in sname_upper and "MASTER" in sname_upper):
                        cdsl_sheet = sname
                        break
                
                if cdsl_sheet:
                    # Read the sheet
                    df_raw = pd.read_excel(
                        io.BytesIO(file_bytes),
                        sheet_name=cdsl_sheet,
                        header=None,
                        engine=engine
                    )
                    
                    if df_raw is not None and not df_raw.empty:
                        # Find header row dynamically
                        header_idx = None
                        for i in range(min(20, len(df_raw))):
                            row_str = " ".join(str(v).upper() for v in df_raw.iloc[i].values)
                            if "ISIN" in row_str and ("QTY" in row_str or "QUANTITY" in row_str):
                                header_idx = i
                                break
                        
                        if header_idx is not None:
                            # Extract holdings starting from header row
                            results = _parse_cdsl_format(df_raw, header_idx, broker, fname)
                            if results:
                                return results
                break  # Don't try next engine if one worked
            except Exception:
                continue
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # ATTEMPT 2: Standard IIFL Portfolio Summary Format
    # ─────────────────────────────────────────────────────────────────────────
    try:
        df_raw = None
        for engine in ["xlrd", "openpyxl"]:
            try:
                df_raw = pd.read_excel(
                    io.BytesIO(file_bytes),
                    sheet_name=0,
                    header=None,
                    engine=engine
                )
                break
            except Exception:
                continue

        if df_raw is None:
            # Try CSV
            df_raw = pd.read_csv(io.BytesIO(file_bytes), dtype=str)

        if df_raw is not None and not df_raw.empty:
            results = _parse_standard_iifl_format(df_raw, broker, fname)
            if results:
                return results
    except Exception:
        pass

    # If we reach here, no format matched
    raise ValueError("No holdings found in the file or file format not recognized")


def _parse_cdsl_format(df_raw: pd.DataFrame, header_idx: int, broker: str, fname: str) -> list[dict]:
    """Parse CDSL CAS format with dynamic column detection."""
    try:
        # Use header row to set column names
        header_row = df_raw.iloc[header_idx]
        df_raw.columns = [str(c).strip() for c in header_row]
        df = df_raw.iloc[header_idx + 1:].reset_index(drop=True)
        
        # Normalize column names
        df.columns = df.columns.str.lower().str.strip()
        
        # Find required columns dynamically
        isin_col = None
        name_col = None
        qty_col = None
        value_col = None
        price_col = None
        
        for col in df.columns:
            col_lower = col.lower()
            if "isin" in col_lower and isin_col is None:
                isin_col = col
            elif any(x in col_lower for x in ["name", "scrip", "description", "company"]) and name_col is None:
                name_col = col
            elif any(x in col_lower for x in ["qty", "quantity", "holdings", "closing", "units"]) and qty_col is None:
                qty_col = col
            elif any(x in col_lower for x in ["value", "current value", "market value", "holding value"]) and value_col is None:
                value_col = col
            elif any(x in col_lower for x in ["price", "closing price", "ltp", "current price"]) and price_col is None:
                price_col = col
        
        # Need at least qty and name columns
        if not qty_col or not name_col:
            return []
        
        results = []
        for _, row in df.iterrows():
            try:
                symbol = str(row.get(name_col, "")).strip().upper()
                if not symbol or symbol in ("NAN", "NONE", ""):
                    continue
                
                # Parse quantity
                qty_str = str(row.get(qty_col, "0")).replace(",", "").strip()
                qty = float(qty_str) if qty_str else 0.0
                
                if qty <= 0:
                    continue
                
                # Parse ISIN
                isin = ""
                if isin_col:
                    isin = str(row.get(isin_col, "")).strip().upper()
                    if isin in ("NAN", "NONE", ""):
                        isin = ""
                
                # Parse market value
                market_value = 0.0
                if value_col:
                    try:
                        val_str = str(row.get(value_col, "0")).replace(",", "").strip()
                        market_value = float(val_str) if val_str else 0.0
                    except:
                        pass
                
                # Parse market price
                market_price = 0.0
                if price_col:
                    try:
                        price_str = str(row.get(price_col, "0")).replace(",", "").strip()
                        market_price = float(price_str) if price_str else 0.0
                    except:
                        pass
                
                results.append({
                    "symbol": symbol,
                    "isin": isin,
                    "quantity": qty,
                    "avg_cost": 0.0,  # CDSL doesn't provide cost basis
                    "market_price": market_price,
                    "market_value": market_value,
                    "as_of_date": "",
                    "broker": broker,
                    "source_file": fname,
                })
            except Exception:
                continue
        
        return results
    except Exception:
        return []


def _parse_standard_iifl_format(df_raw: pd.DataFrame, broker: str, fname: str) -> list[dict]:
    """Parse standard IIFL Portfolio Summary format."""
    try:
        # Find header row
        hdr = None
        if isinstance(df_raw.iloc[0, 0], str):
            for i, row in df_raw.iterrows():
                vals = [str(v).strip().lower() for v in row]
                if any("symbol" in v or "code" in v or "quantity" in v for v in vals):
                    hdr = i
                    break

        if hdr is None:
            return []

        df_raw.columns = df_raw.iloc[hdr]
        df = df_raw.iloc[hdr + 1:].reset_index(drop=True)
        df.columns = df.columns.str.strip().str.lower()

        # Map columns
        col_map = {
            "symbol": "symbol", "stock code": "symbol", "stock": "symbol",
            "isin": "isin", "qty": "quantity", "quantity": "quantity",
            "average price": "avg_cost", "cost price": "avg_cost", 
            "avg cost per share": "avg_cost", "avg price": "avg_cost",
            "ltp": "market_price", "last price": "market_price", 
            "market price": "market_price", "current price": "market_price",
            "market value": "market_value", "current value": "market_value", 
            "investment value": "market_value", "total cost": "market_value",
        }
        df = df.rename(columns=col_map)

        results = []
        for _, row in df.iterrows():
            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol or symbol in ("NAN", "SYMBOL", ""):
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
                "as_of_date": "",
                "broker": broker,
                "source_file": fname,
            })

        return results
    except Exception:
        return []