"""MT5Account model — เก็บข้อมูลบัญชี MT5 ของ user (password เข้ารหัส)"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from database import Base


class MT5Account(Base):
    """หนึ่ง user มีได้หลายบัญชี MT5 — แต่ห้ามซ้ำ login ในบัญชี user เดียวกัน"""

    __tablename__ = "mt5_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "login", name="uq_mt5_user_login"),
        Index("ix_mt5_user_id", "user_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # ─── ข้อมูลที่ user กรอก ────────────────────────────────────────────
    name = Column(String, nullable=False)           # ชื่อเล่นที่ user ตั้ง
    login = Column(Integer, nullable=False)         # MT5 login number
    encrypted_password = Column(String, nullable=False)  # Fernet-encrypted
    server = Column(String, nullable=False)         # broker server เช่น "Exness-Trial5"
    broker = Column(String, nullable=True)          # display name (optional)

    # ─── สถานะการเชื่อมต่อ ─────────────────────────────────────────────
    verified = Column(Boolean, default=False, nullable=False)

    # ─── ข้อมูล cache จาก VPS ─────────────────────────────────────────
    balance_cached = Column(Float, nullable=True)
    equity_cached = Column(Float, nullable=True)
    leverage_cached = Column(Integer, nullable=True)
    currency_cached = Column(String, nullable=True)
    last_check = Column(DateTime, nullable=True)

    # ─── HP / Stamina risk budget (user-set, optional) ──────────────────
    # NULL = bar ซ่อนจาก UI (user ยังไม่ได้ตั้ง limit)
    #
    # HP semantic (NEW — "balance vs cap" model):
    #   - hp_limit_usd = "Total Life" / ceiling cap ที่ user ตั้งเอง (เช่น $20,000)
    #   - HP bar fill % = balance_cached / hp_limit_usd
    #   - "HP เหลือ" (label) = hp_limit_usd - balance_cached (headroom จาก balance ถึง cap)
    #   - port "แตก" = balance_cached drops to 0 → bar fill = 0%
    #   - Validation: hp_limit_usd >= balance_cached ตอน PATCH (ไม่งั้น label จะ negative)
    #
    # initial_balance_usd = snapshot ครั้งแรกที่ verify สำเร็จ → เก็บไว้เป็น historical ref
    # equity_midnight_usd = snapshot equity ตอนเริ่มวันใหม่ UTC → baseline ของ Stamina bar
    #
    # DEPRECATED (ไม่ใช้แล้วในการคำนวณ HP — เก็บ column ใน DB ไว้เพราะ auto_migrate
    # ลบ column ไม่ได้; drop เป็น manual op ที่ต้องใช้ Alembic):
    #   - hp_baseline_equity_usd: baseline ของระบบเก่า (drawdown-from-baseline model)
    #   - hp_baseline_at: timestamp ของ baseline เก่า
    # ดู docs/PORT_STATS_FLOW.md § HP/Stamina Risk Budget Display
    hp_limit_usd = Column(Float, nullable=True)
    stamina_limit_usd = Column(Float, nullable=True)
    initial_balance_usd = Column(Float, nullable=True)
    initial_balance_at = Column(DateTime, nullable=True)
    # DEPRECATED — kept for backwards-compat; never read/written by current code
    hp_baseline_equity_usd = Column(Float, nullable=True)
    hp_baseline_at = Column(DateTime, nullable=True)
    equity_midnight_usd = Column(Float, nullable=True)
    equity_midnight_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
