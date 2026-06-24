"""Order model — live trade order (ต่างจาก backtest_runs)

หนึ่งแถว = หนึ่ง live order ที่ bot ยิงผ่าน VPS
UNIQUE (subscription_id, signal_bar_time, type) เป็น DB-level dedup
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    BigInteger,
    Float,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from database import Base


# Status enum (string column — รองรับ SQLite + PG)
ORDER_STATUSES = ("pending", "open", "closed", "cancelled", "error")
ORDER_TYPES = ("BUY", "SELL")
CLOSE_REASONS = ("sl", "tp", "safety_cut", "manual", "expired")


class Order(Base):
    """Live trade order ที่ bot ยิงออกผ่าน MT5 — ผูกกับ subscription ต้นทาง"""

    __tablename__ = "orders"
    __table_args__ = (
        # critical DB-level dedup — กันยิงซ้ำเมื่อ scheduler ตี 4 ครั้ง/ชั่วโมง
        UniqueConstraint(
            "subscription_id", "signal_bar_time", "type",
            name="uq_order_subscription_signal_type",
        ),
        Index("ix_order_subscription_id", "subscription_id"),
        Index("ix_order_user_id", "user_id"),
        Index("ix_order_signal_bar_time", "signal_bar_time"),
        Index("ix_order_status", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # ─── identity / FK ────────────────────────────────────────────────
    subscription_id = Column(
        String, ForeignKey("user_strategy_subscriptions.id"), nullable=False,
    )
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    ticket = Column(BigInteger, nullable=False)              # MT5 deal ticket

    # ─── dedup key ────────────────────────────────────────────────────
    signal_bar_time = Column(DateTime, nullable=False)       # UTC — เวลาเปิดแท่งที่ทริกเกอร์

    # ─── trade details ────────────────────────────────────────────────
    symbol = Column(String(32), nullable=False)
    type = Column(String(8), nullable=False)                 # "BUY" / "SELL"
    volume = Column(Float, nullable=False)                   # lot จริง
    open_price = Column(Float, nullable=False)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)

    # ─── close details (nullable จนกว่าจะปิด) ───────────────────────────
    close_price = Column(Float, nullable=True)
    profit = Column(Float, nullable=True)
    close_reason = Column(String(16), nullable=True)         # sl / tp / safety_cut / manual / expired

    # ─── meta ────────────────────────────────────────────────────────
    magic = Column(Integer, nullable=False)                  # stable hash จาก subscription_id
    status = Column(String(16), nullable=False, default="open")

    # ─── timestamps ──────────────────────────────────────────────────
    opened_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    closed_at = Column(DateTime, nullable=True)
