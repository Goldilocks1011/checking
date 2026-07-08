from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.models import User
from pydantic import BaseModel
from sqlalchemy import text
from backend.dependencies.auth import get_current_account
from backend.services.task_status import start_task, finish_task
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Users"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class UserCreate(BaseModel):
    username: str
    broker: str


class UserOut(BaseModel):
    id: int
    username: str
    broker: str
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("/users/", response_model=UserOut)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    new_user = User(username=user.username, broker=user.broker)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/users/")
def list_users(
    account_id: int = Depends(get_current_account), db: Session = Depends(get_db)
):
    return db.query(User).filter(User.account_id == account_id).all()


# ── All tables that may reference user_id. Each one is deleted in its own
#    try/except so that a missing table, a stale FK, or one bad row never
#    aborts the whole operation and turns into an unhandled 500. ──────────
_USER_CLEANUP_TABLES = [
    "user_stock_symbol_mapping",
    "transactions",
    "holdings",
    "pnl",
    "intraday",
    "fno_transactions",
    "fno_open_positions",
    "fno_pnl",
    "fno_synthetic_transactions",
    "fno_dividend_adjustments",
    "unmatched_symbols",
    "ledger_entries",
    "ledger_period_summaries",
    "corporate_actions",
    "group_members",  # references user_id
    "wishlist",  # if this table exists in your schema
    "wishlist_items",  # covers either naming — harmless if missing
]


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """
    Cascading delete for a user, hardened so it can never 500:
      - each cleanup statement runs in its own try/except
      - a failure on one table is logged and skipped, not fatal
      - a task-status guard stops two delete clicks from racing
    """
    if not start_task(user_id, "delete_user", "Deleting user and related data..."):
        return {
            "status": "busy",
            "message": "A delete is already in progress for this user.",
        }

    errors: list[str] = []
    try:
        # Verify user exists first — gives a clean 404 instead of a
        # silent no-op delete.
        existing = db.execute(
            text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id}
        ).first()
        if not existing:
            finish_task(user_id, "delete_user", error="User not found")
            raise HTTPException(status_code=404, detail="User not found")

        for table in _USER_CLEANUP_TABLES:
            try:
                db.execute(
                    text(f"DELETE FROM {table} WHERE user_id = :uid"), {"uid": user_id}
                )
                db.commit()
            except Exception as e:
                db.rollback()
                msg = f"Cleanup skipped for table '{table}': {e}"
                logger.warning(msg)
                errors.append(msg)

        # Finally remove the user row itself
        try:
            db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
            db.commit()
        except Exception as e:
            db.rollback()
            finish_task(user_id, "delete_user", error=str(e))
            raise HTTPException(
                status_code=500, detail=f"Could not delete user row: {e}"
            )

        finish_task(user_id, "delete_user")
        return {
            "status": "deleted",
            "warnings": errors if errors else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        finish_task(user_id, "delete_user", error=str(e))
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
