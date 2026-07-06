import numpy as np
import pandas as pd
import plotly.graph_objects as go
from datetime import date
import streamlit as st

def _payoff_single(leg: dict, spot: float) -> float:
    """Intrinsic payoff for ONE leg (not including premium paid/received)."""
    qty    = float(leg["qty"])
    price  = float(leg["price"])
    strike = float(leg.get("strike", 0) or 0)
    itype  = str(leg["instrument"]).upper()

    if itype == "CE":
        return qty * max(0.0, spot - strike)
    elif itype == "PE":
        return qty * max(0.0, strike - spot)
    elif itype == "FUT":
        return (spot - price) * qty
    return 0.0


def _net_premium(legs: list[dict]) -> float:
    """
    Net premium for the combined position.
    Positive = net credit (seller collects more than buyer pays).
    """
    total = 0.0
    for leg in legs:
        itype = str(leg["instrument"]).upper()
        if itype in ("CE", "PE"):
            total -= leg["qty"] * leg["price"]   # short qty is negative → adds credit
    return total


def total_pl(legs: list[dict], spot: float) -> float:
    """Total P&L at expiry for the combined position."""
    return sum(_payoff_single(leg, spot) for leg in legs) + _net_premium(legs)


def _per_leg_pl(leg: dict, spot: float) -> float:
    """P&L for a single leg (payoff + its own premium component)."""
    itype = str(leg["instrument"]).upper()
    qty   = float(leg["qty"])
    price = float(leg["price"])
    if itype in ("CE", "PE"):
        return _payoff_single(leg, spot) + (-qty * price)
    return _payoff_single(leg, spot)


def find_breakevens(legs: list[dict], spot_min: float, spot_max: float,
                    n: int = 4000) -> tuple[list[float], np.ndarray, np.ndarray]:
    """Find sign-change breakeven points via dense scan + linear interpolation."""
    spots   = np.linspace(spot_min, spot_max, n)
    pl_vals = np.array([total_pl(legs, s) for s in spots])

    bes: list[float] = []
    for i in range(len(spots) - 1):
        y0, y1 = pl_vals[i], pl_vals[i + 1]
        if y0 * y1 <= 0 and y0 != y1:
            x0, x1 = spots[i], spots[i + 1]
            be = x0 - y0 * (x1 - x0) / (y1 - y0)
            bes.append(round(be, 2))

    # Deduplicate (within ₹2) and remove negative values
    deduped: list[float] = []
    for b in bes:
        if b < 0:
            continue
        if not deduped or abs(b - deduped[-1]) > 2:
            deduped.append(b)
    return deduped, spots, pl_vals


def _spot_range(legs: list[dict], cmp: float = 0) -> tuple[float, float]:
    """Return (min_spot, max_spot) wide enough to show all breakevens clearly."""
    refs = [cmp] if cmp > 0 else []
    for leg in legs:
        s = leg.get("strike", 0) or 0
        if s > 0:
            refs.append(float(s))
        if str(leg["instrument"]).upper() == "FUT":
            refs.append(float(leg["price"]))

    if not refs:
        refs = [1000.0]

    lo, hi = min(refs), max(refs)
    span = max(hi - lo, lo * 0.15, 200.0)

    margin = 1.5
    low  = max(0.0, lo - span * margin)
    high = hi + span * margin

    # Iteratively extend until BEs are not at the boundary
    for _ in range(10):
        bes, _, _ = find_breakevens(legs, low, high, n=3000)
        if not bes:
            break
        extended = False
        if min(bes) <= low + 0.01 * (high - low):
            low = max(0.0, low - span * 0.5)
            extended = True
        if max(bes) >= high - 0.01 * (high - low):
            high = high + span * 0.5
            extended = True
        if not extended:
            break

    low = max(0.0, low)
    return low, high


def _max_profit_range(legs: list[dict], spots: np.ndarray,
                      pl_vals: np.ndarray) -> tuple[float, float, float]:
    """
    Return (max_pl, range_low, range_high) where P&L ≥ 95% of max_pl.
    For unlimited-profit strategies, range_high = spot_max.
    """
    max_pl = float(pl_vals.max())
    if max_pl <= 0:
        return max_pl, float(spots[0]), float(spots[-1])
    threshold = max_pl * 0.95
    mask = pl_vals >= threshold
    idxs = np.where(mask)[0]
    if len(idxs) == 0:
        return max_pl, float(spots[0]), float(spots[-1])
    return max_pl, float(spots[idxs[0]]), float(spots[idxs[-1]])


def _detect_strategy(legs: list[dict]) -> str:
    """Heuristic strategy name from leg composition."""
    ces    = [l for l in legs if l["instrument"] == "CE"]
    pes    = [l for l in legs if l["instrument"] == "PE"]
    futs   = [l for l in legs if l["instrument"] == "FUT"]
    s_ce   = [l for l in ces  if l["qty"] < 0]
    l_ce   = [l for l in ces  if l["qty"] > 0]
    s_pe   = [l for l in pes  if l["qty"] < 0]
    l_pe   = [l for l in pes  if l["qty"] > 0]

    if futs and s_ce and not s_pe:
        return "Covered Call"
    if s_ce and s_pe and l_ce and l_pe:
        return "Iron Condor"
    if s_ce and s_pe and not l_ce and not l_pe:
        return "Short Strangle"
    if s_ce and s_pe and len(s_ce) == 1 and len(s_pe) == 1 and \
            abs(s_ce[0]["strike"] - s_pe[0]["strike"]) < 50:
        return "Short Straddle"
    if len(s_ce) == 1 and len(l_ce) == 1 and not pes and not futs:
        return "Bear Call Spread" if s_ce[0]["strike"] < l_ce[0]["strike"] else "Bull Call Spread"
    if len(s_pe) == 1 and len(l_pe) == 1 and not ces and not futs:
        return "Bull Put Spread" if s_pe[0]["strike"] > l_pe[0]["strike"] else "Bear Put Spread"
    if s_ce and not s_pe and not l_ce and not l_pe and not futs:
        return "Naked CE Sell"
    if s_pe and not s_ce and not l_ce and not l_pe and not futs:
        return "Naked PE Sell"
    if futs and not ces and not pes:
        return "Long Futures" if futs[0]["qty"] > 0 else "Short Futures"
    return "Custom Strategy"


