from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import SessionLocal
from sqlalchemy.orm import Session
from sqlalchemy import text
from services.group_service import (
    create_group, list_groups, add_member, remove_member,
    get_group_members, get_group_holdings
)
from dependencies.auth import get_current_account

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
def new_group(
    data: GroupCreate,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    try:
        return create_group(data.name, account_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/groups/")
def all_groups(
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    return list_groups(account_id)

@router.post("/groups/{group_id}/members/{user_id}")
def add_member_endpoint(
    group_id: int,
    user_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    try:
        return add_member(group_id, user_id, account_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.delete("/groups/{group_id}/members/{user_id}")
def remove_member_endpoint(
    group_id: int,
    user_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    try:
        return remove_member(group_id, user_id, account_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get("/groups/{group_id}/members")
def members(
    group_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    return get_group_members(group_id, account_id)

@router.get("/groups/{group_id}/holdings")
def group_holdings(
    group_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    return get_group_holdings(group_id, account_id)

@router.delete("/groups/{group_id}")
def delete_group(
    group_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    # Verify ownership
    group = db.execute(
        text("SELECT id FROM user_groups WHERE id = :gid AND account_id = :aid"),
        {"gid": group_id, "aid": account_id}
    ).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    db.execute(text("DELETE FROM user_groups WHERE id = :gid"), {"gid": group_id})
    db.execute(text("DELETE FROM group_members WHERE group_id = :gid"), {"gid": group_id})
    db.commit()
    return {"status": "deleted"}