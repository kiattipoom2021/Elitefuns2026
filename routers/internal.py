"""Internal endpoints (VPS ↔ Railway) — X-API-Key auth, ห้ามเปิดสู่ browser

- GET   /api/internal/active-subscriptions    → VPS poll รายชื่อ subscriptions ที่ active (+ MT5 creds)
- POST  /api/internal/orders                   → VPS รายงาน order ที่เพิ่งเปิด
- PATCH /api/internal/orders/{ticket}/close    → VPS รายงานว่าปิด order แล้ว

Auth: header `X-API-Key` ต้องตรงกับ env `INTERNAL_API_KEY` (fallback `VPS_API_KEY`)
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models.mt5_account import MT5Account
from models.order import Order, ORDER_TYPES, CLOSE_REASONS
from models.subscription import UserStrategySubscription
from services import encryption

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/internal", tags=["internal"])


# ─── Auth dependency ──────────────────────────────────────────────────
def _expected_api_key() -> str:
    """อ่าน internal API key — fallback ไปใช้ VPS_API_KEY ถ้าไม่ตั้ง INTERNAL_API_KEY แยก"""
    return os.getenv("INTERNAL_API_KEY") or os.getenv("VPS_API_KEY") or ""


def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
    """ตรวจ X-API-Key header — 401 ถ้าไม่ตรง"""
    expected = _expected_api_key()
    if not expected or x_api_key != expected:
        logger.warning("internal auth fail — bad/missing X-API-Key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )


# ─── Schemas ──────────────────────────────────────────────────────────
class ActiveSubscriptionItem(BaseModel):
    """one row ที่ VPS bot loop ใช้ในการ poll งาน"""

    subscription_id: str
    run_code: str
    symbol: str
    timeframe: str
    params: dict
    risk_multiplier: float
    mt5_login: int
    mt5_password: str  # decrypted — ใช้ใน VPS โดยไม่ log
    mt5_server: str
    magic: int
    # subscription creation time — VPS ใช้ narrow lookback window ของ
    # history_deals_get (max(created_at, NOW-365d)) เพื่อ accurate peak/dd
    # สำหรับ subscription ที่อายุ > 1 ปี (Known Issue #2)
    created_at: datetime


class OrderCreate(BaseModel):
    subscription_id: str
    ticket: int = Field(..., gt=0)
    signal_bar_time: datetime
    symbol: str = Field(..., min_length=1, max_length=32)
    type: str = Field(..., pattern="^(BUY|SELL)$")
    volume: float = Field(..., gt=0)
    open_price: float = Field(..., gt=0)
    sl: Optional[float] = None
    tp: Optional[float] = None
    magic: int


class OrderClose(BaseModel):
    close_price: float = Field(..., gt=0)
    profit: float
    close_reason: str = Field(..., pattern="^(sl|tp|safety_cut|manual|expired)$")


class PnlUpdate(BaseModel):
    """VPS push cumulative PnL (closed + floating) ของ subscription หนึ่ง"""

    current_pnl_usd: float


class PnlUpdateResponse(BaseModel):
    subscription_id: str
    current_pnl_usd: float
    peak_pnl_usd: float
    current_dd_usd: float
    safety_cut_dd: Optional[float] = None
    safety_cut_triggered: bool


class SafetyCutTrigger(BaseModel):
    """VPS แจ้งว่า safety_cut ทำงาน — Railway mark subscription paused"""

    reason: str = Field(..., pattern="^(dd_threshold|manual)$")
    dd_at_trigger: float = Field(..., ge=0.0)


class BalanceUpdate(BaseModel):
    """VPS push balance/equity/currency/leverage หลัง mt5.account_info() refresh

    ใช้ใน sync_port flow (Port Stats Phase 1) — VPS เป็นคน trusted (X-API-Key)
    Railway ไม่ต้อง ownership check เพราะ mt5_account_id มาจาก /sync request
    ที่ผ่าน JWT มาแล้ว
    """

    mt5_account_id: str
    balance: float
    equity: float
    currency: Optional[str] = None
    leverage: Optional[int] = None


# ─── Helpers ──────────────────────────────────────────────────────────
def _subscription_to_magic(sub_id: str) -> int:
    """แปลง subscription_id (UUID string) → stable 31-bit int magic number

    ใช้ UUID.int truncate ให้อยู่ใน positive int32 range (MT5 magic field คือ int)
    """
    try:
        return int(uuid.UUID(sub_id).int & 0x7FFFFFFF)
    except (ValueError, TypeError):
        # fallback — hash() ใช้ได้แต่ไม่ deterministic ระหว่าง process restart;
        # subscription_id ของเราเป็น UUID เสมอจาก default=lambda → uuid4()
        return abs(hash(sub_id)) & 0x7FFFFFFF


# ─── Endpoints ────────────────────────────────────────────────────────
@router.get(
    "/active-subscriptions",
    response_model=list[ActiveSubscriptionItem],
    dependencies=[Depends(require_api_key)],
)
def list_active_subscriptions(db: Session = Depends(get_db)):
    """VPS poll endpoint — return active subscriptions พร้อม decrypted password

    ⚠️ DO NOT LOG password — decrypt เฉพาะใน loop ที่ build response
    """
    # JOIN subscriptions ↔ mt5_accounts — กรอง verified=true เท่านั้น
    rows = (
        db.query(UserStrategySubscription, MT5Account)
        .join(MT5Account, UserStrategySubscription.mt5_account_id == MT5Account.id)
        .filter(
            UserStrategySubscription.status == "active",
            MT5Account.verified.is_(True),
        )
        .all()
    )

    out: list[ActiveSubscriptionItem] = []
    for sub, account in rows:
        try:
            plain_pw = encryption.decrypt(account.encrypted_password)
        except Exception:
            # decryption fail — skip row, ไม่ leak ข้อมูล + ไม่ทำให้ทั้ง loop ล่ม
            logger.error(
                "failed to decrypt password for mt5_account_id=%s (sub=%s) — skipping",
                account.id, sub.id,
            )
            continue

        out.append(ActiveSubscriptionItem(
            subscription_id=sub.id,
            run_code=sub.run_code,
            symbol=sub.symbol,
            timeframe=sub.timeframe,
            params=sub.params or {},
            risk_multiplier=sub.risk_multiplier,
            mt5_login=account.login,
            mt5_password=plain_pw,
            mt5_server=account.server,
            magic=_subscription_to_magic(sub.id),
            created_at=sub.created_at,
        ))

    logger.info("active-subscriptions polled — %d rows returned", len(out))
    return out


@router.post("/orders", dependencies=[Depends(require_api_key)])
def report_order(body: OrderCreate, db: Session = Depends(get_db)):
    """VPS รายงานว่า order ใหม่ถูกเปิดแล้ว — INSERT พร้อม dedup check

    คืน 409 ถ้า UNIQUE (subscription_id, signal_bar_time, type) ชน — VPS ควรถือเป็น dedup ปกติ
    """
    # หา subscription เพื่อ derive user_id (กัน VPS ส่ง user_id ผิด)
    sub = (
        db.query(UserStrategySubscription)
        .filter(UserStrategySubscription.id == body.subscription_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "ไม่พบ subscription ตาม subscription_id")

    order = Order(
        subscription_id=sub.id,
        user_id=sub.user_id,
        ticket=body.ticket,
        signal_bar_time=body.signal_bar_time,
        symbol=body.symbol,
        type=body.type,
        volume=body.volume,
        open_price=body.open_price,
        sl=body.sl,
        tp=body.tp,
        magic=body.magic,
        status="open",
        opened_at=datetime.now(timezone.utc),
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # race: scheduler ตี 2 ครั้งใน window สั้น ๆ — VPS ควรเข้าใจว่าซ้ำ
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "duplicate order — (subscription_id, signal_bar_time, type) มีอยู่แล้ว",
        )
    db.refresh(order)

    # อัพเดต subscription tracking
    sub.last_order_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        "order created order_id=%s sub=%s ticket=%s type=%s",
        order.id, sub.id, body.ticket, body.type,
    )
    return {"ok": True, "order_id": order.id}


@router.patch("/orders/{ticket}/close", dependencies=[Depends(require_api_key)])
def close_order(ticket: int, body: OrderClose, db: Session = Depends(get_db)):
    """VPS รายงานว่าปิด order แล้ว — UPDATE close_* + status"""
    order = (
        db.query(Order)
        .filter(Order.ticket == ticket, Order.status == "open")
        .first()
    )
    if not order:
        raise HTTPException(404, f"ไม่พบ open order ที่ ticket={ticket}")

    order.close_price = body.close_price
    order.profit = body.profit
    order.close_reason = body.close_reason
    order.status = "closed"
    order.closed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)

    logger.info(
        "order closed order_id=%s ticket=%s reason=%s profit=%.2f",
        order.id, ticket, body.close_reason, body.profit,
    )
    return {"ok": True, "order_id": order.id}


# ─── Safety Cut: PnL update + trigger ─────────────────────────────────
@router.post(
    "/subscriptions/{sub_id}/pnl-update",
    response_model=PnlUpdateResponse,
    dependencies=[Depends(require_api_key)],
)
def update_subscription_pnl(
    sub_id: str, body: PnlUpdate, db: Session = Depends(get_db),
):
    """VPS push cumulative PnL — Railway update current/peak + return DD + trigger flag

    Side effects (atomic):
      - current_pnl_usd = body.current_pnl_usd
      - peak_pnl_usd = max(peak_pnl_usd, current_pnl_usd)  ← monotone-up เท่านั้น
      - last_pnl_update_at = NOW()

    Return:
      - current_dd_usd = max(0, peak - current)
      - safety_cut_triggered = (safety_cut_dd != null AND current_dd_usd >= safety_cut_dd)

    VPS อ่าน safety_cut_triggered → ถ้า True ก็เรียก /safety-cut-trigger ตามมา
    """
    sub = (
        db.query(UserStrategySubscription)
        .filter(UserStrategySubscription.id == sub_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "ไม่พบ subscription ตาม subscription_id")

    new_current = float(body.current_pnl_usd)
    prev_peak = float(sub.peak_pnl_usd or 0.0)
    new_peak = max(prev_peak, new_current)

    sub.current_pnl_usd = new_current
    sub.peak_pnl_usd = new_peak
    sub.last_pnl_update_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)

    current_dd = max(0.0, new_peak - new_current)
    triggered = (
        sub.safety_cut_dd is not None
        and sub.safety_cut_dd > 0
        and current_dd >= float(sub.safety_cut_dd)
    )

    # log เฉพาะตอน trigger (ลด noise — pnl-update มาทุก tick)
    if triggered:
        logger.warning(
            "safety_cut threshold reached sub=%s user=%s dd=$%.2f threshold=$%.2f",
            sub.id, sub.user_id, current_dd, sub.safety_cut_dd,
        )

    return PnlUpdateResponse(
        subscription_id=sub.id,
        current_pnl_usd=new_current,
        peak_pnl_usd=new_peak,
        current_dd_usd=current_dd,
        safety_cut_dd=sub.safety_cut_dd,
        safety_cut_triggered=triggered,
    )


@router.post(
    "/subscriptions/{sub_id}/safety-cut-trigger",
    dependencies=[Depends(require_api_key)],
)
def trigger_safety_cut(
    sub_id: str, body: SafetyCutTrigger, db: Session = Depends(get_db),
):
    """VPS แจ้งว่าได้ปิด positions แล้ว — Railway mark subscription 'paused' + alert log

    Side effects:
      - status = 'paused'  (ไม่ใช่ 'stopped' — user แก้ params/threshold แล้ว resume ได้)
      - log alert พร้อม sub/user/dd/threshold/reason

    NOTE: pause-not-stop เลือกตามสเปก — user ไม่ต้องสร้าง subscription ใหม่ทุกครั้งที่ DD trip
    (ถ้าต้องการ stop ถาวร user กดผ่าน frontend ได้)
    """
    sub = (
        db.query(UserStrategySubscription)
        .filter(UserStrategySubscription.id == sub_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "ไม่พบ subscription ตาม subscription_id")

    sub.status = "paused"
    db.commit()
    db.refresh(sub)

    logger.warning(
        "safety_cut triggered sub=%s user=%s symbol=%s dd=$%.2f threshold=$%s reason=%s",
        sub.id, sub.user_id, sub.symbol, body.dd_at_trigger,
        sub.safety_cut_dd, body.reason,
    )

    return {"ok": True, "subscription_id": sub.id, "status": "paused"}


# ─── Port Stats: Balance refresh (Phase 1) ────────────────────────────
@router.post(
    "/mt5/balance-update",
    dependencies=[Depends(require_api_key)],
)
def update_mt5_balance(body: BalanceUpdate, db: Session = Depends(get_db)):
    """VPS push balance/equity/currency/leverage หลัง mt5.account_info() refresh

    ใช้ใน /bot/sync-port flow — VPS เป็น trusted (X-API-Key) ไม่ต้อง ownership check
    (mt5_account_id ผ่าน JWT มาจาก Railway /sync request แล้ว)

    Side effects:
      - balance_cached, equity_cached overwrite ตรงๆ (latest is truth)
      - currency_cached, leverage_cached update ถ้า VPS ส่งมา
      - last_check = NOW()

    Returns: {ok, mt5_account_id, last_check}
    """
    account = (
        db.query(MT5Account)
        .filter(MT5Account.id == body.mt5_account_id)
        .first()
    )
    if not account:
        raise HTTPException(404, "ไม่พบ mt5_account ตาม mt5_account_id")

    account.balance_cached = float(body.balance)
    account.equity_cached = float(body.equity)
    if body.currency is not None:
        account.currency_cached = body.currency
    if body.leverage is not None:
        account.leverage_cached = int(body.leverage)
    account.last_check = datetime.now(timezone.utc)

    db.commit()
    db.refresh(account)

    logger.info(
        "balance-update mt5_account_id=%s balance=%.2f equity=%.2f",
        account.id, account.balance_cached or 0.0, account.equity_cached or 0.0,
    )

    return {
        "ok": True,
        "mt5_account_id": account.id,
        "last_check": account.last_check,
    }
