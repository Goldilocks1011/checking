from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.models import User
from backend.services.auth_service import SECRET_KEY, ALGORITHM
import logging

logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_account(token: str = Depends(oauth2_scheme)) -> int:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        logger.info(f"🔐 Received token: {token[:10]}...")  # Log first 10 chars
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        account_id: int = int(payload.get("sub"))
        logger.info(f"✅ Decoded account_id: {account_id}")
        if account_id is None:
            raise credentials_exception
        return account_id
    except JWTError as e:
        logger.error(f"❌ JWT decode error: {e}", exc_info=True)
        raise credentials_exception


def get_portfolio_user(
    user_id: int,
    account_id: int = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """Load a portfolio user and verify it belongs to the current account."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.account_id != account_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user
