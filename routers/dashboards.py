"""User dashboard layout sync — JWT-scoped

Endpoints:
  GET  /api/dashboards/{key}     → user's saved layout (empty list ถ้ายังไม่เคย save)
  PUT  /api/dashboards/{key}     → save layout (replace)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.user_dashboard import UserDashboardLayout
from routers.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])


_ALLOWED_KEYS = {"console-data"}


class LayoutPayload(BaseModel):
    layout: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/{key}")
def get_layout(
    key: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if key not in _ALLOWED_KEYS:
        raise HTTPException(404, f"unknown dashboard key: {key}")

    row = (
        db.query(UserDashboardLayout)
        .filter(
            UserDashboardLayout.user_id == user.id,
            UserDashboardLayout.dashboard_key == key,
        )
        .first()
    )
    if not row:
        return {"key": key, "layout": [], "updated_at": None}
    return {
        "key": key,
        "layout": row.layout_json or [],
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/{key}")
def put_layout(
    payload: LayoutPayload,
    key: str = Path(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if key not in _ALLOWED_KEYS:
        raise HTTPException(404, f"unknown dashboard key: {key}")

    # sanity check: layout ไม่ควรใหญ่เกินไป (กัน abuse)
    if len(payload.layout) > 100:
        raise HTTPException(400, "layout เกิน 100 widgets")

    row = (
        db.query(UserDashboardLayout)
        .filter(
            UserDashboardLayout.user_id == user.id,
            UserDashboardLayout.dashboard_key == key,
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    if row:
        row.layout_json = payload.layout
        row.updated_at = now
    else:
        row = UserDashboardLayout(
            user_id=user.id,
            dashboard_key=key,
            layout_json=payload.layout,
            updated_at=now,
        )
        db.add(row)
    db.commit()
    return {"key": key, "layout": row.layout_json, "updated_at": row.updated_at.isoformat()}
