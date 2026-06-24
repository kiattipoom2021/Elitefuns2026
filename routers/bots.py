"""Bot Trade Subscription CRUD — user เลือก backtest combo มาผูกกับ MT5 port

ทุก endpoint ต้องผ่าน JWT auth + filter user_id (isolation)
- POST   /api/bots/subscriptions          → เพิ่ม subscription (snapshot params จาก backtest_run)
- GET    /api/bots/subscriptions          → list (optional filter ?mt5_account_id=)
- PATCH  /api/bots/subscriptions/{id}     → edit risk_multiplier / status
- DELETE /api/bots/subscriptions/{id}     → soft stop (immediate / leave) หรือ hard delete (?hard=true cascade orders)
- GET    /api/bots/subscriptions/{id}/orders → order history
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.backtest_run import BacktestRun
from models.mt5_account import MT5Account
from models.order import Order
from models.strategy import Strategy
from models.subscription import UserStrategySubscription, SUBSCRIPTION_STATUSES
from models.user import User
from routers.auth import get_current_user
from routers.internal import _subscription_to_magic
from services import encryption
from services import run_code as run_code_svc
from services import vps_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bots", tags=["bots"])


# จำกัด 5 subscriptions ต่อ (user, mt5_account) — Phase 1 hard cap
SUBSCRIPTION_CAP_PER_PORT = 5


# ─── Schemas ──────────────────────────────────────────────────────────
# PATCH endpoint distinguishes "field omitted" จาก "field=null" ผ่าน
# Pydantic v2 `model_fields_set` — null = disable threshold,
# field ไม่ส่ง = ไม่แตะค่าเดิม
class SubscriptionCreate(BaseModel):
    mt5_account_id: str
    backtest_run_id: str
    risk_multiplier: float = Field(1.0, ge=0.1, le=10.0)
    # safety_cut_dd: USD threshold สำหรับ peak-to-trough DD — null = ไม่ enforce
    # validate > 0 manually ด้านใน endpoint (Field gt=0 จะ reject null ทั้งที่ null ใช้ได้)
    safety_cut_dd: Optional[float] = None


class SubscriptionUpdate(BaseModel):
    """PATCH body — ทุก field เป็น optional

    safety_cut_dd: รับ 3 รูปแบบ
      - ไม่ส่ง field มา → ไม่แตะค่าเดิม
      - ส่งมาเป็น float > 0 → set threshold ใหม่
      - ส่งมาเป็น null → disable (clear threshold)
    เนื่องจาก Pydantic v2 default None ทำให้แยก "ไม่ส่ง" กับ "ส่ง null" ไม่ได้ตรงๆ
    → endpoint ใช้ model_fields_set ตรวจว่ามี field อยู่จริงไหม
    """

    risk_multiplier: Optional[float] = Field(None, ge=0.1, le=10.0)
    status: Optional[str] = None  # validate manually เพื่อให้ message ภาษาไทย
    safety_cut_dd: Optional[float] = None


class SubscriptionResponse(BaseModel):
    id: str
    user_id: str
    mt5_account_id: str
    backtest_run_id: str
    run_code: str
    strategy_code: str
    symbol: str
    timeframe: str
    params: dict
    risk_multiplier: float
    status: str
    last_check_at: Optional[datetime] = None
    last_signal_at: Optional[datetime] = None
    last_order_at: Optional[datetime] = None
    # ─── safety cut fields ────────────────────────────────────────────
    safety_cut_dd: Optional[float] = None
    peak_pnl_usd: float = 0.0
    current_pnl_usd: float = 0.0
    current_dd_usd: float = 0.0  # computed: max(0, peak - current)
    last_pnl_update_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _to_response(sub: UserStrategySubscription) -> SubscriptionResponse:
    """แปลง ORM → response dict + compute current_dd_usd"""
    peak = float(sub.peak_pnl_usd or 0.0)
    current = float(sub.current_pnl_usd or 0.0)
    dd = max(0.0, peak - current)
    return SubscriptionResponse(
        id=sub.id,
        user_id=sub.user_id,
        mt5_account_id=sub.mt5_account_id,
        backtest_run_id=sub.backtest_run_id,
        run_code=sub.run_code,
        strategy_code=sub.strategy_code,
        symbol=sub.symbol,
        timeframe=sub.timeframe,
        params=sub.params or {},
        risk_multiplier=sub.risk_multiplier,
        status=sub.status,
        last_check_at=sub.last_check_at,
        last_signal_at=sub.last_signal_at,
        last_order_at=sub.last_order_at,
        safety_cut_dd=sub.safety_cut_dd,
        peak_pnl_usd=peak,
        current_pnl_usd=current,
        current_dd_usd=dd,
        last_pnl_update_at=sub.last_pnl_update_at,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
    )


class OrderResponse(BaseModel):
    id: str
    subscription_id: str
    user_id: str
    ticket: int
    signal_bar_time: datetime
    symbol: str
    type: str
    volume: float
    open_price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    close_price: Optional[float] = None
    profit: Optional[float] = None
    close_reason: Optional[str] = None
    magic: int
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ─── Helpers ──────────────────────────────────────────────────────────
def _get_owned_subscription(
    db: Session, sub_id: str, user_id: str,
) -> UserStrategySubscription:
    """หา subscription ที่เป็นของ user — 404 ถ้าไม่เจอ"""
    sub = (
        db.query(UserStrategySubscription)
        .filter(
            UserStrategySubscription.id == sub_id,
            UserStrategySubscription.user_id == user_id,
        )
        .first()
    )
    if not sub:
        raise HTTPException(404, "ไม่พบ subscription นี้")
    return sub


def _validate_account_ownership(
    db: Session, mt5_account_id: str, user_id: str,
) -> MT5Account:
    """ตรวจว่า MT5 account เป็นของ user + verified แล้ว"""
    account = (
        db.query(MT5Account)
        .filter(MT5Account.id == mt5_account_id)
        .first()
    )
    if not account:
        raise HTTPException(404, "ไม่พบบัญชี MT5 นี้")
    if account.user_id != user_id:
        # 403 ตามสเปก — กัน ID enumeration leak
        raise HTTPException(403, "บัญชี MT5 นี้ไม่ใช่ของคุณ")
    if not account.verified:
        raise HTTPException(400, "บัญชี MT5 ต้อง verified ก่อนจึงจะสร้าง subscription ได้")
    return account


def _validate_backtest_latest(
    db: Session, backtest_run_id: str,
) -> tuple[BacktestRun, Strategy]:
    """ตรวจว่า backtest_run.engine_version == latest version ของ strategy นั้น"""
    run = (
        db.query(BacktestRun)
        .filter(BacktestRun.id == backtest_run_id)
        .first()
    )
    if not run:
        raise HTTPException(404, "ไม่พบ backtest run นี้")

    strategy = (
        db.query(Strategy)
        .filter(Strategy.id == run.strategy_id)
        .first()
    )
    if not strategy:
        raise HTTPException(404, "ไม่พบ strategy ที่เกี่ยวข้อง")

    # NOTE: lexicographic max — ใช้ได้กับ semver 0.X.0 ที่ X เป็นเลขหลักเดียว
    # (สอดคล้องกับ optimize_public.list_runs)
    latest = (
        db.query(func.max(BacktestRun.engine_version))
        .filter(BacktestRun.strategy_id == strategy.id)
        .scalar()
    )
    if latest and run.engine_version != latest:
        raise HTTPException(
            400,
            f"backtest run นี้ใช้ engine version เก่า ({run.engine_version}) — latest = {latest}",
        )
    return run, strategy


def _check_duplicate(
    db: Session, user_id: str, mt5_account_id: str, run_code: str,
) -> None:
    """UNIQUE (user, mt5_account, run_code) — 400 ถ้าซ้ำ"""
    dup = (
        db.query(UserStrategySubscription)
        .filter(
            UserStrategySubscription.user_id == user_id,
            UserStrategySubscription.mt5_account_id == mt5_account_id,
            UserStrategySubscription.run_code == run_code,
        )
        .first()
    )
    if dup:
        raise HTTPException(400, "subscription นี้มีอยู่แล้ว (run_code ซ้ำใน port เดียวกัน)")


def _check_cap(db: Session, user_id: str, mt5_account_id: str) -> None:
    """Subscription cap ≤ 5 ต่อ (user, mt5_account) — นับเฉพาะที่ยังไม่ stopped"""
    count = (
        db.query(UserStrategySubscription)
        .filter(
            UserStrategySubscription.user_id == user_id,
            UserStrategySubscription.mt5_account_id == mt5_account_id,
            UserStrategySubscription.status != "stopped",
        )
        .count()
    )
    if count >= SUBSCRIPTION_CAP_PER_PORT:
        raise HTTPException(
            400,
            f"จำนวน subscription ในพอร์ตนี้ครบ {SUBSCRIPTION_CAP_PER_PORT} แล้ว — ลบของเก่าก่อน",
        )


async def _safety_cut_async(
    db: Session, subscription: UserStrategySubscription,
) -> None:
    """trigger VPS safety cut — POST /bot/safety-cut เพื่อปิด positions จริงใน MT5

    Flow:
      1. load MT5Account ตาม subscription.mt5_account_id
      2. decrypt password (Fernet) — เก็บใน local var เท่านั้น ห้าม log
      3. ส่ง login/password/server ไป VPS เพื่อให้ mt5.initialize() ก่อนปิด positions

    Non-fatal: ถ้า VPS ไม่ตอบ / ไม่ configured / mt5_account หาย / decrypt fail
    → log warning แล้ว return เฉยๆ (delete subscription ใน DB สำเร็จเสมอ —
    VPS sync เป็น best-effort) ถ้า VPS report `positions_failed > 0`
    → log warning เพิ่ม เพราะอาจมี orphan positions ค้างใน MT5 ที่ user ต้อง manual close
    """
    if not vps_client.is_configured():
        logger.warning(
            "safety_cut skipped — VPS not configured (sub=%s, symbol=%s)",
            subscription.id, subscription.symbol,
        )
        return

    # ─── load MT5 credentials (non-fatal — FK race / orphan row จะ skip) ──
    account = (
        db.query(MT5Account)
        .filter(MT5Account.id == subscription.mt5_account_id)
        .first()
    )
    if account is None:
        logger.warning(
            "safety_cut skipped — mt5_account not found "
            "(sub=%s, mt5_account_id=%s) — possible FK race",
            subscription.id, subscription.mt5_account_id,
        )
        return

    try:
        plain_pw = encryption.decrypt(account.encrypted_password)
    except Exception:
        # decrypt fail (key rotation, corrupted token) — log + abort
        # ห้าม log token หรือ password
        logger.error(
            "safety_cut skipped — decrypt failed for mt5_account_id=%s (sub=%s)",
            account.id, subscription.id,
        )
        return

    try:
        result = await vps_client.safety_cut(
            subscription_id=subscription.id,
            symbol=subscription.symbol,
            magic=_subscription_to_magic(subscription.id),
            login=account.login,
            password=plain_pw,
            server=account.server,
        )
    except Exception:
        # ห้าม fail การลบเพราะ VPS ไม่ตอบ — แค่ log แล้วปล่อยให้ caller commit DB ต่อ
        logger.exception(
            "safety_cut VPS call failed (non-fatal) sub=%s symbol=%s",
            subscription.id, subscription.symbol,
        )
        return

    closed = int(result.get("positions_closed", 0) or 0)
    failed = int(result.get("positions_failed", 0) or 0)
    logger.info(
        "safety_cut completed sub=%s symbol=%s closed=%d failed=%d",
        subscription.id, subscription.symbol, closed, failed,
    )
    if failed > 0:
        # orphan positions อาจค้างใน MT5 — user ควรเช็คเอง
        logger.warning(
            "safety_cut had %d failed close(s) sub=%s symbol=%s — "
            "user may have orphan positions in MT5",
            failed, subscription.id, subscription.symbol,
        )


# ─── Endpoints ────────────────────────────────────────────────────────
@router.get("/subscriptions", response_model=list[SubscriptionResponse])
def list_subscriptions(
    mt5_account_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """list subscription ของ user ปัจจุบัน — optional filter ตาม port"""
    q = db.query(UserStrategySubscription).filter(
        UserStrategySubscription.user_id == current_user.id,
    )
    if mt5_account_id:
        q = q.filter(UserStrategySubscription.mt5_account_id == mt5_account_id)
    subs = q.order_by(UserStrategySubscription.created_at.desc()).all()
    return [_to_response(s) for s in subs]


@router.post("/subscriptions", response_model=SubscriptionResponse)
def create_subscription(
    body: SubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """สร้าง subscription ใหม่ — snapshot params จาก backtest_run + validate ครบทุก rule"""
    # 1. ownership + verified
    _validate_account_ownership(db, body.mt5_account_id, current_user.id)

    # 2. backtest_run + engine version ล่าสุด
    run, strategy = _validate_backtest_latest(db, body.backtest_run_id)

    # 3. คำนวณ run_code (อาจมีอยู่แล้วใน backtest_run.run_code — แต่บาง row อาจ null)
    code = run.run_code or run_code_svc.encode(
        run.timeframe, strategy.code, run.symbol, run.params or {},
    )
    if not code:
        raise HTTPException(400, "ไม่สามารถสร้าง run_code ได้ — ข้อมูล backtest_run ไม่ครบ")

    # 4. UNIQUE (user, mt5_account, run_code)
    _check_duplicate(db, current_user.id, body.mt5_account_id, code)

    # 5. Subscription cap per port
    _check_cap(db, current_user.id, body.mt5_account_id)

    # 6. validate safety_cut_dd — null = disabled, ค่าอื่นต้อง > 0
    if body.safety_cut_dd is not None and body.safety_cut_dd <= 0:
        raise HTTPException(400, "safety_cut_dd ต้อง > 0 (หรือ null เพื่อปิด)")

    # 7. snapshot params + INSERT
    sub = UserStrategySubscription(
        user_id=current_user.id,
        mt5_account_id=body.mt5_account_id,
        backtest_run_id=run.id,
        run_code=code,
        strategy_code=strategy.code,
        symbol=run.symbol,
        timeframe=run.timeframe,
        params=dict(run.params or {}),  # copy เพื่อ freeze — กัน mutation ภายหลัง
        risk_multiplier=body.risk_multiplier,
        status="active",
        safety_cut_dd=body.safety_cut_dd,
        peak_pnl_usd=0.0,
        current_pnl_usd=0.0,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    logger.info(
        "subscription created sub=%s user=%s port=%s run_code=%s mult=%s safety_cut_dd=%s",
        sub.id, current_user.id, body.mt5_account_id, code,
        body.risk_multiplier, body.safety_cut_dd,
    )
    return _to_response(sub)


@router.patch("/subscriptions/{sub_id}", response_model=SubscriptionResponse)
def update_subscription(
    sub_id: str,
    body: SubscriptionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """แก้ risk_multiplier และ/หรือ status (active/paused/stopped) และ/หรือ safety_cut_dd

    safety_cut_dd semantics:
      - ไม่ส่ง field มา → ไม่แตะค่าเดิม
      - ส่ง null      → disable (clear threshold)
      - ส่ง float > 0 → set threshold ใหม่ (ค่า ≤ 0 reject)
    ใช้ model_fields_set ตรวจว่ามี key อยู่จริงไหม (Pydantic v2)
    """
    sub = _get_owned_subscription(db, sub_id, current_user.id)
    fields_sent = body.model_fields_set

    if "risk_multiplier" in fields_sent and body.risk_multiplier is not None:
        sub.risk_multiplier = body.risk_multiplier

    if "status" in fields_sent and body.status is not None:
        if body.status not in SUBSCRIPTION_STATUSES:
            raise HTTPException(
                400, f"status ต้องเป็น {SUBSCRIPTION_STATUSES}",
            )
        sub.status = body.status

    if "safety_cut_dd" in fields_sent:
        # ส่งมา = เจตนาเปลี่ยน (อาจ set ใหม่ หรือ null เพื่อ disable)
        new_val = body.safety_cut_dd
        if new_val is not None and new_val <= 0:
            raise HTTPException(400, "safety_cut_dd ต้อง > 0 (หรือ null เพื่อปิด)")
        sub.safety_cut_dd = new_val

    db.commit()
    db.refresh(sub)
    return _to_response(sub)


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(
    sub_id: str,
    stop_mode: str = Query("immediate", regex="^(immediate|leave)$"),
    hard: bool = Query(False, description="True = ลบ row จริง (cascade orders)"),
    safety_cut: bool = Query(
        False, description="ใช้ร่วมกับ hard=true — close positions ก่อนลบ row",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ลบ/หยุด subscription — รองรับ 2 mode

    **Mode A — Soft delete (default):**
    - `?stop_mode=immediate` → status='stopped' + trigger VPS safety_cut
    - `?stop_mode=leave`     → status='stopped' อย่างเดียว ปล่อย orders เก่าให้ broker จัดการ
    - row ยังอยู่ใน DB (ใช้สำหรับ history)

    **Mode B — Hard delete (`?hard=true`):**
    - DELETE rows: orders (FK linked) + subscription — atomic ใน 1 transaction
    - `stop_mode` จะถูก ignore (ไม่ต้อง safety_cut เพราะ row จะหาย)
    - ออปชั่นพิเศษ: `?hard=true&safety_cut=true` → trigger safety_cut ก่อน
      ค่อย DELETE row (สำหรับ user ที่อยากปิด positions ใน MT5 ก่อนลบ history)

    **Cascade scope:** เฉพาะ `orders.subscription_id` (FK เดียวที่อ้าง subscription)
    — ตรวจสอบ models/*.py แล้ว ไม่มี FK อื่น (2026-06-09)

    **Design note:** เลือกจัดการ cascade ที่ application layer (ไม่ใช้ ON DELETE CASCADE
    ระดับ schema) เพื่อให้ control ได้ละเอียดกว่า + log จำนวน orders ที่ลบไปได้
    """
    sub = _get_owned_subscription(db, sub_id, current_user.id)

    # ─── Mode B: Hard delete ──────────────────────────────────────────
    if hard:
        # optional: ปิด positions ใน MT5 ก่อนลบ row (user ต้องการ close ของจริงด้วย)
        if safety_cut:
            await _safety_cut_async(db, sub)

        # นับ orders ก่อนลบ (เพื่อ return ใน response + log)
        orders_count = (
            db.query(Order)
            .filter(Order.subscription_id == sub.id)
            .count()
        )

        # atomic transaction — orders ก่อน subscription (FK constraint)
        try:
            db.query(Order).filter(Order.subscription_id == sub.id).delete(
                synchronize_session=False,
            )
            db.delete(sub)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "hard delete failed sub=%s user=%s", sub_id, current_user.id,
            )
            raise HTTPException(500, "ลบ subscription ไม่สำเร็จ — กรุณาลองใหม่")

        logger.info(
            "subscription hard-deleted sub=%s user=%s orders_deleted=%d "
            "run_code=%s safety_cut=%s",
            sub_id, current_user.id, orders_count, sub.run_code, safety_cut,
        )
        return {
            "ok": True,
            "mode": "hard",
            "orders_deleted": orders_count,
            "subscription_id": sub_id,
        }

    # ─── Mode A: Soft delete (existing behavior — unchanged) ──────────
    sub.status = "stopped"
    db.commit()
    db.refresh(sub)

    if stop_mode == "immediate":
        await _safety_cut_async(db, sub)

    return {
        "ok": True,
        "mode": "soft",
        "stop_mode": stop_mode,
        "subscription_id": sub.id,
    }


