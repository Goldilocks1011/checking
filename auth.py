from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from services.auth_service import (create_account, verify_account, create_access_token,
                                   change_password, update_display_name,
                                   create_password_reset_token, verify_reset_token)
from dependencies.auth import get_current_account
from database import SessionLocal
from models import Account, User
from sqlalchemy.orm import Session
from services.auth_service import hash_password
from services.email_service import send_password_reset_email     # <-- your new import

import os
import logging
logger = logging.getLogger(__name__)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")   # optional

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
def create_portfolio_user(
    data: PortfolioUserCreate,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db)
):
    new_user = User(username=data.username, broker=data.broker, account_id=account_id)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "username": new_user.username}

@router.post("/auth/signup")
def signup(data: SignupRequest, db: Session = Depends(get_db)):
    existing = db.query(Account).filter(Account.email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    account = create_account(data.email, data.password, data.display_name)
    return {"message": "Account created", "account_id": account.id}

@router.post("/auth/login")
def login(data: LoginRequest):
    account = verify_account(data.email, data.password)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(account.id)})
    return {"access_token": token, "token_type": "bearer", "display_name": account.display_name}

@router.get("/auth/me")
def get_me(account_id: int = Depends(get_current_account), db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    return {"id": account.id, "email": account.email, "display_name": account.display_name}

@router.put("/auth/change-password")
def change_pwd(data: ChangePasswordRequest, account_id: int = Depends(get_current_account)):
    ok = change_password(account_id, data.old_password, data.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="Old password is incorrect")
    return {"message": "Password updated"}

@router.put("/auth/profile")
def update_profile(data: ProfileUpdate, account_id: int = Depends(get_current_account)):
    update_display_name(account_id, data.display_name)
    return {"message": "Profile updated"}


@router.post("/auth/forgot-password")
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.email == data.email).first()
    if not account:
        return {"message": "If the email is registered, a reset link has been sent."}

    token = create_password_reset_token(account.id)
    reset_url = f"http://localhost:8501/?reset_token={token}"

    # Send the email
    email_sent = send_password_reset_email(account.email, reset_url)
    if not email_sent:
        logger.info("❌ Email sending failed")  # will appear in the uvicorn console

    # Keep the existing debug prints
    logger.info(f"[RESET] Token for {account.email}: {reset_url}")

    return {
        "message": "If the email is registered, a reset link has been sent.",
        "reset_token": token,
        "reset_url": reset_url
    }

@router.post("/auth/reset-password")
def reset_password(data: ResetPasswordRequest):
    account_id = verify_reset_token(data.token)
    if not account_id:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    db = SessionLocal()
    account = db.query(Account).filter(Account.id == account_id).first()
    account.password_hash = hash_password(data.new_password)
    db.commit()
    return {"message": "Password reset successful. You can now login."}

@router.post("/portfolio-users")
def create_portfolio_user(username: str, broker: str,
                          account_id: int = Depends(get_current_account),
                          db: Session = Depends(get_db)):
    # Check uniqueness per account_id maybe
    new_user = User(username=username, broker=broker, account_id=account_id)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "username": new_user.username}