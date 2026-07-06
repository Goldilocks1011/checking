"""
tax_harvest_ui.py  — v2 (fast, session-state cached, instant tab switching)
============================================================================
Key changes vs v1:
  - harvest result cached in session_state per user/group — instant on re-visit
  - price map cached in session_state per user/group — no re-fetch on tab switch
  - Fetch button shows spinner only on first run or explicit re-run
  - render_harvest_ui() now accepts an optional cache_key so caller can scope it
  - All sort controls and filters preserved; data persists across tab switches
  - No st.rerun() inside the renderer (avoids infinite rerun loops)
"""
from __future__ import annotations

import streamlit as st
import pandas as pd


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
# Price-fetch helper
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_with_prices(rows: list[dict], price_map: dict) -> list[dict]:
    """Fill CMP + Unrealized P&L into each row using a {symbol: ltp} price_map."""
    for r in rows:
        stock = r.get("Stock", "")
        cmp   = price_map.get(stock) or price_map.get(stock.upper())
        if cmp and cmp > 0:
            r["CMP (₹)"] = round(cmp, 2)
            qty      = float(r.get("Qty", 0) or 0)
            avg_sell = float(r.get("Avg Sell (₹)", 0) or 0)
            avg_buy  = float(r.get("Avg Buy (₹)",  0) or 0)
            ref_price = avg_sell if avg_sell else avg_buy
            if ref_price and qty:
                r["Unreal. P&L (₹)"] = round((cmp - ref_price) * qty, 2)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Colour helpers
# ─────────────────────────────────────────────────────────────────────────────

def _colour_pnl(val):
    try:
        v = float(val)
        return "color: #2ecc71" if v >= 0 else "color: #e74c3c"
    except Exception:
        return ""


def _apply_colour(styler, col: str):
    """Green/red colouring — compatible with pandas < 2.1 and >= 2.1."""
    try:
        return styler.map(_colour_pnl, subset=[col])
    except AttributeError:
        return styler.applymap(_colour_pnl, subset=[col])


# ─────────────────────────────────────────────────────────────────────────────
# Main render function
# ─────────────────────────────────────────────────────────────────────────────

