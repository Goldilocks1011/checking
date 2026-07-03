"""
IIFL Trade Listing Parser — v2 (Fixed)
=======================================
IIFL format quirks fixed:
  - Buy Qty > 0  → BUY  |  Sell Qty > 0 → SELL (same row can have both)
  - Segment: NSECASH/BSECASH=EQ, NSEFNO/BSEFNO=FNO
  - FNO symbol encoded as: NAME_CE/PE_STRIKE_EXPIRY
  - Date is DD/MM/YYYY format → dayfirst=True required (was missing = wrong dates)
  - Net Qty = 0 rows with both buy=0 & sell=0 → skip (closing/summary rows)

No corporate action rows exist in IIFL format (they handle it separately),
but OffMarket rows may appear — handled like 5paisa.
"""
import pandas as pd
import io


def parse(file, broker: str = "IIFL") -> list[dict]:
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "iifl_file.xls")

    df_raw = None
    for engine in ("xlrd", "openpyxl"):
        try:
            df_raw = pd.read_excel(
                io.BytesIO(file_bytes), sheet_name=0, header=None, engine=engine
            )
            break
        except Exception:
            continue
    if df_raw is None:
        raise ValueError("Could not open IIFL file")

    # Find header row
    hdr = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "Exchange" in vals and "Trade Date" in vals and "Buy Qty" in vals:
            hdr = i
            break
    if hdr is None:
        raise ValueError("Header row not found in IIFL file")

    df = df_raw.iloc[hdr:].reset_index(drop=True)
    raw_cols = list(df.iloc[0])

    # Build unique column names
    seen, cols = {}, []
    for c in raw_cols:
        label = str(c).strip() if str(c) not in ("nan", "None") else "_blank_"
        seen[label] = seen.get(label, -1) + 1
        cols.append(f"{label}_{seen[label]}" if seen[label] > 0 else label)
    df.columns = cols
    df = df.iloc[1:].reset_index(drop=True)
    df = df.dropna(how="all")

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

        exup = exch_raw.upper()   # ← define exup FIRST

        # Skip F&O rows — they belong in the FNO parser
        if "FNO" in exup or "FO" in exup:
            continue

        # Now we safely know it's an equity row
        segment = "EQ"
        clean_exch = "NSE" if "NSE" in exup else ("BSE" if "BSE" in exup else exch_raw)
        
        # Fix: dayfirst=True for DD/MM/YYYY format in IIFL
        try:
            trade_date = pd.to_datetime(
                row.get("Trade Date"), dayfirst=True
            ).strftime("%Y-%m-%d")
        except Exception:
            continue

        name = str(row.get("Name", "")).strip()
        code = str(row.get("Code", "")).strip()
        symbol = name if name and name.lower() != "nan" else code

        if segment == "FNO":
            opt_type = str(row.get("Option Type", "")).strip()
            strike   = sf(row.get("Strike Price", 0))
            expiry   = str(row.get("Expiry Date", "")).strip()
            if opt_type and opt_type not in ("nan", "  ", ""):
                symbol = f"{symbol}_{opt_type}_{int(strike)}_{expiry[:7]}"

        buy_qty  = sf(row.get("Buy Qty", 0))
        buy_rate = sf(row.get("Buy Market Rate", 0))
        buy_brok = sf(row.get("Buy Brokerage", 0))
        sell_qty  = sf(row.get("Sell Qty", 0))
        sell_rate = sf(row.get("Sell Market Rate", 0))
        sell_brok = sf(row.get("Sell Brokerage", 0))

        # Skip pure summary/zero rows
        if buy_qty <= 0 and sell_qty <= 0:
            continue

        base = dict(
            symbol=symbol, company_name=symbol,
            exchange=clean_exch, isin="", segment=segment,
            trade_date=trade_date, broker=broker, source_file=fname,
            tax_charges=0.0, remarks="",
        )

        if buy_qty > 0 and buy_rate > 0:
            results.append({**base, "quantity": buy_qty, "price": buy_rate,
                            "trade_type": "BUY", "brokerage": buy_brok})

        if sell_qty > 0 and sell_rate > 0:
            results.append({**base, "quantity": sell_qty, "price": sell_rate,
                            "trade_type": "SELL", "brokerage": sell_brok})

    return results