# ─────────────────────────────────────────────────────────────────────────────
# DATA EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_legs_from_op_df(op_df: pd.DataFrame) -> dict[str, list[dict]]:
    """Convert open positions DataFrame → {underlying: [leg_dict, ...]}."""
    stock_legs: dict[str, list[dict]] = {}

    for _, row in op_df.iterrows():
        und      = str(row.get("underlying", row.get("symbol", "UNKNOWN")))
        itype    = str(row.get("instrument_type", "")).upper()
        open_qty = float(row.get("open_qty", 0) or 0)
        avg_px   = float(row.get("avg_price", 0) or 0)
        strike   = float(row.get("strike_price", 0) or 0)
        expiry   = str(row.get("expiry_date", ""))[:7]

        if itype not in ("CE", "PE", "FUT") or abs(open_qty) < 0.01:
            continue

        leg = {
            "instrument": itype,
            "strike":     strike,
            "qty":        open_qty,
            "price":      avg_px,
            "expiry":     expiry,
            "label": (
                f"{itype} {int(strike)} ({expiry})"
                if itype in ("CE", "PE") else f"FUT ({expiry})"
            ),
        }
        stock_legs.setdefault(und, []).append(leg)

    return stock_legs


# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY CHART
# ─────────────────────────────────────────────────────────────────────────────

_LEG_COLORS = [
    "#3b82f6", "#f59e0b", "#8b5cf6", "#ec4899",
    "#14b8a6", "#f97316", "#06b6d4", "#84cc16",
]