def render_harvest_ui(
    result: dict,
    fetch_prices_fn=None,
    label: str = "",
    cache_key: str = "_harvest",
):
    """
    Render the full tax harvest output with session-state caching.

    Parameters
    ----------
    result          : dict returned by run_harvest_multi / run_harvest_analysis.
                      The caller (app.py) is responsible for storing this in
                      session_state before calling this function.
    fetch_prices_fn : callable(symbols) → {symbol: ltp}
    label           : optional header label
    cache_key       : unique prefix for session_state keys (e.g. "su_harvest"
                      for single-user, "grp_harvest_42" for group 42).
                      Keeps price maps separate across users/groups.
    """

    if not result:
        st.info("No results yet.")
        return

    if "error" in result:
        st.error(result["error"])
        return

    matched       = result.get("matched", [])
    outstanding   = result.get("outstanding", [])
    unmatched_buy = result.get("unmatched_buy", [])
    summary       = result.get("summary", {})

    # ── Price map — cached in session_state, not re-fetched on tab switch ─────
    price_map_key = f"{cache_key}_price_map"
    price_map: dict = _ss_get(price_map_key, {})
    all_stocks = list({r["Stock"] for r in outstanding + unmatched_buy if r.get("Stock")})

    col_fetch, col_clear, _ = st.columns([2, 1, 5])
    with col_fetch:
        if fetch_prices_fn and all_stocks:
            if st.button(
                "📡 Fetch Live Prices (5paisa)",
                key=f"{cache_key}_fetch_prices",
            ):
                with st.spinner("Fetching prices…"):
                    try:
                        price_map = fetch_prices_fn(all_stocks)
                        _ss_set(price_map_key, price_map)
                        st.success(f"Prices fetched for {len(price_map)} stocks")
                    except Exception as e:
                        st.error(f"Price fetch failed: {e}")

    with col_clear:
        if price_map:
            if st.button("🗑️ Clear Prices", key=f"{cache_key}_clear_prices"):
                _ss_clear(price_map_key)
                price_map = {}
                st.rerun()

    # ── Show last price-fetch status ──────────────────────────────────────────
    if price_map:
        st.caption(f"✅ Live prices loaded for **{len(price_map)}** stocks. Switch tabs freely — prices are cached.")

    # ── Enrich rows with cached prices (instant — no API call) ────────────────
    if price_map:
        outstanding   = _enrich_with_prices([dict(r) for r in outstanding],   price_map)
        unmatched_buy = _enrich_with_prices([dict(r) for r in unmatched_buy], price_map)

    # ── Alert banner ──────────────────────────────────────────────────────────
    n_out = len(outstanding)
    if n_out > 0:
        st.error(f"🚨 {n_out} sell(s) need a rebuy in another account ASAP!", icon="🚨")

    st.success("Analysis complete" + (f" — {label}" if label else ""))

    # ─────────────────────────────────────────────────────────────────────────
    # ✅  MATCHED HARVEST PAIRS
    # ─────────────────────────────────────────────────────────────────────────
    if matched:
        st.subheader("✅ Matched Harvest Pairs")
        df_m = pd.DataFrame(matched).drop(columns=["Corp Actions"], errors="ignore")
        if "Harvest P&L (₹)" in df_m.columns:
            st.dataframe(_apply_colour(df_m.style, "Harvest P&L (₹)"), use_container_width=True)
        else:
            st.dataframe(df_m, use_container_width=True)
    else:
        st.info("No matched harvest pairs found in this date range.")

    # ─────────────────────────────────────────────────────────────────────────
    # 🚨  OUTSTANDING SELLS
    # ─────────────────────────────────────────────────────────────────────────
    if outstanding:
        st.markdown("---")
        st.subheader("🚨 Outstanding Sells")

        total_sv   = summary.get("total_sell_value", 0)
        total_qty  = summary.get("total_sell_qty",   0)
        net_unreal = sum(float(r.get("Unreal. P&L (₹)") or 0) for r in outstanding) or None

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Outstanding Sells", n_out)
        c2.metric("Total Sell Value",  f"₹{total_sv:,.2f}")
        c3.metric("Total Qty",         f"{int(total_qty):,}")
        c4.metric(
            "Net Unrealized P&L",
            f"₹{net_unreal:,.2f}" if net_unreal is not None else "—",
        )

        # Sort controls — keys scoped to cache_key so multiple instances don't clash
        sc1, sc2, sc3 = st.columns([3, 2, 2])
        sort_col_out = sc1.selectbox(
            "Sort outstanding by",
            ["Stock", "Qty", "Realized P&L (₹)", "Avg Sell (₹)", "Sell Dates"],
            key=f"{cache_key}_sort_out_col",
        )
        sort_asc_out = sc3.radio(
            "Order",
            ["Descending", "Ascending"],
            horizontal=True,
            index=0,
            key=f"{cache_key}_sort_out_asc",
        )
        asc_flag = (sort_asc_out == "Ascending")

        df_out = pd.DataFrame(outstanding)
        if sort_col_out in df_out.columns:
            df_out = df_out.sort_values(sort_col_out, ascending=asc_flag)

        preferred_cols = [
            "Stock", "Qty", "Sell Broker", "Sell Dates",
            "Avg Sell (₹)", "Orig Avg Cost (₹)", "Realized P&L (₹)",
            "CMP (₹)", "Unreal. P&L (₹)",
            "FUT Avg Entry (₹)", "FUT Expiry", "FUT Total Qty", "FUT Account",
            "Status", "Action",
        ]
        display_cols = [c for c in preferred_cols if c in df_out.columns]
        df_out_disp  = df_out[display_cols].reset_index(drop=True)

        if "Realized P&L (₹)" in df_out_disp.columns:
            st.dataframe(
                _apply_colour(df_out_disp.style, "Realized P&L (₹)"),
                use_container_width=True,
            )
        else:
            st.dataframe(df_out_disp, use_container_width=True)

    # ─────────────────────────────────────────────────────────────────────────
    # 📦  UNMATCHED BUYS
    # ─────────────────────────────────────────────────────────────────────────
    if unmatched_buy:
        st.markdown("---")
        st.subheader("📦 Unmatched Buys")
        st.caption(
            "Buys that did not match any cross-account sell. "
            "The **Note** column explains the likely reason."
        )

        with st.expander("📖 Column Guide — Unmatched Buys"):
            st.markdown("""
| Column | Meaning |
|---|---|
| **Stock** | Canonical NSE ticker |
| **Qty** | Unmatched quantity remaining after cross-account matching |
| **Buy Broker** | Account that placed the buy |
| **Date** | Date range of buy transaction(s) |
| **Avg Buy (₹)** | Weighted average buy price |
| **Buy Value (₹)** | Total cost = Avg Buy × Qty |
| **CMP (₹)** | Current market price (click Fetch button above) |
| **Unreal. P&L (₹)** | (CMP − Avg Buy) × Qty |
| **Note** | Auto-detected reason: F&O loss coverage / Month-end / Additional / Lot completion |
""")

        n_un    = len(unmatched_buy)
        tot_bv  = summary.get("total_buy_value", 0)
        avg_bv  = tot_bv / n_un if n_un else 0
        net_unreal_buy = sum(
            float(r.get("Unreal. P&L (₹)") or 0) for r in unmatched_buy
        ) or None

        uc1, uc2, uc3, uc4 = st.columns(4)
        uc1.metric("Unmatched Buys",      n_un)
        uc2.metric("Total Buy Value",     f"₹{tot_bv:,.2f}")
        uc3.metric("Net Unrealized P&L",
                   f"₹{net_unreal_buy:,.2f}" if net_unreal_buy is not None else "—")
        uc4.metric("Avg Buy Value/Stock", f"₹{avg_bv:,.2f}")

        bc1, bc2, bc3 = st.columns([3, 2, 2])
        sort_col_un = bc1.selectbox(
            "Sort unmatched by",
            ["Stock", "Qty", "Buy Value (₹)", "Avg Buy (₹)", "Date"],
            key=f"{cache_key}_sort_un_col",
        )
        sort_asc_un = bc3.radio(
            "Order ",
            ["Descending", "Ascending"],
            horizontal=True,
            index=0,
            key=f"{cache_key}_sort_un_asc",
        )
        asc_un = (sort_asc_un == "Ascending")

        df_un = pd.DataFrame(unmatched_buy)
        if sort_col_un in df_un.columns:
            df_un = df_un.sort_values(sort_col_un, ascending=asc_un)

        preferred_un = [
            "Stock", "Qty", "Buy Broker", "Date",
            "Avg Buy (₹)", "Buy Value (₹)",
            "CMP (₹)", "Unreal. P&L (₹)", "Realized P&L (₹)",
            "Note",
        ]
        display_un   = [c for c in preferred_un if c in df_un.columns]
        st.dataframe(df_un[display_un].reset_index(drop=True), use_container_width=True)

    # ── JSON summary ──────────────────────────────────────────────────────────
    with st.expander("📊 Summary JSON"):
        st.json(summary)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience wrapper — single-user harvest tab
