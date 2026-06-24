"""BacktestRun model — one row per (strategy, params) backtest result"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Date,
    DateTime,
    ForeignKey,
    JSON,
    Index,
)
from database import Base


class BacktestRun(Base):
    """ผลลัพธ์ backtest หนึ่งชุดพารามิเตอร์ — ไม่เก็บ equity curve เพื่อประหยัด"""

    __tablename__ = "backtest_runs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # ─── identity ─────────────────────────────────────────────────────
    strategy_id = Column(
        Integer,
        ForeignKey("strategies.id"),
        nullable=False,
        index=True,
    )
    mt5_login_used = Column(Integer, nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False)

    # ─── สิ่งที่ป้อนเข้า ────────────────────────────────────────────────
    params = Column(JSON, nullable=False)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)

    # ─── ผลลัพธ์ ───────────────────────────────────────────────────────
    net_profit = Column(Float, nullable=False)
    profit_pct = Column(Float, nullable=False)
    total_trades = Column(Integer, nullable=False)
    win_trades = Column(Integer, nullable=False)
    loss_trades = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=False)        # 0..1
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=False)   # USD (เปลี่ยนจาก fraction ตั้งแต่ engine v0.5.0)
    recovery_factor = Column(Float, nullable=True) # net_profit / max_drawdown (MAR ratio)

    # ─── trades (optional, NULL สำหรับ legacy runs ก่อน engine ส่ง trades) ─────
    # โครงสร้าง: list[dict] — แต่ละ trade มี keys:
    #   open_time, close_time (ISO UTC), type ("BUY"|"SELL"), volume,
    #   open_price, close_price, sl, tp, profit, close_reason ("tp"|"sl"|"eos")
    # Legacy: existing rows จะมีค่า NULL — frontend ต้อง handle ว่า "ไม่มี trade data"
    trades_json = Column(JSON, nullable=True)

    # ─── metadata ─────────────────────────────────────────────────────
    engine_version = Column(String(20), nullable=False)
    run_code = Column(String(40), nullable=True, index=True)  # compact ID เช่น "H1MaEU05x20lk10rr2"
    run_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("idx_btr_strategy_symbol_tf", "strategy_id", "symbol", "timeframe"),
        Index("idx_btr_net_profit", net_profit.desc()),
        Index("idx_btr_sharpe", sharpe_ratio.desc()),
    )
