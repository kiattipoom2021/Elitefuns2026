"""TVOhlc — cached OHLC bars จาก TradingView

Scheduler (services/tv_scheduler.py) ดึงข้อมูลทุกชม. + upsert ลงตารางนี้
Widget endpoints อ่านจากตารางนี้ ไม่เรียก TV ตรง

Schema:
- UNIQUE (symbol, tf, ts) → คีย์หลักสำหรับ UPSERT
- Index (symbol, tf, ts DESC) → คิวรี "last N bars" สำหรับ Currency Strength + Market Snapshot
"""
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Float, UniqueConstraint, Index, Integer
from database import Base


class TVOhlc(Base):
    """หนึ่งแถว = หนึ่ง OHLC bar (symbol, tf, ts)"""

    __tablename__ = "tv_ohlc"
    __table_args__ = (
        UniqueConstraint("symbol", "tf", "ts", name="uq_tv_ohlc_symbol_tf_ts"),
        Index("ix_tv_ohlc_symbol_tf_ts_desc", "symbol", "tf", "ts"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # symbol identity
    symbol = Column(String(32), nullable=False)        # e.g. "EURUSD"
    exchange = Column(String(16), nullable=False)      # e.g. "OANDA"
    tf = Column(String(8), nullable=False)             # H1 / D1

    # bar timestamp (UTC, candle open time)
    ts = Column(DateTime, nullable=False)

    # OHLCV
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False, default=0.0)

    # last refresh
    fetched_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