# ─────────────────────────────────────────────────────────────────────────────

def render_single_user_harvest_tab(
    user_id: int,
    run_harvest_fn,
    fetch_prices_fn=None,
):
    """
    Full tax harvest tab for a single user, with session-state caching.

    Parameters
    ----------
    user_id         : int
    run_harvest_fn  : callable(user_id, start_str, end_str) → dict
    fetch_prices_fn : callable(symbols) → {symbol: ltp}
    """
    from datetime import date

    st.subheader("🌾 Tax Harvesting Analysis")

    col1, col2 = st.columns(2)
    start_date = col1.date_input(
        "From", value=date.today().replace(month=3, day=1),
        key=f"su_th_start_{user_id}",
    )
    end_date = col2.date_input(
        "To", value=date.today().replace(month=3, day=31),
        key=f"su_th_end_{user_id}",
    )

    result_key   = f"su_th_result_{user_id}"
    cache_key    = f"su_harvest_{user_id}"
    cached_result = _ss_get(result_key)

    # Auto-load on first visit (so user sees last result immediately)
    if cached_result is None:
        with st.spinner("Analysing…"):
            try:
                _ss_set(result_key, run_harvest_fn(user_id, str(start_date), str(end_date)))
            except Exception as e:
                _ss_set(result_key, {"error": str(e)})

    col_run, col_clear = st.columns([2, 1])
    with col_run:
        if col_run.button("▶ Run Harvest Analysis", type="primary", key=f"su_th_run_{user_id}"):
            with st.spinner("Analysing…"):
                try:
                    _ss_set(result_key, run_harvest_fn(user_id, str(start_date), str(end_date)))
                    # clear price map when re-running so stale prices don't linger
                    _ss_clear(f"{cache_key}_price_map")
                except Exception as e:
                    _ss_set(result_key, {"error": str(e)})

    render_harvest_ui(
        result=_ss_get(result_key),
        fetch_prices_fn=fetch_prices_fn,
        cache_key=cache_key,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience wrapper — group harvest tab
# ─────────────────────────────────────────────────────────────────────────────

def render_group_harvest_tab(
    group_id: int,
    member_ids: list[int],
    run_harvest_multi_fn,
    fetch_prices_fn=None,
):
    """
    Full tax harvest tab for a group, with session-state caching.

    Parameters
    ----------
    group_id              : int
    member_ids            : list[int]
    run_harvest_multi_fn  : callable(user_ids, start_str, end_str) → dict
    fetch_prices_fn       : callable(symbols) → {symbol: ltp}
    """
    from datetime import date

    st.subheader("🌾 Tax Harvesting (Cross-Account)")

    col1, col2 = st.columns(2)
    start_date = col1.date_input(
        "From", value=date.today().replace(month=3, day=1),
        key=f"grp_th_start_{group_id}",
    )
    end_date = col2.date_input(
        "To", value=date.today().replace(month=3, day=31),
        key=f"grp_th_end_{group_id}",
    )

    result_key    = f"grp_th_result_{group_id}"
    cache_key     = f"grp_harvest_{group_id}"
    cached_result = _ss_get(result_key)

    # Auto-load on first visit
    if cached_result is None and member_ids:
        with st.spinner("Analysing…"):
            try:
                _ss_set(result_key, run_harvest_multi_fn(member_ids, str(start_date), str(end_date)))
            except Exception as e:
                _ss_set(result_key, {"error": str(e)})

    if st.button("▶ Run Harvest Analysis", type="primary", key=f"grp_th_run_{group_id}"):
        with st.spinner("Analysing…"):
            try:
                _ss_set(result_key, run_harvest_multi_fn(member_ids, str(start_date), str(end_date)))
                _ss_clear(f"{cache_key}_price_map")
            except Exception as e:
                _ss_set(result_key, {"error": str(e)})

    render_harvest_ui(
        result=_ss_get(result_key),
        fetch_prices_fn=fetch_prices_fn,
        cache_key=cache_key,
    )