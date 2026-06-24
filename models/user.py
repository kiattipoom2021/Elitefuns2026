"""User model — owns auth credentials"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Boolean
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Admin flag — granted via SUPER_USER_EMAILS env (auto-promote on login)
    # หรือ manual UPDATE ใน DB
    is_admin = Column(Boolean, nullable=False, default=False, server_default="false")
