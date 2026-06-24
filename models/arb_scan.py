"""ArbScan model — เก็บผลลัพธ์ scan arbitrage Z-score ต่อ pair (snapshot ล่าสุดเท่านั้น)

Scoping: (user_id, mt5_login_used) — แต่ละ user + แต่ละ account มี snapshot ของตัวเอง
Atomic wipe + insert: ใช้ใน arbitrage router — ดู `docs/ARBITRAGE_FLOW.md`
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Index,
    JSON,
)
from database import Base


class ArbScan(Base):
    """หนึ่ง row = หนึ่ง pair ในผลลัพธ์ scan ครั้งล่าสุด ของบัญชี MT5 หนึ่งบัญชี"""

    __tablename__ = "arb_scans"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    mt5_login_used = Column(Integer, nullable=False, index=True)

    # ─── Scan config ที่ใช้รอบนี้ ──────────────────────────────────────
    timeframe = Column(String(8), nullable=False)         # M15/M30/H1/H4/D1
    lookback_bars = Column(Integer, nullable=False)

    # ─── Pair info (high/low ตัดสินจาก mean price รันไทม์) ─────────────
    sym_high = Column(String(20), nullable=False)
    sym_low = Column(String(20), nullable=False)

    # ─── ผลลัพธ์การคำนวณ (nullable — degenerate กรณี var/std=0) ────────
    beta = Column(Float, nullable=True)
    current_spread = Column(Float, nullable=True)
    mean_spread = Column(Float, nullable=True)
    std_spread = Column(Float, nullable=True)
    z_score = Column(Float, nullable=True)
    z_series = Column(JSON, nullable=True)                # list[float] | None — z-score per bar
    bars_used = Column(Integer, nullable=False)

    scan_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_arb_user_login", "user_id", "mt5_login_used"),
        # Postgres รองรับ DESC NULLS LAST; SQLite จะ map เป็น simple desc index
        Index("idx_arb_z_score", z_score.desc()),
    )
