"""Port Stats — per-port aggregate stats + manual sync via VPS

Spec: docs/PORT_STATS_FLOW.md (Phase 1)

Endpoints (JWT auth, user-scoped):
- GET  /api/bots/ports/stats              → list ของ port stats (cached read)
- POST /api/bots/ports/{port_id}/sync     → trigger VPS sync → return fresh stats

Design:
- ทั้งสอง endpoint คืน shape เดียวกัน (per-port dict) — frontend ใช้ component เดิม
- /stats = fast read จาก DB cache (ไม่เรียก VPS)
- /sync  = orchestrate: decrypt password → call VPS → re-aggregate → return
- VPS unreachable/timeout บน /sync → return cached stats + warning (ไม่ raise)
- ใช้ Python aggregation บน รวมจาก SQL (กัน SQLite ไม่มี FILTER clause)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.mt5_account import MT5Account
from models.mt5_trade import MT5Trade
from models.order import Order
from models.subscription import UserStrategySubscription
from models.user import User
from routers.auth import get_current_user
from routers.internal import _subscription_to_magic
from services import encryption
from services import vps_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bots/ports", tags=["ports"])


# ─── Response schemas ────────────────────────────────────────────────
class AccountStats(BaseModel):
    balance: Optional[float] = None
    equity: Optional[float] = None
    floating_pnl: Optional[float] = None
    last_sync: Optional[datetime] = None


# ─── HP / Stamina risk budget (Phase 1 display-only) ─────────────────
class RiskBar(BaseModel):
    """หนึ่ง bar (HP หรือ Stamina) — ค่า ที่ส่งให้ frontend วาด progress"""

    limit: float
    used: float
    remaining: float
    remaining_pct: float
    status: str  # healthy / warning / danger / critical


class RiskBudget(BaseModel):
    """HP+Stamina budget object — bar=None ถ้า user ไม่ได้ตั้ง limit (frontend ซ่อน)

    hp_baseline_at: เวลาที่ user save HP limit ครั้งล่าสุด → frontend อาจแสดง
    "HP tracking since ..."
    """

    hp: Optional[RiskBar] = None
    stamina: Optional[RiskBar] = None
    today_utc_start: datetime
    initial_balance_at: Optional[datetime] = None
    hp_baseline_at: Optional[datetime] = None


class PortStats(BaseModel):
    port_id: str
    name: str
    login: int
    server: str
    currency: Optional[str] = None
    verified: bool

    account: AccountStats
    risk_budget: RiskBudget


class SyncResultSummary(BaseModel):
    """ผลลัพธ์ตอบกลับจาก VPS /bot/sync-port"""

    balance_synced: bool = False
    subs_synced: int = 0
    orders_reconciled: int = 0
    errors: list[str] = []
    warning: Optional[str] = None   # set ถ้า VPS unreachable → return cached


class SyncResponse(BaseModel):
    ok: bool
    sync_result: SyncResultSummary
    stats: PortStats
    synced_at: datetime


class OrderHistoryItem(BaseModel):
    """แถวเดียวใน trade history ของ port — รวม snapshot strategy meta จาก subscription

    Frontend ใช้แสดงตาราง: ticket, symbol, type, volume, prices, profit, close_reason
    ค่า nullable: subscription/strategy meta อาจ None ถ้า subscription ถูกลบไปแล้ว
    """

    id: str
    ticket: int
    subscription_id: str
    run_code: Optional[str] = None
    strategy_code: Optional[str] = None
    symbol: str
    timeframe: Optional[str] = None
    type: str
    volume: float
    open_price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    close_price: Optional[float] = None
    profit: Optional[float] = None
    close_reason: Optional[str] = None
    status: str
    magic: int
    opened_at: datetime
    closed_at: Optional[datetime] = None


class TradesRequest(BaseModel):
    """POST body สำหรับ /trades — ขนาด window + จำนวน trade สูงสุด

    days_back / limit อยู่ใน body (ไม่ใช่ query string) เพื่อให้สอดคล้องกับ
    pattern ของ /sync — POST = trigger action, body = parameters
    """

    days_back: int = Field(30, ge=1, le=365)
    limit: int = Field(50, ge=1, le=500)


class TradeItem(BaseModel):
    """1 trade row จาก VPS (รวม manual + bot)

    nullable fields:
      - close_time/close_price → None ถ้า status='open'
      - profit → realized (closed) หรือ floating (open) หรือ None ถ้า broker drop position
    """

    position_id: int
    symbol: str
    type: str  # "BUY" | "SELL"
    volume: float
    open_time: datetime
    open_price: float
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None
    profit: Optional[float] = None
    magic: int
    comment: str = ""
    status: str  # "open" | "closed"
    source: str  # "bot" | "manual"


class TradesResponse(BaseModel):
    trades: list[TradeItem]
    total_in_window: int
    last_synced_at: Optional[datetime] = None


class SyncTradesResponse(BaseModel):
    """ผลลัพธ์ของ POST /trades/sync — summary หลัง UPSERT"""

    ok: bool
    new: int                  # จำนวน rows ที่ INSERT ใหม่
    updated: int              # จำนวน rows ที่ UPDATE (existing position)
    total_in_window: int      # จำนวนที่ VPS รายงานใน window (ก่อน limit)
    synced_at: datetime


class LimitsUpdate(BaseModel):
    """PATCH body — รับสองรูปแบบต่อ field (Pydantic v2 model_fields_set):
      - ไม่ส่ง field มา → ไม่แตะค่าเดิม
      - ส่งมาเป็น null    → disable bar นั้น
      - ส่งมาเป็น float>0 → set limit ใหม่
    เนื่องจาก Pydantic v2 default None ทำให้แยก "ไม่ส่ง" กับ "ส่ง null" ไม่ได้ตรงๆ
    → endpoint ใช้ model_fields_set ตรวจว่ามี field อยู่จริงไหม
    (pattern เดียวกับ SubscriptionUpdate.safety_cut_dd)
    """

    hp_limit_usd: Optional[float] = None
    stamina_limit_usd: Optional[float] = None


# ─── Helpers ─────────────────────────────────────────────────────────
def _get_owned_account(
    db: Session, port_id: str, user_id: str,
) -> MT5Account:
    """หา MT5Account ที่เป็นของ user — 404 ถ้าไม่เจอ (กัน id enumeration)"""
    account = (
        db.query(MT5Account)
        .filter(
            MT5Account.id == port_id,
            MT5Account.user_id == user_id,
        )
        .first()
    )
    if not account:
        raise HTTPException(404, "ไม่พบบัญชี MT5 นี้")
    return account


def _tier_status(remaining_pct: float) -> str:
    """แปลง remaining percentage → tier ตามสเปค (docs/PORT_STATS_FLOW.md)
      > 0.8 → healthy
      > 0.5 → warning
      > 0.2 → danger
      otherwise → critical
    """
    if remaining_pct > 0.8:
        return "healthy"
    if remaining_pct > 0.5:
        return "warning"
    if remaining_pct > 0.2:
        return "danger"
    return "critical"


def _compute_risk_budget(
    db: Session, account: MT5Account,
) -> RiskBudget:
    """HP ("balance vs cap" model) + Stamina (daily equity drop) bars

    เป็น **account-level** เท่านั้น — ไม่ผูกกับ orders/subscriptions
    เพราะ port อาจมี trade manual ที่ไม่ผ่าน bot ก็ได้

    HP (NEW semantic — "Total Life" model):
      - hp_limit_usd = "Total Life" / ceiling cap ที่ user ตั้ง (เช่น $20,000)
      - balance     = balance_cached (account balance ปัจจุบัน)
      - bar fill %  = balance / total_life (สูง = balance ใกล้ cap = healthy)
      - "HP เหลือ"  = total_life - balance (label headroom — เก็บใน RiskBar.used)
      - remaining   = effective_balance (= ส่วนที่ "เติม" bar)
      - port "แตก" = balance drops to 0 → bar fill = 0%
      - cap effective_balance ที่ total_life (กัน balance > cap → fill > 100%)
      - need hp_limit_usd AND balance_cached (else hp=None — ยังไม่เคย sync)

    Stamina:
      - need stamina_limit_usd (else stamina=None)
      - baseline = equity_midnight_usd (snapshot ตอน first sync ของวันใหม่ UTC)
        fallback: balance_cached ถ้ายังไม่มี snapshot
      - used = max(0, equity_midnight - current_equity)

    Tier thresholds: > 0.8 healthy / > 0.5 warning / > 0.2 danger / else critical
    """
    # today UTC start — ส่งให้ frontend แสดงเวลา cutoff
    today_utc_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )

    # ─── HP bar (balance vs cap model) ───────────────────────────────
    # fill_pct = balance / total_life → สูง = balance ใกล้ cap = healthy
    # ใช้ balance_cached ตรงๆ (ไม่มี baseline snapshot แล้ว)
    hp: Optional[RiskBar] = None
    hp_limit = account.hp_limit_usd
    balance = account.balance_cached
    if (
        hp_limit is not None
        and hp_limit > 0
        and balance is not None
    ):
        total_life = float(hp_limit)
        bal = float(balance)

        # Cap effective_balance ที่ total_life — กัน balance > cap → fill > 100%
        # (เช่น user ตั้ง cap ต่ำ แล้ว balance โตเกิน)
        effective_balance = min(bal, total_life)
        effective_balance = max(0.0, effective_balance)

        # Bar fill % = balance / total_life (clamp 0..1)
        fill_pct = effective_balance / total_life if total_life > 0 else 0.0

        # "HP เหลือ" (user's literal definition) = total_life - balance
        # เก็บใน RiskBar.used (= headroom จาก balance ถึง cap)
        hp_gap = max(0.0, total_life - bal)

        hp = RiskBar(
            limit=total_life,
            used=hp_gap,                  # = total_life - balance ("HP เหลือ" label)
            remaining=effective_balance,  # = filled portion (current "life force")
            remaining_pct=fill_pct,       # bar width % (high = healthy)
            status=_tier_status(fill_pct),
        )

    # ─── Stamina bar ─────────────────────────────────────────────────
    stamina: Optional[RiskBar] = None
    stamina_limit = account.stamina_limit_usd
    if stamina_limit is not None and stamina_limit > 0:
        # baseline: equity_midnight snapshot ถ้ามี — fallback balance_cached
        if account.equity_midnight_usd is not None:
            equity_midnight = float(account.equity_midnight_usd)
        else:
            equity_midnight = float(account.balance_cached or 0.0)

        current_eq = float(account.equity_cached or 0.0)
        stamina_used = max(0.0, equity_midnight - current_eq)
        stamina_remaining = max(0.0, float(stamina_limit) - stamina_used)
        stamina_remaining_pct = stamina_remaining / float(stamina_limit)
        stamina = RiskBar(
            limit=float(stamina_limit),
            used=stamina_used,
            remaining=stamina_remaining,
            remaining_pct=stamina_remaining_pct,
            status=_tier_status(stamina_remaining_pct),
        )

    return RiskBudget(
        hp=hp,
        stamina=stamina,
        today_utc_start=today_utc_start,
        initial_balance_at=account.initial_balance_at,
        hp_baseline_at=account.hp_baseline_at,
    )


def _build_account_stats(account: MT5Account) -> AccountStats:
    """Snapshot บัญชี: balance/equity จาก cache + floating = equity - balance"""
    bal = account.balance_cached
    eq = account.equity_cached
    floating = None
    if bal is not None and eq is not None:
        floating = float(eq) - float(bal)
    return AccountStats(
        balance=float(bal) if bal is not None else None,
        equity=float(eq) if eq is not None else None,
        floating_pnl=floating,
        last_sync=account.last_check,
    )


def _aggregate_port_stats(db: Session, account: MT5Account) -> PortStats:
    """รวม stat block สำหรับ port หนึ่ง — เรียกใช้ทั้งใน /stats, /sync, /limits

    Account-level เท่านั้น: ไม่ผูกกับ orders/subscriptions
    (port อาจมี trade manual ที่ไม่ผ่าน bot ก็ได้)
    """
    risk_budget = _compute_risk_budget(db, account)
    return PortStats(
        port_id=account.id,
        name=account.name,
        login=account.login,
        server=account.server,
        currency=account.currency_cached,
        verified=account.verified,
        account=_build_account_stats(account),
        risk_budget=risk_budget,
    )


# ─── Endpoints ───────────────────────────────────────────────────────
@router.get("/stats", response_model=list[PortStats])
def list_port_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """list port stats ของ user — เฉพาะ verified=True

    Fast read: ดึงจาก DB cache ทั้งหมด (ไม่เรียก VPS)
    """
    accounts = (
        db.query(MT5Account)
        .filter(
            MT5Account.user_id == current_user.id,
            MT5Account.verified.is_(True),
        )
        .order_by(MT5Account.created_at.asc())
        .all()
    )
    return [_aggregate_port_stats(db, acc) for acc in accounts]


@router.post("/{port_id}/sync", response_model=SyncResponse)
async def sync_port(
    port_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger VPS sync → refresh balance + PnL + reconcile orders → return fresh stats

    Orchestration ตาม docs/PORT_STATS_FLOW.md § Railway /sync:
      ① Ownership check (user_id + 404 ถ้าไม่เจอ)
      ② verified check (400 ถ้า verified=False)
      ③ Decrypt password (500 ถ้า decrypt fail)
      ④ Load subscriptions (active+paused — ยังต้อง reconcile) + open orders
      ⑤ Call VPS POST /bot/sync-port (timeout 60s)
         - VPS unreachable/timeout → swallow + warning (อย่า raise)
      ⑥ Re-aggregate stats จาก DB (always — แม้ VPS partial fail)
      ⑦ Return {ok, sync_result, stats, synced_at}

    NOTE: refresh account จาก DB หลัง VPS call เพื่อให้ได้ balance ใหม่
    ที่ VPS push เข้ามาผ่าน /api/internal/mt5/balance-update
    """
    # ① + ② ownership + verified
    account = _get_owned_account(db, port_id, current_user.id)
    if not account.verified:
        raise HTTPException(400, "บัญชี MT5 ยังไม่ได้ verified")

    # ③ decrypt password (เก็บใน local var เท่านั้น — ห้าม log)
    try:
        plain_pw = encryption.decrypt(account.encrypted_password)
    except Exception:
        logger.error(
            "sync_port decrypt failed mt5_account_id=%s user=%s",
            account.id, current_user.id,
        )
        raise HTTPException(500, "ไม่สามารถถอดรหัส MT5 password ได้")

    # ④ load context จาก Railway DB
    # subscriptions: ทุก status != 'stopped' (active + paused ยังต้อง reconcile)
    subs = (
        db.query(UserStrategySubscription)
        .filter(
            UserStrategySubscription.mt5_account_id == account.id,
            UserStrategySubscription.status != "stopped",
        )
        .all()
    )
    sub_ids = [s.id for s in subs]
    subs_payload = [
        {
            "subscription_id": s.id,
            "magic": _subscription_to_magic(s.id),
            "symbol": s.symbol,
        }
        for s in subs
    ]

    open_orders = []
    if sub_ids:
        rows = (
            db.query(Order)
            .filter(
                Order.subscription_id.in_(sub_ids),
                Order.status == "open",
            )
            .all()
        )
        open_orders = [
            {
                "ticket": o.ticket,
                "subscription_id": o.subscription_id,
                "symbol": o.symbol,
                "magic": o.magic,
                "type": o.type,
                "volume": o.volume,
                "sl": o.sl,
                "tp": o.tp,
                "opened_at": o.opened_at.isoformat() if o.opened_at else None,
            }
            for o in rows
        ]

    # ⑤ Call VPS (best-effort — unreachable → return cached + warning)
    sync_result = SyncResultSummary()
    if not vps_client.is_configured():
        logger.warning(
            "sync_port VPS not configured — return cached stats (port=%s)",
            account.id,
        )
        sync_result.warning = "VPS ยังไม่ได้ตั้งค่า — แสดงข้อมูล cache"
    else:
        try:
            result = await vps_client.sync_port(
                login=account.login,
                password=plain_pw,
                server=account.server,
                mt5_account_id=account.id,
                subscriptions=subs_payload,
                open_orders=open_orders,
            )
            sync_result = SyncResultSummary(
                balance_synced=bool(result.get("balance_synced", False)),
                subs_synced=int(result.get("subs_synced", 0) or 0),
                orders_reconciled=int(result.get("orders_reconciled", 0) or 0),
                errors=list(result.get("errors", []) or []),
            )
            logger.info(
                "sync_port done port=%s balance_synced=%s subs_synced=%d "
                "orders_reconciled=%d errors=%d",
                account.id, sync_result.balance_synced,
                sync_result.subs_synced, sync_result.orders_reconciled,
                len(sync_result.errors),
            )
        except HTTPException as exc:
            # vps_client raise 503 — swallow + return cached + warning
            if exc.status_code == 503:
                logger.warning(
                    "sync_port VPS unreachable port=%s — return cached stats",
                    account.id,
                )
                sync_result.warning = (
                    "VPS ไม่ตอบสนอง — แสดงข้อมูล cache ล่าสุด"
                )
            else:
                raise
        except Exception:
            # ไม่คาดคิด — log แล้ว return cached
            logger.exception(
                "sync_port unexpected error port=%s — return cached stats",
                account.id,
            )
            sync_result.warning = "เกิดข้อผิดพลาดระหว่าง sync — แสดงข้อมูล cache"

    # ⑥ refresh account จาก DB (balance อาจถูก VPS push update เข้ามาแล้ว)
    db.refresh(account)

    # ⑥.5 equity_midnight snapshot — first sync ของวันใหม่ UTC → snapshot equity
    # เป็น baseline ของ Stamina bar (account-level daily drop)
    now_utc = datetime.now(timezone.utc)
    today_utc_date = now_utc.date()
    last_snapshot_date = (
        account.equity_midnight_at.date()
        if account.equity_midnight_at else None
    )
    if last_snapshot_date != today_utc_date and account.equity_cached is not None:
        account.equity_midnight_usd = account.equity_cached
        account.equity_midnight_at = now_utc
        db.commit()
        db.refresh(account)
        logger.info(
            "equity_midnight snapshot mt5_account=%s value=%s",
            account.id, account.equity_midnight_usd,
        )

    # ⑦ aggregate fresh stats (always — แม้ VPS fail ก็คืน cached)
    stats = _aggregate_port_stats(db, account)
    return SyncResponse(
        ok=True,
        sync_result=sync_result,
        stats=stats,
        synced_at=datetime.now(timezone.utc),
    )


