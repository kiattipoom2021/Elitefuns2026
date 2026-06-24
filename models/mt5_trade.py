"""MT5Trade — persisted snapshot ของ MT5 trades ต่อ port

แยกจาก orders table (ผูก subscription) เพราะ:
- รวม manual trades (magic=0) ที่ไม่ผ่าน bot
- decouple จาก subscription lifecycle (sub ลบไม่กระทบ trade history)
- UPSERT ทุกครั้งที่ user กด sync

Schema design:
- UNIQUE (mt5_account_id, position_id) → คีย์หลักสำหรับ UPSERT
  (position_id ของ MT5 unique ภายในบัญชีเดียว — รวม manual + bot)
- Index (mt5_account_id, open_time) → คิวรีหน้า list (ORDER BY open_time DESC)
- Index (mt5_account_id, status) → คิวรีแยก open/closed ถ้าต้องการในอนาคต
- mutable fields (close_*, profit, status, synced_at) ถูก update ทุก sync;
  immutable fields (symbol, type, volume, open_time, open_price, magic, source)
  คงเดิมหลัง insert ครั้งแรก
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


class MT5Trade(Base):
    """Snapshot ของ trade หนึ่งตัวจาก MT5 (รวม manual + bot)

    หนึ่งแถว = หนึ่ง MT5 position (เปิด/ปิด) — sync ทุกครั้งที่ user กด refresh
    """

    __tablename__ = "mt5_trades"
    __table_args__ = (
        UniqueConstraint(
            "mt5_account_id", "position_id",
            name="uq_mt5_trade_account_position",
        ),
        Index(
            "ix_mt5_trade_account_open_time",
            "mt5_account_id", "open_time",
        ),
        Index(
            "ix_mt5_trade_account_status",
            "mt5_account_id", "status",
        ),
    )

    id = Column(
        String, primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    mt5_account_id = Column(
        String, ForeignKey("mt5_accounts.id"), nullable=False,
    )
    position_id = Column(BigInteger, nullable=False)

    # ─── trade identity ───────────────────────────────────────────────
    symbol = Column(String(32), nullable=False)
    type = Column(String(8), nullable=False)         # BUY / SELL
    volume = Column(Float, nullable=False)

    # ─── open snapshot (immutable หลัง insert) ────────────────────────
    open_time = Column(DateTime, nullable=False)     # UTC
    open_price = Column(Float, nullable=False)

    # ─── close snapshot (mutable — เปลี่ยนเมื่อ position ปิด) ──────────
    close_time = Column(DateTime, nullable=True)     # UTC
    close_price = Column(Float, nullable=True)
    profit = Column(Float, nullable=True)            # floating (open) / realized (closed)

    # ─── meta ─────────────────────────────────────────────────────────
    magic = Column(Integer, nullable=False, default=0)
    comment = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False)      # open / closed
    source = Column(String(16), nullable=False)      # bot / manual

    # ─── sync metadata ────────────────────────────────────────────────
    synced_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
