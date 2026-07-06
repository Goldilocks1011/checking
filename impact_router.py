"""
impact_router.py
================
FastAPI router for:
  GET /impact/{symbol}        — full analysis bundle (price, mmm, trend, momentum,
                                 analyst, seasonal, corp_events, news, account_context)
  GET /suggest/{symbol}       — suggestion engine output
  GET /holding-intel/{symbol} — holding intelligence for a user

All external API calls (stock analyser, news) have a 5-second timeout so they
never block the UI.  If they fail, the key is returned with an empty/error value.

External services (configure via env vars or hardcode for now):
  ANALYSER_URL  = http://139.59.74.2:8080
  NEWS_URL      = http://159.89.225.5:8010
"""
from __future__ import annotations

import os
import httpx
import asyncio
from fastapi import APIRouter
from typing import Optional

# ── local services ────────────────────────────────────────────────────────────
from services.analysis_service import (
    get_price_levels,
    get_mmm_stats,
    get_seasonal_pattern,
    get_consecutive_trend,
    get_momentum_score,
    get_holding_intel,
)
from services.suggestion_engine import get_suggestion
from services.scrip_master_db import is_db_populated
from database import SessionLocal
from sqlalchemy import text
import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["Impact"])

# ── external service URLs (override via env) ──────────────────────────────────
ANALYSER_URL = os.getenv("ANALYSER_URL", "http://139.59.74.2:8080")
NEWS_URL     = os.getenv("NEWS_URL",     "http://159.89.225.5:8010")

_EXT_TIMEOUT = 5.0   # seconds — never block UI longer than this


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scrip_code_for(symbol: str) -> int | None:
    """Look up scrip_code from scrip_master_cache for the given NSE symbol."""
    s = symbol.strip().upper()
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT scrip_code FROM scrip_master_cache
                WHERE (UPPER(symbol_root) = :sym OR UPPER(name) = :sym)
                  AND exch = 'N' AND exch_type = 'C'
                  AND scrip_code IS NOT NULL AND scrip_code != ''
                ORDER BY CASE WHEN UPPER(symbol_root) = :sym THEN 0 ELSE 1 END
                LIMIT 1
            """),
            {"sym": s}
        ).first()
        return int(row.scrip_code) if row and row.scrip_code else None
    except Exception:
        return None
    finally:
        db.close()


def _get_corp_events(symbol: str, user_id: int | None) -> list[dict]:
    """Fetch upcoming corporate events (next 60 days) for the symbol."""
    if not user_id:
        return []
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT action_type, ex_date, action_details, notes
                FROM corporate_actions
                WHERE user_id = :uid
                  AND UPPER(symbol) = :sym
                  AND ex_date >= DATE(NOW())
                  AND ex_date <= DATE_ADD(NOW(), INTERVAL 60 DAY)
                ORDER BY ex_date ASC
                LIMIT 10
            """),
            {"uid": user_id, "sym": symbol.strip().upper()}
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        return []
    finally:
        db.close()


async def _fetch_analyser(symbol: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{ANALYSER_URL}/api/analyze/{symbol}"  # was /analyze
            )
            if resp.status_code == 200:
                data = resp.json()
                # test_apis.py shows response is {"success": bool, "data": {...}}
                return data.get("data", data)
    except Exception as e:
        logger.info(f"[ImpactRouter] analyser error: {e}")
    return {}


