from fastapi import APIRouter, Query
from typing import List
from services.price_service import fetch_current_prices

router = APIRouter(tags=["Prices"])

@router.get("/prices")
def get_prices(symbols: List[str] = Query(...)):
    prices = fetch_current_prices(symbols)
    return prices

from services.engine_price_fetch import fetch_prices_with_change as _fetch_with_change

@router.get("/prices/with-change")
def get_prices_with_change(symbols: List[str] = Query(...)):
    return _fetch_with_change(symbols)