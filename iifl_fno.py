"""
IIFL F&O Trade Listing Parser — v3 (Fixed)
============================================
Same file as iifl.py equity parser (Trade_Listing_<CLIENT>.xls),
but this extracts only NSEFNO / BSEFNO rows into fno_transactions schema.

KEY FIXES vs v2:
  1. Net Qty for open positions: SUM all rows for same contract, NOT "latest".
     In IIFL file each row = 1 trade, Net Qty = BuyQty-SellQty for that trade.
     True open position = sum of all trades' net qtys for same contract.
  2. Expired contracts filtered from open_positions (expiry < today).
  3. Expiry date parsing: handles string '20260428' (YYYYMMDD) and float.
  4. Duplicate file check REMOVED — same file can be uploaded multiple times.
"""
import io
import re
import pandas as pd
from datetime import datetime, date


def _sf(val, default=0.0) -> float:
    try:
        v = str(val).strip()
        return float(v) if v not in ("", "nan", "None") else default
    except Exception:
        return default


def _parse_expiry(val) -> str:
    """
    Parse expiry date to YYYY-MM-DD.
    Handles: '20260428' (str YYYYMMDD), 20260428.0 (float), '28/04/2026', '2026-04-28'.
    """
    if val is None or str(val).strip() in ("", "nan", "None", "0"):
        return ""
    s = str(val).strip()
    # Strip float decimal (.0)
    if s.endswith('.0'):
        s = s[:-2]
    for fmt in ("%Y%m%d", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return s


def _instrument_type(option_type_raw: str) -> str:
    ot = str(option_type_raw).strip().upper()
    if ot in ("CE", "PE"):
        return ot
    return "FUT"


def parse(file, broker: str = "IIFL") -> tuple[list[dict], list[dict]]:
    """
    Returns:
        (fno_transactions, open_positions)
        Both are lists of dicts.
    """
    file_bytes = file.read() if hasattr(file, "read") else file
    fname = getattr(file, "name", "iifl_fno.xls")

    df_raw = None
    for engine in ("xlrd", "openpyxl"):
        try:
            df_raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0,
                                   header=None, engine=engine)
            break
        except Exception:
            continue
    if df_raw is None:
        raise ValueError("Cannot open IIFL file")

    # Find header row
    hdr = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "Exchange" in vals and "Trade Date" in vals and "Buy Qty" in vals:
            hdr = i
            break
    if hdr is None:
        raise ValueError("Header row not found in IIFL F&O file")

    df = df_raw.iloc[hdr:].reset_index(drop=True)
    raw_cols = list(df.iloc[0])

    # Deduplicate column names
    seen, cols = {}, []
    for c in raw_cols:
        label = str(c).strip() if str(c) not in ("nan", "None") else "_blank_"
        seen[label] = seen.get(label, -1) + 1
        cols.append(f"{label}_{seen[label]}" if seen[label] > 0 else label)
    df.columns = cols
    df = df.iloc[1:].reset_index(drop=True)
    df = df.dropna(how="all")

    today = date.today()
    transactions: list[dict] = []

    # Per-contract accumulator for open position calculation
    # Key = symbol_str (contract identifier)
    # Value = dict tracking: net_qty_sum, earliest_buy info, latest dates, closing price
    contract_acc: dict[str, dict] = {}

    for _, row in df.iterrows():
        exch_raw = str(row.get("Exchange", "")).strip()
        if not exch_raw or exch_raw.lower() in ("nan", "exchange"):
            continue

        exup = exch_raw.upper()
        if "FNO" not in exup and "FO" not in exup:
            continue   # skip equity (NSECASH/BSECASH) rows

        clean_exch = "NSE" if "NSE" in exup else ("BSE" if "BSE" in exup else exup)

        try:
            trade_date = pd.to_datetime(
                row.get("Trade Date"), dayfirst=True
            ).strftime("%Y-%m-%d")
        except Exception:
            continue

        code       = str(row.get("Code", "")).strip()
        name       = str(row.get("Name", "")).strip()
        underlying = code if code and code.lower() != "nan" else name

        expiry_raw  = row.get("Expiry Date", "")
        strike_raw  = row.get("Strike Price", 0)
        option_raw  = row.get("Option Type", "")
        inst_type   = _instrument_type(option_raw)
        expiry_date = _parse_expiry(expiry_raw) if str(expiry_raw).strip() not in ("nan", "0", "") else ""
        strike      = _sf(strike_raw)

        buy_qty  = _sf(row.get("Buy Qty",  0))
        buy_rate = _sf(row.get("Buy Market Rate",  0))
        buy_brok = _sf(row.get("Buy Brokerage", 0))
        sell_qty  = _sf(row.get("Sell Qty",  0))
        sell_rate = _sf(row.get("Sell Market Rate",  0))
        sell_brok = _sf(row.get("Sell Brokerage", 0))
        # Net qty for THIS trade (positive=buy, negative=sell)
        trade_net_qty = buy_qty - sell_qty

        # Closing price
        closing_price = 0.0
        for k in list(row.index):
            if "Closing Price" in str(k):
                closing_price = _sf(row[k])
                break

        symbol_str = (
            f"{underlying}_{inst_type}_{int(strike)}_{expiry_date[:7]}"
            if inst_type in ("CE", "PE")
            else f"{underlying}_FUT_{expiry_date[:7]}"
        )

        base = dict(
            symbol=symbol_str,
            underlying=underlying,
            exchange=clean_exch,
            instrument_type=inst_type,
            expiry_date=expiry_date,
            strike_price=strike,
            trade_date=trade_date,
            broker=broker,
            source_file=fname,
            remarks="",
        )

        if buy_qty > 0 and buy_rate > 0:
            transactions.append({
                **base,
                "trade_type": "BUY",
                "quantity":   buy_qty,
                "price":      buy_rate,
                "brokerage":  buy_brok,
                "tax_charges": 0.0,
            })

        if sell_qty > 0 and sell_rate > 0:
            transactions.append({
                **base,
                "trade_type": "SELL",
                "quantity":   sell_qty,
                "price":      sell_rate,
                "brokerage":  sell_brok,
                "tax_charges": 0.0,
            })

        # Accumulate NET QTY by SUMMING each trade's net qty (FIX vs v2 "latest" approach)
        if symbol_str not in contract_acc:
            contract_acc[symbol_str] = {
                "underlying":      underlying,
                "exchange":        clean_exch,
                "instrument_type": inst_type,
                "expiry_date":     expiry_date,
                "strike_price":    strike,
                "latest_date":     trade_date,
                "net_qty_sum":     trade_net_qty,   # ← SUM, starts with this trade
                "closing_price":   closing_price,
                "total_buy_value": buy_qty * buy_rate if buy_qty > 0 and buy_rate > 0 else 0,
                "total_buy_qty":   buy_qty if buy_qty > 0 and buy_rate > 0 else 0,
                "total_sell_value": sell_qty * sell_rate if sell_qty > 0 and sell_rate > 0 else 0,
                "total_sell_qty":  sell_qty if sell_qty > 0 and sell_rate > 0 else 0,
            }
        else:
            acc = contract_acc[symbol_str]
            # SUM all trade net qtys for correct open position
            acc["net_qty_sum"] += trade_net_qty
            if trade_date >= acc["latest_date"]:
                acc["latest_date"] = trade_date
                if closing_price > 0:
                    acc["closing_price"] = closing_price
            if buy_qty > 0 and buy_rate > 0:
                acc["total_buy_value"] += buy_qty * buy_rate
                acc["total_buy_qty"]   += buy_qty
            if sell_qty > 0 and sell_rate > 0:
                acc["total_sell_value"] += sell_qty * sell_rate
                acc["total_sell_qty"]   += sell_qty

    # Build open_positions list
    # Only include contracts with:
    #   (a) non-zero net qty
    #   (b) expiry >= today (skip expired contracts — they settled, not truly "open")
    open_positions: list[dict] = []

    for symbol_str, acc in contract_acc.items():
        net_qty = round(acc["net_qty_sum"], 4)

        # Skip fully closed (net = 0)
        if abs(net_qty) < 0.001:
            continue

        # Skip expired contracts
        exp_str = acc.get("expiry_date", "")
        if exp_str:
            try:
                exp_dt = datetime.strptime(exp_str[:10], "%Y-%m-%d").date()
                if exp_dt < today:
                    continue   # already expired — settlement handled by exchange
            except Exception:
                pass

        # Weighted average price
        # Long positions: use avg buy price; short: use avg sell price
        avg_price = 0.0
        if net_qty > 0:
            bq = acc["total_buy_qty"]
            bv = acc["total_buy_value"]
            avg_price = round(bv / bq, 4) if bq > 0 else 0.0
        else:
            sq = acc["total_sell_qty"]
            sv = acc["total_sell_value"]
            avg_price = round(sv / sq, 4) if sq > 0 else 0.0

        closing_price = acc.get("closing_price", 0.0)
        open_positions.append({
            "symbol":          symbol_str,
            "underlying":      acc["underlying"],
            "exchange":        acc["exchange"],
            "instrument_type": acc["instrument_type"],
            "expiry_date":     acc["expiry_date"],
            "strike_price":    acc["strike_price"],
            "open_qty":        net_qty,          # positive=long, negative=short
            "avg_price":       avg_price,
            "closing_price":   closing_price,
            "unrealized_pnl":  round((closing_price - avg_price) * net_qty, 2) if avg_price else 0,
            "trade_date":      acc["latest_date"],
            "broker":          broker,
            "source_file":     fname,
            "as_of_date":      acc["latest_date"],
        })

    return transactions, open_positions