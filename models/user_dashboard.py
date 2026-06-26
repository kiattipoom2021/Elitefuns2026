"""UserDashboardLayout — per-user widget layout (Console Data + future dashboards)

Schema:
- UNIQUE (user_id, dashboard_key) → คีย์หลัก: หนึ่ง user มีหนึ่ง layout ต่อ dashboard
- layout_json: serialized layout (array ของ {type, config, x, y, w, h})
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, DateTime, ForeignKey, UniqueConstraint, JSON, Index,
)
from database import Base


class UserDashboardLayout(Base):
    __tablename__ = "user_dashboard_layouts"
    __table_args__ = (
        UniqueConstraint("user_id", "dashboard_key", name="uq_user_dashboard"),
        Index("ix_user_dashboard_user", "user_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    dashboard_key = Column(String(64), nullable=False)  # e.g. "console-data"
    layout_json = Column(JSON, nullable=False, default=list)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
