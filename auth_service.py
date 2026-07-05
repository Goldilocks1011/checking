import bcrypt
import secrets
from datetime import datetime, timedelta
from jose import jwt
from database import SessionLocal
from models import Account, PasswordReset

SECRET_KEY = "your-secret-key-change-me"   # change this in production
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7   # 7 days

# ── Password hashing ────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

# ── Token handling ──────────────────────────────────────────────
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    # IMPORTANT: the subject must be a string
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ── Account management ──────────────────────────────────────────
def get_account_by_email(email: str):
    db = SessionLocal()
    return db.query(Account).filter(Account.email == email).first()

def create_account(email: str, password: str, display_name: str = "") -> Account:
    db = SessionLocal()
    account = Account(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name or email.split('@')[0]
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account

def verify_account(email: str, password: str):
    account = get_account_by_email(email)
    if not account:
        return None
    if not verify_password(password, account.password_hash):
        return None
    return account

def change_password(account_id: int, old_password: str, new_password: str) -> bool:
    db = SessionLocal()
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return False
    if not verify_password(old_password, account.password_hash):
        return False
    account.password_hash = hash_password(new_password)
    db.commit()
    return True

def update_display_name(account_id: int, new_name: str):
    db = SessionLocal()
    account = db.query(Account).filter(Account.id == account_id).first()
    if account:
        account.display_name = new_name
        db.commit()

# ── Password reset tokens ───────────────────────────────────────
def create_password_reset_token(account_id: int) -> str:
    db = SessionLocal()
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=1)
    reset = PasswordReset(account_id=account_id, token=token, expires_at=expires)
    db.add(reset)
    db.commit()
    return token

def verify_reset_token(token: str) -> int | None:
    db = SessionLocal()
    reset = db.query(PasswordReset).filter(
        PasswordReset.token == token,
        PasswordReset.used == False,
        PasswordReset.expires_at > datetime.utcnow()
    ).first()
    if reset:
        reset.used = True
        db.commit()
        return reset.account_id
    return None