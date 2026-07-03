"""
ce_pe_ui.py  — v5 (fast, session-state cached, instant tab switching)
=======================================================================
Key changes vs v4:
  - All data keyed by user_id / group_id in session_state → instant on re-visit
  - No data is fetched unless explicitly triggered by a Run/Refresh button
  - Cached data is shown immediately even after page rerun / tab switch
  - Price map cached per-user in session_state (no re-fetch on re-render)
  - All heavy spinner calls only on button click
"""
from __future__ import annotations

import datetime
import streamlit as st
import pandas as pd

try:
    import backend.services.market_status as market_status
    _HAS_MARKET_STATUS = True
except ImportError:
    _HAS_MARKET_STATUS = False


# ─────────────────────────────────────────────────────────────────────────────
# Session-state helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ss_get(key, default=None):
    return st.session_state.get(key, default)


def _ss_set(key, value):
    st.session_state[key] = value


def _ss_clear(key):
    st.session_state.pop(key, None)


# ─────────────────────────────────────────────────────────────────────────────
# Format helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fp(v, d: int = 2) -> str:
    if v is None or v == "" or v == 0:
        return "—"
    try:
        n = float(v)
        sign = "-" if n < 0 else ""
        abs_n = abs(n)
        int_part = int(abs_n)
        s = str(int_part)
        if len(s) > 3:
            result = s[-3:]
            s = s[:-3]
            while s:
                result = s[-2:] + "," + result
                s = s[:-2]
        else:
            result = s
        if d > 0:
            frac = round(abs_n - int_part, d)
            dec_str = f"{frac:.{d}f}"[1:]
            return f"{sign}{result}{dec_str}"
        return f"{sign}{result}"
    except Exception:
        return str(v)


