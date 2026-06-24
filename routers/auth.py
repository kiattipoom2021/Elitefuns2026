"""Authentication — register/login + JWT issuance + current_user dependency"""
import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from database import get_db
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

_DEV_MODE = os.getenv("APP_ENV", "production") == "development"


# ─── Schemas ──────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_admin: bool = False


# ─── Helpers ──────────────────────────────────────────────────────────
def make_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode({"sub": user_id, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _super_user_emails() -> set[str]:
    """Parse SUPER_USER_EMAILS env (comma-separated) → lowercase set"""
    raw = os.getenv("SUPER_USER_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _check_super_user(user: User, db: Session) -> None:
    """ถ้า email อยู่ใน SUPER_USER_EMAILS env → ensure is_admin=True (auto-promote)

    Note: lazy approach — promote ตอน login เท่านั้น
    ถ้า user ถูกลบจาก env แต่ is_admin=True ใน DB → ไม่ revoke (ต้อง manual UPDATE)
    """
    super_emails = _super_user_emails()
    if user.email.lower() in super_emails and not user.is_admin:
        user.is_admin = True
        db.commit()
        logger.info(
            "auto-promoted user=%s to admin via SUPER_USER_EMAILS", user.email
        )


def get_current_user(token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ไม่ถูกต้องหรือหมดอายุ")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "ไม่พบผู้ใช้")
    return user


def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Reject 403 ถ้า user ไม่ใช่ admin — ใช้ guard endpoints /api/admin/*"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="ต้องเป็น admin")
    return current_user


# ─── Endpoints ────────────────────────────────────────────────────────
@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "อีเมลนี้ถูกใช้แล้ว")
    if len(body.password) < 6:
        raise HTTPException(400, "รหัสผ่านต้องยาวอย่างน้อย 6 ตัวอักษร")

    user = User(email=body.email, hashed_password=pwd_ctx.hash(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    # auto-promote ถ้า email อยู่ใน super list (สะดวกกับ test account)
    _check_super_user(user, db)
    return TokenResponse(access_token=make_token(user.id), is_admin=user.is_admin)


@router.post("/login", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not pwd_ctx.verify(form.password, user.hashed_password):
        raise HTTPException(400, "อีเมลหรือรหัสผ่านไม่ถูกต้อง")
    # auto-promote ก่อน generate token เพื่อให้ response สะท้อนสถานะล่าสุด
    _check_super_user(user, db)
    return TokenResponse(access_token=make_token(user.id), is_admin=user.is_admin)


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    """ข้อมูล user ปัจจุบัน — ใช้ตรวจสอบว่า token ยังใช้ได้"""
    return {
        "id":         current_user.id,
        "email":      current_user.email,
        "is_admin":   current_user.is_admin,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
    }