@router.post(
    "/subscriptions/{sub_id}/reset-peak",
    response_model=SubscriptionResponse,
)
def reset_peak(
    sub_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reset peak_pnl_usd → current_pnl_usd → DD กลับเป็น 0

    ใช้แก้กรณี safety_cut trigger แล้ว user resume → peak ค้างค่าเดิม +
    current ใกล้เดิม (positions ถูกปิดไปแล้ว) → DD ≈ threshold → trigger ซ้ำทันที

    Manual button — user ตัดสินใจกดเองหลัง review สถานการณ์
    Atomic: peak := current → current_dd_usd = max(0, peak - current) = 0
    """
    sub = _get_owned_subscription(db, sub_id, current_user.id)

    peak_before = float(sub.peak_pnl_usd or 0.0)
    current = float(sub.current_pnl_usd or 0.0)
    sub.peak_pnl_usd = current

    db.commit()
    db.refresh(sub)

    logger.info(
        "subscription peak reset sub=%s user=%s peak_before=%s peak_after=%s",
        sub.id, current_user.id, peak_before, sub.peak_pnl_usd,
    )
    return _to_response(sub)


@router.get(
    "/subscriptions/{sub_id}/orders",
    response_model=list[OrderResponse],
)
def list_subscription_orders(
    sub_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """order history ของ subscription นี้ — เรียงจากใหม่สุด"""
    # ตรวจ ownership ผ่าน subscription ก่อน — ป้องกัน enumerate id คนอื่น
    _get_owned_subscription(db, sub_id, current_user.id)

    return (
        db.query(Order)
        .filter(Order.subscription_id == sub_id)
        .order_by(Order.opened_at.desc())
        .all()
    )