def build_be_figure(
    legs:       list[dict],
    underlying: str,
    cmp:        float = 0.0,
) -> go.Figure:
    """Build the combined P&L Plotly figure."""

    spot_min, spot_max = _spot_range(legs, cmp)
    bes, spots, pl_vals = find_breakevens(legs, spot_min, spot_max)
    net_prem  = _net_premium(legs)
    max_pl, mp_low, mp_high = _max_profit_range(legs, spots, pl_vals)
    min_pl    = float(pl_vals.min())
    strategy  = _detect_strategy(legs)

    fig = go.Figure()

    # ── Individual leg traces (legend-only, hidden by default) ────────────────
    for i, leg in enumerate(legs):
        color   = _LEG_COLORS[i % len(_LEG_COLORS)]
        leg_pls = [_per_leg_pl(leg, s) for s in spots]
        qs      = "Short" if leg["qty"] < 0 else "Long"
        fig.add_trace(go.Scatter(
            x=spots, y=leg_pls,
            mode="lines",
            name=f"{qs} {leg['label']}",
            line=dict(color=color, width=1.5, dash="dot"),
            opacity=0.75,
            visible="legendonly",
            hovertemplate="Spot ₹%{x:,.0f}<br>Leg P&L ₹%{y:,.0f}<extra>" + leg["label"] + "</extra>",
        ))

    # ── Green profit fill ─────────────────────────────────────────────────────
    profit_y = np.where(pl_vals >= 0, pl_vals, 0.0)
    fig.add_trace(go.Scatter(
        x=list(spots) + list(spots[::-1]),
        y=list(profit_y) + [0.0] * len(spots),
        fill="toself",
        fillcolor="rgba(34,197,94,0.22)",
        line=dict(width=0),
        name="Profit Zone",
        showlegend=True,
        hoverinfo="skip",
    ))

    # ── Red loss fill ─────────────────────────────────────────────────────────
    loss_y = np.where(pl_vals < 0, pl_vals, 0.0)
    fig.add_trace(go.Scatter(
        x=list(spots) + list(spots[::-1]),
        y=list(loss_y) + [0.0] * len(spots),
        fill="toself",
        fillcolor="rgba(239,68,68,0.22)",
        line=dict(width=0),
        name="Loss Zone",
        showlegend=True,
        hoverinfo="skip",
    ))

    # ── Main combined P&L line ────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=spots, y=pl_vals,
        mode="lines",
        name="Net P&L",
        line=dict(color="#2563eb", width=3),
        hovertemplate="Spot ₹%{x:,.0f}<br><b>P&L ₹%{y:+,.0f}</b><extra></extra>",
    ))

    # ── Zero baseline ─────────────────────────────────────────────────────────
    fig.add_hline(y=0, line_dash="solid",
                  line_color="rgba(150,150,150,0.6)", line_width=1)

    # ── Max profit range annotation ───────────────────────────────────────────
    if max_pl > 0:
        # Shaded box for max profit corridor
        fig.add_vrect(
            x0=mp_low, x1=mp_high,
            fillcolor="rgba(34,197,94,0.10)",
            layer="below",
            line_width=0,
        )
        # Annotation at centre of the range
        mid_x = (mp_low + mp_high) / 2
        fig.add_annotation(
            x=mid_x, y=max_pl,
            text=f"<b>Max Profit: ₹{max_pl:,.0f}</b><br>(₹{mp_low:,.0f} – ₹{mp_high:,.0f})",
            showarrow=True,
            arrowhead=2,
            arrowcolor="#16a34a",
            ax=0, ay=-40,
            bgcolor="#f0fdf4",
            bordercolor="#16a34a",
            borderwidth=1.5,
            borderpad=4,
            font=dict(color="#14532d", size=11),
        )
        # Dots at the max-profit start/end corners
        fig.add_trace(go.Scatter(
            x=[mp_low, mp_high],
            y=[float(total_pl(legs, mp_low)), float(total_pl(legs, mp_high))],
            mode="markers",
            marker=dict(color="#16a34a", size=9, symbol="circle"),
            showlegend=False,
            hovertemplate="Spot ₹%{x:,.0f}<br>P&L ₹%{y:+,.0f}<extra>Max Profit Boundary</extra>",
        ))

    # ── Breakeven vertical lines ──────────────────────────────────────────────
    for be in bes:
        fig.add_vline(
            x=be,
            line_dash="dash",
            line_color="#dc2626",
            line_width=1.8,
            opacity=0.9,
        )
        # Red dot at the zero crossing
        fig.add_trace(go.Scatter(
            x=[be], y=[0],
            mode="markers",
            marker=dict(color="#dc2626", size=10, symbol="circle"),
            showlegend=False,
            hovertemplate=f"Breakeven ₹{be:,.0f}<extra></extra>",
        ))
        fig.add_annotation(
            x=be, y=0,
            text=f"BE<br><b>₹{be:,.0f}</b>",
            showarrow=True,
            arrowhead=2,
            arrowcolor="#dc2626",
            ax=0, ay=48,          # below the zero line
            bgcolor="#fef2f2",
            bordercolor="#dc2626",
            borderwidth=1,
            borderpad=3,
            font=dict(color="#991b1b", size=11),
        )

    # ── CMP overlay ───────────────────────────────────────────────────────────
    if cmp > 0:
        cmp_pl = total_pl(legs, cmp)
        fig.add_vline(
            x=cmp, line_dash="dot",
            line_color="#7c3aed", line_width=2, opacity=0.9,
        )
        fig.add_annotation(
            x=cmp, y=cmp_pl,
            text=f"CMP ₹{cmp:,.0f}<br>P&L <b>₹{cmp_pl:+,.0f}</b>",
            showarrow=True, arrowhead=2, arrowcolor="#7c3aed",
            ax=55, ay=-38,
            bgcolor="#ede9fe", bordercolor="#7c3aed",
            borderwidth=1, borderpad=3,
            font=dict(color="#5b21b6", size=11),
        )

    # ── Strike price guide lines ──────────────────────────────────────────────
    strikes_shown: set[float] = set()
    for leg in legs:
        sk = float(leg.get("strike", 0) or 0)
        if sk > 0 and sk not in strikes_shown:
            strikes_shown.add(sk)
            itype = leg["instrument"]
            fig.add_vline(
                x=sk, line_dash="longdash",
                line_color="rgba(148,163,184,0.55)", line_width=1,
            )
            fig.add_annotation(
                x=sk, y=min_pl * 0.6,
                text=f"{itype}<br>₹{int(sk):,}",
                showarrow=False,
                font=dict(color="rgba(148,163,184,0.9)", size=9),
                bgcolor="rgba(0,0,0,0)",
            )

    # ── Y-axis range: profit side always gets generous headroom ──────────────
    y_pos = max(max_pl, 0.0)
    y_neg = min(min_pl, 0.0)

    # Profit headroom: always at least 35% above max_pl for title/annotations
    profit_pad = max(abs(y_pos) * 0.35, abs(y_neg) * 0.08, 500.0)
    y_high     = y_pos + profit_pad

    # Loss headroom: modest 10% below min_pl
    loss_pad = max(abs(y_neg) * 0.10, 500.0)
    y_low    = y_neg - loss_pad

    # Hard cap: if loss is extremely large compared to profit, clip y_low so
    # the profit zone occupies at least 30% of the visible chart height.
    chart_height = y_high - y_low
    profit_height = y_high - 0
    if profit_height < 0.30 * chart_height and y_neg != 0:
        y_low = y_high - (profit_height / 0.30)

    net_sign = "Credit" if net_prem >= 0 else "Debit"
    fig.update_layout(
        title=dict(
            text=(
                f"<b>{underlying}</b> — {strategy} | Combined Payoff at Expiry&nbsp;&nbsp;"
                f"<span style='font-size:13px;font-weight:normal'>"
                f"Net {net_sign}: ₹{abs(net_prem):,.0f}</span>"
            ),
            font=dict(size=15),
            x=0,
        ),
        xaxis=dict(
            title="Spot Price at Expiry (₹)",
            tickformat=",.0f",
            showgrid=True,
            gridcolor="rgba(200,200,200,0.25)",
        ),
        yaxis=dict(
            title="Profit / Loss (₹)",
            tickformat="+,.0f",
            zeroline=False,
            showgrid=True,
            gridcolor="rgba(200,200,200,0.25)",
            range=[y_low, y_high],
        ),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left",   x=0,
            font=dict(size=11),
        ),
        margin=dict(t=90, b=55, l=80, r=30),
        height=500,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# BREAKEVEN VERIFICATION TABLE  (exact P&L = 0 check)
# ─────────────────────────────────────────────────────────────────────────────

