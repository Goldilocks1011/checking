"""
wishlist_ui.py  — v4  (Decision Co-Pilot Edition)
====================================================
Key changes vs v3:
  ① Decision Matrix rendered FIRST in Why? dropdown — verdict before data
  ② Confidence Score 2/5 now shows expandable pass/fail breakdown per criterion
  ③ buy_date = 0.0 / None → displayed as "—" not "0.0"
  ④ "Hold" renamed to "Wait for Trigger" with concrete price levels stated
  ⑤ Statistics tab: "Your Position vs History" narrative card
  ⑥ Analyst tab: "Technical Alignment" narrative connecting signals to your cost
  ⑦ Seasonality tab: "Projection Impact on Your Holding" card
  ⑧ Suggestion tab unchanged (already functional)
"""
from __future__ import annotations

import time
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import api_client
import logging
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]

_SIGNAL_COLOR = {
    "STRONG BUY":  "#00c853",
    "BUY":         "#69f0ae",
    "HOLD":        "#ffd740",
    "SELL":        "#ff6d00",
    "STRONG SELL": "#d50000",
    "MONITOR":     "#78909c",
    "NEUTRAL":     "#78909c",
    "SELL_CE":     "#00b0ff",
    "SELL_PE":     "#ff6d00",
    "SQUARE_OFF":  "#00c853",
    "ROLLOVER":    "#ffd740",
}

_SUGGESTION_LABEL = {
    "SELL_CE":   "📉 Sell CE",
    "SELL_PE":   "📈 Sell PE",
    "SQUARE_OFF":"✅ Square Off",
    "ROLLOVER":  "🔄 Rollover",
    "NEUTRAL":   "⏸ Neutral",
}

_SENTIMENT_EMOJI = {
    "positive": "🟢",
    "negative": "🔴",
    "neutral":  "🟡",
}

# ─────────────────────────────────────────────────────────────────────────────
# Cached loaders
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def cached_impact(symbol: str, user_id: int = 0) -> dict:
    try:
        return api_client.get_impact(symbol, user_id if user_id else None)
    except Exception as e:
        logger.error(f"[WishlistUI] cached_impact error for {symbol}: {e}")
        return {}


