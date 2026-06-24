"""Arbitrage router — scan spread Z-score ข้าม pair universe (JWT-gated)

Pattern:
  POST /api/arbitrage/scan       → trigger VPS scan + atomic wipe+insert
  GET  /api/arbitrage/latest     → อ่าน snapshot ล่าสุดของ (user, mt5_login)

Cross-user prevention: ทุก query filter user_id == current_user.id
Atomic: DELETE + INSERT ใน transaction เดียว — commit ทีเดียวตอนจบ
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from database import get_db
from models.arb_scan import ArbScan
from models.mt5_account import MT5Account
from models.user import User
from routers.auth import get_current_user
from services import encryption, vps_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])


# ─── Schemas ──────────────────────────────────────────────────────────
class ArbScanRequest(BaseModel):
    mt5_account_id: str
    timeframe: str = Field(..., pattern="^(M15|M30|H1|H4|D1)$")
    lookback_bars: int = Field(200, ge=50, le=1000)


class ArbScanRow(BaseModel):
    sym_high: str
    sym_low: str
    beta: Optional[float] = None
    current_spread: Optional[float] = None
    mean_spread: Optional[float] = None
    std_spread: Optional[float] = None
    z_score: Optional[float] = None
    bars_used: int
    z_series: Optional[list[float]] = None

    class Config:
        from_attributes = True


class ArbScanResult(BaseModel):
    scan_at: Optional[datetime] = None
    timeframe: Optional[str] = None
    lookback_bars: Optional[int] = None
    mt5_login_used: Optional[int] = None
    scans: list[ArbScanRow]


# ─── Helpers ──────────────────────────────────────────────────────────
def _get_owned_account(db: Session, account_id: str, user_id: str) -> MT5Account:
    """ค้นหา account ที่เป็นของ user ปัจจุบัน — 404 ถ้าไม่เจอ (no info leak)"""
    account = (
        db.query(MT5Account)
        .filter(MT5Account.id == account_id, MT5Account.user_id == user_id)
        .first()
    )
    if not account:
        raise HTTPException(404, "ไม่พบบัญชี MT5 นี้")
    return account


def _parse_scan_at(raw: Optional[str]) -> datetime:
    """แปลง iso string → datetime UTC; fallback = now ถ้า VPS ไม่ส่ง"""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _wipe_and_insert_scans(
    db: Session,
    user_id: str,
    account: MT5Account,
    body: ArbScanRequest,
    result: dict,
) -> list[ArbScan]:
    """Atomic wipe ของเก่าของ (user, login) + insert ใหม่ — commit ครั้งเดียว"""
    db.query(ArbScan).filter(
        ArbScan.user_id == user_id,
        ArbScan.mt5_login_used == account.login,
    ).delete(synchronize_session=False)

    scan_at = _parse_scan_at(result.get("scan_at"))
    rows: list[ArbScan] = []
    for s in result.get("scans", []):
        row = ArbScan(
            user_id=user_id,
            mt5_login_used=account.login,
            timeframe=body.timeframe,
            lookback_bars=body.lookback_bars,
            sym_high=s.get("sym_high", ""),
            sym_low=s.get("sym_low", ""),
            beta=s.get("beta"),
            current_spread=s.get("current_spread"),
            mean_spread=s.get("mean_spread"),
            std_spread=s.get("std_spread"),
            z_score=s.get("z_score"),
            z_series=s.get("z_series"),
            bars_used=s.get("bars_used", 0),
            scan_at=scan_at,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def _rows_to_result(rows: list[ArbScan]) -> ArbScanResult:
    """แปลง list[ArbScan] → ArbScanResult (อ่าน meta จาก row แรก ถ้ามี)"""
    if not rows:
        return ArbScanResult(scans=[])
    first = rows[0]
    return ArbScanResult(
        scan_at=first.scan_at,
        timeframe=first.timeframe,
        lookback_bars=first.lookback_bars,
        mt5_login_used=first.mt5_login_used,
        scans=[ArbScanRow.model_validate(r) for r in rows],
    )


# ─── Endpoints ────────────────────────────────────────────────────────
@router.post("/scan", response_model=ArbScanResult)
async def trigger_scan(
    body: ArbScanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """รัน arbitrage scan ผ่าน VPS แล้วบันทึก snapshot ใหม่ (atomic wipe+insert)"""
    account = _get_owned_account(db, body.mt5_account_id, current_user.id)
    if not account.verified:
        raise HTTPException(400, "บัญชี MT5 ยังไม่ verify")

    # decrypt password เก็บใน local var เท่านั้น — ห้าม log
    plain_pw = encryption.decrypt(account.encrypted_password)

    result = await vps_client.arb_scan(
        login=account.login,
        password=plain_pw,
        server=account.server,
        timeframe=body.timeframe,
        lookback_bars=body.lookback_bars,
    )

    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "scan failed"))

    rows = _wipe_and_insert_scans(db, current_user.id, account, body, result)
    # เรียงตาม |z_score| DESC (NULLS last) ให้ตรงกับ GET /latest
    rows.sort(key=lambda r: abs(r.z_score) if r.z_score is not None else -1.0, reverse=True)
    return _rows_to_result(rows)


@router.get("/latest", response_model=ArbScanResult)
def get_latest(
    mt5_account_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """อ่าน snapshot ล่าสุดของ (user, mt5_login) — sort by |z_score| DESC NULLS last"""
    account = _get_owned_account(db, mt5_account_id, current_user.id)

    # |z_score| DESC NULLS last — ใช้ CASE เพื่อรองรับทั้ง Postgres + SQLite
    abs_z = func.abs(ArbScan.z_score)
    null_first = case((ArbScan.z_score.is_(None), 1), else_=0)
    rows = (
        db.query(ArbScan)
        .filter(
            ArbScan.user_id == current_user.id,
            ArbScan.mt5_login_used == account.login,
        )
        .order_by(null_first.asc(), abs_z.desc())
        .all()
    )
    return _rows_to_result(rows)
