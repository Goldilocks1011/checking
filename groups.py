from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.database import SessionLocal
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.services.group_service import (
    create_group,
    list_groups,
    add_member,
    remove_member,
    get_group_members,
    get_group_holdings,
)
from backend.dependencies.auth import get_current_account
from backend.services.task_status import start_task, finish_task
import asyncio

router = APIRouter(tags=["Groups"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class GroupCreate(BaseModel):
    name: str


@router.post("/groups/")
async def new_group(
    data: GroupCreate,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    try:
        return await asyncio.to_thread(create_group, data.name, account_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/groups/")
async def all_groups(
    account_id: int = Depends(get_current_account), db: Session = Depends(get_db)
):
    return await asyncio.to_thread(list_groups, account_id)


@router.post("/groups/{group_id}/members/{user_id}")
async def add_member_endpoint(
    group_id: int,
    user_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    try:
        return await asyncio.to_thread(add_member, group_id, user_id, account_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.delete("/groups/{group_id}/members/{user_id}")
async def remove_member_endpoint(
    group_id: int,
    user_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    try:
        return await asyncio.to_thread(remove_member, group_id, user_id, account_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/groups/{group_id}/members")
async def members(
    group_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    return await asyncio.to_thread(get_group_members, group_id, account_id)


@router.get("/groups/{group_id}/holdings")
async def group_holdings(
    group_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    return await asyncio.to_thread(get_group_holdings, group_id, account_id)


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    if not start_task(account_id, f"delete_group_{group_id}", "Deleting group..."):
        return {"status": "busy", "message": "This group is already being deleted."}

    def _do_delete():
        group = db.execute(
            text("SELECT id FROM user_groups WHERE id = :gid AND account_id = :aid"),
            {"gid": group_id, "aid": account_id},
        ).first()
        if not group:
            return None
        db.execute(text("DELETE FROM user_groups WHERE id = :gid"), {"gid": group_id})
        db.execute(
            text("DELETE FROM group_members WHERE group_id = :gid"), {"gid": group_id}
        )
        db.commit()
        return True

    try:
        result = await asyncio.to_thread(_do_delete)
        finish_task(account_id, f"delete_group_{group_id}")
        if result is None:
            raise HTTPException(status_code=404, detail="Group not found")
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        finish_task(account_id, f"delete_group_{group_id}", error=str(e))
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