def be_verification_table(legs: list[dict], bes: list[float]) -> pd.DataFrame:
    """
    For each BE point show a clean step-by-step formula proving P&L ≈ 0.
    The 'P&L at BE' column should always show ≈ ₹0 (residuals < ₹5 are fine).
    """
    net_prem = _net_premium(legs)
    rows: list[dict] = []

    for be in bes:
        parts: list[str] = []
        for leg in legs:
            itype  = str(leg["instrument"]).upper()
            qty    = float(leg["qty"])
            strike = float(leg.get("strike", 0) or 0)
            price  = float(leg.get("price", 0) or 0)
            lbl    = leg["label"]

            if itype == "CE":
                intrinsic = max(0.0, be - strike)
                premium_c = -qty * price           # credit component
                payoff_c  = qty * intrinsic
                contrib   = payoff_c + premium_c
                parts.append(
                    f"{lbl}: "
                    f"{'Short' if qty<0 else 'Long'} premium ₹{abs(qty*price):,.0f} "
                    f"+ payoff ₹{payoff_c:+,.0f} = ₹{contrib:+,.0f}"
                )
            elif itype == "PE":
                intrinsic = max(0.0, strike - be)
                premium_c = -qty * price
                payoff_c  = qty * intrinsic
                contrib   = payoff_c + premium_c
                parts.append(
                    f"{lbl}: "
                    f"{'Short' if qty<0 else 'Long'} premium ₹{abs(qty*price):,.0f} "
                    f"+ payoff ₹{payoff_c:+,.0f} = ₹{contrib:+,.0f}"
                )
            elif itype == "FUT":
                fut_pl = (be - price) * qty
                parts.append(f"{lbl}: (₹{be:,.0f} - ₹{price:,.0f}) × {int(qty)} = ₹{fut_pl:+,.0f}")

        verify    = round(total_pl(legs, be), 2)
        check_str = "✅ ≈ 0" if abs(verify) < 10 else f"⚠️ ₹{verify:,.2f}"

        rows.append({
            "Breakeven (₹)":   f"₹{be:,.2f}",
            "Net Premium":     f"₹{net_prem:+,.0f}",
            "P&L at BE":       check_str,
            "Step-by-step":    " | ".join(parts) if parts else "—",
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# REGION-BY-REGION DERIVATION HELPER
# ─────────────────────────────────────────────────────────────────────────────

def region_for_leg(legs: list[dict], target_leg: dict) -> str:
    """
    Return the breakeven‑solving text for the interval that has *target_leg*'s
    strike as its boundary.  For calls we use the interval just above the
    strike; for puts the interval ending at the strike.
    """
    stock_leg = None
    options = []
    for l in legs:
        if l["instrument"] == "FUT":
            stock_leg = l
        elif l["instrument"] in ("CE", "PE"):
            options.append(l)
    if not stock_leg:
        return "—"
    stock_price = float(stock_leg["price"])
    net_prem = _net_premium(legs)          # total credit (positive)
    per_share_prem = sum(ll["price"] for ll in options)   # sum of premiums per share
    net_cost = stock_price - per_share_prem

    strikes = sorted({float(ll["strike"]) for ll in options if ll["strike"] > 0})
    if not strikes:
        return "—"

    intervals = []
    if strikes[0] > 0:
        intervals.append((0, strikes[0]))
    for i in range(len(strikes)-1):
        intervals.append((strikes[i], strikes[i+1]))
    intervals.append((strikes[-1], float("inf")))

    tgt_strike = float(target_leg.get("strike", 0))
    tgt_itype = str(target_leg["instrument"]).upper()

    # Pick the interval: for CE we want low == tgt_strike, for PE high == tgt_strike
    picked = None
    for low, high in intervals:
        if tgt_itype == "CE" and low == tgt_strike:
            picked = (low, high)
            break
        elif tgt_itype == "PE" and high == tgt_strike:
            picked = (low, high)
            break

    if picked is None:
        return "—"

    low, high = picked
    # ITM legs in this interval
    itm_ce = [l for l in options if l["instrument"]=="CE" and l["strike"] <= low]
    itm_pe = [l for l in options if l["instrument"]=="PE" and l["strike"] >= high]

    n_ce = len(itm_ce)
    n_pe = len(itm_pe)
    sum_ce_k = sum(l["strike"] for l in itm_ce)
    sum_pe_k = sum(l["strike"] for l in itm_pe)

    lines = []
    lines.append(f"**Region {low:,.0f} $<$ S $\\leq$ {high if high != float('inf') else '∞'}**")
    lines.append(f"- ITM calls: {[int(l['strike']) for l in itm_ce]}")
    lines.append(f"- ITM puts:  {[int(l['strike']) for l in itm_pe]}")

    if n_ce == n_pe:
        const_part = sum_pe_k - sum_ce_k
        lines.append(f"- Total IV = constant **{const_part:,.0f}** (flat region)")
        if net_cost + const_part == 0:
            lines.append("- **Any S here is a breakeven** (plateau)")
        else:
            lines.append("- No solution in this region")
    else:
        # IV expression
        ce_str = " + ".join([f"(S – {int(l['strike'])})" for l in itm_ce]) or "0"
        pe_str = " + ".join([f"({int(l['strike'])} – S)" for l in itm_pe]) or "0"
        lines.append(f"- Total IV = {ce_str} + {pe_str}")
        lines.append(f"             = ({n_ce}S - {sum_ce_k:,.0f}) + ({sum_pe_k:,.0f} - {n_pe}S)")
        lines.append(f"             = ({n_ce-n_pe})S + {sum_pe_k - sum_ce_k:,.0f}")

        lhs_coeff = 1 - n_ce + n_pe
        rhs = net_cost + sum_pe_k - sum_ce_k
        if lhs_coeff == 0:
            lines.append("- No unique solution")
        else:
            S_sol = rhs / lhs_coeff
            lines.append(f"- Equation: S – {net_cost:,.2f} = ({n_ce-n_pe})S + {sum_pe_k - sum_ce_k:,.0f}")
            lines.append(f"  → {lhs_coeff}S = {rhs:,.2f}")
            lines.append(f"  → S = {S_sol:,.2f}")
            if low - 1e-6 <= S_sol <= high + 1e-6:
                lines.append(f"- **✅ Valid (lies in this region)**")
            else:
                lines.append(f"- **❌ Outside interval**")
    return "\n".join(lines)


def derive_be_steps(legs: list[dict], be: float) -> str:
    """
    Generate a markdown string that walks through the entire breakeven solving
    process REGION BY REGION, as if done by hand.
    - Shows total premium received per share
    - Shows net cost basis
    - For each interval defined by the unique strikes, lists:
        * ITM calls and puts with their strikes
        * Total intrinsic value (IV) expression
        * Equation: S – net_cost = Total IV
        * Solved S and whether it lies in the interval
    - Finally, highlights the region that actually contains this BE.
    """
    # Separate stock/fut leg and option legs
    stock_leg = None
    options = []
    for l in legs:
        if l["instrument"] == "FUT":
            stock_leg = l
        elif l["instrument"] in ("CE", "PE"):
            options.append(l)

    if not stock_leg:
        return "*No stock/FUT leg found – cannot derive for option‑only positions.*"

    stock_price = float(stock_leg["price"])   # per‑share cost
    net_prem = _net_premium(legs)             # total credit (positive) or debit (negative)
    net_cost = stock_price - net_prem          # real cost basis after premium

    # Collect unique strikes (ignore 0)
    strikes_set = sorted({float(l["strike"]) for l in options if l["strike"] > 0})

    lines = []
    lines.append(f"### 1. Position summary (per share)")
    lines.append(f"- Long stock/FUT cost = ₹{stock_price:,.2f}")
    prem_strs = [f"  {l['instrument']} {int(l['strike'])}: ₹{abs(l['qty'])*l['price']:,.0f}" for l in options]
    lines.append(f"- Premiums collected:  " + "  \n" + "  \n".join(prem_strs))
    lines.append(f"- **Total net credit** = ₹{net_prem:+,.0f}")
    lines.append(f"- **Net cost basis** = Stock cost – credit = {stock_price:,.2f} – {net_prem:+,.0f} = **₹{net_cost:,.2f}**")
    lines.append("")
    lines.append("### 2. Equation at spot S")
    lines.append("If spot at expiry = **S**, the intrinsic value (IV) of each short option is:")
    lines.append("- Put IV = max(Strike – S, 0)")
    lines.append("- Call IV = max(S – Strike, 0)")
    lines.append("Total P&L = (S – stock cost) + [Net credit – sum of all IVs]")
    lines.append(f"          = S – {net_cost:,.2f} – (Total IV)")
    lines.append("**Set P&L = 0  →  S – " + f"{net_cost:,.2f}" + " = Total IV**")
    lines.append("")

    # Build intervals between consecutive strikes, plus ends
    if strikes_set:
        intervals = [(0, strikes_set[0])] + \
                    [(strikes_set[i], strikes_set[i+1]) for i in range(len(strikes_set)-1)] + \
                    [(strikes_set[-1], float("inf"))]
    else:
        intervals = [(0, float("inf"))]

    found_flag = False
    for low, high in intervals:
        # Determine ITM legs in this whole interval (no internal strikes)
        itm_ce = []
        itm_pe = []
        for l in options:
            sk = float(l["strike"])
            if l["instrument"] == "CE" and sk <= low:
                itm_ce.append(l)
            elif l["instrument"] == "PE" and sk >= high:
                itm_pe.append(l)

        # Build the IV expression
        # IV = (sum over CE: (S - strike)) + (sum over PE: (strike - S))
        #    = (#CE - #PE) * S + (sum_PE_strike - sum_CE_strike)
        n_ce = len(itm_ce)
        n_pe = len(itm_pe)
        sum_ce_k = sum(l["strike"] for l in itm_ce)
        sum_pe_k = sum(l["strike"] for l in itm_pe)

        if n_ce == n_pe:
            # Flat region: P&L independent of S
            const_part = sum_pe_k - sum_ce_k
            if net_cost + const_part == 0:
                lines.append(f"**Region {low:,.0f} ≤ S ≤ {high if high != float('inf') else '∞'}**")
                lines.append(f"- ITM calls: {[int(l['strike']) for l in itm_ce]}")
                lines.append(f"- ITM puts:  {[int(l['strike']) for l in itm_pe]}")
                lines.append(f"- Total IV = (S–strikes...) = constant **{const_part:,.0f}** (flat region)")
                lines.append(f"- Equation: S – {net_cost:,.2f} = {const_part:,.0f}  →  **0 = 0**")
                lines.append(f"- **Any S in this interval is a breakeven!** (no unique point)")
                lines.append("")
                found_flag = True
                break   # show only the first flat region
            else:
                # No solution in this region (will be ignored below)
                pass
        else:
            # Solve S
            # Equation: S - net_cost = (n_ce - n_pe) * S + (sum_pe_k - sum_ce_k)
            # => S - (n_ce - n_pe)*S = net_cost + (sum_pe_k - sum_ce_k)
            # => (1 - n_ce + n_pe) * S = net_cost + sum_pe_k - sum_ce_k
            lhs_coeff = 1 - n_ce + n_pe
            rhs = net_cost + sum_pe_k - sum_ce_k
            if lhs_coeff == 0:
                if rhs == 0:
                    # Identical to flat region? (shouldn't happen with n_ce != n_pe)
                    pass
                else:
                    continue   # no solution
            S_sol = rhs / lhs_coeff

            # Check if solution lies in interval (with small epsilon)
            if low - 1e-6 <= S_sol <= high + 1e-6:
                # Show this interval's reasoning
                lines.append(f"**Region {low:,.0f} ≤ S ≤ {high if high != float('inf') else '∞'}**")
                lines.append(f"- ITM calls: {[int(l['strike']) for l in itm_ce]}")
                lines.append(f"- ITM puts:  {[int(l['strike']) for l in itm_pe]}")
                # Write IV expression nicely
                if n_ce > 0:
                    ce_str = " + ".join([f"(S – {int(l['strike'])})" for l in itm_ce])
                else:
                    ce_str = "0"
                if n_pe > 0:
                    pe_str = " + ".join([f"({int(l['strike'])} – S)" for l in itm_pe])
                else:
                    pe_str = "0"
                lines.append(f"- Total IV = {ce_str} + {pe_str}")
                lines.append(f"             = ({n_ce}S - {sum_ce_k:,.0f}) + ({sum_pe_k:,.0f} - {n_pe}S)")
                lines.append(f"             = ({n_ce-n_pe})S + {sum_pe_k - sum_ce_k:,.0f}")
                lines.append(f"- Equation: S – {net_cost:,.2f} = ({n_ce-n_pe})S + {sum_pe_k - sum_ce_k:,.0f}")
                lines.append(f"  → (1 - ({n_ce-n_pe}))S = {net_cost:,.2f} + {sum_pe_k - sum_ce_k:,.0f}")
                lines.append(f"  → {lhs_coeff}S = {rhs:,.2f}")
                lines.append(f"  → S = {S_sol:,.2f}")
                # Determine if solution is within interval
                within = "**✅ Valid (lies in this region)**" if (low <= S_sol <= high) else "**❌ Outside interval**"
                lines.append(f"- {within}")
                if within.startswith("✅"):
                    # If it matches the BE we are looking for, mark it
                    if abs(S_sol - be) < 1:
                        lines.append(f"- **This is Breakeven ₹{be:,.2f}**")
                    found_flag = True
                    break   # stop after first matching region

    if not found_flag:
        lines.append(f"⚠️ Could not find a region that contains Breakeven ₹{be:,.2f} with this method.")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN RENDER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def render_be_tab(user: dict, op_df: pd.DataFrame):
    """Full Streamlit rendering for the BE Graphs tab."""
    st.subheader("📉 Breakeven (BE) Strategy Visualiser")
    st.caption(
        "Select a stock with open F&O positions to see the combined payoff curve at expiry. "
        "🟢 Green = profit zone · 🔴 Red = loss zone · Red dashed lines = breakeven points · "
        "Green shaded band = max profit range."
    )

    if op_df.empty:
        st.info("No open F&O positions found. Upload F&O files to get started.")
        return

    stock_legs = extract_legs_from_op_df(op_df)
    if not stock_legs:
        st.info("No valid F&O legs (CE / PE / FUT) found in open positions.")
        return

    # ── Stock selector + CMP controls ────────────────────────────────────────
    sorted_stocks = sorted(stock_legs.keys())
    sel_col, price_col, fetch_col = st.columns([3, 2, 2])

    with sel_col:
        selected = st.selectbox(
            "Select underlying stock", sorted_stocks, key="be_stock_select"
        )

    legs    = stock_legs[selected]
    cmp_key = f"be_cmp_{selected}"
    cmp_val = float(st.session_state.get(cmp_key, 0.0))

    with fetch_col:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("🔄 Fetch CMP (5paisa)", key=f"be_fetch_{selected}"):
            with st.spinner(f"Fetching {selected} price…"):
                try:
                    from backend.services.engine_price_fetch import fetch_current_prices
                    prices = fetch_current_prices([selected])
                    if selected in prices:
                        st.session_state[cmp_key] = float(prices[selected])
                        cmp_val = st.session_state[cmp_key]
                        st.success(f"₹{cmp_val:,.2f}")
                    else:
                        st.warning("Price not found via 5paisa. Enter manually below.")
                except Exception as e:
                    st.error(f"Fetch failed: {e}")

    with price_col:
        cmp_val = st.number_input(
            "Current Market Price (₹)",
            min_value=0.0,
            value=cmp_val,
            step=10.0,
            key=f"be_cmp_input_{selected}",
            help="Fetched from 5paisa or enter manually. Leave 0 to hide the CMP overlay.",
        )
        if cmp_val > 0:
            st.session_state[cmp_key] = cmp_val

    st.divider()

    # ── Compute for KPIs ──────────────────────────────────────────────────────
    spot_min, spot_max = _spot_range(legs, cmp_val)
    bes, spots, pl_vals = find_breakevens(legs, spot_min, spot_max)
    net_prem  = _net_premium(legs)
    max_pl, mp_low, mp_high = _max_profit_range(legs, spots, pl_vals)
    strategy  = _detect_strategy(legs)

    # Max loss: scan a 3x wider range so unlimited-loss strategies
    # (short strangle, naked options) show a realistic worst-case figure.
    _wide_lo = max(0.0, spot_min - (spot_max - spot_min))
    _wide_hi = spot_max + (spot_max - spot_min)
    _wide_sp = np.linspace(_wide_lo, _wide_hi, 2000)
    _wide_pl = np.array([total_pl(legs, s) for s in _wide_sp])
    min_pl   = float(_wide_pl.min())
    # Truly unlimited = still falling at the far edges
    _is_unlimited_loss = (
        total_pl(legs, _wide_hi) < min_pl * 0.95 or
        (_wide_lo > 0 and total_pl(legs, _wide_lo) < min_pl * 0.95)
    )

    # ── KPI strip ─────────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Strategy",       strategy)
    k2.metric("Net Premium",
              f"₹{abs(net_prem):,.0f}",
              delta="Credit" if net_prem >= 0 else "Debit",
              delta_color="normal" if net_prem >= 0 else "inverse")
    k3.metric("Max Profit",
              "Unlimited" if max_pl > 1e6 else f"₹{max_pl:,.0f}",
              delta="↑ Profit",
              delta_color="normal")
    k4.metric("Max Loss (if held to expiry)",
              "Unlimited" if _is_unlimited_loss else f"₹{abs(min_pl):,.0f}",
              delta="↓ Unlimited Risk" if _is_unlimited_loss else f"↓ Risk at far strikes",
              delta_color="inverse")
    k5.metric("Breakevens",     len(bes))
    if bes:
        be_range = f"₹{min(bes):,.0f}" + (f" – ₹{max(bes):,.0f}" if len(bes) > 1 else "")
        k6.metric("BE Range",   be_range)
    else:
        k6.metric("BE Range",   "—")

    # ── Profit range info bar ─────────────────────────────────────────────────
    if max_pl > 0 and abs(mp_high - mp_low) > 1:
        st.success(
            f"🎯 **Max Profit Zone**: ₹{mp_low:,.0f} — ₹{mp_high:,.0f} spot "
            f"(width ₹{mp_high - mp_low:,.0f}) · Max P&L = **₹{max_pl:,.0f}**"
        )

    # ── CMP status vs BEs ─────────────────────────────────────────────────────
    if cmp_val > 0 and bes:
        cmp_pl = total_pl(legs, cmp_val)
        lower  = min(bes)
        upper  = max(bes)
        if cmp_val < lower:
            st.error(
                f"⚠️ CMP ₹{cmp_val:,.0f} is **₹{lower - cmp_val:,.0f} below** the lower BE (₹{lower:,.0f}). "
                f"Current P&L: **₹{cmp_pl:+,.0f}**"
            )
        elif len(bes) > 1 and cmp_val > upper:
            st.error(
                f"⚠️ CMP ₹{cmp_val:,.0f} is **₹{cmp_val - upper:,.0f} above** the upper BE (₹{upper:,.0f}). "
                f"Current P&L: **₹{cmp_pl:+,.0f}**"
            )
        else:
            st.success(
                f"✅ CMP ₹{cmp_val:,.0f} is within the **profit zone**. "
                f"Current P&L: **₹{cmp_pl:+,.0f}**"
            )

    # ── Main chart ────────────────────────────────────────────────────────────
    fig = build_be_figure(legs, selected, cmp=float(cmp_val))
    st.plotly_chart(fig, use_container_width=True)

    # ── Position legs table ───────────────────────────────────────────────────
    st.markdown("#### 📋 Position Legs")
    st.caption("Leg‑by‑leg breakeven derivation. Each row’s **Leg Calculation** column shows the region that uses its strike to solve for the breakeven.")
    with st.expander("📐 Column Guide — Position Legs"):
        st.markdown("""
| Column | What it means |
|---|---|
| **Instrument** | CE = Call option · PE = Put option · FUT = Futures contract |
| **Strike (₹)** | The option strike price (blank for futures) |
| **Expiry** | Contract expiry month (YYYY-MM) |
| **Direction** | Short (sold/written) = you collected premium · Long (bought) = you paid premium |
| **Qty** | Number of units (in shares, not lots) |
| **Avg Price (₹)** | Your average entry price — premium for options, futures price for FUT |
| **Premium (₹)** | Total premium = Qty × Avg Price · for Short legs this is income received |
| **Leg Calculation** | Displays the equation and derivation region for the interval where this leg's strike forms the boundary. |
""")
    with st.expander("📋 View all legs", expanded=True):
        # ── Premium & net cost summary (per share) ─────────────────────────
        if any(l["instrument"] == "FUT" for l in legs):
            stock_leg_ = next(l for l in legs if l["instrument"] == "FUT")
            stock_cost_ps = float(stock_leg_["price"])
        else:
            stock_cost_ps = 0.0
        option_legs_ = [l for l in legs if l["instrument"] in ("CE", "PE")]
        per_share_prem = sum(l["price"] for l in option_legs_)
        net_cost_ps = stock_cost_ps - per_share_prem

        st.markdown(f"""
        **Per‑share breakdown** - Stock cost = ₹{stock_cost_ps:,.2f}  
        - Total premium received = { " + ".join(f"{l['price']:,.2f}" for l in option_legs_) } = **₹{per_share_prem:,.2f}** - **Net cost basis** = Stock – Premium = {stock_cost_ps:,.2f} – {per_share_prem:,.2f} = **₹{net_cost_ps:,.2f}** **Pay‑off at expiry (per share)** If spot = S, Intrinsic Value (IV) of each short option is:  
        - Put IV = max(Strike – S, 0)  
        - Call IV = max(S – Strike, 0)  

        Total P&L = (S – {stock_cost_ps:,.2f}) + [Total Premium – sum of all IVs]  
        = **S – {net_cost_ps:,.2f} – (Total IV)** Set P&L = 0 → **S – {net_cost_ps:,.2f} = Total IV**
        """)

        leg_rows = []
        for leg in legs:
            qty   = float(leg["qty"])
            sk    = int(float(leg.get("strike", 0) or 0))
            price = float(leg.get("price", 0) or 0)
            itype = leg["instrument"]
            row = {
                "Instrument":    f"{itype} {'Call' if itype=='CE' else 'Put' if itype=='PE' else 'Future'}",
                "Strike (₹)":   f"₹{sk:,}" if sk > 0 else "—",
                "Expiry":        leg.get("expiry", ""),
                "Direction":     "🔴 Short (sold)" if qty < 0 else "🟢 Long (bought)",
                "Qty":           int(abs(qty)),
                "Avg Price (₹)": f"₹{price:,.2f}",
                "Premium (₹)":   f"₹{abs(qty) * price:,.0f}",
            }
            # ── Leg Calculation (region‑by‑region derivation step) ──────────
            if itype in ("CE", "PE"):
                row["Leg Calculation"] = region_for_leg(legs, leg)
            else:
                # For the stock/FUT leg, show the overall equation note
                row["Leg Calculation"] = (
                    f"Stock cost basis: ₹{stock_cost_ps:,.2f}. "
                    f"Net premium collected: ₹{per_share_prem:,.2f}. "
                    f"Net cost = ₹{net_cost_ps:,.2f}."
                )
            leg_rows.append(row)

        def _style_legs(row):
            out = []
            for col in row.index:
                if "Direction" in col:
                    out.append(
                        "color:#dc3545;font-weight:bold"
                        if "Short" in str(row.get("Direction",""))
                        else "color:#28a745;font-weight:bold"
                    )
                elif "Leg Calculation" in col:
                    # Keep normal text
                    out.append("")
                else:
                    out.append("")
            return out

        st.dataframe(
            pd.DataFrame(leg_rows).style.apply(_style_legs, axis=1),
            use_container_width=True, hide_index=True,
        )
        net_label = "🟢 Net Credit (received)" if net_prem >= 0 else "🔴 Net Debit (paid)"
        st.markdown(f"**{net_label}: ₹{abs(net_prem):,.0f}**")

    # ── Breakeven verification table ──────────────────────────────────────────
    st.markdown("#### 📐 Breakeven Verification")
    with st.expander("📐 Column Guide — Breakeven Table"):
        st.markdown("""
| Column | What it means |
|---|---|
| **Breakeven (₹)** | The exact spot price where total P&L crosses zero |
| **Net Premium** | Total net credit or debit of the entire position |
| **P&L at BE** | Should be ✅ ≈ 0 — confirms the math is correct. Small residuals (< ₹10) are due to interpolation. |
| **Step-by-step** | Shows each leg's contribution (premium collected/paid + intrinsic payoff) at this spot price |
""")

    if bes:
        be_df = be_verification_table(legs, bes)
        if not be_df.empty:
            def _style_be(row):
                return [
                    "color:#16a34a;font-weight:bold" if "✅" in str(row.get("P&L at BE",""))
                    else "color:#dc2626;font-weight:bold" if "⚠️" in str(row.get("P&L at BE",""))
                    else ""
                    if col == "P&L at BE" else ""
                    for col in row.index
                ]
            st.dataframe(
                be_df.style.apply(_style_be, axis=1),
                use_container_width=True, hide_index=True,
            )
    else:
        if net_prem > 0:
            st.success("✅ No breakeven — this credit position is in profit across the entire displayed range.")
        else:
            st.error("⚠️ No breakeven found — the position may be in loss across the entire displayed range.")

    # ── Region‑by‑Region Derivation ─────────────────────────────────────
    st.divider()
    st.markdown("#### 📐 Breakeven Derivation (Region‑by‑Region)")
    st.caption(
        "Complete step‑by‑step solving for each breakeven, with all the algebra shown — "
        "just as you would derive it by hand. Use this to verify the computed BE points."
    )
    if bes:
        for be in bes:
            with st.expander(f"Breakeven ₹{be:,.2f}", expanded=False):
                deriv_text = derive_be_steps(legs, be)
                st.markdown(deriv_text)
    else:
        st.info("No breakeven points to derive.")

    # ── Max Profit / Max Loss summary ─────────────────────────────────────────
    st.markdown("#### 📊 Profit & Loss Summary")
    with st.expander("📐 Column Guide — P&L Summary"):
        st.markdown("""
| Column | What it means |
|---|---|
| **Metric** | What is being measured |
| **Value** | The computed amount in ₹ |
| **At Spot** | The stock price at which this value occurs (if bounded) |
| **Notes** | Context or interpretation |
""")
    pl_summary = []
    pl_summary.append({
        "Metric": "Max Profit",
        "Value":  f"₹{max_pl:,.0f}" if max_pl < 1e6 else "Unlimited",
        "At Spot": f"₹{mp_low:,.0f} – ₹{mp_high:,.0f}" if abs(mp_high - mp_low) > 1 else f"₹{mp_low:,.0f}",
        "Notes": "Green zone on chart · Stock must stay in this range by expiry",
    })
    pl_summary.append({
        "Metric": "Max Loss (worst case)",
        "Value":  "Unlimited" if _is_unlimited_loss else f"₹{abs(min_pl):,.0f}",
        "At Spot": "Far OTM" if _is_unlimited_loss else "—",
        "Notes": "Always shown as positive ₹ amount · Red zone on chart · Risk if stock moves far outside breakevens",
    })
    pl_summary.append({
        "Metric": "Net Premium",
        "Value":  f"₹{abs(net_prem):,.0f} {'Credit' if net_prem >= 0 else 'Debit'}",
        "At Spot": "—",
        "Notes": "Collected upfront (credit) or paid upfront (debit)",
    })
    for i, be in enumerate(bes, 1):
        pl_summary.append({
            "Metric": f"Breakeven {i}",
            "Value":  f"₹{be:,.0f}",
            "At Spot": f"₹{be:,.0f}",
            "Notes": "P&L = ₹0 at this spot price at expiry",
        })
    st.dataframe(pd.DataFrame(pl_summary), use_container_width=True, hide_index=True)

    # ── All stocks quick summary ──────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🗂️ All Stocks — Quick Summary")
    with st.expander("📐 Column Guide — Quick Summary"):
        st.markdown("""
| Column | What it means |
|---|---|
| **Stock** | Underlying ticker symbol |
| **Legs** | Number of open F&O legs (CE + PE + FUT contracts) |
| **Strategy** | Auto-detected strategy name |
| **Net Premium (₹)** | Net Credit = income collected · Net Debit = premium paid |
| **Breakevens** | Number of spot prices where P&L = 0 |
| **BE Range** | Lower and upper breakeven spot prices |
| **Signal** | 🟢 Credit strategy (positive income) · 🔴 Debit strategy (paid for position) |
""")
    summary_rows = []
    for und in sorted_stocks:
        ll        = stock_legs[und]
        np_       = _net_premium(ll)
        smin, smax = _spot_range(ll)
        be_list, sp_all, pl_all = find_breakevens(ll, smin, smax)
        max_p, _, _ = _max_profit_range(ll, sp_all, pl_all)
        strat     = _detect_strategy(ll)
        summary_rows.append({
            "Stock":            und,
            "Legs":             len(ll),
            "Strategy":         strat,
            "Net Premium (₹)":  f"₹{abs(np_):,.0f} {'Credit' if np_>=0 else 'Debit'}",
            "Max Profit (₹)":   "Unlimited" if max_p > 1e6 else f"₹{max_p:,.0f}",
            "Breakevens":       len(be_list),
            "BE Range":         (
                f"₹{min(be_list):,.0f} – ₹{max(be_list):,.0f}"
                if len(be_list) > 1 else
                f"₹{be_list[0]:,.0f}" if be_list else "—"
            ),
            "Signal": "🟢 Credit" if np_ >= 0 else "🔴 Debit",
        })

    if summary_rows:
        def _style_summary(row):
            return [
                "color:#16a34a;font-weight:bold" if "Credit" in str(row.get("Signal",""))
                else "color:#dc2626;font-weight:bold" if col == "Signal"
                else ""
                for col in row.index
            ]
        st.dataframe(
            pd.DataFrame(summary_rows).style.apply(_style_summary, axis=1),
            use_container_width=True, hide_index=True,
        )