def _fpct(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return str(v)


def _fiv(v) -> str:
    if v is None or v == "" or v == 0:
        return "—"
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return str(v)


def _fpnl(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        n = float(v)
        sign = "+" if n >= 0 else "-"
        abs_n = abs(n)
        int_part = int(abs_n)
        s = str(int_part)
        if len(s) > 3:
            result = s[-3:]
            s = s[:-3]
            while s:
                result = s[-2:] + "," + result
                s = s[:-2]
        else:
            result = s
        return f"₹{sign}{result}"
    except Exception:
        return str(v)


def _fint(v) -> str:
    if v is None or v == "" or v == 0:
        return "—"
    try:
        return str(int(float(v)))
    except Exception:
        return str(v)


def _ffut(v) -> str:
    if v is None or v == "" or v == 0:
        return "—"
    try:
        n = int(float(v))
        if n == 0:
            return "—"
        sign = "+" if n > 0 else ""
        return f"{sign}{n:,d}"
    except Exception:
        return str(v)


def _fdelta(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)


def _fprob(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.0f}%"
    except Exception:
        return str(v)


# ─────────────────────────────────────────────────────────────────────────────
# Styler helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_map(styler, fn, subset):
    try:
        return styler.map(fn, subset=subset)
    except AttributeError:
        return styler.applymap(fn, subset=subset)


def _style_signal(val):
    if val == "SELL CE":
        return "background-color:#1a4731;color:#6fcf97;font-weight:bold"
    if val == "SELL PE":
        return "background-color:#4a1020;color:#f48fb1;font-weight:bold"
    if val == "NEUTRAL":
        return "color:#aaaaaa"
    return ""


def _style_pos_signal(val):
    return {
        "SQUARE_OFF":   "background-color:#7f1d1d;color:#fca5a5;font-weight:bold",
        "OPT_ROLLOVER": "background-color:#1e3a5f;color:#93c5fd;font-weight:bold",
        "FUT_ROLLOVER": "background-color:#1e3a5f;color:#bfdbfe;font-weight:bold",
        "ROLLOVER":     "background-color:#1e3a5f;color:#93c5fd;font-weight:bold",
        "CORRECTION":   "background-color:#4a3000;color:#fcd34d;font-weight:bold",
        "FRESH":        "background-color:#14532d;color:#86efac;font-weight:bold",
        "HOLD":         "background-color:#1e1b4b;color:#a5b4fc;font-weight:bold",
    }.get(val, "")


def _style_fin_signal(val):
    return _style_signal(val)


def _style_pct(val):
    if val == "—":
        return "color:#555"
    try:
        num = float(str(val).replace("%", "").replace("+", ""))
        if num > 0:
            return "color:#6fcf97"
        if num < 0:
            return "color:#f48fb1"
    except Exception:
        pass
    return ""


def _style_pnl(val):
    if val == "—":
        return "color:#555"
    try:
        num = float(str(val).replace("₹", "").replace(",", "").replace("+", ""))
        if num > 0:
            return "color:#6fcf97"
        if num < 0:
            return "color:#f48fb1"
    except Exception:
        pass
    return ""


def _style_delta(val):
    if val == "—":
        return "color:#555"
    try:
        num = abs(float(val))
        if num <= 0.20:
            return "color:#6fcf97"
        if num <= 0.30:
            return "color:#fcd34d"
        return "color:#f48fb1"
    except Exception:
        pass
    return ""


def _style_fut_qty(val):
    if val == "—":
        return "color:#555"
    try:
        n = int(str(val).replace(",", "").replace("+", ""))
        if n > 0:
            return "color:#6fcf97;font-weight:bold"
        if n < 0:
            return "color:#f48fb1;font-weight:bold"
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Section A — Basic Screener
# ─────────────────────────────────────────────────────────────────────────────

def _render_basic_screener(user_id: int, get_basic_fn):
    """
    Basic CE/PE screener with full session-state caching.
    Data is fetched only when the Refresh button is clicked.
    On every tab revisit the cached table is shown instantly.
    """
    st.caption(
        "F&O-eligible holdings — signal based on 52W extremes. "
        "OHLC + option chain fetched live. "
        "⏱ **Estimated load time: 15-20 sec on first load.**"
    )

    # ── Session-state keys scoped to this user ────────────────────────────────
    data_key  = f"ce_pe_basic_data_{user_id}"
    time_key  = f"ce_pe_basic_ts_{user_id}"

    cached_data = _ss_get(data_key)
    last_loaded = _ss_get(time_key)

    # ── Header row: refresh button + last-loaded time ─────────────────────────
    col_btn, col_time = st.columns([2, 3])
    with col_btn:
        refresh_clicked = st.button(
            "🔄 Refresh Basic Screener (~15-20 sec)",
            key=f"ce_pe_refresh_basic_{user_id}",
        )
    with col_time:
        if last_loaded:
            st.caption(f"⏱ Last loaded: **{last_loaded}**  |  Click refresh to update")
        elif cached_data:
            st.caption("⏱ Loaded this session")
        else:
            st.caption("ℹ️ No data yet — click Refresh to load")

    # ── Fetch only on button click ─────────────────────────────────────────────
    if refresh_clicked:
        with st.spinner("📡 Scanning F&O-eligible holdings… (~15-20 sec)"):
            try:
                resp = get_basic_fn(user_id)
                if resp.get("status") == "success":
                    _ss_set(data_key, resp["data"])
                    _ss_set(time_key, datetime.datetime.now().strftime("%H:%M:%S"))
                    cached_data = _ss_get(data_key)
                    st.success(f"✅ Loaded {resp['rows']} rows")
                else:
                    st.error(resp.get("message", "Unknown error"))
                    return
            except Exception as e:
                st.error(f"Failed: {e}")
                return

    # ── Nothing cached yet — prompt user ──────────────────────────────────────
    if not cached_data:
        st.info("Click **🔄 Refresh Basic Screener** to load the table.")
        return

    # ── Render cached table (instant, no API call) ────────────────────────────
    df = pd.DataFrame(cached_data)

    display_rows = []
    for _, row in df.iterrows():
        display_rows.append({
            "Symbol":         row.get("symbol", "—"),
            "NSE Ticker":     row.get("canonical_symbol", "—"),
            "Qty":            _fint(row.get("quantity")),
            "Lots":           _fint(row.get("lots_held")),
            "Lot Size":       _fint(row.get("lot_size")),
            "Avg Cost (₹)":   _fp(row.get("avg_buy_price")),
            "Spot (₹)":       _fp(row.get("spot_price")),
            "Unreal P&L":     _fpnl(row.get("unrealized_pnl")),
            "52W High":       _fp(row.get("high_52w")),
            "52W Low":        _fp(row.get("low_52w")),
            "% 52W H":        _fpct(row.get("pct_52w_high")),
            "% 52W L":        _fpct(row.get("pct_52w_low")),
            "6M High":        _fp(row.get("high_6m")),
            "6M Low":         _fp(row.get("low_6m")),
            "% 6M H":         _fpct(row.get("pct_6m_high")),
            "% 6M L":         _fpct(row.get("pct_6m_low")),
            "3M High":        _fp(row.get("high_3m")),
            "3M Low":         _fp(row.get("low_3m")),
            "% 3M H":         _fpct(row.get("pct_3m_high")),
            "% 3M L":         _fpct(row.get("pct_3m_low")),
            "1M High":        _fp(row.get("high_1m")),
            "1M Low":         _fp(row.get("low_1m")),
            "% 1M H":         _fpct(row.get("pct_1m_high")),
            "% 1M L":         _fpct(row.get("pct_1m_low")),
            "Signal":         row.get("signal", "—"),
            "Expiry":         str(row.get("nearest_expiry") or "—"),
            "CE Strike":      _fp(row.get("ce_strike"), 0),
            "CE Premium (₹)": _fp(row.get("ce_premium")),
            "CE IV (%)":      _fiv(row.get("ce_iv")),
            "PE Strike":      _fp(row.get("pe_strike"), 0),
            "PE Premium (₹)": _fp(row.get("pe_premium")),
            "PE IV (%)":      _fiv(row.get("pe_iv")),
            "CE Position":    row.get("existing_ce", "—"),
            "PE Position":    row.get("existing_pe", "—"),
        })

    disp = pd.DataFrame(display_rows)

    pct_cols = [c for c in disp.columns if c.startswith("% ")]
    styled = disp.style
    if "Signal" in disp.columns:
        styled = _safe_map(styled, _style_signal, ["Signal"])
    if "Unreal P&L" in disp.columns:
        styled = _safe_map(styled, _style_pnl, ["Unreal P&L"])
    for col in pct_cols:
        styled = _safe_map(styled, _style_pct, [col])

    sell_ce_n = sum(1 for r in display_rows if r["Signal"] == "SELL CE")
    sell_pe_n = sum(1 for r in display_rows if r["Signal"] == "SELL PE")
    neutral_n = sum(1 for r in display_rows if r["Signal"] == "NEUTRAL")

    st.metric("F&O-eligible holdings", len(disp))
    m1, m2, m3 = st.columns(3)
    m1.success(f"🟢 SELL CE: {sell_ce_n} — near 52W high")
    m2.error(f"🔴 SELL PE: {sell_pe_n} — near 52W low")
    m3.info(f"⬜ NEUTRAL: {neutral_n} — mid-range")

    st.dataframe(styled, use_container_width=True, height=420)


# ─────────────────────────────────────────────────────────────────────────────
# Section B — Advanced Options Screener
# ─────────────────────────────────────────────────────────────────────────────

def _render_advanced_screener(user_id: int, get_advanced_fn):
    """
    Advanced screener with full session-state caching per user_id.
    Cached table shown instantly on tab revisit — no API call until Refresh.
    """
    st.caption(
        "Holdings + Futures exposure → Live spot → 1M/3M/52W OHLC → "
        "Position-aware signals → Nearest + far-month option Δ & Prob-OTM → Corp events. "
        "⏱ **Estimated load time: 30-40 sec on first load.**"
    )

    with st.expander("📖 Signal Guide", expanded=False):
        st.markdown("""
| Position Signal | Meaning |
|---|---|
| 🔴 **SQUARE_OFF** | Bought option deep ITM — estimated profit > 90%. Close it. |
| 🔁 **OPT_ROLLOVER** | Sold/bought option expires in < 15 days — roll to next month. |
| 🔀 **FUT_ROLLOVER** | FUT position only (no sold option) expires in < 15 days — roll FUT. |
| ⚠️ **CORRECTION** | Existing sold option is threatened — add hedge on opposite side. |
| 🆕 **FRESH** | No existing sold position — signal based on price vs OHLC levels. |
| ✅ **HOLD** | Existing sold position is healthy — no action needed. |

| FUT Qty (Shares) column | Meaning |
|---|---|
| **+550** | Long 550 shares (positive = long futures) |
| **-700** | Short 700 shares (negative = short futures) |
| **—** | No futures position |

| Final Signal | When triggered |
|---|---|
| 🟢 **SELL CE** | Spot within 5% of 1M or 3M high, or long-futures bias. |
| 🔴 **SELL PE** | Spot within 5% of 1M or 3M low. |
| ⬜ **NEUTRAL** | Mid-range price or corp event upcoming. |
""")

    # ── Session-state keys scoped to this user ────────────────────────────────
    data_key = f"adv_screener_data_{user_id}"
    time_key = f"adv_screener_ts_{user_id}"

    adv_data    = _ss_get(data_key)
    last_loaded = _ss_get(time_key)

    col_btn, col_time = st.columns([2, 3])
    with col_btn:
        run_clicked = st.button(
            "🔄 Run Advanced Screener (~30-40 sec)",
            type="primary",
            key=f"adv_screener_run_{user_id}",
            help="Fetch live data for all holdings. This may take 30-40 seconds.",
        )
    with col_time:
        if last_loaded:
            st.caption(f"⏱ Last loaded: **{last_loaded}**  |  Click to refresh")
            if _HAS_MARKET_STATUS and market_status.get_market_status()[0]:
                st.caption("🕐 Market is open — click refresh for latest signals")
        elif adv_data:
            st.caption("⏱ Loaded this session")
        else:
            st.caption("ℹ️ No data yet — click Run to start. Takes ~30-40 sec.")

    # ── Fetch only on button click ─────────────────────────────────────────────
    if run_clicked:
        with st.spinner("📡 Fetching live data for all holdings… (~30-40 sec)"):
            try:
                resp = get_advanced_fn(user_id)
                if resp.get("status") == "success":
                    _ss_set(data_key, resp["data"])
                    _ss_set(time_key, datetime.datetime.now().strftime("%H:%M:%S"))
                    adv_data = _ss_get(data_key)
                    st.success(f"✅ Loaded {resp['rows']} rows")
                else:
                    st.error(resp.get("message", "Unknown error"))
                    return
            except Exception as e:
                st.error(f"Failed: {e}")
                return

    if not adv_data:
        st.info("Click **🔄 Run Advanced Screener** to load. This fetches live data — allow 30-40 sec.")
        return

    # ── KPI bar (rendered from cached data — instant) ─────────────────────────
    sq_off       = sum(1 for r in adv_data if r.get("position_signal") == "SQUARE_OFF")
    opt_rollover = sum(1 for r in adv_data if r.get("position_signal") == "OPT_ROLLOVER")
    fut_rollover = sum(1 for r in adv_data if r.get("position_signal") == "FUT_ROLLOVER")
    rollover     = opt_rollover + fut_rollover
    correct      = sum(1 for r in adv_data if r.get("position_signal") == "CORRECTION")
    hold_n       = sum(1 for r in adv_data if r.get("position_signal") == "HOLD")
    sell_ce      = sum(1 for r in adv_data if r.get("final_signal") == "SELL CE")
    sell_pe      = sum(1 for r in adv_data if r.get("final_signal") == "SELL PE")
    corp_ev      = sum(1 for r in adv_data if r.get("corp_event_alert", "—") not in ("—", "", None))

    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
    k1.error(  f"🔴 SQ OFF\n**{sq_off}**")
    k2.warning(f"🔁 ROLLOVER\n**{rollover}** (Opt:{opt_rollover}/FUT:{fut_rollover})")
    k3.info(   f"⚠️ CORR\n**{correct}**")
    k4.success(f"✅ HOLD\n**{hold_n}**")
    k5.success(f"🟢 SELL CE\n**{sell_ce}**")
    k6.error(  f"🔴 SELL PE\n**{sell_pe}**")
    k7.warning(f"📅 Corp Ev\n**{corp_ev}**")

    st.markdown("---")
    view_mode = st.radio(
        "Table columns",
        ["Summary", "OHLC", "Option Chain (Nearest)", "Option Chain (Far)", "Futures", "All"],
        horizontal=True,
        key=f"adv_view_mode_{user_id}",
    )

    disp_rows = _build_adv_display_rows(adv_data, view_mode)
    disp      = pd.DataFrame(disp_rows)
    styled    = _apply_adv_styles(disp)

    row_height = min(35 * len(disp) + 38, 1200)
    st.dataframe(styled, use_container_width=True, height=row_height)

    # ── Actionable signal cards ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📋 Actionable Signals — Detail")
    _render_actionable_cards(adv_data)


# ─────────────────────────────────────────────────────────────────────────────
# Shared row-builder and style helper (used by both user and group renderers)
# ─────────────────────────────────────────────────────────────────────────────

def _build_adv_display_rows(adv_data: list, view_mode: str) -> list:
    disp_rows = []
    for r in adv_data:
        corp        = r.get("corp_event_alert", "—") or "—"
        fut_qty_raw = r.get("fut_qty_shares") or r.get("fut_contracts")

        row_out: dict = {
            "Symbol":           r.get("canonical_symbol") or r.get("symbol", "—"),
            "EQ Qty":           _fint(r.get("equity_qty")),
            "FUT Qty (Shares)": _ffut(fut_qty_raw),
            "Total Qty":        _fint(r.get("total_qty")),
            "Spot (₹)":         _fp(r.get("spot_price")),
            "Unreal P&L":       _fpnl(r.get("unrealized_pnl")),
            "Pos. Signal":      r.get("position_signal", "—"),
            "Final Signal":     r.get("final_signal", "—"),
        }

        if view_mode in ("Summary", "All"):
            row_out.update({
                "Reason":      r.get("signal_reason", "—"),
                "CE Position": r.get("existing_sold_ce", "—"),
                "PE Position": r.get("existing_sold_pe", "—"),
                "CE Bought":   r.get("existing_bought_ce", "—"),
                "PE Bought":   r.get("existing_bought_pe", "—"),
                "DTE (open)":  _fint(r.get("days_to_open_expiry")),
                "Corp Event":  corp,
                "Action":      r.get("suggested_action", "—"),
            })

        if view_mode in ("OHLC", "All"):
            row_out.update({
                "1M H": _fp(r.get("high_1m")), "1M L": _fp(r.get("low_1m")),
                "% 1M H": _fpct(r.get("pct_1m_high")), "% 1M L": _fpct(r.get("pct_1m_low")),
                "3M H": _fp(r.get("high_3m")), "3M L": _fp(r.get("low_3m")),
                "% 3M H": _fpct(r.get("pct_3m_high")), "% 3M L": _fpct(r.get("pct_3m_low")),
                "52W H": _fp(r.get("high_52w")), "52W L": _fp(r.get("low_52w")),
                "% 52W H": _fpct(r.get("pct_52w_high")), "% 52W L": _fpct(r.get("pct_52w_low")),
            })

        if view_mode in ("Option Chain (Nearest)", "All"):
            row_out.update({
                "N Expiry":    r.get("n_expiry", "—"),
                "N CE Strike": _fp(r.get("n_ce_strike"), 0),
                "N CE Prem":   _fp(r.get("n_ce_premium")),
                "N CE IV%":    _fiv(r.get("n_ce_iv")),
                "N CE Δ":      _fdelta(r.get("n_ce_delta")),
                "N CE P-OTM":  _fprob(r.get("n_ce_prob_otm")),
                "N PE Strike": _fp(r.get("n_pe_strike"), 0),
                "N PE Prem":   _fp(r.get("n_pe_premium")),
                "N PE IV%":    _fiv(r.get("n_pe_iv")),
                "N PE Δ":      _fdelta(r.get("n_pe_delta")),
                "N PE P-OTM":  _fprob(r.get("n_pe_prob_otm")),
            })

        if view_mode in ("Option Chain (Far)", "All"):
            row_out.update({
                "F Expiry":    r.get("f_expiry", "—"),
                "F CE Strike": _fp(r.get("f_ce_strike"), 0),
                "F CE Prem":   _fp(r.get("f_ce_premium")),
                "F CE IV%":    _fiv(r.get("f_ce_iv")),
                "F CE Δ":      _fdelta(r.get("f_ce_delta")),
                "F CE P-OTM":  _fprob(r.get("f_ce_prob_otm")),
                "F PE Strike": _fp(r.get("f_pe_strike"), 0),
                "F PE Prem":   _fp(r.get("f_pe_premium")),
                "F PE IV%":    _fiv(r.get("f_pe_iv")),
                "F PE Δ":      _fdelta(r.get("f_pe_delta")),
                "F PE P-OTM":  _fprob(r.get("f_pe_prob_otm")),
            })

        if view_mode in ("Futures", "All"):
            row_out.update({
                "FUT Entry (₹)":    _fp(r.get("fut_avg_entry")),
                "FUT Expiry":       str(r.get("fut_expiry") or "—"),
                "FUT Open Qty":     _fp(r.get("fut_open_qty"), 0),
                "FUT Qty (Shares)": _fint(r.get("fut_qty_shares")) if r.get("fut_qty_shares") else "—",
            })

        disp_rows.append(row_out)
    return disp_rows


def _apply_adv_styles(disp: pd.DataFrame):
    styled = disp.style
    for col, fn in [
        ("Pos. Signal",      _style_pos_signal),
        ("Final Signal",     _style_fin_signal),
        ("Unreal P&L",       _style_pnl),
        ("FUT Qty (Shares)", _style_fut_qty),
    ]:
        if col in disp.columns:
            styled = _safe_map(styled, fn, [col])
    for col in [c for c in disp.columns if c.startswith("% ")]:
        styled = _safe_map(styled, _style_pct, [col])
    for col in [c for c in disp.columns if "Δ" in c]:
        styled = _safe_map(styled, _style_delta, [col])
    return styled


def _render_actionable_cards(adv_data: list):
    actionable = [
        r for r in adv_data
        if r.get("position_signal") in ("SQUARE_OFF", "OPT_ROLLOVER", "FUT_ROLLOVER", "ROLLOVER", "CORRECTION")
        or r.get("final_signal") in ("SELL CE", "SELL PE")
    ]

    if not actionable:
        st.info("No actionable signals found.")
        return

    _ICONS     = {"SQUARE_OFF": "🔴", "OPT_ROLLOVER": "🔁", "FUT_ROLLOVER": "🔀",
                  "ROLLOVER": "🔁", "CORRECTION": "⚠️", "FRESH": "🆕", "HOLD": "✅"}
    _FIN_ICONS = {"SELL CE": "🟢", "SELL PE": "🔴", "NEUTRAL": "⬜"}

    for r in actionable:
        can         = r.get("canonical_symbol") or r.get("symbol", "")
        pos_sig     = r.get("position_signal", "")
        fin_sig     = r.get("final_signal", "")
        action_txt  = r.get("suggested_action", "")
        corp        = r.get("corp_event_alert", "—") or "—"
        icon        = _ICONS.get(pos_sig, "•")
        fin_icon    = _FIN_ICONS.get(fin_sig, "•")
        fut_qty_raw = r.get("fut_qty_shares") or r.get("fut_contracts")

        with st.expander(f"{icon} **{can}** — {pos_sig} → {fin_icon} {fin_sig}", expanded=False):
            col_left, col_right = st.columns([1, 2])
            with col_left:
                st.metric("Spot (₹)", _fp(r.get("spot_price")))
                st.metric("EQ Qty",   _fint(r.get("equity_qty")))
                if fut_qty_raw and fut_qty_raw != 0:
                    direction = "Long" if int(float(fut_qty_raw)) > 0 else "Short"
                    st.metric("FUT Position (shares)", f"{_ffut(fut_qty_raw)} ({direction})")
                if (r.get("days_to_open_expiry") or 9999) < 9999:
                    st.metric("DTE (open pos)", r["days_to_open_expiry"])
                if corp != "—":
                    st.warning(f"📅 Corp Event: {corp}")
            with col_right:
                st.markdown("**Suggested Action:**")
                for line in (action_txt or "").split("\n"):
                    if line.strip():
                        st.markdown(line)
                st.markdown("**Key OHLC Levels:**")
                levels = {
                    "1M High": r.get("high_1m"), "1M Low": r.get("low_1m"),
                    "3M High": r.get("high_3m"), "3M Low": r.get("low_3m"),
                    "52W High": r.get("high_52w"), "52W Low": r.get("low_52w"),
                }
                lv_cols = st.columns(3)
                for i, (k, v) in enumerate(levels.items()):
                    lv_cols[i % 3].metric(k, f"₹{float(v):,.0f}" if v else "—")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point — single user
# ─────────────────────────────────────────────────────────────────────────────

def render_ce_pe_tab(user_id: int, get_basic_fn, get_advanced_fn):
    """
    Render the full CE/PE Screener tab (single-user mode).

    Parameters
    ----------
    user_id         : int
    get_basic_fn    : callable(user_id) → dict
    get_advanced_fn : callable(user_id) → dict
    """
    with st.expander("📊 Section A — Basic Covered Call / CSP Screener", expanded=True):
        _render_basic_screener(user_id, get_basic_fn)

    st.divider()
    st.subheader("🧠 Section B — Advanced Options Screener")
    _render_advanced_screener(user_id, get_advanced_fn)


# ─────────────────────────────────────────────────────────────────────────────
# Group Advanced Screener
# ─────────────────────────────────────────────────────────────────────────────

def _render_group_advanced_screener(group_id: int, get_group_advanced_fn):
    """
    Group advanced screener — session-state cached by group_id.
    Instant on re-visit; fetches only when Run button is clicked.
    """
    st.caption(
        "All F&O-eligible underlyings held or traded across the group — "
        "signals based on 1M/3M price levels. "
        "⏱ **Estimated load time: 30-50 sec on first load.**"
    )

    with st.expander("📖 Column Guide", expanded=False):
        st.markdown("""
| Column | Meaning |
|---|---|
| **Total EQ Qty** | Sum of equity shares held by all members |
| **FUT Shares** | Group net FUT position in shares (+long / -short) |
| **Lots** | `total_qty ÷ lot_size` (rounded down) |
| **Pending Qty** | Shares needed to complete the next full lot |
| **{name} EQ** | Equity shares held by that member |
| **{name} FUT** | FUT shares for that member (+/-); blank if none |
| **{name} Sold CE** | Sold call positions or `—` |
| **{name} Sold PE** | Sold put positions |
| **Conflict Alert** | ⚠️ Distributed straddle / 📋 Covered call across accounts |
""")

    # ── Session-state keys scoped to this group ───────────────────────────────
    data_key = f"grp_adv_data_{group_id}"
    time_key = f"grp_adv_ts_{group_id}"

    adv_data    = _ss_get(data_key)
    last_loaded = _ss_get(time_key)

    col_btn, col_time = st.columns([2, 3])
    with col_btn:
        run_clicked = st.button(
            "🔄 Run Group Advanced Screener (~30-50 sec)",
            type="primary",
            key=f"grp_adv_run_{group_id}",
        )
    with col_time:
        if last_loaded:
            st.caption(f"⏱ Last loaded: **{last_loaded}**  |  Click to refresh")
        elif adv_data:
            st.caption("⏱ Loaded this session")
        else:
            st.caption("ℹ️ No data yet — click Run to start.")

    if run_clicked:
        with st.spinner("🔍 Fetching live data for all group underlyings… (~30-50 sec)"):
            try:
                resp = get_group_advanced_fn(group_id)
                if resp.get("status") == "success":
                    _ss_set(data_key, resp["data"])
                    _ss_set(time_key, datetime.datetime.now().strftime("%H:%M:%S"))
                    adv_data = _ss_get(data_key)
                    st.success(f"✅ Loaded {resp['rows']} underlyings")
                else:
                    st.error(resp.get("message", "Unknown error"))
                    return
            except Exception as e:
                st.error(f"Failed: {e}")
                return

    if not adv_data:
        st.info("Click **🔄 Run Group Advanced Screener** to load.")
        return

    # ── KPI bar ───────────────────────────────────────────────────────────────
    sell_ce   = sum(1 for r in adv_data if r.get("final_signal") == "SELL CE")
    sell_pe   = sum(1 for r in adv_data if r.get("final_signal") == "SELL PE")
    neutral   = sum(1 for r in adv_data if r.get("final_signal") == "NEUTRAL")
    conflicts = sum(1 for r in adv_data if r.get("conflict_alert", "—") not in ("—", "", None))
    corp_ev   = sum(1 for r in adv_data if r.get("corp_event_alert", "—") not in ("—", "", None))

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.success(f"🟢 SELL CE\n**{sell_ce}**")
    k2.error(  f"🔴 SELL PE\n**{sell_pe}**")
    k3.info(   f"⬜ NEUTRAL\n**{neutral}**")
    k4.warning(f"⚠️ Conflicts\n**{conflicts}**")
    k5.warning(f"📅 Corp Events\n**{corp_ev}**")

    # ── Detect per-member column names ────────────────────────────────────────
    sample = adv_data[0] if adv_data else {}
    member_labels = [
        k[: -len("_eq_qty")]
        for k in sample.keys()
        if k.endswith("_eq_qty") and not k.startswith("total")
    ]

    st.markdown("---")
    view_mode = st.radio(
        "Table columns",
        ["Summary", "OHLC", "Option Chain (Nearest)", "Option Chain (Far)", "All"],
        horizontal=True,
        key=f"grp_adv_view_{group_id}",
    )

    # ── Build display rows ────────────────────────────────────────────────────
    disp_rows = []
    for r in adv_data:
        conflict = r.get("conflict_alert", "—") or "—"
        corp     = r.get("corp_event_alert", "—") or "—"

        row_out: dict = {
            "Symbol":      r.get("canonical_symbol") or r.get("symbol", "—"),
            "Spot (₹)":    _fp(r.get("spot_price")),
            "Total EQ":    _fint(r.get("total_eq_qty")),
            "FUT Shares":  _ffut(r.get("total_fut_shares")),
            "Lots":        _fint(r.get("lots")),
            "Lot Size":    _fint(r.get("lot_size")),
            "Pending Qty": _fint(r.get("pending_qty")),
            "Signal":      r.get("final_signal", "—"),
        }

        if view_mode in ("Summary", "All"):
            for lbl in member_labels:
                row_out[f"{lbl} EQ"]        = _fint(r.get(f"{lbl}_eq_qty"))
                row_out[f"{lbl} FUT"]       = _ffut(r.get(f"{lbl}_fut_qty"))
                row_out[f"{lbl} Sold CE"]   = r.get(f"{lbl}_sold_ce",   "—") or "—"
                row_out[f"{lbl} Sold PE"]   = r.get(f"{lbl}_sold_pe",   "—") or "—"
                row_out[f"{lbl} Bought CE"] = r.get(f"{lbl}_bought_ce", "—") or "—"
                row_out[f"{lbl} Bought PE"] = r.get(f"{lbl}_bought_pe", "—") or "—"
            row_out["Lot Dist"]   = r.get("lot_distribution", "—")
            row_out["Conflict"]   = conflict
            row_out["Corp Event"] = corp
            row_out["Reason"]     = r.get("signal_reason", "—")

        if view_mode in ("OHLC", "All"):
            row_out.update({
                "1M H": _fp(r.get("high_1m")), "1M L": _fp(r.get("low_1m")),
                "% 1M H": _fpct(r.get("pct_1m_high")), "% 1M L": _fpct(r.get("pct_1m_low")),
                "3M H": _fp(r.get("high_3m")), "3M L": _fp(r.get("low_3m")),
                "% 3M H": _fpct(r.get("pct_3m_high")), "% 3M L": _fpct(r.get("pct_3m_low")),
                "52W H": _fp(r.get("high_52w")), "52W L": _fp(r.get("low_52w")),
                "% 52W H": _fpct(r.get("pct_52w_high")), "% 52W L": _fpct(r.get("pct_52w_low")),
            })

        if view_mode in ("Option Chain (Nearest)", "All"):
            row_out.update({
                "N Expiry":    r.get("n_expiry", "—"),
                "N CE Strike": _fp(r.get("n_ce_strike"), 0),
                "N CE Prem":   _fp(r.get("n_ce_premium")),
                "N CE IV%":    _fiv(r.get("n_ce_iv")),
                "N CE Δ":      _fdelta(r.get("n_ce_delta")),
                "N CE P-OTM":  _fprob(r.get("n_ce_prob_otm")),
                "N PE Strike": _fp(r.get("n_pe_strike"), 0),
                "N PE Prem":   _fp(r.get("n_pe_premium")),
                "N PE IV%":    _fiv(r.get("n_pe_iv")),
                "N PE Δ":      _fdelta(r.get("n_pe_delta")),
                "N PE P-OTM":  _fprob(r.get("n_pe_prob_otm")),
            })

        if view_mode in ("Option Chain (Far)", "All"):
            row_out.update({
                "F Expiry":    r.get("f_expiry", "—"),
                "F CE Strike": _fp(r.get("f_ce_strike"), 0),
                "F CE Prem":   _fp(r.get("f_ce_premium")),
                "F CE IV%":    _fiv(r.get("f_ce_iv")),
                "F CE Δ":      _fdelta(r.get("f_ce_delta")),
                "F CE P-OTM":  _fprob(r.get("f_ce_prob_otm")),
                "F PE Strike": _fp(r.get("f_pe_strike"), 0),
                "F PE Prem":   _fp(r.get("f_pe_premium")),
                "F PE IV%":    _fiv(r.get("f_pe_iv")),
                "F PE Δ":      _fdelta(r.get("f_pe_delta")),
                "F PE P-OTM":  _fprob(r.get("f_pe_prob_otm")),
            })

        disp_rows.append(row_out)

    disp = pd.DataFrame(disp_rows)

    # ── Style ─────────────────────────────────────────────────────────────────
    styled = disp.style
    if "Signal" in disp.columns:
        styled = _safe_map(styled, _style_signal, ["Signal"])
    if "FUT Shares" in disp.columns:
        styled = _safe_map(styled, _style_fut_qty, ["FUT Shares"])
    for fc in [c for c in disp.columns if c.endswith(" FUT")]:
        styled = _safe_map(styled, _style_fut_qty, [fc])
    for col in [c for c in disp.columns if c.startswith("% ")]:
        styled = _safe_map(styled, _style_pct, [col])
    for col in [c for c in disp.columns if "Δ" in c]:
        styled = _safe_map(styled, _style_delta, [col])

    st.metric("Underlyings shown", len(disp))
    st.dataframe(styled, use_container_width=True, height=460)

    # ── Actionable signal cards ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📋 Actionable Signals — Per-Stock Detail")

    actionable = [
        r for r in adv_data
        if r.get("final_signal") in ("SELL CE", "SELL PE")
        or r.get("conflict_alert", "—") not in ("—", "", None)
    ]

    if not actionable:
        st.info("No actionable signals. All positions are neutral or mid-range.")
        return

    _FIN_ICONS = {"SELL CE": "🟢", "SELL PE": "🔴", "NEUTRAL": "⬜"}

    for r in actionable:
        can      = r.get("canonical_symbol") or r.get("symbol", "")
        fin_sig  = r.get("final_signal", "NEUTRAL")
        fin_icon = _FIN_ICONS.get(fin_sig, "•")
        conflict = r.get("conflict_alert", "—") or "—"
        corp_evt = r.get("corp_event_alert", "—") or "—"
        suffix   = f"  |  {conflict}" if conflict not in ("—", "") else ""

        with st.expander(f"{fin_icon} **{can}** → {fin_sig}{suffix}", expanded=False):
            gc1, gc2, gc3, gc4 = st.columns(4)
            gc1.metric("Spot (₹)",  _fp(r.get("spot_price")))
            gc2.metric("Total Qty", _fint(r.get("total_qty")))
            gc3.metric("Lots",      _fint(r.get("lots")))
            gc4.metric("Pending",   _fint(r.get("pending_qty")))

            if corp_evt not in ("—", ""):
                st.warning(f"📅 Corp Event: {corp_evt}")
            if conflict not in ("—", ""):
                st.error(f"Conflict: {conflict}")

            st.markdown("**Per-member position breakdown:**")
            member_rows = []
            for lbl in member_labels:
                eq   = r.get(f"{lbl}_eq_qty",    0) or 0
                fut  = r.get(f"{lbl}_fut_qty",   None)
                s_ce = r.get(f"{lbl}_sold_ce",   "—") or "—"
                s_pe = r.get(f"{lbl}_sold_pe",   "—") or "—"
                b_ce = r.get(f"{lbl}_bought_ce", "—") or "—"
                b_pe = r.get(f"{lbl}_bought_pe", "—") or "—"
                if not any([eq, fut, s_ce != "—", s_pe != "—", b_ce != "—", b_pe != "—"]):
                    continue
                member_rows.append({
                    "Member":    lbl,
                    "EQ Qty":    int(eq),
                    "FUT Qty":   _ffut(fut),
                    "Sold CE":   s_ce,
                    "Sold PE":   s_pe,
                    "Bought CE": b_ce,
                    "Bought PE": b_pe,
                })
            if member_rows:
                st.dataframe(pd.DataFrame(member_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No per-member data available.")

            n_exp   = r.get("n_expiry")
            lot     = int(r.get("lot_size") or 1)
            total_q = int(r.get("total_qty") or 0)
            lots_n  = int(r.get("lots") or 0)

            if n_exp and n_exp != "—":
                st.markdown(
                    f"**Nearest expiry: {n_exp}** | Lot: {lot:,} | "
                    f"Total qty: {total_q:,} = {lots_n} lot(s)"
                )
                oc1, oc2 = st.columns(2)
                oc1.markdown(
                    f"🟢 **CE** Strike: {_fp(r.get('n_ce_strike'), 0)}  "
                    f"Premium: ₹{_fp(r.get('n_ce_premium'))}  IV: {_fiv(r.get('n_ce_iv'))}"
                )
                oc2.markdown(
                    f"🔴 **PE** Strike: {_fp(r.get('n_pe_strike'), 0)}  "
                    f"Premium: ₹{_fp(r.get('n_pe_premium'))}  IV: {_fiv(r.get('n_pe_iv'))}"
                )

            if lot > 1 and total_q > 0:
                st.markdown(f"**Lot coverage:**  {r.get('lot_distribution', '—')}")
                if int(r.get("pending_qty") or 0) > 0:
                    st.caption(f"ℹ️ {r['pending_qty']} more shares needed to fill the next lot.")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point — group
# ─────────────────────────────────────────────────────────────────────────────

def render_group_ce_pe_tab(group_id: int, get_group_advanced_fn):
    """
    Render the Group CE/PE Screener tab (Section B only).

    Parameters
    ----------
    group_id              : int
    get_group_advanced_fn : callable(group_id) → dict
    """
    st.subheader("🧠 Group Advanced Options Screener")
    st.caption(
        "Aggregated view across all group members. "
        "Shows equity holdings, FUT positions, and existing option positions "
        "per member alongside group-level signals and option chain data."
    )
    _render_group_advanced_screener(group_id, get_group_advanced_fn)