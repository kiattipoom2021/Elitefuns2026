"""MT5 Account CRUD — เพิ่ม/ดู/ทดสอบ/ลบ บัญชี MT5 (ทุก endpoint ต้อง login)"""
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models.mt5_account import MT5Account
from models.user import User
from routers.auth import get_current_user
from services import encryption, vps_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mt5", tags=["mt5"])


# ─── Schemas ──────────────────────────────────────────────────────────
class MT5AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    login: int = Field(..., gt=0)
    password: str = Field(..., min_length=1)
    server: str = Field(..., min_length=1, max_length=128)
    broker: Optional[str] = Field(None, max_length=128)


class MT5AccountPasswordUpdate(BaseModel):
    password: str = Field(..., min_length=1)


class MT5AccountResponse(BaseModel):
    """Response schema — ห้ามมี password หรือ encrypted_password ใน field"""

    id: str
    user_id: str
    name: str
    login: int
    server: str
    broker: Optional[str] = None
    verified: bool
    balance_cached: Optional[float] = None
    equity_cached: Optional[float] = None
    leverage_cached: Optional[int] = None
    currency_cached: Optional[str] = None
    last_check: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Helpers ──────────────────────────────────────────────────────────
def _get_owned_account(db: Session, account_id: str, user_id: str) -> MT5Account:
    """ค้นหา account ที่เป็นของ user ปัจจุบัน — 404 ถ้าไม่เจอหรือไม่ใช่เจ้าของ"""
    account = (
        db.query(MT5Account)
        .filter(MT5Account.id == account_id, MT5Account.user_id == user_id)
        .first()
    )
    if not account:
        raise HTTPException(404, "ไม่พบบัญชี MT5 นี้")
    return account


async def _verify_and_update(db: Session, account: MT5Account, password: str) -> None:
    """เรียก VPS verify แล้ว update field cache บน account (commit ให้แล้ว)

    VPS response shape:
        success: {"ok": True, "account_info": {"balance", "equity", "leverage", "currency", "name"}}
        failure: {"ok": False, "error": "..."}
    """
    result = await vps_client.verify_mt5(account.login, password, account.server)
    if not result.get("ok"):
        account.verified = False
        account.last_check = datetime.now(timezone.utc)
        db.commit()
        msg = result.get("error") or "เชื่อมต่อ MT5 ไม่สำเร็จ"
        raise HTTPException(400, msg)

    # อ่านจาก account_info ตาม contract กับ VPS (ไม่ใช่ top-level)
    info = result.get("account_info") or {}
    fresh_balance = info.get("balance")

    # ─── HP baseline snapshot ────────────────────────────────────────
    # ถ้ายังไม่เคย snapshot + ได้ balance > 0 จาก MT5 → freeze ค่านี้เป็น baseline ของ HP bar
    # ตามสเปค docs/PORT_STATS_FLOW.md § HP/Stamina Risk Budget Display
    if (
        account.initial_balance_usd is None
        and fresh_balance is not None
        and float(fresh_balance) > 0
    ):
        account.initial_balance_usd = float(fresh_balance)
        account.initial_balance_at = datetime.now(timezone.utc)
        logger.info(
            "initial_balance snapshot mt5_account=%s balance=%s",
            account.id, fresh_balance,
        )

    account.verified = True
    account.balance_cached = fresh_balance
    account.equity_cached = info.get("equity")
    account.leverage_cached = info.get("leverage")
    account.currency_cached = info.get("currency")
    account.last_check = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)


# ─── Endpoints ────────────────────────────────────────────────────────
@router.post("/accounts", response_model=MT5AccountResponse)
async def create_account(
    body: MT5AccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """เพิ่มบัญชี MT5 ใหม่ + ทดสอบ login ทันที"""
    # 1. กันซ้ำ (user_id + login)
    dup = (
        db.query(MT5Account)
        .filter(MT5Account.user_id == current_user.id, MT5Account.login == body.login)
        .first()
    )
    if dup:
        raise HTTPException(400, "บัญชี MT5 นี้ถูกเพิ่มแล้ว")

    # 2. encrypt password ก่อนเก็บ
    enc_pw = encryption.encrypt(body.password)
    account = MT5Account(
        user_id=current_user.id,
        name=body.name,
        login=body.login,
        encrypted_password=enc_pw,
        server=body.server,
        broker=body.broker,
        verified=False,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    # 3. Best-effort verify กับ VPS (ใช้ plaintext ใน memory เท่านั้น — ห้าม log)
    #    - VPS unreachable (503)   → log warning, ปล่อย verified=False, return account
    #    - credentials wrong (400) → re-raise ตามเดิม (user ป้อนผิด)
    #    - error อื่น              → 500
    try:
        await _verify_and_update(db, account, body.password)
    except HTTPException as exc:
        if exc.status_code == 503:
            logger.warning(
                "VPS unreachable on create_account mt5_account=%s — "
                "account saved with verified=False (verify later)",
                account.id,
            )
            # _verify_and_update raise ก่อน commit ของ success path → refresh
            # ให้ได้ค่า verified=False จาก insert ตอน step 2
            db.refresh(account)
            return account
        # 400 credentials wrong → re-raise (user เห็น error ที่ถูกต้อง)
        raise
    except Exception:
        logger.exception("unexpected verify error mt5_account=%s", account.id)
        raise HTTPException(500, "verify error — ตรวจ logs")

    return account


@router.get("/accounts", response_model=list[MT5AccountResponse])
def list_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ลิสต์บัญชี MT5 ของ user ปัจจุบัน — เรียงจากใหม่สุด"""
    return (
        db.query(MT5Account)
        .filter(MT5Account.user_id == current_user.id)
        .order_by(MT5Account.created_at.desc())
        .all()
    )


@router.post("/accounts/{account_id}/verify", response_model=MT5AccountResponse)
async def verify_account(
    account_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ทดสอบ login ใหม่ + อัปเดต cache (ไม่ insert)"""
    account = _get_owned_account(db, account_id, current_user.id)
    plain_pw = encryption.decrypt(account.encrypted_password)
    await _verify_and_update(db, account, plain_pw)
    return account


@router.put("/accounts/{account_id}/password", response_model=MT5AccountResponse)
async def update_password(
    account_id: str,
    body: MT5AccountPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """แก้ password MT5 ของบัญชีเดิม + best-effort verify (ห้าม log password)"""
    account = _get_owned_account(db, account_id, current_user.id)
    enc_pw = encryption.encrypt(body.password)
    account.encrypted_password = enc_pw
    account.verified = False
    db.commit()
    # Best-effort verify (เหมือน create_account):
    # VPS down → ปล่อย verified=False; creds wrong → 400; อื่นๆ → 500
    try:
        await _verify_and_update(db, account, body.password)
    except HTTPException as exc:
        if exc.status_code == 503:
            logger.warning(
                "VPS unreachable on update_password mt5_account=%s — "
                "password updated but verified=False (verify later)",
                account.id,
            )
            db.refresh(account)
            return account
        raise
    except Exception:
        logger.exception("unexpected verify error mt5_account=%s", account.id)
        raise HTTPException(500, "verify error — ตรวจ logs")

    return account


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ลบบัญชี MT5 — 404 ถ้าไม่ใช่เจ้าของ"""
    account = _get_owned_account(db, account_id, current_user.id)
    db.delete(account)
    db.commit()
    return {"ok": True}
