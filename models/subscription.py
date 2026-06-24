"""UserStrategySubscription model — user เลือก backtest combo มาผูกกับ MT5 port + risk multiplier

หนึ่งแถว = หนึ่ง strategy ที่ user เปิดให้ bot รันต่อ port
Multiple subscriptions ต่อ (user, mt5_account) ได้ แต่ห้ามซ้ำ run_code
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Float,
    DateTime,
    ForeignKey,
    JSON,
    UniqueConstraint,
    Index,
)
from database import Base


# Status enum (ใช้เป็น string column — กัน DB-specific enum type issues)
SUBSCRIPTION_STATUSES = ("active", "paused", "stopped")


class UserStrategySubscription(Base):
    """Subscription ของ user — ผูก backtest combo เข้ากับ MT5 port ที่ verified แล้ว"""

    __tablename__ = "user_strategy_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "mt5_account_id", "run_code",
            name="uq_sub_user_account_runcode",
        ),
        Index("ix_sub_user_id", "user_id"),
        Index("ix_sub_status", "status"),
        Index("ix_sub_mt5_account_id", "mt5_account_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # ─── identity / FK ────────────────────────────────────────────────
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    mt5_account_id = Column(String, ForeignKey("mt5_accounts.id"), nullable=False)
    backtest_run_id = Column(String, ForeignKey("backtest_runs.id"), nullable=False)

    # ─── snapshot จาก backtest_run (freeze ตอน subscribe) ─────────────
    run_code = Column(String(40), nullable=False)            # "H1MaEU20x100lk10rr2"
    strategy_code = Column(String(64), nullable=False)       # "ma_cross"
    symbol = Column(String(32), nullable=False)
    timeframe = Column(String(8), nullable=False)
    params = Column(JSON, nullable=False)                    # snapshot: {fast, slow, sl_lookback, rr_ratio, risk_per_trade_usd}

    # ─── user-controlled ──────────────────────────────────────────────
    risk_multiplier = Column(Float, nullable=False, default=1.0)
    status = Column(String(16), nullable=False, default="active")

    # ─── bot tracking ─────────────────────────────────────────────────
    last_check_at = Column(DateTime, nullable=True)
    last_signal_at = Column(DateTime, nullable=True)
    last_order_at = Column(DateTime, nullable=True)

    # ─── safety cut (per-subscription DD threshold) ───────────────────
    # safety_cut_dd: USD threshold — null = disabled (opt-out)
    # peak_pnl_usd: running peak ของ cumulative PnL (ขึ้น monotone — update เมื่อ current สูงกว่า)
    # current_pnl_usd: latest cumulative PnL ที่ VPS push ล่าสุด (closed + floating)
    # current_dd_usd: computed = max(0, peak - current) — ไม่เก็บใน DB, render ใน response
    # last_pnl_update_at: timestamp ที่ VPS push ค่าล่าสุด (null = ยังไม่เคย update)
    safety_cut_dd = Column(Float, nullable=True)
    peak_pnl_usd = Column(Float, nullable=False, default=0.0)
    current_pnl_usd = Column(Float, nullable=False, default=0.0)
    last_pnl_update_at = Column(DateTime, nullable=True)

    # ─── timestamps ───────────────────────────────────────────────────
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