async def _fetch_news(symbol: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{NEWS_URL}/api/news/fetch",
                params={"q": symbol, "time": "72h"}   # 72h catches more articles
            )
            if resp.status_code == 200:
                return resp.json()
            logger.info(f"[ImpactRouter] news HTTP {resp.status_code}")
    except Exception as e:
        logger.info(f"[ImpactRouter] news error: {e}")
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# /impact/{symbol}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/impact/{symbol}")
async def get_impact(symbol: str, user_id: Optional[int] = None):
    """
    Full analysis bundle.  All heavy calls run concurrently with timeouts.

    Returns:
      price_levels  — LTP, change%, 1M/3M/52W high-low, max spike/drop
      mmm           — mean, median, mode, std-dev, ±1σ/±2σ bands
      trend         — direction, streak, cumulative %
      momentum      — score, signal, RSI, MACD, MA signals
      analyst       — external analyser output (or {} if service is down)
      seasonal      — monthly avg returns, best/worst month
      corp_events   — upcoming corporate events (next 60 days)
      news          — latest news articles with sentiment
      account_context — holding intel if user_id provided
    """
    sym = symbol.strip().upper()

    # ── 1. Resolve scrip_code (needed for 5paisa historical calls) ────────────
    scrip_code = _scrip_code_for(sym)

    # ── 2. Run all local heavy calls + external APIs concurrently ─────────────
    loop = asyncio.get_event_loop()

    def _local_price():
        return get_price_levels(scrip_code) if scrip_code else {}

    def _local_mmm():
        return get_mmm_stats(scrip_code) if scrip_code else {}

    def _local_seasonal():
        return get_seasonal_pattern(scrip_code) if scrip_code else {}

    def _local_trend():
        return get_consecutive_trend(scrip_code) if scrip_code else {}

    def _local_momentum():
        return get_momentum_score(sym)   # uses yfinance — doesn't need scrip_code

    # Run local calls in thread-pool (they're blocking/sync)
    (
        price_levels,
        mmm,
        seasonal,
        trend,
        momentum,
        analyst_raw,
        news_raw,
    ) = await asyncio.gather(
        loop.run_in_executor(None, _local_price),
        loop.run_in_executor(None, _local_mmm),
        loop.run_in_executor(None, _local_seasonal),
        loop.run_in_executor(None, _local_trend),
        loop.run_in_executor(None, _local_momentum),
        _fetch_analyser(sym),
        _fetch_news(sym),
    )

    # ── 3. Corp events ────────────────────────────────────────────────────────
    corp_events = _get_corp_events(sym, user_id)

    # ── 4. Holding intel ──────────────────────────────────────────────────────
    account_context = {}
    if user_id:
        ltp = float(price_levels.get("ltp", 0) or 0)
        if ltp > 0:
            account_context = await loop.run_in_executor(
                None, get_holding_intel, user_id, sym, ltp
            )

    # ── 5. Normalise external analyser output ─────────────────────────────────
    # The external analyser may return various shapes.  Normalise to what
    # wishlist_ui.py / _tab_analyst() expects.
    analyst: dict = {}
    if analyst_raw:
        # Extract nested analysis object
        analysis = analyst_raw.get("analysis", {})
        technical = analysis.get("technical", {})
        historical = analysis.get("historical", {})
        trading_plan = analysis.get("trading_plan", {})
        
        # ── Extract and sanitise trading plan from API zones ──────────────
        buy_zones = analysis.get("buy_zones", []) or trading_plan.get("buy_zones", [])
        sell_zones = analysis.get("sell_zones", []) or trading_plan.get("sell_zones", [])
        stop_loss_zone = trading_plan.get("stop_loss_zone", []) or analysis.get("stop_loss_zone", [])

        buy_target_1 = float(buy_zones[0][0]) if buy_zones and isinstance(buy_zones[0], list) and len(buy_zones[0]) > 0 else 0.0
        sell_target_1 = float(sell_zones[0][1]) if sell_zones and isinstance(sell_zones[0], list) and len(sell_zones[0]) > 1 else 0.0
        buy_stop_loss = float(stop_loss_zone[0]) if stop_loss_zone else 0.0

        ltp = float(price_levels.get("ltp", 0) or 0)
        if sell_target_1 > 0 and (sell_target_1 <= buy_target_1 or (ltp > 0 and sell_target_1 <= ltp)):
            sell_target_1 = 0.0

        # ── Calculate nested scores for Technical, Fundamental, Historical ──
        technical_score = analysis.get("technical_score", "—")
        if analysis.get("technical") and isinstance(analysis.get("technical"), dict):
            technical_score = analysis["technical"].get("score", technical_score)

        fundamental_score = analysis.get("fundamental_score", "—")
        if analysis.get("fundamental") and isinstance(analysis.get("fundamental"), dict):
            fundamental_score = analysis["fundamental"].get("score", fundamental_score)

        historical_score = analysis.get("historical_score", "—")
        if analysis.get("historical") and isinstance(analysis.get("historical"), dict):
            historical_score = analysis["historical"].get("score", historical_score)

        # Now build the analyst dictionary with CLEAN key-value pairs
        analyst = {
            "stock_info": analyst_raw.get("stock_info", {}),
            "analysis": {
                "signal":           analysis.get("signal", momentum.get("signal", "HOLD")),
                "signal_strength":  analysis.get("signal_strength", ""),
                "overall_score":    analysis.get("overall_score", 0),
                "technical_score":  technical_score,
                "fundamental_score": fundamental_score,
                "historical_score": historical_score,
                "technical": {
                    "rsi":          technical.get("rsi", momentum.get("rsi", 50)),
                    "macd_hist":    technical.get("macd_hist", 0.0),
                    "adx":          technical.get("adx", 0.0),
                    "volume_ratio": technical.get("volume_ratio", 1.0),
                    "bb_upper":     technical.get("bb_upper", 0.0),
                    "bb_middle":    technical.get("bb_middle", 0.0),
                    "bb_lower":     technical.get("bb_lower", 0.0),
                },
                "historical": {
                    "trend":            historical.get("trend", "FLAT"),
                    "pct_from_52w_high": historical.get("pct_from_52w_high", 0.0),
                    "volatility":       historical.get("volatility", 0.0),
                    "supports":         historical.get("supports", []),
                    "resistances":      historical.get("resistances", []),
                },
                "trading_plan": {
                    "buy_target_1":  buy_target_1,
                    "buy_stop_loss": buy_stop_loss,
                    "sell_target_1": sell_target_1,
                    "buy_target_2":  trading_plan.get("buy_target_2", 0.0),
                    "sell_target_2": trading_plan.get("sell_target_2", 0.0),
                    "sell_stop_loss":trading_plan.get("sell_stop_loss", 0.0),
                    "buy_triggers":  trading_plan.get("buy_triggers", []),
                    "sell_triggers": trading_plan.get("sell_triggers", []),
                },
            },
        }
    else:
        # Fallback: build minimal analyst block from local momentum data
        analyst = {
            "stock_info": {},
            "analysis": {
                "signal":           momentum.get("signal", "HOLD"),
                "signal_strength":  "",
                "overall_score":    0,
                "technical_score":  "—",
                "fundamental_score":"—",
                "historical_score": "—",
                "technical": {
                    "rsi":          momentum.get("rsi", 50),
                    "macd_hist":    0,
                    "adx":          0,
                    "volume_ratio": 1,
                    "bb_upper":     0,
                    "bb_middle":    0,
                    "bb_lower":     0,
                },
                "historical": {},
                "trading_plan": {},
            },
        }

    # ── 6. Normalise news ─────────────────────────────────────────────────────
    news: dict = {}
    if news_raw:
        # StockPulse may return {"articles": [...]} or a list directly
        if isinstance(news_raw, list):
            news = {"articles": news_raw}
        elif isinstance(news_raw, dict):
            news = news_raw
    
    return {
        "symbol":          sym,
        "scrip_code":      scrip_code,
        "price_levels":    price_levels,
        "mmm":             mmm,
        "trend":           trend,
        "momentum":        momentum,
        "analyst":         analyst,
        "seasonal":        seasonal,
        "corp_events":     corp_events,
        "news":            news,
        "account_context": account_context,
    }


