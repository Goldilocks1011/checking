from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from backend.services.auth_service import (
    create_account,
    verify_account,
    create_access_token,
    change_password,
    update_display_name,
    create_password_reset_token,
    verify_reset_token,
)
from backend.dependencies.auth import get_current_account
from backend.database import SessionLocal
from backend.models import Account, User
from sqlalchemy.orm import Session
from backend.services.auth_service import hash_password
from backend.services.email_service import send_password_reset_email
import asyncio

import os
import logging

logger = logging.getLogger(__name__)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")

router = APIRouter(tags=["Authentication"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class ProfileUpdate(BaseModel):
    display_name: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class PortfolioUserCreate(BaseModel):
    username: str
    broker: str


@router.post("/portfolio-users")
async def create_portfolio_user(
    data: PortfolioUserCreate,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    def _do():
        new_user = User(
            username=data.username, broker=data.broker, account_id=account_id
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return {"id": new_user.id, "username": new_user.username}

    return await asyncio.to_thread(_do)


@router.post("/auth/signup")
async def signup(data: SignupRequest, db: Session = Depends(get_db)):
    def _do():
        existing = db.query(Account).filter(Account.email == data.email).first()
        if existing:
            return {"__error__": "Email already registered"}
        account = create_account(data.email, data.password, data.display_name)
        return {"message": "Account created", "account_id": account.id}

    # bcrypt hashing inside create_account is CPU-blocking — run in a thread
    result = await asyncio.to_thread(_do)
    if "__error__" in result:
        raise HTTPException(status_code=400, detail=result["__error__"])
    return result


@router.post("/auth/login")
async def login(data: LoginRequest):
    # bcrypt password check is CPU-blocking — run in a thread so it never
    # freezes the server for other logged-in users
    account = await asyncio.to_thread(verify_account, data.email, data.password)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(account.id)})
    return {
        "access_token": token,
        "token_type": "bearer",
        "display_name": account.display_name,
    }


@router.get("/auth/me")
async def get_me(
    account_id: int = Depends(get_current_account), db: Session = Depends(get_db)
):
    def _fetch():
        account = db.query(Account).filter(Account.id == account_id).first()
        return {
            "id": account.id,
            "email": account.email,
            "display_name": account.display_name,
        }

    return await asyncio.to_thread(_fetch)


@router.put("/auth/change-password")
async def change_pwd(
    data: ChangePasswordRequest, account_id: int = Depends(get_current_account)
):
    ok = await asyncio.to_thread(
        change_password, account_id, data.old_password, data.new_password
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Old password is incorrect")
    return {"message": "Password updated"}


@router.put("/auth/profile")
async def update_profile(
    data: ProfileUpdate, account_id: int = Depends(get_current_account)
):
    await asyncio.to_thread(update_display_name, account_id, data.display_name)
    return {"message": "Profile updated"}


@router.post("/auth/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    def _do():
        account = db.query(Account).filter(Account.email == data.email).first()
        if not account:
            return None
        token = create_password_reset_token(account.id)
        reset_url = f"http://localhost:8501/?reset_token={token}"
        email_sent = send_password_reset_email(account.email, reset_url)
        if not email_sent:
            logger.info("❌ Email sending failed")
        logger.info(f"[RESET] Token for {account.email}: {reset_url}")
        return {"reset_token": token, "reset_url": reset_url}

    # Sending email is a network call — must not block other users
    result = await asyncio.to_thread(_do)
    if result is None:
        return {"message": "If the email is registered, a reset link has been sent."}

    return {
        "message": "If the email is registered, a reset link has been sent.",
        "reset_token": result["reset_token"],
        "reset_url": result["reset_url"],
    }


@router.post("/auth/reset-password")
async def reset_password(data: ResetPasswordRequest):
    def _do():
        account_id = verify_reset_token(data.token)
        if not account_id:
            return False
        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            account.password_hash = hash_password(data.new_password)
            db.commit()
            return True
        finally:
            db.close()

    ok = await asyncio.to_thread(_do)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    return {"message": "Password reset successful. You can now login."}
