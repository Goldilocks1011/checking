from fastapi import APIRouter, Query
from typing import List
import asyncio
from backend.services.price_service import fetch_current_prices

router = APIRouter(tags=["Prices"])


@router.get("/prices")
async def get_prices(symbols: List[str] = Query(...)):
    prices = await asyncio.to_thread(fetch_current_prices, symbols)
    return prices


from backend.services.engine_price_fetch import (
    fetch_prices_with_change as _fetch_with_change,
)


@router.get("/prices/with-change")
async def get_prices_with_change(symbols: List[str] = Query(...)):
    return await asyncio.to_thread(_fetch_with_change, symbols)