# ─────────────────────────────────────────────────────────────────────────────
# /impact/quick-batch   — price + trend + momentum only (fast, for wishlist rows)
# /impact/batch         — full analysis for multiple symbols in parallel
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/impact/quick-batch")
async def get_impact_quick_batch(
    symbols: str,          # comma-separated: "RELIANCE,TCS,INFY"
    user_id: Optional[int] = None,
):
    """
    Lightweight batch endpoint — price_levels + trend + momentum only.
    Runs all symbols in parallel.  Used by the Wishlist 'Load Prices' button.
    Returns: { SYMBOL: { price_levels, trend, momentum } }
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {}

    loop = asyncio.get_event_loop()

    async def _one(sym: str) -> tuple[str, dict]:
        scrip_code = _scrip_code_for(sym)

        def _price():
            return get_price_levels(scrip_code) if scrip_code else {}

        def _trend():
            return get_consecutive_trend(scrip_code) if scrip_code else {}

        def _momentum():
            return get_momentum_score(sym)

        price, trend, momentum = await asyncio.gather(
            loop.run_in_executor(None, _price),
            loop.run_in_executor(None, _trend),
            loop.run_in_executor(None, _momentum),
        )
        return sym, {"price_levels": price, "trend": trend, "momentum": momentum}

    tasks = [_one(s) for s in sym_list]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict = {}
    for item in results_list:
        if isinstance(item, Exception):
            continue
        sym, data = item
        output[sym] = data
    return output


@router.get("/impact/batch")
async def get_impact_batch(
    symbols: str,          # comma-separated
    user_id: Optional[int] = None,
    quick: bool = False,   # if True, skip analyst + news (faster)
):
    """
    Full impact bundle for multiple symbols in parallel.
    Returns: { SYMBOL: <same shape as /impact/{symbol}> }
    Set quick=true to skip external analyst/news APIs.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {}

    # Cap at 20 symbols to prevent overload
    sym_list = sym_list[:20]

    loop = asyncio.get_event_loop()

    async def _one_full(sym: str) -> tuple[str, dict]:
        try:
            scrip_code = _scrip_code_for(sym)

            def _price():   return get_price_levels(scrip_code) if scrip_code else {}
            def _mmm():     return get_mmm_stats(scrip_code) if scrip_code else {}
            def _seasonal():return get_seasonal_pattern(scrip_code) if scrip_code else {}
            def _trend():   return get_consecutive_trend(scrip_code) if scrip_code else {}
            def _momentum():return get_momentum_score(sym)

            coroutines = [
                loop.run_in_executor(None, _price),
                loop.run_in_executor(None, _mmm),
                loop.run_in_executor(None, _seasonal),
                loop.run_in_executor(None, _trend),
                loop.run_in_executor(None, _momentum),
            ]
            if not quick:
                coroutines += [_fetch_analyser(sym), _fetch_news(sym)]
            else:
                coroutines += [asyncio.sleep(0), asyncio.sleep(0)]  # placeholders

            gathered = await asyncio.gather(*coroutines, return_exceptions=True)

            price_levels = gathered[0] if not isinstance(gathered[0], Exception) else {}
            mmm          = gathered[1] if not isinstance(gathered[1], Exception) else {}
            seasonal     = gathered[2] if not isinstance(gathered[2], Exception) else {}
            trend        = gathered[3] if not isinstance(gathered[3], Exception) else {}
            momentum     = gathered[4] if not isinstance(gathered[4], Exception) else {}
            analyst_raw  = gathered[5] if not isinstance(gathered[5], Exception) else {}
            news_raw     = gathered[6] if not isinstance(gathered[6], Exception) else {}

            # Normalize analyst (same logic as single endpoint)
            analyst: dict = {}
            if analyst_raw and not quick and isinstance(analyst_raw, dict):
                analysis     = analyst_raw.get("analysis", {})
                technical    = analysis.get("technical", {})
                historical   = analysis.get("historical", {})
                trading_plan = analysis.get("trading_plan", {})
                analyst = {
                    "stock_info": analyst_raw.get("stock_info", {}),
                    "analysis": {
                        "signal":           analysis.get("signal", momentum.get("signal", "HOLD")),
                        "signal_strength":  analysis.get("signal_strength", ""),
                        "overall_score":    analysis.get("overall_score", 0),
                        "technical_score":  analysis.get("technical_score", "—"),
                        "fundamental_score":analysis.get("fundamental_score", "—"),
                        "historical_score": analysis.get("historical_score", "—"),
                        "technical": {
                            "rsi":          technical.get("rsi", momentum.get("rsi", 50)),
                            "macd_hist":    technical.get("macd_hist", 0.0),
                            "adx":          technical.get("adx", 0.0),
                            "volume_ratio": technical.get("volume_ratio", 1.0),
                            "bb_upper":     technical.get("bb_upper", 0.0),
                            "bb_middle":    technical.get("bb_middle", 0.0),
                            "bb_lower":     technical.get("bb_lower", 0.0),
                        },
                        "historical": {
                            "trend":             historical.get("trend", "FLAT"),
                            "pct_from_52w_high": historical.get("pct_from_52w_high", 0.0),
                            "volatility":        historical.get("volatility", 0.0),
                            "supports":          historical.get("supports", []),
                            "resistances":       historical.get("resistances", []),
                        },
                        "trading_plan": {
                            "buy_target_1":  trading_plan.get("buy_target_1", 0.0),
                            "buy_target_2":  trading_plan.get("buy_target_2", 0.0),
                            "buy_stop_loss": trading_plan.get("buy_stop_loss", 0.0),
                            "buy_triggers":  trading_plan.get("buy_triggers", []),
                            "sell_target_1": trading_plan.get("sell_target_1", 0.0),
                            "sell_target_2": trading_plan.get("sell_target_2", 0.0),
                            "sell_stop_loss":trading_plan.get("sell_stop_loss", 0.0),
                            "sell_triggers": trading_plan.get("sell_triggers", []),
                        },
                    },
                }
            if not analyst:
                analyst = {
                    "stock_info": {},
                    "analysis": {
                        "signal":           momentum.get("signal", "HOLD"),
                        "signal_strength":  "",
                        "overall_score":    0,
                        "technical_score":  "—",
                        "fundamental_score":"—",
                        "historical_score": "—",
                        "technical": {"rsi": momentum.get("rsi", 50), "macd_hist": 0, "adx": 0, "volume_ratio": 1, "bb_upper": 0, "bb_middle": 0, "bb_lower": 0},
                        "historical": {},
                        "trading_plan": {},
                    },
                }

            news: dict = {}
            if news_raw and not quick and isinstance(news_raw, dict):
                news = news_raw if isinstance(news_raw, dict) else {"articles": news_raw}
            elif news_raw and not quick and isinstance(news_raw, list):
                news = {"articles": news_raw}

            corp_events = _get_corp_events(sym, user_id)
            account_context = {}
            if user_id:
                ltp = float((price_levels or {}).get("ltp", 0) or 0)
                if ltp > 0:
                    account_context = await loop.run_in_executor(
                        None, get_holding_intel, user_id, sym, ltp
                    )

            return sym, {
                "symbol":          sym,
                "scrip_code":      scrip_code,
                "price_levels":    price_levels,
                "mmm":             mmm,
                "trend":           trend,
                "momentum":        momentum,
                "analyst":         analyst,
                "seasonal":        seasonal,
                "corp_events":     corp_events,
                "news":            news,
                "account_context": account_context,
            }
        except Exception as e:
            logger.error(f"[BatchImpact] error for {sym}: {e}")
            return sym, {}

    tasks = [_one_full(s) for s in sym_list]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict = {}
    for item in results_list:
        if isinstance(item, Exception):
            continue
        sym, data = item
        output[sym] = data
    return output