@router.patch("/{port_id}/limits", response_model=PortStats)
def update_limits(
    port_id: str,
    body: LimitsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ตั้ง/แก้ HP / Stamina limit ของ port (display-only — Phase 1 ไม่ enforce)

    Semantic (per field):
      - ไม่ส่ง field มา → ไม่แตะค่าเดิม
      - ส่ง null      → disable bar นั้น (clear limit)
      - ส่ง float > 0 → set limit ใหม่ (ค่า ≤ 0 reject)

    ใช้ model_fields_set แยก "omitted" จาก "explicit null" (Pydantic v2 pattern)
    เดียวกับ SubscriptionUpdate.safety_cut_dd

    HP semantic ("Total Life" / cap model):
      - hp_limit_usd = ceiling cap ("Total Life") ที่ user ตั้ง
      - bar fill = balance_cached / hp_limit_usd
      - Validation: hp_limit_usd >= balance_cached
        → กัน "HP เหลือ" (= cap - balance) ไป negative
        → user ต้อง sync หรือเลือก cap สูงกว่า balance ปัจจุบัน
      - ไม่ต้อง snapshot baseline (ใช้ balance_cached ตรงๆ)
      - clear limit (ส่ง null) → hp bar ซ่อน (frontend ไม่แสดง)

    Validation:
      - ownership (404 ถ้าไม่ใช่เจ้าของ — กัน id enumeration)
      - verified=True (400 ถ้ายัง verified ไม่ผ่าน — balance_cached ยังไม่มี)

    Returns: updated PortStats (รวม risk_budget ใหม่)
    """
    account = _get_owned_account(db, port_id, current_user.id)
    if not account.verified:
        raise HTTPException(400, "บัญชี MT5 ยังไม่ได้ verified")

    fields_sent = body.model_fields_set

    if "hp_limit_usd" in fields_sent:
        new_val = body.hp_limit_usd
        if new_val is not None:
            if new_val <= 0:
                raise HTTPException(
                    400, "hp_limit_usd ต้อง > 0 (หรือ null เพื่อปิด bar)",
                )
            # NEW constraint: total_life >= balance_cached
            # (ไม่งั้น "HP เหลือ" = cap - balance จะติดลบ)
            current_balance = account.balance_cached or 0.0
            if new_val < float(current_balance):
                raise HTTPException(
                    400,
                    f"total_life (${new_val:.2f}) ต้อง >= balance ปัจจุบัน "
                    f"(${float(current_balance):.2f}) — sync บัญชีก่อน "
                    f"หรือตั้งค่าใหม่ให้สูงกว่า",
                )
            account.hp_limit_usd = float(new_val)
            logger.info(
                "HP cap set port=%s total_life=%s balance=%s",
                account.id, account.hp_limit_usd, current_balance,
            )
        else:
            # disable HP → clear cap (frontend ซ่อน bar)
            # ไม่ต้อง validate balance — disable ทำได้เสมอ
            account.hp_limit_usd = None
            logger.info("HP cap cleared port=%s", account.id)

    if "stamina_limit_usd" in fields_sent:
        new_val = body.stamina_limit_usd
        if new_val is not None and new_val <= 0:
            raise HTTPException(
                400, "stamina_limit_usd ต้อง > 0 (หรือ null เพื่อปิด bar)",
            )
        account.stamina_limit_usd = new_val

    db.commit()
    db.refresh(account)
    logger.info(
        "update_limits port=%s user=%s hp=%s stamina=%s",
        account.id, current_user.id,
        account.hp_limit_usd, account.stamina_limit_usd,
    )
    return _aggregate_port_stats(db, account)


def _order_to_history_item(
    order: Order,
    run_code: Optional[str],
    strategy_code: Optional[str],
    timeframe: Optional[str],
) -> OrderHistoryItem:
    """แปลง Order row (+ subscription meta) → OrderHistoryItem schema"""
    return OrderHistoryItem(
        id=order.id,
        ticket=int(order.ticket),
        subscription_id=order.subscription_id,
        run_code=run_code,
        strategy_code=strategy_code,
        symbol=order.symbol,
        timeframe=timeframe,
        type=order.type,
        volume=float(order.volume),
        open_price=float(order.open_price),
        sl=float(order.sl) if order.sl is not None else None,
        tp=float(order.tp) if order.tp is not None else None,
        close_price=(
            float(order.close_price) if order.close_price is not None else None
        ),
        profit=float(order.profit) if order.profit is not None else None,
        close_reason=order.close_reason,
        status=order.status,
        magic=int(order.magic),
        opened_at=order.opened_at,
        closed_at=order.closed_at,
    )


@router.get("/{port_id}/orders", response_model=list[OrderHistoryItem])
def get_port_orders(
    port_id: str,
    limit: int = Query(50, ge=1, le=200),
    status: str = Query("all", pattern="^(all|open|closed)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Order history ของ port (closed + open) เรียงใหม่→เก่า

    Query:
      - limit: 1..200 (default 50)
      - status: all | open | closed (default all)

    Ownership: _get_owned_account() → 404 ถ้าไม่ใช่เจ้าของ port
    JOIN subscriptions: ดึง run_code/strategy_code/timeframe (snapshot ตอน subscribe)
    ใช้ inner join — orders ทุกแถวต้องมี subscription_id (FK NOT NULL)
    """
    # ownership check (404 ถ้าไม่ใช่เจ้าของ — กัน id enumeration)
    account = _get_owned_account(db, port_id, current_user.id)

    Sub = UserStrategySubscription
    q = (
        db.query(Order, Sub.run_code, Sub.strategy_code, Sub.timeframe)
        .join(Sub, Sub.id == Order.subscription_id)
        .filter(Sub.mt5_account_id == account.id)
    )
    if status != "all":
        q = q.filter(Order.status == status)
    q = q.order_by(Order.opened_at.desc()).limit(limit)

    rows = q.all()
    logger.info(
        "get_port_orders port=%s user=%s status=%s limit=%d count=%d",
        account.id, current_user.id, status, limit, len(rows),
    )
    return [
        _order_to_history_item(o, run_code, strat_code, tf)
        for o, run_code, strat_code, tf in rows
    ]


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """แปลง ISO string จาก VPS → datetime (รองรับทั้ง 'Z' suffix และ +offset)

    VPS อาจส่ง '2024-06-01T12:00:00Z' หรือ '2024-06-01T12:00:00+00:00'
    datetime.fromisoformat() ใน Python 3.11+ รองรับ Z แล้ว แต่ replace ให้ชัวร์
    คืน None ถ้า input None / parse fail (ไม่ raise — กัน sync ล้มทั้ง batch)
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _trade_to_item(row: MT5Trade) -> TradeItem:
    """แปลง MT5Trade ORM row → TradeItem schema (สำหรับ GET response)"""
    return TradeItem(
        position_id=row.position_id,
        symbol=row.symbol,
        type=row.type,
        volume=row.volume,
        open_time=row.open_time,
        open_price=row.open_price,
        close_time=row.close_time,
        close_price=row.close_price,
        profit=row.profit,
        magic=row.magic,
        comment=row.comment or "",
        status=row.status,
        source=row.source,
    )


@router.get("/{port_id}/trades", response_model=TradesResponse)
def list_trades(
    port_id: str,
    days_back: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    source: str = Query("all", regex="^(all|bot|manual)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """อ่าน trade history จาก DB cache — fast, ไม่เรียก VPS

    ใช้ในหน้า port detail (initial render) — frontend แสดง cached data ทันที
    แล้วค่อย trigger POST /trades/sync แยกถ้า user กดปุ่ม refresh.

    Query:
      - days_back: 1..365 (default 30) — window ย้อนหลัง (filter จาก open_time)
      - limit:     1..500 (default 50) — จำนวน rows สูงสุด (ORDER BY open_time DESC)
      - source:    all | bot | manual (default all)

    Ownership: _get_owned_account() → 404 ถ้าไม่ใช่เจ้าของ
    NOTE: ไม่ต้อง verified check — DB cache อ่านได้แม้ port ยัง verify ไม่ผ่าน
    """
    account = _get_owned_account(db, port_id, current_user.id)

    from_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
    q = (
        db.query(MT5Trade)
        .filter(MT5Trade.mt5_account_id == account.id)
        .filter(MT5Trade.open_time >= from_dt)
    )
    if source != "all":
        q = q.filter(MT5Trade.source == source)

    rows = (
        q.order_by(MT5Trade.open_time.desc())
        .limit(limit)
        .all()
    )

    # last_synced_at จากแถวล่าสุดของ port (ไม่จำกัด window — ใช้แสดงเวลา sync ครั้งสุดท้าย)
    last_sync = (
        db.query(func.max(MT5Trade.synced_at))
        .filter(MT5Trade.mt5_account_id == account.id)
        .scalar()
    )

    logger.info(
        "list_trades port=%s user=%s days_back=%d source=%s returned=%d",
        account.id, current_user.id, days_back, source, len(rows),
    )

    return TradesResponse(
        trades=[_trade_to_item(r) for r in rows],
        total_in_window=len(rows),
        last_synced_at=last_sync,
    )


@router.post("/{port_id}/trades/sync", response_model=SyncTradesResponse)
async def sync_trades(
    port_id: str,
    body: TradesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger VPS fetch จาก MT5 → UPSERT into mt5_trades → return summary

    Orchestration flow:
      ① Ownership check (404 ถ้าไม่ใช่เจ้าของ port)
      ② verified check (400 ถ้ายัง verified ไม่ผ่าน — login MT5 ไม่ได้)
      ③ Decrypt password (500 ถ้า decrypt fail)
      ④ Call VPS /bot/fetch-trades (timeout 60s)
         - VPS unreachable → 503 propagate (ไม่ swallow — user รอผล)
      ⑤ UPSERT trades:
         - existing row (match by mt5_account_id + position_id) → update mutable
           fields (close_*, profit, status, synced_at) + floating PnL ของ open trade
         - new row → INSERT ทั้งแถว
      ⑥ Return summary {new, updated, total_in_window, synced_at}

    Body:
      - days_back: 1..365 (default 30)
      - limit:     1..500 (default 50)

    UPSERT key: (mt5_account_id, position_id) — DB-level UNIQUE
    Immutable fields หลัง insert: symbol, type, volume, open_time, open_price,
                                   magic, source, comment
    Mutable fields (update ทุก sync): close_time, close_price, profit, status, synced_at
    """
    # ① + ② ownership + verified
    account = _get_owned_account(db, port_id, current_user.id)
    if not account.verified:
        raise HTTPException(400, "บัญชี MT5 ยังไม่ได้ verified")

    # ③ decrypt password (ห้าม log)
    try:
        plain_pw = encryption.decrypt(account.encrypted_password)
    except Exception:
        logger.exception(
            "sync_trades decrypt failed port=%s user=%s",
            account.id, current_user.id,
        )
        raise HTTPException(500, "ไม่สามารถถอดรหัส MT5 password ได้")

    # ④ call VPS — vps_client.fetch_trades raise HTTPException(503) เอง
    if not vps_client.is_configured():
        logger.warning(
            "sync_trades VPS not configured port=%s", account.id,
        )
        raise HTTPException(503, "VPS ยังไม่ได้ตั้งค่า")

    result = await vps_client.fetch_trades(
        login=account.login,
        password=plain_pw,
        server=account.server,
        days_back=body.days_back,
        limit=body.limit,
    )

    # ⑤ UPSERT
    now_utc = datetime.now(timezone.utc)
    new_count = 0
    updated_count = 0
    for t in result.get("trades", []) or []:
        try:
            position_id = int(t["position_id"])
        except (KeyError, TypeError, ValueError):
            # skip malformed row — VPS ควรไม่ส่ง แต่กัน sync ล้มทั้ง batch
            logger.warning(
                "sync_trades skip malformed trade (no position_id) port=%s",
                account.id,
            )
            continue

        row = (
            db.query(MT5Trade)
            .filter(MT5Trade.mt5_account_id == account.id)
            .filter(MT5Trade.position_id == position_id)
            .first()
        )

        if row:
            # update mutable fields — รวม floating PnL ของ open trade
            # + finalize profit/close_* เมื่อ position ปิดไปแล้ว
            row.close_time = _parse_iso(t.get("close_time"))
            row.close_price = t.get("close_price")
            row.profit = t.get("profit")
            row.status = t.get("status", row.status)
            row.synced_at = now_utc
            updated_count += 1
        else:
            db.add(MT5Trade(
                mt5_account_id=account.id,
                position_id=position_id,
                symbol=t["symbol"],
                type=t["type"],
                volume=float(t["volume"]),
                open_time=_parse_iso(t["open_time"]),
                open_price=float(t["open_price"]),
                close_time=_parse_iso(t.get("close_time")),
                close_price=t.get("close_price"),
                profit=t.get("profit"),
                magic=int(t.get("magic", 0) or 0),
                comment=(t.get("comment") or "")[:64],
                status=t["status"],
                source=t["source"],
                synced_at=now_utc,
            ))
            new_count += 1

    db.commit()

    total_in_window = int(result.get("total_in_window", 0) or 0)
    logger.info(
        "trades sync port=%s user=%s new=%d updated=%d total_window=%d",
        account.id, current_user.id, new_count, updated_count, total_in_window,
    )

    return SyncTradesResponse(
        ok=True,
        new=new_count,
        updated=updated_count,
        total_in_window=total_in_window,
        synced_at=now_utc,
    )
