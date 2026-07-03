"""
routers/wishlist.py
===================
FastAPI router exposing wishlist endpoints for single-user and group modes.

Mount in main.py:
    from routers import wishlist
    app.include_router(wishlist.router, prefix="/api/v1")
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from database import SessionLocal
from services.wishlist_service import (
    # Single user
    get_wishlist,
    add_to_wishlist,
    remove_from_wishlist,
    sync_from_holdings,
    clear_auto_added,
    clear_all,
    # Group
    get_group_wishlist,
    add_to_group_wishlist,
    remove_from_group_wishlist,
    sync_group_from_holdings,
    clear_group_auto_added,
    clear_group_all,
)

router = APIRouter(tags=["Wishlist"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class WishlistAdd(BaseModel):
    symbol: str
    canonical_symbol: Optional[str] = ""
    is_auto_added:    Optional[bool] = False
    notes:            Optional[str]  = ""


# ─────────────────────────────────────────────────────────────────────────────
# Single-user endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/wishlist/{user_id}")
def get_user_wishlist(user_id: int):
    """Return all symbols in this user's wishlist."""
    return get_wishlist(user_id)


@router.post("/wishlist/{user_id}/add")
def add_user_symbol(user_id: int, data: WishlistAdd):
    """Add a symbol to the user's wishlist."""
    return add_to_wishlist(
        user_id,
        data.symbol,
        data.canonical_symbol or "",
        data.is_auto_added or False,
        data.notes or "",
    )


@router.delete("/wishlist/{user_id}/symbol/{symbol}")
def remove_user_symbol(user_id: int, symbol: str):
    """Remove a specific symbol from the user's wishlist."""
    return remove_from_wishlist(user_id, symbol)


@router.post("/wishlist/{user_id}/sync")
def sync_user_wishlist(user_id: int):
    """
    Auto-populate the user's wishlist from current holdings +
    open / historical F&O positions.  Only adds rows; does not remove.
    """
    return sync_from_holdings(user_id)


@router.delete("/wishlist/{user_id}/clear-auto")
def clear_user_auto(user_id: int):
    """Remove all auto-synced symbols; keep manually added ones."""
    return clear_auto_added(user_id)


@router.delete("/wishlist/{user_id}/clear-all")
def clear_user_all(user_id: int):
    """Remove ALL symbols from this user's wishlist."""
    return clear_all(user_id)


# ─────────────────────────────────────────────────────────────────────────────
# Group endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/wishlist/group/{group_id}")
def get_grp_wishlist(group_id: int):
    """Return all symbols in the group's wishlist."""
    return get_group_wishlist(group_id)


@router.post("/wishlist/group/{group_id}/add")
def add_grp_symbol(group_id: int, data: WishlistAdd):
    """Add a symbol to the group's wishlist."""
    return add_to_group_wishlist(
        group_id,
        data.symbol,
        data.canonical_symbol or "",
        data.is_auto_added or False,
        data.notes or "",
    )


@router.delete("/wishlist/group/{group_id}/symbol/{symbol}")
def remove_grp_symbol(group_id: int, symbol: str):
    """Remove a specific symbol from the group's wishlist."""
    return remove_from_group_wishlist(group_id, symbol)


@router.post("/wishlist/group/{group_id}/sync")
def sync_grp_wishlist(group_id: int):
    """
    Auto-populate the group's wishlist from all members'
    holdings + open / historical F&O positions.
    """
    return sync_group_from_holdings(group_id)


@router.delete("/wishlist/group/{group_id}/clear-auto")
def clear_grp_auto(group_id: int):
    """Remove auto-synced symbols from the group wishlist."""
    return clear_group_auto_added(group_id)


@router.delete("/wishlist/group/{group_id}/clear-all")
def clear_grp_all(group_id: int):
    """Remove ALL symbols from the group's wishlist."""
    return clear_group_all(group_id)