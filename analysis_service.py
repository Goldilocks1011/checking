"""
analysis_service.py
===================
Pure calculation functions extracted from reference modules.
No Streamlit. No hardcoded paths. All functions return plain dicts.

Functions:
  get_price_levels(scrip_code)         → LTP, 1M/3M/52W high/low, max spike %
  get_mmm_stats(scrip_code, days=90)   → mean, median, mode, std_dev
  get_seasonal_pattern(scrip_code)     → monthly avg returns + best/worst month
  get_consecutive_trend(scrip_code)    → direction, streak, cumulative %
  get_momentum_score(symbol)           → score (-12 to +12), signal text
"""
from __future__ import annotations

import os
import sys
import math
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
logger = logging.getLogger(__name__)

def _clean_float(v):
    if v is None:
        return 0.0
    # Skip strings entirely (they are not numeric)
    if isinstance(v, str):
        return v
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    except (ValueError, TypeError):
        return 0.0

def _clean_dict(d):
    """Recursively replace NaN/Inf with 0.0 in dict values."""
    if not isinstance(d, dict):
        return d
    cleaned = {}
    for k, v in d.items():
        if isinstance(v, dict):
            cleaned[k] = _clean_dict(v)
        elif isinstance(v, list):
            cleaned[k] = [_clean_dict(item) if isinstance(item, dict) else _clean_float(item) for item in v]
        else:
            cleaned[k] = _clean_float(v)
    return cleaned

