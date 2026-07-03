"""
fno_positions.py  — modified to merge synthetic transactions into position compute.

Changes vs original:
  • _compute_from_transactions now also reads fno_synthetic_transactions
    and merges them before computing net quantities.
  • Endpoint logic unchanged.
  • FIXED: P&L calculation now handles None strike_price correctly
  • ADDED: live_price column alongside live_pnl for CMP display
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import SessionLocal
from datetime import date
import pandas as pd
from services.engine_price_fetch import fetch_fno_prices
import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["F&O Positions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Compute net positions from fno_transactions + fno_synthetic_transactions
# ─────────────────────────────────────────────────────────────────────────────

def _compute_from_transactions(user_id: int, db: Session) -> list[dict]:
    """
    Net BUY vs SELL quantities per contract, merging:
      • fno_transactions           (real uploaded broker trades)
      • fno_synthetic_transactions (dividend-adjustment bookkeeping)

    open_qty > 0 = long (bought)
    open_qty < 0 = short (sold)

    Only returns contracts:
      • with expiry >= today  (not expired)
      • with abs(net_qty) > 0.001  (not fully closed)
    """
    today_str = date.today().strftime("%Y-%m-%d")  # MUST be defined before queries

    # ── Real transactions ──────────────────────────────────────────────────────
    real_rows = db.execute(
        text("""
            SELECT
                underlying,
                instrument_type,
                expiry_date,
                strike_price,
                broker,
                SUM(CASE WHEN trade_type = 'BUY'  THEN  quantity ELSE 0 END)        AS buy_qty,
                SUM(CASE WHEN trade_type = 'SELL' THEN  quantity ELSE 0 END)         AS sell_qty,
                SUM(CASE WHEN trade_type = 'BUY'  THEN quantity * price ELSE 0 END) AS buy_value,
                SUM(CASE WHEN trade_type = 'SELL' THEN quantity * price ELSE 0 END) AS sell_value
            FROM fno_transactions
            WHERE user_id = :uid
              AND (expiry_date IS NULL OR expiry_date >= :today)
            GROUP BY underlying, instrument_type, expiry_date, strike_price, broker
        """),
        {"uid": user_id, "today": today_str},
    ).fetchall()

    # ── Synthetic transactions (dividend adjustments) ─────────────────────────
    syn_rows = []
    try:
        syn_rows = db.execute(
            text("""
                SELECT
                    underlying,
                    instrument_type,
                    expiry_date,
                    strike_price,
                    'SYNTHETIC' AS broker,
                    SUM(CASE WHEN trade_type = 'BUY'  THEN  quantity ELSE 0 END)        AS buy_qty,
                    SUM(CASE WHEN trade_type = 'SELL' THEN  quantity ELSE 0 END)         AS sell_qty,
                    SUM(CASE WHEN trade_type = 'BUY'  THEN quantity * price ELSE 0 END) AS buy_value,
                    SUM(CASE WHEN trade_type = 'SELL' THEN quantity * price ELSE 0 END) AS sell_value
                FROM fno_synthetic_transactions
                WHERE user_id = :uid
                  AND (expiry_date IS NULL OR expiry_date >= :today)
                GROUP BY underlying, instrument_type, expiry_date, strike_price
            """),
            {"uid": user_id, "today": today_str},
        ).fetchall()
    except Exception as e:
        # Table may not exist yet on first run — safe to skip
        logger.debug(f"[FNO Positions] fno_synthetic_transactions not available: {e}")

    # ── Merge into dict keyed by (underlying, itype, expiry, strike) ──────────
    # We merge real + synthetic by contract key (ignoring broker in the key
    # so that e.g. "5paisa BUY 18000 CE" merges with "SYNTHETIC SELL 18000 CE").
    merged: dict[tuple, dict] = {}

    for r in list(real_rows) + list(syn_rows):
        key = (
            str(r.underlying      or "").strip().upper(),
            str(r.instrument_type or "").strip().upper(),
            str(r.expiry_date     or "")[:10],
            float(r.strike_price  or 0),
        )
        if key not in merged:
            merged[key] = {
                "underlying":      key[0],
                "instrument_type": key[1],
                "expiry_date":     key[2],
                "strike_price":    key[3],
                "buy_qty":   0.0,
                "sell_qty":  0.0,
                "buy_value": 0.0,
                "sell_value": 0.0,
                "broker":    str(r.broker or ""),
            }
        m = merged[key]
        m["buy_qty"]    += float(r.buy_qty    or 0)
        m["sell_qty"]   += float(r.sell_qty   or 0)
        m["buy_value"]  += float(r.buy_value  or 0)
        m["sell_value"] += float(r.sell_value or 0)

    # ── Build result list ──────────────────────────────────────────────────────
    result: list[dict] = []

    for key, m in merged.items():
        buy_qty  = m["buy_qty"]
        sell_qty = m["sell_qty"]
        net_qty  = buy_qty - sell_qty

        if abs(net_qty) < 0.001:
            continue  # fully closed contract

        # avg_price: use buy-side for long, sell-side for short
        if net_qty > 0:
            avg_price = m["buy_value"]  / buy_qty  if buy_qty  > 0 else 0.0
        else:
            avg_price = m["sell_value"] / sell_qty if sell_qty > 0 else 0.0

        expiry = m["expiry_date"]
        symbol = (
            f"{m['underlying']} {m['instrument_type']} "
            f"{expiry[:10] if expiry else ''} "
            f"{float(m['strike_price']):.0f}"
        ).strip()

        result.append({
            "id":              None,
            "user_id":         user_id,
            "symbol":          symbol,
            "underlying":      m["underlying"],
            "exchange":        "NSE",
            "instrument_type": m["instrument_type"],
            "expiry_date":     expiry[:10] if expiry else None,
            "strike_price":    m["strike_price"],
            "open_qty":        round(net_qty, 4),
            "avg_price":       round(avg_price, 4),
            "closing_price":   0.0,
            "unrealized_pnl":  0.0,
            "as_of_date":      today_str,
            "trade_date":      today_str,
            "broker":          m["broker"],
            "source_file":     "computed_from_transactions",
            "_source":         "computed",
        })

    # Sort: FUT → CE → PE, then underlying, then expiry
    result.sort(key=lambda x: (
        {"FUT": 0, "CE": 1, "PE": 2}.get(str(x.get("instrument_type") or ""), 9),
        str(x.get("underlying")   or ""),
        str(x.get("expiry_date")  or ""),
    ))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/fno/positions/{user_id}")
def get_fno_positions(user_id: int, db: Session = Depends(get_db)):
    today_str = date.today().strftime("%Y-%m-%d")

    # ── 1. Try uploaded snapshot ──────────────────────────────────────────────
    rows = db.execute(
        text("""
            SELECT * FROM fno_open_positions
            WHERE user_id = :uid
              AND (expiry_date IS NULL OR expiry_date >= :today)
            ORDER BY instrument_type, underlying, expiry_date
        """),
        {"uid": user_id, "today": today_str},
    ).fetchall()

    positions = [dict(row._mapping) for row in rows]
    positions = [p for p in positions if abs(float(p.get("open_qty", 0) or 0)) > 0.001]

    for p in positions:
        p["_source"] = "file_upload"

    # ── 2. Fallback: compute from transactions ─────────────────────────────────
    if not positions:
        positions = _compute_from_transactions(user_id, db)

    # ══════════════════════════════════════════════════════════════════════════
    # ⭐ FIXED: Use the exact working REST API method from your test script!
    # ══════════════════════════════════════════════════════════════════════════
    if positions:
        from services.ce_pe_service import _get_5paisa_client, _parse_expiry_ms
        import datetime as _dt
        
        client = _get_5paisa_client()
        if client:
            for p in positions:
                underlying = p.get("underlying", "")
                itype = p.get("instrument_type", "")
                expiry = p.get("expiry_date", "")
                strike = p.get("strike_price", 0.0)
                qty = float(p.get("open_qty", 0) or 0)
                avg = float(p.get("avg_price", 0) or 0)
                live_price = 0.0

                # Skip if there's no expiry date
                if not expiry:
                    p['live_pnl'] = None
                    p['live_price'] = None
                    continue

                try:
                    # --- LOGIC FOR CE / PE OPTIONS ---
                    if itype in ("CE", "PE"):
                        exp_resp = client.get_expiry("N", underlying)
                        if exp_resp and exp_resp.get("Status") == 0:
                           
                          #  FIX: Validate expiry string and clear IDE warnings
                            try:
                                target_dt = datetime.datetime.strptime(expiry[:10], "%Y-%m-%d").date()
                            except ValueError:
                                # If expiry string is bad, skip this contract
                                p['live_pnl'] = None
                                p['live_price'] = None
                                continue

                            target_ts = None
                            for exp_entry in exp_resp.get("Expiry", []):
                                ts = _parse_expiry_ms(str(exp_entry.get("ExpiryDate", "")))
                                if ts is not None:   # ⭐ Explicitly check for None
                                    exp_dt = datetime.datetime.fromtimestamp(ts / 1000).date()
                                    if exp_dt == target_dt:
                                        target_ts = int(ts)  # ⭐ Cast to int to fix IDE type-hint warning
                                        break

                            if target_ts:
                                chain = client.get_option_chain("N", underlying, target_ts)
                                opts = chain.get("Options") or chain.get("Data", []) if isinstance(chain, dict) else chain
                                if opts:
                                    for opt in opts:
                                        cp = opt.get("CPType") or opt.get("OptionType", "")
                                        if itype == "CE" and cp not in ("C", "CE"): continue
                                        if itype == "PE" and cp not in ("P", "PE"): continue
                                        if abs(float(opt.get("StrikeRate", 0) or 0) - strike) < 0.5:
                                            live_price = float(opt.get("CPLastRate") or opt.get("LastRate") or 0)
                                            break

                    # --- LOGIC FOR FUTURES CONTRACTS ---
                    elif itype == "FUT":
                        # For FUT, we fetch the Underlying Spot Price from get_expiry
                        exp_resp = client.get_expiry("N", underlying)
                        if exp_resp and exp_resp.get("Status") == 0:
                            lastrate_list = exp_resp.get("lastrate", [])
                            if lastrate_list:
                                live_price = float(lastrate_list[0].get("LTP", 0.0))

                except Exception as e:
                    logger.warning(f"Price fetch failed for {underlying} {itype}: {e}")

                # --- CALCULATE P&L ---
                if live_price > 0 and avg > 0:
                    if qty > 0: # Long position
                        pnl = (live_price - avg) * qty
                    else:       # Short position
                        pnl = (avg - live_price) * abs(qty)
                    p['live_pnl'] = round(pnl, 2)
                    p['live_price'] = round(live_price, 2)
                else:
                    p['live_pnl'] = None
                    p['live_price'] = None

    return positions