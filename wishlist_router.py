from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.services.wishlist_service import (
    get_wishlist,
    add_to_wishlist,
    remove_from_wishlist,
    sync_from_holdings,
    clear_auto_added,
    clear_all,
    get_group_wishlist,
    add_to_group_wishlist,
    remove_from_group_wishlist,
    sync_group_from_holdings,
    clear_group_auto_added,
    clear_group_all,
)
from backend.services.task_status import start_task, finish_task
import asyncio

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
    is_auto_added: Optional[bool] = False
    notes: Optional[str] = ""


@router.get("/wishlist/{user_id}")
async def get_user_wishlist(user_id: int):
    return await asyncio.to_thread(get_wishlist, user_id)


@router.post("/wishlist/{user_id}/add")
async def add_user_symbol(user_id: int, data: WishlistAdd):
    return await asyncio.to_thread(
        add_to_wishlist,
        user_id,
        data.symbol,
        data.canonical_symbol or "",
        data.is_auto_added or False,
        data.notes or "",
    )


@router.delete("/wishlist/{user_id}/symbol/{symbol}")
async def remove_user_symbol(user_id: int, symbol: str):
    return await asyncio.to_thread(remove_from_wishlist, user_id, symbol)


@router.post("/wishlist/{user_id}/sync")
async def sync_user_wishlist(user_id: int):
    """
    Auto-populate from holdings + open/historical F&O positions.
    Guarded so double-clicking Sync doesn't launch overlapping jobs.
    """
    if not start_task(user_id, "wishlist_sync", "Syncing wishlist from holdings..."):
        return {"status": "busy", "message": "Wishlist sync already in progress."}
    try:
        result = await asyncio.to_thread(sync_from_holdings, user_id)
        finish_task(user_id, "wishlist_sync")
        return result
    except Exception as e:
        finish_task(user_id, "wishlist_sync", error=str(e))
        return {"status": "error", "message": str(e)}


@router.delete("/wishlist/{user_id}/clear-auto")
async def clear_user_auto(user_id: int):
    return await asyncio.to_thread(clear_auto_added, user_id)


@router.delete("/wishlist/{user_id}/clear-all")
async def clear_user_all(user_id: int):
    return await asyncio.to_thread(clear_all, user_id)


@router.get("/wishlist/group/{group_id}")
async def get_grp_wishlist(group_id: int):
    return await asyncio.to_thread(get_group_wishlist, group_id)


@router.post("/wishlist/group/{group_id}/add")
async def add_grp_symbol(group_id: int, data: WishlistAdd):
    return await asyncio.to_thread(
        add_to_group_wishlist,
        group_id,
        data.symbol,
        data.canonical_symbol or "",
        data.is_auto_added or False,
        data.notes or "",
    )


@router.delete("/wishlist/group/{group_id}/symbol/{symbol}")
async def remove_grp_symbol(group_id: int, symbol: str):
    return await asyncio.to_thread(remove_from_group_wishlist, group_id, symbol)


@router.post("/wishlist/group/{group_id}/sync")
async def sync_grp_wishlist(group_id: int):
    if not start_task(group_id, "group_wishlist_sync", "Syncing group wishlist..."):
        return {"status": "busy", "message": "Group wishlist sync already in progress."}
    try:
        result = await asyncio.to_thread(sync_group_from_holdings, group_id)
        finish_task(group_id, "group_wishlist_sync")
        return result
    except Exception as e:
        finish_task(group_id, "group_wishlist_sync", error=str(e))
        return {"status": "error", "message": str(e)}


@router.delete("/wishlist/group/{group_id}/clear-auto")
async def clear_grp_auto(group_id: int):
    return await asyncio.to_thread(clear_group_auto_added, group_id)


@router.delete("/wishlist/group/{group_id}/clear-all")
async def clear_grp_all(group_id: int):
    return await asyncio.to_thread(clear_group_all, group_id)