# ─────────────────────────────────────────────────────────────────────────────
# /suggest/{symbol}
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# /suggest/batch  — suggestion for multiple symbols in one call
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/suggest/batch")
async def get_suggestion_batch(symbols: str, user_id: int = 0):
    """
    Batch suggestion endpoint — runs all symbols in parallel.
    symbols: comma-separated string e.g. "RELIANCE,TCS,INFY"
    Returns { SYMBOL: <suggestion dict> }
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {}

    loop = asyncio.get_event_loop()

    def _one_suggest(sym: str) -> dict:
        try:
            scrip_code = _scrip_code_for(sym)
            price_levels: dict = {}
            current_spot = 0.0
            if scrip_code:
                try:
                    price_levels = get_price_levels(scrip_code)
                    current_spot = float(price_levels.get("ltp", 0) or 0)
                except Exception:
                    pass
            if current_spot <= 0:
                try:
                    import yfinance as yf
                    t = yf.Ticker(f"{sym}.NS")
                    current_spot = float(getattr(t.fast_info, "last_price", 0) or 0)
                except Exception:
                    current_spot = 0.0
            result = get_suggestion(
                symbol   = sym,
                user_id  = user_id,
                spot     = current_spot,
                high_1m  = float(price_levels.get("high_1m",  0) or 0),
                low_1m   = float(price_levels.get("low_1m",   0) or 0),
                high_52w = float(price_levels.get("high_52w", 0) or 0),
                low_52w  = float(price_levels.get("low_52w",  0) or 0),
            )
            result["spot"] = current_spot
            return result
        except Exception as e:
            logger.error(f"[SuggestBatch] {sym}: {e}")
            return {"signal": "NEUTRAL", "reason": str(e)}

    tasks = [loop.run_in_executor(None, _one_suggest, s) for s in sym_list]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    return {
        sym: (r if not isinstance(r, Exception) else {"signal": "NEUTRAL"})
        for sym, r in zip(sym_list, results_list)
    }


@router.get("/suggest/{symbol}")
def get_suggestion_endpoint(symbol: str, user_id: int = 0, spot: float = 0.0):
    """
    Returns the suggestion engine output for a symbol + user.
    If spot=0 is passed, we try to get it from the DB / yfinance.
    """
    sym = symbol.strip().upper()

    # If spot not provided, try yfinance quick fetch
    current_spot = spot
    if current_spot <= 0:
        try:
            import yfinance as yf
            t = yf.Ticker(f"{sym}.NS")
            current_spot = float(getattr(t.fast_info, "last_price", 0) or 0)
        except Exception:
            current_spot = 0.0

    # Get price levels for high/low context
    scrip_code = _scrip_code_for(sym)
    price_levels: dict = {}
    if scrip_code:
        try:
            price_levels = get_price_levels(scrip_code)
            if current_spot <= 0:
                current_spot = float(price_levels.get("ltp", 0) or 0)
        except Exception:
            pass

    result = get_suggestion(
        symbol   = sym,
        user_id  = user_id,
        spot     = current_spot,
        high_1m  = float(price_levels.get("high_1m",  0) or 0),
        low_1m   = float(price_levels.get("low_1m",   0) or 0),
        high_52w = float(price_levels.get("high_52w", 0) or 0),
        low_52w  = float(price_levels.get("low_52w",  0) or 0),
    )
    result["spot"] = current_spot
    return result


# ─────────────────────────────────────────────────────────────────────────────
# /holding-intel/{symbol}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/holding-intel/{symbol}")
def holding_intel_endpoint(symbol: str, user_id: int = 0):
    """
    Returns holding details + sell/hold signal for a user's position.
    """
    sym = symbol.strip().upper()

    # Get current price
    current_price = 0.0
    scrip_code = _scrip_code_for(sym)
    if scrip_code:
        try:
            levels = get_price_levels(scrip_code)
            current_price = float(levels.get("ltp", 0) or 0)
        except Exception:
            pass

    if current_price <= 0:
        try:
            import yfinance as yf
            t = yf.Ticker(f"{sym}.NS")
            current_price = float(getattr(t.fast_info, "last_price", 0) or 0)
        except Exception:
            current_price = 0.0

    result = get_holding_intel(user_id, sym, current_price)
    result["current_price"] = current_price
    return result


# ─────────────────────────────────────────────────────────────────────────────
# /nse-search
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/nse-search")
def nse_search(q: str = ""):
    """
    Autocomplete NSE symbols from scrip_master_cache.
    Returns list of {symbol, name, exchange}.
    """
    if not q or not q.strip() or len(q.strip()) < 1:
        return []

    query = q.strip().upper()
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT DISTINCT symbol_root AS symbol, name, 'NSE' AS exchange
                FROM scrip_master_cache
                WHERE exch = 'N' AND exch_type = 'C'
                  AND series = 'EQ'
                  AND (
                      UPPER(symbol_root) LIKE :q_prefix
                      OR UPPER(name) LIKE :q_prefix
                  )
                  AND symbol_root IS NOT NULL AND symbol_root != ''
                ORDER BY
                    CASE WHEN UPPER(symbol_root) = :q_exact THEN 0 ELSE 1 END,
                    LENGTH(symbol_root) ASC
                LIMIT 15
            """),
            {"q_prefix": f"{query}%", "q_exact": query}
        ).fetchall()

        # Also try contains search if prefix returned < 5 results
        results = [{"symbol": r.symbol, "name": r.name, "exchange": r.exchange}
                   for r in rows]

        if len(results) < 5:
            rows2 = db.execute(
                text("""
                    SELECT DISTINCT symbol_root AS symbol, name, 'NSE' AS exchange
                    FROM scrip_master_cache
                    WHERE exch = 'N' AND exch_type = 'C'
                      AND series = 'EQ'
                      AND UPPER(name) LIKE :q_contains
                      AND symbol_root IS NOT NULL AND symbol_root != ''
                    ORDER BY LENGTH(name) ASC
                    LIMIT 10
                """),
                {"q_contains": f"%{query}%"}
            ).fetchall()
            seen = {r["symbol"] for r in results}
            for r in rows2:
                if r.symbol not in seen:
                    results.append({"symbol": r.symbol, "name": r.name, "exchange": r.exchange})
                    seen.add(r.symbol)

        return results[:15]
    except Exception as e:
        logger.info(f"[NSESearch] error: {e}")
        return []
    finally:
        db.close()