@st.cache_data(ttl=300, show_spinner=False)
def cached_impact_quick_batch(symbols_key: str, user_id: int = 0) -> dict:
    syms = [s.strip() for s in symbols_key.split(",") if s.strip()]
    if not syms:
        return {}
    try:
        return api_client.get_impact_quick_batch(syms, user_id if user_id else None)
    except Exception as e:
        logger.error(f"[WishlistUI] cached_impact_quick_batch error: {e}")
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def cached_suggestion(symbol: str, user_id: int = 0) -> dict:
    try:
        return api_client.get_suggestion(symbol, user_id, spot=0.0)
    except Exception as e:
        logger.error(f"[WishlistUI] cached_suggestion error for {symbol}: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_date(val) -> str:
    """Return date string or '—', never '0.0' or None."""
    if not val:
        return "—"
    s = str(val).strip()
    if s in ("0", "0.0", "None", "nan", ""):
        return "—"
    return s[:10]  # trim to YYYY-MM-DD


def _get_chart_data(symbol: str, days: int) -> pd.DataFrame | None:
    try:
        import yfinance as yf
        period_map = {30: "1mo", 90: "3mo", 180: "6mo", 365: "1y"}
        period = period_map.get(days, "1mo")
        sym = symbol.upper()
        for suffix in [".NS", ".BO"]:
            ticker = yf.Ticker(f"{sym}{suffix}")
            df = ticker.history(period=period)
            if not df.empty:
                df = df.reset_index()
                if "Date" in df.columns:
                    df.rename(columns={"Date": "Datetime"}, inplace=True)
                return df
        return None
    except Exception as e:
        logger.error(f"[WishlistUI] _get_chart_data error: {e}")
        return None


def _trend_arrow(trend: dict) -> str:
    d = trend.get("direction", "FLAT")
    s = trend.get("streak", 0)
    if d == "UP":
        return f"▲ {s}d"
    if d == "DOWN":
        return f"▼ {s}d"
    return "─"


def _confidence_badge(score: int) -> str:
    colors = ["#546e7a","#78909c","#ffd740","#69f0ae","#00c853","#00c853"]
    labels = ["Weak","Low","Fair","Good","Strong","Strong"]
    i = int(min(max(score, 0), 5))
    return (
        f"<span style='background:{colors[i]};color:#000;padding:2px 8px;"
        f"border-radius:10px;font-size:11px;font-weight:700;'>{i}/5 {labels[i]}</span>"
    )


def _pct_badge(pct: float) -> str:
    color = "#00c853" if pct >= 0 else "#d50000"
    sign  = "+" if pct >= 0 else ""
    return f"<span style='color:{color};font-weight:700;'>{sign}{pct:.2f}%</span>"


def _fmt_inr(v) -> str:
    try:
        n = float(v)
        return f"₹{n:,.2f}"
    except Exception:
        return "—"


def _get_quick_data(symbol: str, user_id: int = 0) -> dict:
    cache_key = f"_ql_{symbol}"
    cached = st.session_state.get(cache_key)
    if cached:
        return cached
    try:
        data = cached_impact(symbol, user_id)
        st.session_state[cache_key] = {
            "price":    data.get("price_levels", {}),
            "trend":    data.get("trend", {}),
            "momentum": data.get("momentum", {}),
        }
    except Exception:
        st.session_state[cache_key] = {"price": {}, "trend": {}, "momentum": {}}
    return st.session_state[cache_key]


def _load_prices_batch(rows: list, entity_id: int, is_group: bool, wl_cache_key: str) -> int:
    symbols = [r.get("symbol", "") for r in rows if r.get("symbol")]
    if not symbols:
        return 0

    uid = 0 if is_group else entity_id
    symbols_key = ",".join(sorted(set(symbols)))
    loaded = 0

    try:
        batch = cached_impact_quick_batch(symbols_key, uid)
        for sym, data in batch.items():
            if not isinstance(data, dict):
                continue
            price_levels = data.get("price_levels", {}) or {}
            trend        = data.get("trend", {}) or {}
            momentum     = data.get("momentum", {}) or {}
            st.session_state[f"_ql_{sym}"] = {
                "price": price_levels, "trend": trend, "momentum": momentum,
            }
            if float(price_levels.get("ltp", 0) or 0) > 0:
                loaded += 1
    except Exception as e:
        logger.error(f"[WishlistUI] quick-batch error: {e}")

    missing = [
        sym for sym in symbols
        if float((st.session_state.get(f"_ql_{sym}") or {}).get("price", {}).get("ltp", 0) or 0) == 0
    ]
    if missing:
        try:
            prices_wc = api_client.fetch_prices_with_change(missing)
            for sym in missing:
                pd_data  = prices_wc.get(sym) or {}
                ltp      = float(pd_data.get("price", 0) or 0)
                pct      = float(pd_data.get("pct_change", 0) or 0)
                price_levels = {"ltp": ltp, "change_pct": pct} if ltp > 0 else {}
                existing = st.session_state.get(f"_ql_{sym}") or {}
                st.session_state[f"_ql_{sym}"] = {
                    "price":    price_levels if price_levels else existing.get("price", {}),
                    "trend":    existing.get("trend", {}),
                    "momentum": existing.get("momentum", {}),
                }
                if ltp > 0:
                    loaded += 1
        except Exception as e:
            logger.error(f"[WishlistUI] fetch_prices_with_change fallback error: {e}")

    for sym in symbols:
        if f"_ql_{sym}" not in st.session_state:
            st.session_state[f"_ql_{sym}"] = {"price": {}, "trend": {}, "momentum": {}}

    st.session_state[f"prices_loaded_{wl_cache_key}"] = True
    return loaded


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Decision Matrix — rendered first in Why? dropdown
# ─────────────────────────────────────────────────────────────────────────────
def _is_short_term(buy_date: str | None) -> bool:
    """
    Check if stock was bought after April 1st of current year.
    Short-term = bought in current FY (after April 1st)
    """
    if not buy_date:
        return False
    
    try:
        from datetime import datetime
        
        buy_dt = datetime.fromisoformat(str(buy_date)[:10])
        current_year = datetime.now().year
        fy_start = datetime(current_year, 4, 1)
        return buy_dt >= fy_start
    except Exception:
        return False
 
 
def _check_short_term_profit_rule(
    held: bool,
    term: str,
    pnl: float,
    buy_date: str | None
) -> tuple[bool, str]:
    """CUSTOM RULE 1: Force SELL if short-term profit >= ₹10,000"""
    if not held or term != "SHORT":
        return False, ""
    
    MIN_PROFIT = 10000.0
    
    if pnl >= MIN_PROFIT and _is_short_term(buy_date):
        reason = (
            f"🔥 CUSTOM RULE TRIGGERED: Short-term profit (₹{pnl:,.0f}) ≥ ₹10,000. "
            f"Lock profit now before tax year-end to secure gains at 20% STCG."
        )
        return True, reason
    
    return False, ""
 
 
def _check_low_price_buy_more_rule(
    held: bool,
    avg_cost: float,
    ltp: float,
    price_levels: dict
) -> tuple[bool, str]:
    """CUSTOM RULE 2: Suggest BUY MORE if underwater + at 10% lowest"""
    if not held or ltp <= 0 or avg_cost <= 0:
        return False, ""
    
    if ltp >= avg_cost:  # Not underwater
        return False, ""
    
    low_1m = float(price_levels.get("low_1m", 0) or 0)
    low_3m = float(price_levels.get("low_3m", 0) or 0)
    low_6m = float(price_levels.get("low_6m", 0) or 0)
    low_52w = float(price_levels.get("low_52w", 0) or 0)
    
    period_lows = [x for x in [low_1m, low_3m, low_6m, low_52w] if x > 0]
    if not period_lows:
        return False, ""
    
    lowest_price = min(period_lows)
    price_threshold = lowest_price * 1.10
    
    if ltp <= price_threshold:
        discount_pct = round((avg_cost - ltp) / avg_cost * 100, 1)
        reason = (
            f"💰 CUSTOM RULE TRIGGERED: Price ₹{ltp:,.2f} is at 10% LOWEST of recent periods "
            f"(lowest: ₹{lowest_price:,.2f}). Position underwater by {discount_pct}%. "
            f"Strong buying opportunity to average down."
        )
        return True, reason
    
    return False, ""

def _render_decision_matrix(symbol: str, impact: dict, intel: dict, entity_id: int):
    """
    Top-level verdict card: tells the user exactly what to do and why.
    Rendered before all other tabs so the action is never buried.
    """
    price_levels = impact.get("price_levels", {})
    momentum     = impact.get("momentum", {})
    analyst      = impact.get("analyst", {})
    analysis     = analyst.get("analysis", {})
    plan         = analysis.get("trading_plan", {})
    corp_events  = impact.get("corp_events", [])

    ltp       = float(price_levels.get("ltp", 0) or 0)
    held      = intel.get("held", False)
    avg_cost  = float(intel.get("avg_buy_price", 0) or 0)
    qty       = float(intel.get("qty", 0) or 0)
    pnl       = float(intel.get("pnl", 0) or 0)
    pnl_pct   = float(intel.get("pnl_pct", 0) or 0)
    term      = intel.get("term", "SHORT")

    buy_t1    = float(plan.get("buy_target_1", 0) or 0)
    buy_sl    = float(plan.get("buy_stop_loss", 0) or 0)
    sell_t1   = float(plan.get("sell_target_1", 0) or 0)

    # ── Determine verdict ───────────────────────────────────────────────────
    verdict        = "WAIT FOR TRIGGER"
    verdict_color  = "#ffd740"
    verdict_emoji  = "⏳"
    action_lines   = []
    rule_text      = ""
 
    has_corp_event = bool(corp_events)
 
    # ═════════════════════════════════════════════════════════════════════════
    # 🔥 CUSTOM RULE 1: Force SELL if short-term profit >= ₹10,000
    # ═════════════════════════════════════════════════════════════════════════
    force_sell, force_sell_reason = _check_short_term_profit_rule(
        held=held,
        term=term,
        pnl=pnl,
        buy_date=intel.get("buy_date")
    )
    
    if force_sell:
        verdict       = "🔥 BOOK PROFIT — LOCK IN GAINS NOW"
        verdict_color = "#00c853"
        verdict_emoji = "✅"
        profit_val    = pnl
        action_lines  = [
            f"Your short-term profit: ₹{profit_val:,.0f} ({pnl_pct:+.1f}%)",
            force_sell_reason,
            f"At current price ₹{ltp:,.2f}, you're locking profit with only 20% short-term tax.",
            f"If price drops, losses will also suffer 20% tax — capture gains NOW.",
            f"**Action:** Place a SELL order at market price or ₹{ltp:,.2f} limit.",
        ]
        rule_text = "🔥 SHORT-TERM PROFIT LOCK RULE TRIGGERED"
    
    # ═════════════════════════════════════════════════════════════════════════
    # 💰 CUSTOM RULE 2: Suggest BUY MORE if underwater + at 10% lowest
    # ═════════════════════════════════════════════════════════════════════════
    elif held and avg_cost > 0 and ltp < avg_cost:
        buy_more, buy_more_reason = _check_low_price_buy_more_rule(
            held=held,
            avg_cost=avg_cost,
            ltp=ltp,
            price_levels=price_levels
        )
        
        if buy_more:
            verdict       = "💰 BUY MORE — STRONG AVERAGING OPPORTUNITY"
            verdict_color = "#69f0ae"
            verdict_emoji = "📈"
            max_loss      = round((avg_cost - ltp) / avg_cost * 100, 1)
            action_lines  = [
                f"Current position: avg cost ₹{avg_cost:,.2f}, current ₹{ltp:,.2f} ({-max_loss:.1f}%)",
                buy_more_reason,
                "**Action Plan:**",
                f"1. Calculate max shares: ₹XX budget ÷ ₹{ltp:,.2f} = shares to buy",
                f"2. Buy at ₹{ltp:,.2f} or lower to bring down average cost",
                f"3. New breakeven after averaging will be closer to current price",
            ]
            rule_text = "💰 LOW PRICE + UNDERWATER RULE TRIGGERED"
        else:
            verdict       = "WAIT FOR TRIGGER"
            verdict_color = "#ffd740"
            verdict_emoji = "⏳"
            below_cost    = round((avg_cost - ltp) / avg_cost * 100, 1)
            to_breakeven  = round(avg_cost - ltp, 2)
            action_lines  = [
                f"You hold {int(qty):,} shares at avg cost ₹{avg_cost:,.2f}. Current: ₹{ltp:,.2f} ({pnl_pct:+.1f}%).",
                f"You need price to rise ₹{to_breakeven:,.2f} ({below_cost:.1f}%) to break even.",
            ]
            if buy_sl > 0:
                action_lines.append(f"**Sell Rule:** Exit if price drops below ₹{buy_sl:,.2f} (stop loss).")
    
    # ═════════════════════════════════════════════════════════════════════════
    # STANDARD ANALYST-BASED RULES (if custom rules didn't trigger)
    # ═════════════════════════════════════════════════════════════════════════
    elif not force_sell:
        
        sell_target_valid = (
            sell_t1 > 0
            and (buy_t1 <= 0 or sell_t1 > buy_t1)
            and (ltp <= 0 or sell_t1 > ltp)
        )
        is_actually_profitable = held and avg_cost > 0 and ltp > avg_cost
 
        if ltp > 0 and buy_sl > 0 and ltp < buy_sl:
            verdict       = "CUT LOSS — EXIT NOW"
            verdict_color = "#d50000"
            verdict_emoji = "🚨"
            loss_val      = round((ltp - avg_cost) * qty, 2) if held else 0
            action_lines  = [
                f"Price ₹{ltp:,.2f} has broken below your Stop Loss (₹{buy_sl:,.2f}).",
                f"Holding further risks deeper losses. Your current loss: ₹{abs(loss_val):,.0f}.",
                "**Rule:** Exit at market price. Do not average down into a broken stock.",
            ]
            rule_text = f"EXIT if price < ₹{buy_sl:,.2f} → Rule triggered."
 
        elif ltp > 0 and sell_t1 > 0 and ltp >= sell_t1 and held and is_actually_profitable:
            verdict       = "BOOK PROFIT — CONSIDER SELLING"
            verdict_color = "#00c853"
            verdict_emoji = "✅"
            profit_val    = round((ltp - avg_cost) * qty, 2) if held else 0
            action_lines  = [
                f"Price ₹{ltp:,.2f} has reached/exceeded your Sell Target 1 (₹{sell_t1:,.2f}).",
                f"You are sitting on a profit of ₹{profit_val:,.0f} ({pnl_pct:+.1f}%).",
                "**Rule:** You can book partial or full profits here. If you hold, move stop-loss up to protect gains.",
            ]
            rule_text = f"SELL at ₹{sell_t1:,.2f} → Rule triggered."
 
        elif ltp > 0 and sell_t1 > 0 and ltp >= sell_t1 and held and not is_actually_profitable:
            verdict       = "STILL AT A LOSS — DO NOT 'BOOK PROFIT'"
            verdict_color = "#ff6d00"
            verdict_emoji = "⚠️"
            loss_val      = round((ltp - avg_cost) * qty, 2)
            action_lines  = [
                f"The generic analyst Sell Target (₹{sell_t1:,.2f}) has been hit, but your avg cost is ₹{avg_cost:,.2f} — "
                f"you are still down ₹{abs(loss_val):,.0f} ({pnl_pct:+.1f}%).",
                "The analyst's target was set for a different entry point — it does not apply to your position.",
                f"**Rule:** Your real break-even is ₹{avg_cost:,.2f}. Don't sell into a loss based on a generic target.",
            ]
            rule_text = "Analyst target ≠ your break-even. Ignore for sell decisions."
 
        elif ltp > 0 and buy_t1 > 0 and ltp <= buy_t1 and not held and sell_target_valid:
            verdict       = "BUY TRIGGER HIT"
            verdict_color = "#69f0ae"
            verdict_emoji = "🟢"
            upside_pct    = round((sell_t1 - ltp) / ltp * 100, 1) if ltp > 0 else 0
            action_lines  = [
                f"Price ₹{ltp:,.2f} is at or below Buy Target 1 (₹{buy_t1:,.2f}).",
                f"Stop Loss if you buy: ₹{buy_sl:,.2f} ({round((buy_sl - ltp) / ltp * 100, 1) if ltp > 0 else 0:.1f}% below).",
                f"If you buy now and it reaches Sell Target ₹{sell_t1:,.2f}, potential upside is {upside_pct:+.1f}%.",
                "**Rule:** Enter if momentum + volume confirm. Check confidence score ≥ 3/5 before entering.",
            ]
            rule_text = f"BUY at ₹{buy_t1:,.2f} → Rule triggered."
 
        elif ltp > 0 and buy_t1 > 0 and ltp <= buy_t1 and not held and not sell_target_valid:
            verdict       = "BUY LEVEL HIT — TARGET DATA UNRELIABLE"
            verdict_color = "#ffd740"
            verdict_emoji = "⚠️"
            action_lines  = [
                f"Price ₹{ltp:,.2f} is at or below Buy Target 1 (₹{buy_t1:,.2f}), normally a buy signal.",
                f"However, the analyst's Sell Target (₹{sell_t1:,.2f}) is below your entry/current price — "
                f"that math implies a guaranteed loss, so the target data can't be trusted right now.",
                "**Rule:** Do not enter on this signal alone. Check the Analyst tab manually before acting.",
            ]
            rule_text = "Sell target inconsistent with buy target/price — verdict downgraded."
 
        elif has_corp_event:
            verdict       = "HOLD — CORPORATE EVENT PENDING"
            verdict_color = "#ff9800"
            verdict_emoji = "📅"
            ev = corp_events[0]
            action_lines  = [
                f"Upcoming {ev.get('action_type','event')} on {_safe_date(ev.get('ex_date',''))}.",
                "On ex-date, price typically drops by the dividend amount.",
                "**Rule:** Do not open new option positions until after the ex-date. Existing sold CEs may go ITM if the drop is large.",
            ]
            rule_text = "Wait until after corporate event before acting."
 
        else:
            verdict       = "WAIT FOR TRIGGER"
            verdict_color = "#ffd740"
            verdict_emoji = "⏳"
            if held and avg_cost > 0 and ltp > 0:
                below_cost   = round((avg_cost - ltp) / avg_cost * 100, 1)
                to_breakeven = round(avg_cost - ltp, 2)
                action_lines = [
                    f"You hold {int(qty):,} shares at avg cost ₹{avg_cost:,.2f}. Current: ₹{ltp:,.2f} ({pnl_pct:+.1f}%).",
                    f"You need price to rise ₹{to_breakeven:,.2f} ({below_cost:.1f}%) to break even.",
                ]
                if buy_sl > 0:
                    action_lines.append(f"**Sell Rule:** Exit if price drops below ₹{buy_sl:,.2f} (stop loss).")
                if buy_t1 > 0 and buy_t1 > ltp:
                    action_lines.append(f"**Buy More Rule:** Average down only if price hits ₹{buy_t1:,.2f} AND confidence ≥ 3/5.")
            elif buy_t1 > 0 and buy_sl > 0:
                action_lines = [
                    f"Price ₹{ltp:,.2f} is between Buy Trigger (₹{buy_t1:,.2f}) and Stop Loss (₹{buy_sl:,.2f}).",
                    f"No position — nothing to do yet. Set an alert for ₹{buy_t1:,.2f}.",
                ]
                rule_text = f"Enter only when price ≤ ₹{buy_t1:,.2f}."
            else:
                action_lines = [
                    "Insufficient data to generate a specific trigger. Review the Analyst tab for signals.",
                ]

    # ── Render verdict card ─────────────────────────────────────────────────
    st.markdown(
        f"<div style='background:{verdict_color}18;border:2px solid {verdict_color};"
        f"border-radius:10px;padding:16px 20px;margin-bottom:16px;'>"
        f"<div style='font-size:22px;font-weight:900;color:{verdict_color};'>"
        f"{verdict_emoji} {verdict}</div>"
        f"<div style='color:#ccc;font-size:12px;margin-top:4px;'>{rule_text}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if action_lines:
        for line in action_lines:
            st.markdown(f"› {line}")

    # ── Quick stats bar ─────────────────────────────────────────────────────
    if ltp > 0:
        st.markdown("")
        cols = st.columns(4)
        cols[0].metric("Current Price", _fmt_inr(ltp))
        if held:
            pnl_sign = "+" if pnl >= 0 else ""
            cols[1].metric("Your P&L", f"₹{pnl_sign}{pnl:,.0f}", delta=f"{pnl_pct:+.2f}%")
            cols[2].metric("Avg Cost", _fmt_inr(avg_cost))
            cols[3].metric("Term", term,
                           delta="Long-term tax (12.5%)" if term == "LONG" else "Short-term tax (20%)")
        else:
            if buy_t1 > 0:
                gap = round((buy_t1 - ltp) / ltp * 100, 1) if ltp > 0 else 0
                cols[1].metric("Buy Trigger", _fmt_inr(buy_t1), delta=f"{gap:+.1f}% from now")
            if buy_sl > 0:
                cols[2].metric("Stop Loss", _fmt_inr(buy_sl))
            if sell_t1 > 0:
                cols[3].metric("Sell Target", _fmt_inr(sell_t1))

    st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Confidence Score Breakdown
# ─────────────────────────────────────────────────────────────────────────────

def _render_confidence_breakdown(impact: dict, intel: dict) -> int:
    """
    Evaluates 5 criteria, shows pass/fail/neutral for each, returns total score.
    """
    momentum  = impact.get("momentum", {})
    analyst   = impact.get("analyst", {})
    analysis  = analyst.get("analysis", {})
    technical = analysis.get("technical", {})
    trend     = impact.get("trend", {})
    seasonal  = impact.get("seasonal", {})
    mmm       = impact.get("mmm", {})
    levels    = impact.get("price_levels", {})

    held     = intel.get("held", False)
    avg_cost = float(intel.get("avg_buy_price", 0) or 0)
    ltp      = float(levels.get("ltp", 0) or 0)

    criteria: list[dict] = []

    # 1. Trend
    direction = trend.get("direction", "FLAT")
    streak    = trend.get("streak", 0)
    if direction == "UP" and streak >= 3:
        criteria.append({"name": "Price Trend", "status": "✅ Pass",
            "color": "#00c853",
            "detail": f"Price has been rising for {streak} consecutive days — momentum is with you."})
        score_trend = 1
    elif direction == "DOWN":
        criteria.append({"name": "Price Trend", "status": "❌ Fail",
            "color": "#d50000",
            "detail": f"Price is in a {streak}-day downtrend. Buying into a falling stock adds risk."})
        score_trend = 0
    else:
        criteria.append({"name": "Price Trend", "status": "🟡 Neutral",
            "color": "#ffd740",
            "detail": f"Price direction is flat or mixed. No strong trend signal either way."})
        score_trend = 0

    # 2. Momentum (MACD + RSI)
    m_signal = momentum.get("signal", "HOLD")
    rsi      = float(momentum.get("rsi", 50) or 50)
    mac_sig  = momentum.get("macd_signal", "Neutral")
    if m_signal in ("BUY", "STRONG BUY") and rsi < 70:
        criteria.append({"name": "Momentum (MACD + RSI)", "status": "✅ Pass",
            "color": "#00c853",
            "detail": f"RSI {rsi:.0f} (not overbought) + {mac_sig} = bullish momentum confirmed."})
        score_mom = 1
    elif m_signal in ("SELL", "STRONG SELL") or rsi > 75:
        criteria.append({"name": "Momentum (MACD + RSI)", "status": "❌ Fail",
            "color": "#d50000",
            "detail": f"RSI {rsi:.0f} {'(overbought)' if rsi > 70 else ''} + {mac_sig} = bearish/exhausted momentum."})
        score_mom = 0
    else:
        criteria.append({"name": "Momentum (MACD + RSI)", "status": "🟡 Neutral",
            "color": "#ffd740",
            "detail": f"RSI {rsi:.0f} is neutral. {mac_sig}. No clear momentum edge."})
        score_mom = 0

    # 3. Valuation vs Mean
    mean    = float(mmm.get("mean", 0) or 0)
    std_dev = float(mmm.get("std_dev", 0) or 0)
    if mean > 0 and ltp > 0:
        deviation = (ltp - mean) / std_dev if std_dev > 0 else 0
        if deviation < -0.5:
            criteria.append({"name": "Statistical Valuation", "status": "✅ Pass",
                "color": "#00c853",
                "detail": f"Price ₹{ltp:,.2f} is {abs(deviation):.1f}σ BELOW 90-day mean ₹{mean:,.2f} — statistically undervalued."})
            score_val = 1
        elif deviation > 1.5:
            criteria.append({"name": "Statistical Valuation", "status": "❌ Fail",
                "color": "#d50000",
                "detail": f"Price ₹{ltp:,.2f} is {deviation:.1f}σ ABOVE 90-day mean ₹{mean:,.2f} — statistically stretched."})
            score_val = 0
        else:
            criteria.append({"name": "Statistical Valuation", "status": "🟡 Neutral",
                "color": "#ffd740",
                "detail": f"Price ₹{ltp:,.2f} is within ±1σ of the mean ₹{mean:,.2f}. Fair-valued range."})
            score_val = 0
    else:
        criteria.append({"name": "Statistical Valuation", "status": "⚫ No Data",
            "color": "#546e7a", "detail": "Historical price data not available."})
        score_val = 0

    # 4. Seasonality
    cur_rank  = seasonal.get("current_month_rank", "neutral")
    best_m    = seasonal.get("best_month", "")
    worst_m   = seasonal.get("worst_month", "")
    import datetime
    cur_month = _MONTH_NAMES[datetime.datetime.now().month - 1]
    if cur_rank == "best":
        criteria.append({"name": "Seasonality", "status": "✅ Pass",
            "color": "#00c853",
            "detail": f"{cur_month} is historically the BEST month for this stock. Seasonal tailwind."})
        score_sea = 1
    elif cur_rank == "worst":
        criteria.append({"name": "Seasonality", "status": "❌ Fail",
            "color": "#d50000",
            "detail": f"{cur_month} is historically the WORST month. Seasonal headwind working against you."})
        score_sea = 0
    else:
        criteria.append({"name": "Seasonality", "status": "🟡 Neutral",
            "color": "#ffd740",
            "detail": f"{cur_month} is a neutral month. Best: {best_m}. Worst: {worst_m}."})
        score_sea = 0

    # 5. Risk / Position context
    if held and avg_cost > 0 and ltp > 0:
        plan = analysis.get("trading_plan", {})
        sl   = float(plan.get("buy_stop_loss", 0) or 0)
        gap_to_sl = round((ltp - sl) / ltp * 100, 1) if sl > 0 and ltp > 0 else 99
        if gap_to_sl > 5:
            criteria.append({"name": "Risk Management (Stop Loss Buffer)", "status": "✅ Pass",
                "color": "#00c853",
                "detail": f"Stop Loss at ₹{sl:,.2f} is {gap_to_sl:.1f}% below current price — adequate buffer."})
            score_risk = 1
        elif 0 < gap_to_sl <= 5:
            criteria.append({"name": "Risk Management (Stop Loss Buffer)", "status": "❌ Fail",
                "color": "#d50000",
                "detail": f"Stop Loss ₹{sl:,.2f} is only {gap_to_sl:.1f}% away — dangerously close. High exit risk."})
            score_risk = 0
        else:
            criteria.append({"name": "Risk Management (Stop Loss Buffer)", "status": "🟡 Neutral",
                "color": "#ffd740", "detail": "No stop loss data available to assess risk buffer."})
            score_risk = 0
    else:
        criteria.append({"name": "Risk Management (Stop Loss Buffer)", "status": "🟡 Neutral",
            "color": "#ffd740", "detail": "Not currently holding — risk assessment pending entry."})
        score_risk = 0

    total = score_trend + score_mom + score_val + score_sea + score_risk

    # ── Render breakdown ────────────────────────────────────────────────────
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:12px;margin-bottom:8px;'>"
        f"<span style='font-size:18px;font-weight:800;'>Confidence Score</span>"
        f"{_confidence_badge(total)}"
        f"<span style='color:#666;font-size:12px;'>({total}/5 criteria met)</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    with st.expander(f"Why {total}/5? — Click to see full breakdown", expanded=(total <= 2)):
        for c in criteria:
            st.markdown(
                f"<div style='display:flex;gap:10px;align-items:flex-start;"
                f"padding:8px 0;border-bottom:1px solid #1e1e2e;'>"
                f"<span style='min-width:160px;font-size:13px;font-weight:600;"
                f"color:{c['color']};'>{c['status']}</span>"
                f"<div><span style='font-weight:700;font-size:13px;'>{c['name']}</span><br>"
                f"<span style='color:#aaa;font-size:12px;'>{c['detail']}</span></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        st.markdown("")
        if total <= 1:
            st.error("⚠️ Very low confidence — avoid new positions until at least 3 criteria pass.")
        elif total == 2:
            st.warning("🟡 Low confidence — monitor only. Do not add to position yet.")
        elif total == 3:
            st.info("🔵 Moderate confidence — entry possible with tight stop-loss.")
        else:
            st.success("🟢 High confidence — conditions are favourable for action.")

    return total


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — My Holdings  (bug-fixed + enhanced)
# ─────────────────────────────────────────────────────────────────────────────

def _tab_holdings(symbol: str, user_id: int, confidence: list[int]):
    try:
        intel = api_client.get_holding_intel(symbol, user_id)
    except Exception as e:
        st.error(f"Could not load holding data: {e}")
        return intel if 'intel' in dir() else {}

    if not intel.get("held"):
        st.info("You do not currently hold this stock.")
        st.caption("Showing general market data only. Use the Analyst tab for entry signals.")
        return intel

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Quantity",       f"{intel['qty']:,.0f}")
    col2.metric("Avg Buy Price",  _fmt_inr(intel["avg_buy_price"]))
    # ✅ FIX: never show "0.0" for buy date
    buy_date_display = _safe_date(intel.get("buy_date"))
    col3.metric("Buy Date",       buy_date_display)
    holding_days = intel.get("holding_days", 0) or 0
    col4.metric("Holding Period", f"{holding_days} days" if holding_days else "—")

    term_color = "#69f0ae" if intel["term"] == "LONG" else "#ffd740"
    term_label = intel["term"]
    tax_rate   = "12.5%" if intel["term"] == "LONG" else "20%"
    st.markdown(
        f"<span style='background:{term_color};color:#000;padding:3px 10px;"
        f"border-radius:8px;font-weight:700;'>{term_label} TERM</span>"
        f"<span style='color:#888;font-size:12px;margin-left:8px;'>"
        f"Tax on gains: {tax_rate} {'(LTCG)' if intel['term'] == 'LONG' else '(STCG)'}</span>",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    col5, col6, col7 = st.columns(3)
    col5.metric("Unrealized P&L",
                f"₹{intel['pnl']:+,.2f}",
                delta=f"{intel['pnl_pct']:+.2f}%")
    col6.metric("XIRR (approx)", f"{intel['xirr_approx']:.1f}%/yr")
    col7.metric("Current Price", _fmt_inr(intel.get("current_price", 0)))

    sig_col = _SIGNAL_COLOR.get(intel["signal"], "#78909c")
    st.markdown(
        f"<div style='background:{sig_col}22;border-left:4px solid {sig_col};"
        f"padding:10px;border-radius:4px;margin-top:8px;'>"
        f"<b style='color:{sig_col};font-size:16px;'>Signal: {intel['signal']}</b><br>"
        f"<span style='color:#ccc;font-size:13px;'>{intel['signal_reason']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── Holding narrative ───────────────────────────────────────────────────
    avg   = float(intel.get("avg_buy_price", 0) or 0)
    ltp   = float(intel.get("current_price", 0) or 0)
    pnl_p = float(intel.get("pnl_pct", 0) or 0)
    xirr  = float(intel.get("xirr_approx", 0) or 0)

    if avg > 0 and ltp > 0:
        st.markdown("##### 📖 What This Means")
        diff     = round(ltp - avg, 2)
        pct_sign = "+" if diff >= 0 else ""
        context  = (
            f"You bought at ₹{avg:,.2f}. The stock is now at ₹{ltp:,.2f} "
            f"({pct_sign}₹{diff:,.2f}, {pnl_p:+.1f}%)."
        )
        if pnl_p >= 10:
            context += f" At an annualized return of {xirr:.1f}%, this is a strong performer. Consider protecting profits with a trailing stop-loss."
        elif pnl_p >= 0:
            context += f" You are in a small profit. Watch for momentum signals before adding more."
        elif pnl_p >= -10:
            context += f" You are in a minor loss. The stock needs to recover ₹{abs(diff):,.2f} for you to break even."
        else:
            context += f" This is a significant unrealized loss. Review your stop-loss rule — if breached, cut the position."
        st.info(context)

    confidence.append(intel.get("confidence_add", 0))
    return intel


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Analyst & Volatility  (enhanced with alignment narrative)
# ─────────────────────────────────────────────────────────────────────────────

def _tab_analyst(impact: dict, intel: dict, confidence: list[int]):
    analyst  = impact.get("analyst", {})
    momentum = impact.get("momentum", {})

    if not analyst and not momentum:
        st.info("Analyst data not available.")
        return

    analysis  = analyst.get("analysis", {})
    technical = analysis.get("technical", {})
    hist      = analysis.get("historical", {})
    plan      = analysis.get("trading_plan", {})

    sig     = analysis.get("signal", momentum.get("signal", "HOLD"))
    o_score = analysis.get("overall_score", 0)
    display_score = round(o_score / 5, 1) if o_score > 20 else o_score

    sig_col = _SIGNAL_COLOR.get(sig, "#78909c")
    st.markdown(
        f"<div style='background:{sig_col}22;border-left:4px solid {sig_col};"
        f"padding:12px;border-radius:4px;'>"
        f"<span style='font-size:20px;font-weight:800;color:{sig_col};'>{sig}</span>"
        f"<span style='float:right;color:#aaa;font-size:13px;'>Score {display_score}/20</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    c1, c2, c3 = st.columns(3)
    c1.metric("Technical Score",    analysis.get("technical_score", "—"))
    c2.metric("Fundamental Score",  analysis.get("fundamental_score", "—"))
    c3.metric("Historical Score",   analysis.get("historical_score", "—"))

    if technical:
        st.markdown("##### 📊 Technical Indicators")
        t1, t2, t3, t4 = st.columns(4)
        rsi = float(technical.get("rsi", 50) or 50)
        t1.metric("RSI", f"{rsi:.1f}",
                  delta="Oversold ↑" if rsi < 40 else ("Overbought ↓" if rsi > 70 else "Neutral"))
        t2.metric("MACD Hist",   f"{technical.get('macd_hist', 0):.3f}")
        t3.metric("ADX",         f"{technical.get('adx', 0):.1f}")
        t4.metric("Vol Ratio",   f"{technical.get('volume_ratio', 0):.2f}x")

    # ── Technical Alignment Narrative (NEW) ────────────────────────────────
    held     = intel.get("held", False)
    avg_cost = float(intel.get("avg_buy_price", 0) or 0)
    ltp      = float(impact.get("price_levels", {}).get("ltp", 0) or 0)

    if avg_cost > 0 and ltp > 0:
        st.markdown("##### 🧭 How These Indicators Apply to Your Position")

        rsi_val   = float(technical.get("rsi", 50) or 50) if technical else 50
        macd_h    = float(technical.get("macd_hist", 0) or 0) if technical else 0
        mac_label = str(momentum.get("macd_signal", "Neutral"))
        ma_label = str(momentum.get("ma_signal", "Neutral"))

        lines = []

        # RSI interpretation for HOLDER
        if rsi_val < 40:
            lines.append(f"**RSI {rsi_val:.0f} (Oversold):** The market has sold this stock down too aggressively. Statistically, a bounce is more likely than further selling. This is a signal to hold — not exit.")
        elif rsi_val > 70:
            lines.append(f"**RSI {rsi_val:.0f} (Overbought):** The stock has run up quickly. If you are in profit, consider booking partial gains. A pullback may be coming.")
        else:
            lines.append(f"**RSI {rsi_val:.0f} (Neutral):** Neither overbought nor oversold. Price could go either way — follow the MACD for directional bias.")

        # MACD
        if "BEARISH" in mac_label.upper():
            lines.append(f"**MACD ({mac_label}):** Bearish signal — selling pressure is accelerating. If you are in a loss, this increases the risk of the stock going further against you.")
        elif "BULLISH" in mac_label.upper():
            lines.append(f"**MACD ({mac_label}):** Bullish signal — buying pressure is building. This works in your favour if you are holding.")
        else:
            lines.append(f"**MACD ({mac_label}):** No clear directional signal. Price is in equilibrium.")

        # MA
        if "STRONG UPTREND" in ma_label.upper():
            lines.append(f"**MA ({ma_label}):** All moving averages are stacked bullishly. Price > 20-day > 50-day MA. Strong structural support below.")
        elif "DOWNTREND" in ma_label.upper():
            lines.append(f"**MA ({ma_label}):** Moving averages are bearishly stacked. Price is below key support levels — risk is elevated.")
        else:
            lines.append(f"**MA ({ma_label}):** Mixed moving average signals. No dominant trend.")

        for line in lines:
            st.markdown(f"› {line}")

    if hist:
        st.markdown("##### 📈 Price Context")
        h1, h2, h3 = st.columns(3)
        h1.metric("Trend",           hist.get("trend", "—"))
        h2.metric("% from 52W High", f"{hist.get('pct_from_52w_high', 0):.1f}%")
        h3.metric("Volatility",      f"{hist.get('volatility', 0):.2f}")

        sup = hist.get("supports", [])
        res = hist.get("resistances", [])
        if sup:
            st.caption(f"Supports: {' | '.join([_fmt_inr(x) for x in sup[:3]])}")
        if res:
            st.caption(f"Resistances: {' | '.join([_fmt_inr(x) for x in res[:3]])}")

    if plan and avg_cost > 0 and ltp > 0:
        st.markdown("##### 🎯 Trading Plan vs Your Position")
        buy_t1 = float(plan.get("buy_target_1", 0) or 0)
        buy_sl = float(plan.get("buy_stop_loss", 0) or 0)
        sell_t1= float(plan.get("sell_target_1", 0) or 0)

        p1, p2, p3 = st.columns(3)
        p1.metric("Buy Trigger",  _fmt_inr(buy_t1))
        p2.metric("Stop Loss",    _fmt_inr(buy_sl))
        p3.metric("Sell Target",  _fmt_inr(sell_t1))

        if buy_t1 > 0 and buy_t1 < avg_cost:
            st.warning(
                f"⚠️ **Note:** The generic analyst Buy Target (₹{buy_t1:,.2f}) is BELOW your average cost "
                f"(₹{avg_cost:,.2f}). Following this target would lock in a loss. "
                f"Your personal break-even is ₹{avg_cost:,.2f}. "
                f"Use the **'🎯 Suggestion' tab** for advice specific to your entry price."
            )
        elif sell_t1 > 0 and ltp >= sell_t1 * 0.97:
            st.success(
                f"✅ Price ₹{ltp:,.2f} is approaching the Sell Target ₹{sell_t1:,.2f}. "
                f"Consider booking profits partially."
            )

    if sig in ("BUY","STRONG BUY","SELL","STRONG SELL"):
        confidence.append(1)
    else:
        confidence.append(0)


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — Statistics & Chart  (enhanced with position narrative)
# ─────────────────────────────────────────────────────────────────────────────

def _tab_statistics(impact: dict, intel: dict, symbol: str, confidence: list[int]):
    mmm    = impact.get("mmm", {})
    levels = impact.get("price_levels", {})

    if not mmm and not levels:
        st.info("Statistics not available.")
        return

    if mmm and not mmm.get("error"):
        st.markdown("##### 📐 Mean · Median · Mode · Std Dev (Last 90 days)")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Mean",     _fmt_inr(mmm.get("mean")))
        s2.metric("Median",   _fmt_inr(mmm.get("median")))
        s3.metric("Mode Zone",
                  f"{_fmt_inr(mmm.get('mode_low'))} – {_fmt_inr(mmm.get('mode_high'))}",
                  delta=f"{mmm.get('mode_count',0)} trading days")
        s4.metric("Std Dev",  _fmt_inr(mmm.get("std_dev")))

        s5, s6 = st.columns(2)
        s5.metric("±1σ Band",
                  f"{_fmt_inr(mmm.get('band_1s_low'))} – {_fmt_inr(mmm.get('band_1s_high'))}")
        s6.metric("±2σ Band",
                  f"{_fmt_inr(mmm.get('band_2s_low'))} – {_fmt_inr(mmm.get('band_2s_high'))}")

        # ── "Your Position vs History" narrative (NEW) ──────────────────────
        avg_cost = float(intel.get("avg_buy_price", 0) or 0)
        ltp      = float(levels.get("ltp", 0) or 0)
        mean     = float(mmm.get("mean", 0) or 0)
        std_dev  = float(mmm.get("std_dev", 0) or 0)
        held     = intel.get("held", False)

        if ltp > 0 and mean > 0 and std_dev > 0:
            st.markdown("##### 📖 Your Position vs Historical Range")
            deviation = (ltp - mean) / std_dev
            dev_label = f"{abs(deviation):.1f}σ {'below' if deviation < 0 else 'above'} mean"

            narrative_parts = [
                f"The stock has traded around **₹{mean:,.2f}** on average over the last 90 days (with a ±₹{std_dev:,.2f} standard deviation).",
                f"Current price ₹{ltp:,.2f} is **{dev_label}** (₹{mean:,.2f}).",
            ]
            if deviation < -1.5:
                narrative_parts.append(
                    f"This level (below -1.5σ) is statistically rare — the stock is **deep in the oversold zone**. "
                    f"Historically, prices at this level tend to mean-revert upward. "
                    f"If you hold at avg cost ₹{avg_cost:,.2f}, you need a ₹{mean - ltp:,.2f} recovery to reach the mean."
                )
                confidence.append(1)
            elif deviation < -0.5:
                narrative_parts.append(
                    f"Price is modestly below the mean. A mean-reversion to ₹{mean:,.2f} "
                    f"would represent a {round((mean - ltp) / ltp * 100, 1):.1f}% gain from here."
                )
            elif deviation > 1.5:
                narrative_parts.append(
                    f"Price is **stretched above the historical mean** by {deviation:.1f}σ. "
                    f"This is where pullbacks historically occur. "
                    f"If you hold, consider setting a trailing stop-loss to protect gains."
                )
            else:
                narrative_parts.append(
                    f"Price is within the normal ±1σ range. No statistical edge in either direction."
                )

            if held and avg_cost > 0:
                recovery_needed = mean - avg_cost
                if recovery_needed > 0:
                    narrative_parts.append(
                        f"Your break-even is ₹{avg_cost:,.2f}. The stock needs to rise ₹{recovery_needed:,.2f} "
                        f"({recovery_needed / avg_cost * 100:.1f}%) to reach the historical mean."
                    )
                elif recovery_needed < 0:
                    narrative_parts.append(
                        f"Your avg cost ₹{avg_cost:,.2f} is **above** the 90-day mean ₹{mean:,.2f}. "
                        f"The stock would need exceptional momentum to sustain above your entry price."
                    )

            for part in narrative_parts:
                st.markdown(f"› {part}")

    if levels:
        st.markdown("##### 📊 Price Levels")
        l1, l2, l3 = st.columns(3)
        l1.metric("52W High",  _fmt_inr(levels.get("high_52w")))
        l1.metric("52W Low",   _fmt_inr(levels.get("low_52w")))
        l2.metric("3M High",   _fmt_inr(levels.get("high_3m")))
        l2.metric("3M Low",    _fmt_inr(levels.get("low_3m")))
        l3.metric("1M High",   _fmt_inr(levels.get("high_1m")))
        l3.metric("1M Low",    _fmt_inr(levels.get("low_1m")))

        st.markdown("##### ⚡ Max Single-Day Moves (52W)")
        d1, d2 = st.columns(2)
        d1.metric("Biggest Spike",
                  f"{levels.get('max_spike_pct',0):+.2f}%",
                  delta=levels.get("max_spike_date", ""))
        d2.metric("Biggest Drop",
                  f"{levels.get('max_drop_pct',0):.2f}%",
                  delta=levels.get("max_drop_date", ""))

    # Chart
    st.markdown("##### 📉 Price Chart")
    range_map = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365}
    chosen = st.radio("Range", list(range_map.keys()), horizontal=True,
                      key=f"chart_range_{symbol}")
    days = range_map[chosen]

    chart_key = f"_chart_{symbol}_{days}"
    if chart_key not in st.session_state or st.session_state[chart_key] is None:
        with st.spinner(f"Fetching chart for {symbol}..."):
            st.session_state[chart_key] = _get_chart_data(symbol, days)

    df_chart = st.session_state.get(chart_key)

    if df_chart is None:
        st.info("Chart data not available. Ensure yfinance is installed and the symbol is valid.")
        return

    if not df_chart.empty:
        fig = go.Figure()
        date_col  = "Datetime" if "Datetime" in df_chart.columns else df_chart.columns[0]
        close_col = "Close"

        fig.add_trace(go.Scatter(
            x=df_chart[date_col], y=df_chart[close_col],
            mode="lines", name="Close",
            line=dict(color="#1976d2", width=2),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Price: ₹%{y:,.2f}<extra></extra>",
        ))

        # Add avg cost line for holders
        avg_cost = float(intel.get("avg_buy_price", 0) or 0)
        if avg_cost > 0 and intel.get("held"):
            fig.add_hline(y=avg_cost, line_dash="solid", line_color="#ff6d00",
                          line_width=2,
                          annotation_text=f"Your Cost ₹{avg_cost:,.2f}",
                          annotation_position="right")

        if mmm and not mmm.get("error"):
            for y_val, name, color, dash in [
                (mmm.get("mean"),         "Mean",    "#4caf50", "dash"),
                (mmm.get("band_1s_high"), "+1σ",     "#ff9800", "dot"),
                (mmm.get("band_1s_low"),  "−1σ",     "#ff9800", "dot"),
                (mmm.get("band_2s_high"), "+2σ",     "#f44336", "dashdot"),
                (mmm.get("band_2s_low"),  "−2σ",     "#f44336", "dashdot"),
            ]:
                if y_val:
                    fig.add_hline(y=y_val, line_dash=dash, line_color=color,
                                  annotation_text=name, annotation_position="right")

        fig.update_layout(
            height=350, template="plotly_dark",
            title=f"{symbol} — {chosen} Price Chart",
            margin=dict(t=40, b=20, l=10, r=120),
            xaxis=dict(showgrid=False),
            yaxis=dict(autorange=True, tickprefix="₹"),
            hovermode="x unified",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
        if avg_cost > 0 and intel.get("held"):
            st.caption("🟠 Orange line = your average cost. Use this as your personal break-even reference.")


# ─────────────────────────────────────────────────────────────────────────────
# Tab 4 — Seasonality & Events  (enhanced with projection card)
# ─────────────────────────────────────────────────────────────────────────────

def _tab_seasonal_events(impact: dict, intel: dict, confidence: list[int]):
    seasonal    = impact.get("seasonal", {})
    corp_events = impact.get("corp_events", [])
    news_data   = impact.get("news", {})
    levels      = impact.get("price_levels", {})

    months = seasonal.get("months", [])
    if months:
        st.markdown("##### 🌡 Seasonal Monthly Returns (avg over last 7 years)")
        best  = seasonal.get("best_month",  "")
        worst = seasonal.get("worst_month", "")
        rank  = seasonal.get("current_month_rank", "neutral")

        import datetime
        cur_month_name = _MONTH_NAMES[datetime.datetime.now().month - 1]

        rank_badge = (
            f"🟢 **{cur_month_name} is historically the BEST month** for this stock" if rank == "best" else
            f"🔴 **{cur_month_name} is historically the WORST month** for this stock" if rank == "worst" else
            f"🟡 **{cur_month_name} is a neutral month** (best: {best}, worst: {worst})"
        )
        st.markdown(rank_badge)

        # ── Projection on Your Holding (NEW) ────────────────────────────────
        avg_cost = float(intel.get("avg_buy_price", 0) or 0)
        qty      = float(intel.get("qty", 0) or 0)
        ltp      = float(levels.get("ltp", 0) or 0)
        held     = intel.get("held", False)

        import datetime as _dt
        cur_m = _dt.datetime.now().month
        cur_month_data = next((m for m in months if m["month_num"] == cur_m), None)

        if cur_month_data and avg_cost > 0 and ltp > 0 and held:
            avg_ret  = float(cur_month_data.get("avg_ret", 0) or 0)
            win_rate = float(cur_month_data.get("win_rate", 0) or 0)
            projected_ltp = ltp * (1 + avg_ret / 100)
            projected_pnl = round((projected_ltp - avg_cost) * qty, 2)

            st.markdown("##### 📊 Seasonal Projection on Your Holding")
            p1, p2, p3 = st.columns(3)
            p1.metric(f"Avg Return in {cur_month_name}",
                      f"{avg_ret:+.1f}%",
                      delta=f"Green {win_rate:.0f}% of years")
            p2.metric("Projected Price (if avg plays out)",
                      _fmt_inr(projected_ltp),
                      delta=f"{avg_ret:+.1f}% from ₹{ltp:,.2f}")
            sign = "+" if projected_pnl >= 0 else ""
            p3.metric("Projected P&L on Your Holdings",
                      f"₹{sign}{projected_pnl:,.0f}",
                      delta="at avg seasonal return")

            if avg_ret < 0 and avg_cost > 0:
                st.warning(
                    f"⚠️ **Seasonal Headwind:** Historically, {cur_month_name} sees an average return of "
                    f"{avg_ret:.1f}%. If this pattern holds, your position (avg cost ₹{avg_cost:,.2f}) "
                    f"may see additional pressure. **Avoid averaging down** until the seasonal headwind passes."
                )
            elif avg_ret > 0:
                st.success(
                    f"✅ **Seasonal Tailwind:** {cur_month_name} has historically been positive ({avg_ret:+.1f}% avg). "
                    f"This works in your favour. However, seasonality is a bias, not a guarantee."
                )

        names  = [m["name"] for m in months]
        rets   = [m["avg_ret"] for m in months]
        colors = ["#00c853" if r >= 0 else "#d50000" for r in rets]
        labels = [f"{r:+.1f}% ({m['win_rate']:.0f}%↑)" for r, m in zip(rets, months)]

        fig = go.Figure(go.Bar(
            x=rets,
            y=list(range(len(names))),
            orientation="h",
            marker_color=colors,
            text=labels,
            textposition="outside",
        ))
        fig.update_layout(
            height=400, template="plotly_dark",
            margin=dict(t=20, b=20, l=200, r=80),
            xaxis_title="Avg Monthly Return (%)",
            yaxis=dict(
                tickmode="array",
                tickvals=list(range(len(names))),
                ticktext=names,
                showgrid=False,
            ),
            xaxis=dict(zeroline=True, zerolinecolor="#555"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Seasonal data not available.")
        confidence.append(0)

    # Corp events
    if corp_events:
        st.markdown("##### 📅 Upcoming Corporate Events (next 60 days)")
        avg_cost = float(intel.get("avg_buy_price", 0) or 0)
        qty      = float(intel.get("qty", 0) or 0)
        ltp      = float(impact.get("price_levels", {}).get("ltp", 0) or 0)

        for ev in corp_events:
            atype  = ev.get("action_type", "")
            edate  = _safe_date(ev.get("ex_date", ""))
            notes  = ev.get("notes") or ev.get("action_details") or ""
            icon   = {"DIVIDEND":"💰","BONUS":"🎁","SPLIT":"✂️","AGM":"📋"}.get(atype, "📌")
            st.markdown(f"{icon} **{atype}** — Ex Date: `{edate}`" + (f"  ·  {notes}" if notes else ""))

            # Impact estimate for DIVIDEND
            if atype == "DIVIDEND" and notes and qty > 0 and ltp > 0:
                try:
                    div_amt = float(str(notes).replace("₹","").strip().split()[0])
                    drop_pct = round(div_amt / ltp * 100, 1)
                    cash_received = round(div_amt * qty, 2)
                    st.info(
                        f"💡 **Dividend Impact on Your Holding:** "
                        f"You will receive ₹{cash_received:,.0f} cash (₹{div_amt} × {int(qty):,} shares). "
                        f"However, the stock typically **drops ~{drop_pct}%** on ex-date to reflect this. "
                        f"Net effect on portfolio value: ~zero on ex-date."
                        + (f" If you have a stop-loss below ₹{ltp - div_amt:,.2f}, it may trigger on ex-date." if avg_cost > 0 else "")
                    )
                except Exception:
                    pass
    else:
        st.success("✅ No upcoming corporate events in next 60 days")

    articles = news_data.get("articles", [])
    if articles:
        st.markdown(f"##### 📰 Latest News ({len(articles)} articles)")
        for art in articles[:6]:
            sentiment = art.get("sentiment", "neutral").lower()
            emoji     = _SENTIMENT_EMOJI.get(sentiment, "🔵")
            title     = art.get("title", "")
            source    = art.get("source", "")
            pub       = str(art.get("published_at", ""))[:10]
            url       = art.get("url", "")
            if url:
                st.markdown(
                    f"{emoji} [{title}]({url})  "
                    f"<span style='color:#888;font-size:11px;'>{source} · {pub}</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"{emoji} **{title}**  "
                    f"<span style='color:#888;font-size:11px;'>{source} · {pub}</span>",
                    unsafe_allow_html=True,
                )
    else:
        st.info("No recent news found.")


# ─────────────────────────────────────────────────────────────────────────────
# Tab 5 — Suggestion (unchanged from v3 — already functional)
# ─────────────────────────────────────────────────────────────────────────────
def _render_sell_ce_conditions(sug: dict) -> None:
    """Display 4-condition check results for SELL_CE signal"""
    if sug.get("signal") != "SELL_CE" or "conditions" not in sug:
        return
    
    conditions = sug.get("conditions", {})
    condition_verdict = sug.get("condition_verdict", "⚠️ UNKNOWN")
    condition_detail = sug.get("condition_detail", "")
    
    st.markdown("---")
    st.markdown("#### 📊 4-Condition Check for Covered Call Eligibility")
    
    col1, col2 = st.columns([1, 3])
    with col1:
        st.markdown(f"**Verdict:** {condition_verdict}")
    with col2:
        st.markdown(f"**Detail:** {condition_detail}")
    
    # Show individual condition results
    st.markdown("**Individual Condition Results:**")
    
    for cond_name, cond_key in [("Profit", "profit"), ("Market", "market"), ("Seasonal", "seasonal"), ("Lot Size", "lot_size")]:
        cond = conditions.get(cond_key, {})
        status = cond.get("status", "⚠️")
        message = cond.get("message", "")
        
        col1, col2 = st.columns([0.5, 4])
        with col1:
            st.markdown(f"{status}")
        with col2:
            st.markdown(f"**{cond_name}:** {message}")
    
    # Show pass/fail count
    pass_count = sum(1 for c in conditions.values() if isinstance(c, dict) and c.get("status", "").startswith("✅"))
    st.info(f"✅ {pass_count}/4 conditions favorable")
    
def _tab_suggestion(symbol: str, entity_id: int, impact: dict, is_group: bool):
    if is_group:
        st.info("Suggestion details available in single-user mode only.")
        return

    price_levels = impact.get("price_levels", {})
    spot = float(price_levels.get("ltp", 0) or 0)

    sug_key = f"_sug_{symbol}_{entity_id}"
    if sug_key not in st.session_state:
        try:
            sug = api_client.get_suggestion(symbol=symbol, user_id=entity_id, spot=spot)
        except Exception as e:
            sug = {"signal": "NEUTRAL", "reason": f"Could not load: {e}",
                   "confidence": "LOW", "flags": []}
        st.session_state[sug_key] = sug

    sug        = st.session_state.get(sug_key, {})
    signal     = sug.get("signal", "NEUTRAL")
    reason     = sug.get("reason", "No signal available")
    confidence = sug.get("confidence", "LOW")
    flags      = sug.get("flags", [])
    strike     = sug.get("strike")
    expiry     = sug.get("expiry")
    breakeven  = sug.get("breakeven")

    sig_col  = _SIGNAL_COLOR.get(signal, "#78909c")
    conf_col = {"HIGH": "#00c853", "MEDIUM": "#ffd740", "LOW": "#78909c"}.get(confidence, "#78909c")

    st.markdown(
        f"<div style='background:{sig_col}22;border-left:5px solid {sig_col};"
        f"padding:14px;border-radius:6px;margin-bottom:12px;'>"
        f"<span style='font-size:22px;font-weight:900;color:{sig_col};'>"
        f"{_SUGGESTION_LABEL.get(signal, signal)}</span>"
        f"<span style='float:right;background:{conf_col};color:#000;"
        f"padding:2px 8px;border-radius:8px;font-size:12px;font-weight:700;'>"
        f"{confidence} confidence</span><br>"
        f"<span style='color:#ccc;font-size:13px;margin-top:4px;display:block;'>{reason}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if strike or expiry or breakeven:
        m1, m2, m3 = st.columns(3)
        m1.metric("Suggested Strike", _fmt_inr(strike) if strike else "—")
        m2.metric("Expiry",           expiry if expiry else "—")
        m3.metric("Breakeven",        _fmt_inr(breakeven) if breakeven else "—")

    # Flags
    FLAG_EXPLANATIONS = {
        "corp_event_soon": {"icon": "💰", "label": "Corporate Event Soon",
            "impact_ce_seller": "✅ GOOD for CE seller — dividend-driven price drop reduces ITM risk.",
            "generic": "Stock has a dividend/results event within the option expiry window."},
        "rollover_due_CE": {"icon": "⏰", "label": "CE Expiring Soon (< 15 days)",
            "impact_ce_seller": "⚠️ Time decay accelerates. If OTM, let it expire. If losing, square off.",
            "generic": "Your CE position expires in less than 15 days."},
        "rollover_due_PE": {"icon": "⏰", "label": "PE Expiring Soon (< 15 days)",
            "impact_pe_seller": "⚠️ Time decay accelerates. Square off if in loss.",
            "generic": "Your PE position expires in less than 15 days."},
        "near_1m_high": {"icon": "📈", "label": "Near 1-Month High",
            "impact_ce_seller": "🔴 RISKY — breakout above this could put your CE ITM.",
            "generic": "Stock price is within 5% of its 1-month high."},
        "near_1m_low": {"icon": "📉", "label": "Near 1-Month Low",
            "impact_pe_seller": "🔴 RISKY — breakdown below this could put your PE ITM.",
            "generic": "Stock price is within 5% of its 1-month low."},
        "loss_near_expiry": {"icon": "🚨", "label": "Losing Position Expiring Soon",
            "impact_ce_seller": "🔴 CRITICAL — Square off the CE first. Never rollover a losing position.",
            "generic": "You are in a loss AND the contract expires soon."},
    }

    pos_key = f"_fno_pos_{entity_id}"
    if pos_key not in st.session_state:
        try:
            st.session_state[pos_key] = api_client.get_fno_positions(entity_id)
        except Exception:
            st.session_state[pos_key] = []

    all_pos  = st.session_state.get(pos_key, [])
    sym_pos  = [p for p in all_pos if str(p.get("underlying","")).upper() == symbol.upper()]

    if flags:
        st.markdown("##### 🏷️ Flags — What They Mean for You")
        pos_key_str = ""
        if sym_pos:
            itype = sym_pos[0].get("instrument_type","")
            qty_v = float(sym_pos[0].get("open_qty", 0) or 0)
            direction = "seller" if qty_v < 0 else "buyer"
            pos_key_str = f"{itype.lower()}_{direction}"

        for flag in flags:
            info       = FLAG_EXPLANATIONS.get(flag, {"icon":"📌","label":flag,"generic":"See details above."})
            impact_msg = info.get(f"impact_{pos_key_str}","") or info.get("generic","")
            st.markdown(
                f"<div style='background:#1a1a2e;border-left:3px solid #555;"
                f"padding:10px;border-radius:4px;margin:4px 0;'>"
                f"<b>{info['icon']} {info['label']}</b><br>"
                f"<span style='color:#ccc;font-size:13px;'>{impact_msg}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")
    st.markdown("##### 📋 Current F&O Positions (this symbol)")
    if sym_pos:
        rows = []
        for p in sym_pos:
            qty_r  = float(p.get("open_qty", 0) or 0)
            avg_r  = float(p.get("avg_price", 0) or 0)
            itype  = str(p.get("instrument_type", ""))
            pnl_r  = round((spot - avg_r) * qty_r, 2) if itype == "FUT" and spot > 0 else 0.0
            rows.append({
                "Type":     itype,
                "Expiry":   str(p.get("expiry_date",""))[:10],
                "Strike":   f"{float(p.get('strike_price', 0) or 0):,.0f}" if itype in ("CE","PE") else "—",
                "Qty":      f"{int(abs(qty_r)):,}",
                "Direction":"SOLD" if qty_r < 0 else "BOUGHT",
                "Avg":      _fmt_inr(avg_r),
                "FUT P&L":  f"₹{pnl_r:+,.2f}" if pnl_r else "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info(f"No open F&O positions for {symbol}.")

    st.markdown("##### 📦 Equity Holding")
    intel = impact.get("account_context", {})
    if not intel:
        try:
            intel = api_client.get_holding_intel(symbol, entity_id)
        except Exception:
            intel = {}

    if intel.get("held"):
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Qty Held",  f"{intel.get('qty',0):,.0f}")
        e2.metric("Avg Cost",  _fmt_inr(intel.get("avg_buy_price",0)))
        e3.metric("CMP",       _fmt_inr(intel.get("current_price", spot)))
        pnl_h = float(intel.get("pnl", 0) or 0)
        pct_h = float(intel.get("pnl_pct", 0) or 0)
        e4.metric("P&L",       f"₹{pnl_h:+,.0f}", delta=f"{pct_h:+.2f}%")
    else:
        st.info("No equity holding for this stock.")

    if st.button("🔄 Refresh Suggestion", key=f"ref_sug_{symbol}_{entity_id}"):
        st.session_state.pop(sug_key, None)
        st.session_state.pop(pos_key, None)
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Why dropdown — orchestrator  (redesigned: Decision Matrix first)
# ─────────────────────────────────────────────────────────────────────────────

def _render_why_dropdown(symbol: str, entity_id: int, is_group: bool = False):
    """Load full impact data then render decision matrix + 5-tab analysis."""
    with st.spinner(f"Loading analysis for {symbol}…"):
        try:
            impact = cached_impact(symbol, 0 if is_group else entity_id)
        except Exception as e:
            st.error(f"Could not load impact data: {e}")
            return

    # Load holding intel (needed by multiple tabs)
    intel: dict = {}
    if not is_group:
        try:
            ltp = float(impact.get("price_levels", {}).get("ltp", 0) or 0)
            ctx = impact.get("account_context", {})
            if ctx and ctx.get("held"):
                intel = ctx
            else:
                intel = api_client.get_holding_intel(symbol, entity_id)
                if ltp > 0:
                    intel["current_price"] = ltp
        except Exception:
            intel = {}

    # ── 1. Decision Matrix (always visible, no tab required) ────────────────
    st.markdown(f"#### 🧭 {symbol} — Decision Centre")
    _render_decision_matrix(symbol, impact, intel, entity_id)

    # ── 2. Confidence score with breakdown ──────────────────────────────────
    confidence_score = _render_confidence_breakdown(impact, intel)

    # ── Reconciliation: don't let the top verdict and confidence score
    # silently contradict each other. If the verdict above said "BUY"/"SELL"
    # action but the confidence breakdown says low/very-low, call it out
    # explicitly instead of leaving the user to spot the conflict themselves.
    _decision_verdict_key = f"_last_verdict_{symbol}_{entity_id}"
    _last_verdict = st.session_state.get(_decision_verdict_key, "")
    _action_verdicts = ("BUY TRIGGER HIT", "BOOK PROFIT — CONSIDER SELLING")
    if _last_verdict in _action_verdicts and confidence_score <= 2:
        st.warning(
            f"⚠️ **Conflicting signals:** The verdict above suggests action, but the Confidence "
            f"Score is only {confidence_score}/5 — most criteria don't actually support it. "
            f"Treat the top verdict as a price-level trigger only, not a green light to act, "
            f"until more criteria confirm."
        )

    st.divider()

    # ── 3. Deep-dive tabs ───────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📂 My Holdings",
        "🤖 Analyst",
        "📊 Statistics",
        "🌡 Seasonality & News",
        "🎯 Suggestion",
    ])

    confidence: list[int] = [1 if confidence_score >= 3 else 0]

    with tab1:
        if is_group:
            st.info("Holding details available in single-user mode.")
        else:
            _tab_holdings(symbol, entity_id, confidence)

    with tab2:
        _tab_analyst(impact, intel, confidence)

    with tab3:
        _tab_statistics(impact, intel, symbol, confidence)

    with tab4:
        _tab_seasonal_events(impact, intel, confidence)

    with tab5:
        _tab_suggestion(symbol, entity_id, impact, is_group)

    total = sum(confidence)
    st.markdown(
        f"<div style='margin-top:12px;text-align:right;'>"
        f"Overall Confidence: {_confidence_badge(total)}</div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main list row renderer
# ─────────────────────────────────────────────────────────────────────────────

def _render_stock_row(
    row: dict,
    idx: int,
    entity_id: int,
    is_group: bool,
    edit_mode: bool,
    selected_for_delete: list[str],
):
    symbol = row.get("symbol", "")
    cache_key = f"_ql_{symbol}"
    qd = st.session_state.get(cache_key, {"price": {}, "trend": {}, "momentum": {}})

    price   = qd.get("price", {})
    trend   = qd.get("trend", {})
    moment  = qd.get("momentum", {})

    ltp        = float(price.get("ltp", 0) or 0)
    change_pct = float(price.get("change_pct", 0) or 0)
    t_arrow    = _trend_arrow(trend)
    m_signal   = moment.get("signal", "HOLD")
    m_score    = float(moment.get("score", 0) or 0)
    quick_conf = int(min(5, max(0, round((m_score + 12) / 4.8))))

    sug_key    = f"_sug_{symbol}_{entity_id}"
    sug        = st.session_state.get(sug_key, {})
    sug_signal = sug.get("signal", "")

    # ── Translate generic "HOLD" to something more meaningful ──────────────
    display_signal = m_signal
    if m_signal == "HOLD":
        if ltp > 0:
            display_signal = "⏳ Wait"
        else:
            display_signal = "—"

    col_check, col_name, col_price, col_pct, col_trend, col_sig, col_sug, col_conf, col_why = \
        st.columns([0.4, 2, 1.1, 0.9, 0.7, 1.1, 1.2, 0.8, 0.6])

    with col_check:
        if edit_mode:
            checked = st.checkbox("", key=f"del_{idx}_{symbol}",
                                  value=(symbol in selected_for_delete))
            if checked and symbol not in selected_for_delete:
                selected_for_delete.append(symbol)
            elif not checked and symbol in selected_for_delete:
                selected_for_delete.remove(symbol)

    with col_name:
        st.markdown(f"**{symbol}**")
        canon = row.get("canonical_symbol", "")
        if canon and canon != symbol:
            st.caption(canon)

    with col_price:
        if ltp:
            st.markdown(f"**{_fmt_inr(ltp)}**")
        else:
            st.markdown("—")

    with col_pct:
        if change_pct:
            st.markdown(_pct_badge(change_pct), unsafe_allow_html=True)
        else:
            st.markdown("—")

    with col_trend:
        color = "#00c853" if "▲" in t_arrow else ("#d50000" if "▼" in t_arrow else "#aaa")
        st.markdown(
            f"<span style='color:{color};font-weight:700;'>{t_arrow}</span>",
            unsafe_allow_html=True,
        )

    with col_sig:
        sig_col = _SIGNAL_COLOR.get(m_signal, "#78909c")
        st.markdown(
            f"<span style='color:{sig_col};font-weight:700;font-size:12px;'>"
            f"{display_signal}</span>",
            unsafe_allow_html=True,
        )

    with col_sug:
        if sug_signal and sug_signal != "NEUTRAL":
            s_col   = _SIGNAL_COLOR.get(sug_signal, "#78909c")
            s_label = _SUGGESTION_LABEL.get(sug_signal, sug_signal)
            st.markdown(
                f"<span style='color:{s_col};font-weight:700;font-size:11px;'>{s_label}</span>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown("<span style='color:#555;font-size:11px;'>—</span>",
                        unsafe_allow_html=True)

    with col_conf:
        st.markdown(_confidence_badge(quick_conf), unsafe_allow_html=True)

    with col_why:
        if st.button("Why?", key=f"why_{idx}_{symbol}", type="secondary"):
            toggle_key = f"_why_open_{symbol}_{entity_id}"
            st.session_state[toggle_key] = not st.session_state.get(toggle_key, False)

    if st.session_state.get(f"_why_open_{symbol}_{entity_id}", False):
        with st.container():
            st.markdown(
                f"<div style='border:1px solid #333;border-radius:8px;"
                f"padding:16px;margin:4px 0 12px 0;background:#0e1117;'>",
                unsafe_allow_html=True,
            )
            _render_why_dropdown(symbol, entity_id, is_group=is_group)
            st.markdown("</div>", unsafe_allow_html=True)

    st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# NSE search widget (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def _search_and_add_widget(cache_key: str, entity_id: int, add_fn, rows: list[dict]):
    existing_symbols = {r.get("symbol", "").upper() for r in rows}
    st.markdown("#### 🔍 Search & Add Stock")
    search_input = st.text_input(
        "Type NSE symbol or company name",
        key=f"nse_search_{cache_key}",
        placeholder="e.g. RELIANCE, HDFC Bank…",
    )
    suggestions = []
    if search_input and len(search_input.strip()) >= 1:
        try:
            suggestions = api_client.nse_search(search_input.strip())
        except Exception:
            suggestions = []

    selected_symbol = None
    if suggestions:
        for sug in suggestions[:8]:
            sym  = sug.get("symbol", "")
            name = sug.get("name", sym)
            already = sym.upper() in existing_symbols
            btn_label = f"**{sym}** — {name}" + ("  ✓ already added" if already else "")
            if st.button(btn_label, key=f"sug_{cache_key}_{sym}", disabled=already):
                selected_symbol = sym

    if selected_symbol:
        try:
            result = add_fn(entity_id, selected_symbol, selected_symbol, False, "")
            if result.get("status") == "added":
                st.success(f"✅ {selected_symbol} added to wishlist")
                if cache_key in st.session_state:
                    st.session_state[cache_key] = None
                st.rerun()
            else:
                st.warning(result.get("message", "Already in wishlist"))
        except Exception as e:
            st.error(f"Add failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Core renderer (shared by single-user & group) — unchanged from v3
# ─────────────────────────────────────────────────────────────────────────────

def _render_core(
    *,
    cache_key:    str,
    entity_id:    int,
    is_group:     bool,
    get_fn,
    add_fn,
    remove_fn,
    sync_fn,
    clear_auto_fn,
    clear_all_fn,
):
    if cache_key not in st.session_state or st.session_state[cache_key] is None:
        with st.spinner("Loading wishlist..."):
            try:
                st.session_state[cache_key] = get_fn(entity_id)
            except Exception as e:
                st.error(f"Failed to load wishlist: {e}")
                st.session_state[cache_key] = []

    rows: list[dict] = st.session_state.get(cache_key, [])

    header_col, n_col, sync_col, edit_col = st.columns([4, 1, 1.5, 1])
    with header_col:
        st.markdown(f"**{len(rows)} stocks**")
    with n_col:
        if st.button("➕", key=f"add_btn_{cache_key}", help="Add stock"):
            st.session_state[f"show_search_{cache_key}"] = not \
                st.session_state.get(f"show_search_{cache_key}", False)
    with sync_col:
        if st.button("🔄 Sync", key=f"sync_{cache_key}", help="Sync from holdings"):
            with st.spinner("Syncing…"):
                try:
                    r = sync_fn(entity_id)
                    st.success(f"Synced — {r.get('added',0)} new")
                    st.session_state[cache_key] = None
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    with edit_col:
        if st.button("✏️", key=f"edit_btn_{cache_key}", help="Edit / remove"):
            st.session_state[f"edit_mode_{cache_key}"] = not \
                st.session_state.get(f"edit_mode_{cache_key}", False)

    if st.session_state.get(f"show_search_{cache_key}", False):
        _search_and_add_widget(cache_key, entity_id, add_fn, rows)
        st.markdown("---")

    edit_mode          = st.session_state.get(f"edit_mode_{cache_key}", False)
    selected_for_delete: list[str] = st.session_state.get(f"_del_sel_{cache_key}", [])

    sort_options = ["As added", "Market price ↓", "Day change % ↓", "Alphabetically"]
    sort_key_s   = f"sort_{cache_key}"
    if sort_key_s not in st.session_state:
        st.session_state[sort_key_s] = "As added"

    sort_choice = st.selectbox("Sort by", sort_options, key=sort_key_s,
                               label_visibility="collapsed")
    sorted_rows = list(rows)
    if sort_choice == "Market price ↓":
        sorted_rows.sort(
            key=lambda r: float(st.session_state.get(f"_ql_{r['symbol']}", {}).get("price", {}).get("ltp", 0) or 0),
            reverse=True,
        )
    elif sort_choice == "Day change % ↓":
        sorted_rows.sort(
            key=lambda r: float(st.session_state.get(f"_ql_{r['symbol']}", {}).get("price", {}).get("change_pct", 0) or 0),
            reverse=True,
        )
    elif sort_choice == "Alphabetically":
        sorted_rows.sort(key=lambda r: r.get("symbol", ""))

    import time as _time_mod
    prices_loaded    = st.session_state.get(f"prices_loaded_{cache_key}", False)
    last_price_fetch = st.session_state.get(f"prices_last_fetch_{cache_key}", 0)
    _now             = _time_mod.time()
    _five_min        = 5 * 60

    if not prices_loaded and sorted_rows:
        with st.spinner(f"📡 Loading live prices for {len(sorted_rows)} stocks…"):
            _load_prices_batch(sorted_rows, entity_id, is_group, cache_key)
        st.session_state[f"prices_last_fetch_{cache_key}"] = _now
        st.rerun()

    if prices_loaded and sorted_rows and (_now - last_price_fetch) >= _five_min:
        import threading as _threading
        def _bg_refresh():
            try:
                _load_prices_batch(sorted_rows, entity_id, is_group, cache_key)
                st.session_state[f"prices_last_fetch_{cache_key}"] = _time_mod.time()
            except Exception:
                pass
        _threading.Thread(target=_bg_refresh, daemon=True).start()
        st.session_state[f"prices_last_fetch_{cache_key}"] = _now

    if prices_loaded:
        _elapsed = int(_now - last_price_fetch)
        _age_str  = "just now" if _elapsed < 60 else (
            f"{_elapsed // 60}m ago" if _elapsed < 3600 else f"{_elapsed // 3600}h ago"
        )
        _next_sec = max(0, _five_min - _elapsed)
        _next_str = f"{_next_sec // 60}m {_next_sec % 60}s" if _next_sec > 0 else "any moment"

        c1, c2, c3 = st.columns([2, 2, 4])
        with c1:
            if st.button("🔄 Refresh Now", key=f"refresh_prices_{cache_key}"):
                for sym in [r.get("symbol", "") for r in sorted_rows]:
                    st.session_state.pop(f"_ql_{sym}", None)
                cached_impact_quick_batch.clear()
                st.session_state[f"prices_loaded_{cache_key}"] = False
                st.session_state[f"prices_last_fetch_{cache_key}"] = 0
                st.rerun()
        with c2:
            if st.button("💡 Load Suggestions", key=f"load_sug_{cache_key}"):
                if not is_group:
                    syms_missing = [
                        r.get("symbol", "") for r in sorted_rows
                        if r.get("symbol") and f"_sug_{r['symbol']}_{entity_id}" not in st.session_state
                    ]
                    if syms_missing:
                        with st.spinner(f"Fetching suggestions for {len(syms_missing)} stocks…"):
                            batch_sug = api_client.get_suggestion_batch(syms_missing, entity_id)
                        for sym, sug in batch_sug.items():
                            st.session_state[f"_sug_{sym}_{entity_id}"] = sug
                    st.rerun()
                else:
                    st.info("Suggestions available in single-user mode.")
        with c3:
            st.caption(f"⏱ Prices updated {_age_str} · auto-refreshes in {_next_str}")

    hc = st.columns([0.4, 2, 1.1, 0.9, 0.7, 1.1, 1.2, 0.8, 0.6])
    labels = ["", "Symbol", "Price", "Chg%", "Trend", "Signal", "F&O Action", "Conf.", ""]
    for col, lbl in zip(hc, labels):
        col.markdown(f"<span style='color:#666;font-size:11px;'>{lbl}</span>",
                     unsafe_allow_html=True)
    st.divider()

    if not sorted_rows:
        st.info("Wishlist is empty. Click ➕ to add stocks or 🔄 to sync from holdings.")
        return

    for idx, row in enumerate(sorted_rows):
        _render_stock_row(row, idx, entity_id, is_group, edit_mode, selected_for_delete)

    if edit_mode:
        st.session_state[f"_del_sel_{cache_key}"] = selected_for_delete
        if selected_for_delete:
            st.warning(f"Selected for removal: {', '.join(selected_for_delete)}")
            if st.button("🗑 Remove selected", key=f"del_confirm_{cache_key}", type="primary"):
                for sym in selected_for_delete:
                    try:
                        remove_fn(entity_id, sym)
                    except Exception:
                        pass
                st.session_state[cache_key] = None
                st.session_state[f"_del_sel_{cache_key}"] = []
                st.session_state[f"edit_mode_{cache_key}"] = False
                st.rerun()

        col_ca, col_all = st.columns(2)
        with col_ca:
            if st.button("🧹 Remove auto-synced", key=f"clr_auto_{cache_key}"):
                r = clear_auto_fn(entity_id)
                st.info(f"Removed {r.get('removed',0)} auto-synced symbols")
                st.session_state[cache_key] = None
                st.rerun()
        with col_all:
            if st.button("🗑 Clear all", key=f"clr_all_{cache_key}", type="primary"):
                r = clear_all_fn(entity_id)
                st.warning(f"Cleared {r.get('removed',0)} symbols")
                st.session_state[cache_key] = None
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

def render_wishlist_tab(user_id: int, api_fns: dict) -> None:
    st.header("📋 Wishlist")
    st.caption(
        "Prices auto-load and refresh every 5 minutes. "
        "Click **Why?** on any row for a full decision breakdown — verdict, confidence score, and action guide."
    )
    _render_core(
        cache_key    = f"wl_user_{user_id}",
        entity_id    = user_id,
        is_group     = False,
        get_fn       = api_fns["get"],
        add_fn       = api_fns["add"],
        remove_fn    = api_fns["remove"],
        sync_fn      = api_fns["sync"],
        clear_auto_fn= api_fns["clear_auto"],
        clear_all_fn = api_fns["clear_all"],
    )


def render_group_wishlist_tab(group_id: int, api_fns: dict) -> None:
    st.header("📋 Group Wishlist")
    st.caption(
        "Shared watchlist across all group members. "
        "Holding and suggestion details are available in single-user mode."
    )
    _render_core(
        cache_key    = f"wl_group_{group_id}",
        entity_id    = group_id,
        is_group     = True,
        get_fn       = api_fns["get"],
        add_fn       = api_fns["add"],
        remove_fn    = api_fns["remove"],
        sync_fn      = api_fns["sync"],
        clear_auto_fn= api_fns["clear_auto"],
        clear_all_fn = api_fns["clear_all"],
    )