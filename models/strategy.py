"""Strategy model — catalog of available trading strategies (e.g. MA Cross)"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from database import Base


class Strategy(Base):
    """หนึ่ง strategy ต่อหนึ่ง code — ใช้ code เป็น key อ้างอิงจาก VPS"""

    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