# ── resolve project root so auth_manager is importable ───────────────────────
_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)          # backend/
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)       # v4/
for _p in (_BACKEND_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── lazy client (auth_manager lives in project root or backend/) ─────────────
_client = None

def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from auth_manager import get_client
        _client = get_client()
    except Exception as e:
        logger.info(f"[AnalysisService] auth_manager error: {e}")
        _client = None
    return _client


def _fetch_daily(scrip_code: int, days: int) -> pd.DataFrame | None:
    """Fetch N calendar days of daily OHLCV. Returns sorted DataFrame or None.
       Falls back to yfinance if 5paisa fails.
    """
    # Try 5paisa first (don't break existing flow)
    client = _get_client()
    if client is not None:
        try:
            end   = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=days + 15)).strftime("%Y-%m-%d")
            df = client.historical_data("N", "C", int(scrip_code), "1d", start, end)
            if df is not None and not df.empty:
                df["Datetime"] = pd.to_datetime(df["Datetime"])
                df = df.sort_values("Datetime").reset_index(drop=True)
                return df
        except Exception as e:
            logger.info(f"[AnalysisService] 5paisa historical error: {e}")
    
    # ── FALLBACK: yfinance ──────────────────────────────────────────────────
    # We need to map scrip_code to a symbol. For now, use a hardcoded lookup
    # Or fetch from DB. But given your scrip_code 535755 = ABFRL, try ABFRL.NS
    try:
        import yfinance as yf
        # Map known scrip_codes to symbols (add more as needed)
        symbol_map = {
            535755: "ABFRL",
            30008: "INFY",
            30010: "HDFCBANK",
            # Add others as needed
        }
        symbol = symbol_map.get(scrip_code)
        if not symbol:
            # Fallback: try to fetch from DB
            from sqlalchemy import text
            from database import SessionLocal
            db = SessionLocal()
            try:
                row = db.execute(
                    text("SELECT symbol_root FROM scrip_master_cache WHERE scrip_code=:code LIMIT 1"),
                    {"code": scrip_code}
                ).first()
                if row and row.symbol_root:
                    symbol = row.symbol_root
            finally:
                db.close()
        
        if symbol:
            ticker = yf.Ticker(f"{symbol}.NS")
            df = ticker.history(period=f"{days}d")
            if not df.empty:
                df = df.reset_index()
                df.rename(columns={"Date": "Datetime"}, inplace=True)
                return df
    except Exception as e:
        logger.info(f"[AnalysisService] yfinance fallback error: {e}")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. PRICE LEVELS  (ref: spot_app_2.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_price_levels(scrip_code: int) -> dict:
    result: dict = {
        "ltp": 0.0, "prev_close": 0.0, "change": 0.0, "change_pct": 0.0,
        "high_1m": 0.0, "low_1m": 0.0,
        "high_3m": 0.0, "low_3m": 0.0,
        "high_52w": 0.0, "low_52w": 0.0,
        "max_spike_pct": 0.0, "max_spike_date": "",
        "max_drop_pct": 0.0,  "max_drop_date": "",
    }

    def _fetch(days):
        return _fetch_daily(scrip_code, days)

    try:
        with ThreadPoolExecutor(max_workers=3) as ex:
            f30  = ex.submit(_fetch, 30)
            f90  = ex.submit(_fetch, 90)
            f365 = ex.submit(_fetch, 365)
            df30  = f30.result()
            df90  = f90.result()
            df365 = f365.result()

        if df365 is None or df365.empty:
            return _clean_dict(result)

        # 🛡️ Drop NaN rows to avoid JSON serialization errors
        df365 = df365.dropna(subset=['Close', 'High', 'Low'])
        if df365.empty:
            return _clean_dict(result)

        ltp        = float(df365.iloc[-1]["Close"])
        prev_close = float(df365.iloc[-2]["Close"]) if len(df365) > 1 else ltp
        change     = round(ltp - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        result.update({
            "ltp":        round(ltp, 2),
            "prev_close": round(prev_close, 2),
            "change":     change,
            "change_pct": change_pct,
            "high_52w":   round(float(np.nanmax(df365["High"].values)), 2),
            "low_52w":    round(float(np.nanmin(df365["Low"].values)), 2),
        })

        if df90 is not None and not df90.empty:
            df90 = df90.dropna(subset=['High', 'Low'])
            if not df90.empty:
                result["high_3m"] = round(float(np.nanmax(df90["High"].values)), 2)
                result["low_3m"]  = round(float(np.nanmin(df90["Low"].values)), 2)
        if df30 is not None and not df30.empty:
            df30 = df30.dropna(subset=['High', 'Low'])
            if not df30.empty:
                result["high_1m"] = round(float(np.nanmax(df30["High"].values)), 2)
                result["low_1m"]  = round(float(np.nanmin(df30["Low"].values)), 2)

        # max single-day spike / drop
        df365["prev_c"]  = df365["Close"].shift(1)
        df365["day_pct"] = (df365["Close"] - df365["prev_c"]) / df365["prev_c"] * 100
        df365.dropna(subset=["day_pct"], inplace=True)

        if not df365.empty:
            spike_idx = df365["day_pct"].idxmax()
            drop_idx  = df365["day_pct"].idxmin()
            result["max_spike_pct"]  = round(float(df365.loc[spike_idx, "day_pct"]), 2)
            result["max_spike_date"] = str(df365.loc[spike_idx, "Datetime"].date())
            result["max_drop_pct"]   = round(float(df365.loc[drop_idx, "day_pct"]), 2)
            result["max_drop_date"]  = str(df365.loc[drop_idx, "Datetime"].date())

    except Exception as e:
        logger.info(f"[AnalysisService] get_price_levels error: {e}")

    return _clean_dict(result)

# ─────────────────────────────────────────────────────────────────────────────
# 2. MMM STATS  (ref: M_M_M_2.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_mmm_stats(scrip_code: int, days: int = 90) -> dict:
    result: dict = {
        "mean": 0.0, "median": 0.0,
        "mode_low": 0.0, "mode_high": 0.0, "mode_count": 0,
        "std_dev": 0.0,
        "band_1s_low": 0.0, "band_1s_high": 0.0,
        "band_2s_low": 0.0, "band_2s_high": 0.0,
        "days_used": 0,
    }
    try:
        df = _fetch_daily(scrip_code, days)
        if df is None or df.empty or len(df) < 5:   # 🛡️ Guard against insufficient data
            return _clean_dict(result)

        df = df.tail(days)
        df = df.dropna(subset=['Close'])            # 🛡️ Remove NaN rows
        if df.empty or len(df) < 5:
            return _clean_dict(result)

        closes = df["Close"].values.astype(float)

        # 🛡️ Use NaN-safe numpy functions
        mean   = float(np.nanmean(closes))
        median = float(np.nanmedian(closes))
        std    = float(np.nanstd(closes, ddof=1))

        # range-based mode (₹100 bucket)
        bucket = 100.0
        p_min  = np.nanmin(closes)
        p_max  = np.nanmax(closes)
        if np.isnan(p_min) or np.isnan(p_max) or p_min == p_max:
            return _clean_dict(result)
        
        p_min = np.floor(p_min / bucket) * bucket
        p_max = np.ceil(p_max / bucket) * bucket
        bins   = np.arange(p_min, p_max + bucket, bucket)
        counts, edges = np.histogram(closes, bins=bins)
        best   = int(np.argmax(counts))

        result.update({
            "mean":        round(mean, 2),
            "median":      round(median, 2),
            "mode_low":    round(float(edges[best]), 2),
            "mode_high":   round(float(edges[best + 1]), 2),
            "mode_count":  int(counts[best]),
            "std_dev":     round(std, 2),
            "band_1s_low":  round(mean - std, 2),
            "band_1s_high": round(mean + std, 2),
            "band_2s_low":  round(mean - 2 * std, 2),
            "band_2s_high": round(mean + 2 * std, 2),
            "days_used":   len(closes),
        })
    except Exception as e:
        logger.info(f"[AnalysisService] get_mmm_stats error: {e}")

    return _clean_dict(result)
# ─────────────────────────────────────────────────────────────────────────────
# 3. SEASONAL PATTERN  (ref: seasonal.py)
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]

def get_seasonal_pattern(scrip_code: int, years: int = 7) -> dict:
    result: dict = {
        "months": [],
        "best_month": "",
        "worst_month": "",
        "current_month_rank": "neutral",
    }
    try:
        df = _fetch_daily(scrip_code, years * 365 + 30)
        if df is None or df.empty:
            return _clean_dict(result)

        df = df.set_index("Datetime")
        monthly = df["Close"].resample("ME").agg(["first", "last"])
        monthly.columns = ["open_m", "close_m"]
        monthly["ret"] = (monthly["close_m"] - monthly["open_m"]) / monthly["open_m"] * 100
        monthly["month"] = monthly.index.month

        rows = []
        for m in range(1, 13):
            grp = monthly[monthly["month"] == m]
            if grp.empty:
                continue
            green = int((grp["ret"] > 0).sum())
            total = len(grp)
            rows.append({
                "month_num": m,
                "name":      _MONTH_NAMES[m - 1],
                "avg_ret":   round(float(grp["ret"].mean()), 2),
                "win_rate":  round((green / total) * 100, 1),
                "green":     green,
                "red":       total - green,
                "total":     total,
            })

        if rows:
            best  = max(rows, key=lambda x: x["avg_ret"])
            worst = min(rows, key=lambda x: x["avg_ret"])
            cur_m = datetime.now().month
            rank  = ("best" if best["month_num"]  == cur_m else
                     "worst" if worst["month_num"] == cur_m else "neutral")
            result.update({
                "months":              rows,
                "best_month":          best["name"],
                "worst_month":         worst["name"],
                "current_month_rank":  rank,
            })
    except Exception as e:
        logger.info(f"[AnalysisService] get_seasonal_pattern error: {e}")

    return _clean_dict(result)

# ─────────────────────────────────────────────────────────────────────────────
# 4. CONSECUTIVE TREND  (ref: movement.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_consecutive_trend(scrip_code: int, window: int = 30) -> dict:
    result: dict = {
        "direction": "FLAT",
        "streak": 0,
        "cumulative_pct": 0.0,
        "last_close": 0.0,
    }
    try:
        df = _fetch_daily(scrip_code, window + 10)
        if df is None or len(df) < 3:
            return _clean_dict(result)

        closes = df["Close"].values.astype(float)
        result["last_close"] = round(float(closes[-1]), 2)

        streak    = 0
        direction = "FLAT"

        for i in range(len(closes) - 1, 0, -1):
            diff = closes[i] - closes[i - 1]
            cur_dir = "UP" if diff > 0 else ("DOWN" if diff < 0 else "FLAT")
            if streak == 0:
                direction = cur_dir
                streak    = 1
            elif cur_dir == direction:
                streak += 1
            else:
                break

        if streak >= 2 and direction != "FLAT":
            base  = closes[-(streak + 1)] if len(closes) > streak else closes[0]
            cum_p = round((closes[-1] - base) / base * 100, 2) if base else 0.0
            result.update({
                "direction":      direction,
                "streak":         streak,
                "cumulative_pct": cum_p,
            })
    except Exception as e:
        logger.info(f"[AnalysisService] get_consecutive_trend error: {e}")

    return _clean_dict(result)

# ─────────────────────────────────────────────────────────────────────────────
# 5. MOMENTUM SCORE  (ref: analyst.py / stock_signal_pro)
# ─────────────────────────────────────────────────────────────────────────────

def get_momentum_score(symbol: str) -> dict:
    result: dict = {
        "score": 0.0, "signal": "HOLD",
        "rsi": 50.0, "rsi_signal": "Neutral",
        "macd_signal": "Neutral", "ma_signal": "Neutral",
        "error": "",
    }
    try:
        import yfinance as yf
        for suffix in (".NS", ".BO"):
            df = yf.download(f"{symbol}{suffix}", period="6mo",
                             progress=False, timeout=15)
            if not df.empty and len(df) >= 50:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                break
        else:
            result["error"] = "No data from yfinance"
            return _clean_dict(result)

        # RSI
        delta = df["Close"].diff()
        gain  = delta.where(delta > 0, 0).rolling(14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi   = float((100 - 100 / (1 + gain / loss)).iloc[-1])

        # MACD
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()

        # MAs
        sma20 = df["Close"].rolling(20).mean()
        sma50 = df["Close"].rolling(50).mean()

        # Bollinger
        std_b  = df["Close"].rolling(20).std()
        bb_up  = sma20 + 2 * std_b
        bb_lo  = sma20 - 2 * std_b

        price      = float(df["Close"].iloc[-1])
        prev_price = float(df["Close"].iloc[-2])

        score = 0.0

        # 1 RSI
        if rsi < 30:   score += 2; rsi_sig = "OVERSOLD ↑"
        elif rsi < 40: score += 1; rsi_sig = "Mildly Oversold"
        elif rsi > 70: score -= 2; rsi_sig = "OVERBOUGHT ↓"
        elif rsi > 60: score -= 1; rsi_sig = "Mildly Overbought"
        else:                       rsi_sig = "Neutral"

        # 2 MACD
        m_cur  = float(macd.iloc[-1]); m_prv = float(macd.iloc[-2])
        s_cur  = float(sig.iloc[-1]);  s_prv = float(sig.iloc[-2])
        if m_cur > s_cur and m_prv <= s_prv: score += 2; mac_sig = "BULLISH CROSS ↑"
        elif m_cur > s_cur:                  score += 1; mac_sig = "Bullish"
        elif m_cur < s_cur and m_prv >= s_prv: score -= 2; mac_sig = "BEARISH CROSS ↓"
        elif m_cur < s_cur:                  score -= 1; mac_sig = "Bearish"
        else:                                             mac_sig = "Neutral"

        # 3 MA trend
        s20 = float(sma20.iloc[-1]); s50 = float(sma50.iloc[-1])
        if price > s20 > s50:   score += 2; ma_sig = "STRONG UPTREND ↑"
        elif s20 > s50:         score += 1; ma_sig = "Uptrend"
        elif price < s20 < s50: score -= 2; ma_sig = "STRONG DOWNTREND ↓"
        elif s20 < s50:         score -= 1; ma_sig = "Downtrend"
        else:                               ma_sig = "Neutral"

        # 4 Bollinger
        bb_lo_v = float(bb_lo.iloc[-1]); bb_up_v = float(bb_up.iloc[-1])
        if price < bb_lo_v:   score += 1.5
        elif price > bb_up_v: score -= 1.5

        # 5 volume
        avg_vol = float(df["Volume"].rolling(20).mean().iloc[-1])
        cur_vol = float(df["Volume"].iloc[-1])
        if avg_vol > 0 and cur_vol / avg_vol > 1.5:
            score += 0.5 if score > 0 else -0.5

        # Signal label
        if   score >= 6:  sig_label = "STRONG BUY"
        elif score >= 3:  sig_label = "BUY"
        elif score <= -6: sig_label = "STRONG SELL"
        elif score <= -3: sig_label = "SELL"
        else:             sig_label = "HOLD"

        result.update({
            "score":       round(score, 1),
            "signal":      sig_label,
            "rsi":         round(rsi, 1),
            "rsi_signal":  rsi_sig,
            "macd_signal": mac_sig,
            "ma_signal":   ma_sig,
        })
    except Exception as e:
        result["error"] = str(e)

    return _clean_dict(result)

# ─────────────────────────────────────────────────────────────────────────────
# 6. HOLDING INTELLIGENCE  (ref: combined_PL2.py, score.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_holding_intel(user_id: int, symbol: str, current_price: float) -> dict:
    result: dict = {
        "held": False,
        "qty": 0.0, "avg_buy_price": 0.0,
        "buy_date": "", "holding_days": 0,
        "term": "SHORT",
        "pnl": 0.0, "pnl_pct": 0.0, "xirr_approx": 0.0,
        "signal": "MONITOR", "signal_reason": "Stock not held",
        "confidence_add": 0,
    }
    try:
        from database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            row = db.execute(text("""
                SELECT quantity, avg_buy_price, first_buy_date
                FROM holdings
                WHERE user_id = :uid
                  AND UPPER(symbol) = :sym
                  AND quantity > 0
                LIMIT 1
            """), {"uid": user_id, "sym": symbol.upper()}).first()

            if row is None:
                return _clean_dict(result)

            qty        = float(row.quantity)
            avg_price  = float(row.avg_buy_price)
            buy_date_s = str(row.first_buy_date or "")

            holding_days = 0
            if buy_date_s:
                try:
                    buy_dt       = datetime.strptime(buy_date_s[:10], "%Y-%m-%d")
                    holding_days = (datetime.now() - buy_dt).days
                except Exception:
                    pass

            pnl     = round((current_price - avg_price) * qty, 2)
            pnl_pct = round((current_price - avg_price) / avg_price * 100, 2) if avg_price else 0.0
            term    = "LONG" if holding_days > 365 else "SHORT"

            # XIRR approximation = annualised return
            xirr_approx = 0.0
            if holding_days > 0 and avg_price > 0:
                xirr_approx = round(
                    ((current_price / avg_price) ** (365.0 / holding_days) - 1) * 100, 1
                )

            # signal logic
            signal         = "MONITOR"
            signal_reason  = "Continue holding"
            confidence_add = 0

            if term == "SHORT" and pnl_pct >= 5.0:
                signal         = "SELL"
                signal_reason  = f"Short-term holding up {pnl_pct:.1f}% ≥ 5% threshold"
                confidence_add = 1
            elif term == "LONG" and xirr_approx > 50.0 and holding_days > 180:
                signal         = "SELL"
                signal_reason  = f"Long-term XIRR {xirr_approx:.1f}% > 50% and held {holding_days} days"
                confidence_add = 1
            elif pnl_pct < -10.0:
                signal        = "HOLD"
                signal_reason = f"Down {abs(pnl_pct):.1f}% — wait for recovery"

            # 🛡️ Safeguard NaN values for JSON
            import math
            pnl = 0.0 if math.isnan(pnl) else pnl
            pnl_pct = 0.0 if math.isnan(pnl_pct) else pnl_pct
            xirr_approx = 0.0 if math.isnan(xirr_approx) else xirr_approx
            current_price = 0.0 if math.isnan(current_price) else current_price

            result.update({
                "held":           True,
                "qty":            qty,
                "avg_buy_price":  round(avg_price, 2),
                "buy_date":       buy_date_s[:10],
                "holding_days":   holding_days,
                "term":           term,
                "pnl":            pnl,
                "pnl_pct":        pnl_pct,
                "xirr_approx":    xirr_approx,
                "signal":         signal,
                "signal_reason":  signal_reason,
                "confidence_add": confidence_add,
            })
        finally:
            db.close()
    except Exception as e:
        logger.info(f"[AnalysisService] get_holding_intel error: {e}")

    return _clean_dict(result)