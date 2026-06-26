"""Admin endpoints — list all MT5 accounts + trigger backtest sweep

Guard: ทุก endpoint ใน router นี้ต้องผ่าน `require_admin` (403 ถ้าไม่ใช่ admin)
"""
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models.mt5_account import MT5Account
from models.strategy import Strategy
from models.backtest_run import BacktestRun
from models.user import User
from routers.auth import require_admin
from services import encryption, vps_client, run_code
from services.constants import MAJOR_SYMBOLS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


# ─── Schemas ──────────────────────────────────────────────────────────
class AdminMT5AccountResponse(BaseModel):
    """Response สำหรับ /admin/mt5-accounts — ห้าม include encrypted_password"""

    id: str
    name: str
    login: int
    server: str
    broker: Optional[str] = None
    verified: bool
    last_check: Optional[datetime] = None

    class Config:
        from_attributes = True


class OptimizeRunRequest(BaseModel):
    mt5_account_id: str
    strategy_code: str = "ma_cross"
    timeframe: str = "H1"
    risk_per_trade_usd: float = Field(10.0, gt=0, le=10000)


class BacktestRunResponse(BaseModel):
    id: str
    strategy_id: int
    mt5_login_used: int
    symbol: str
    timeframe: str
    params: dict
    date_from: date
    date_to: date
    net_profit: float
    profit_pct: float
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    sharpe_ratio: Optional[float] = None
    max_drawdown: float                       # USD (engine v0.5.0+)
    recovery_factor: Optional[float] = None   # net_profit / max_drawdown
    engine_version: str
    run_code: Optional[str] = None            # compact ID เช่น "H1MaEU05x20lk10rr2"
    run_at: datetime

    class Config:
        from_attributes = True


class OptimizeRunResult(BaseModel):
    ok: bool
    inserted: int
    runs: list[BacktestRunResponse]


# ─── Helpers ──────────────────────────────────────────────────────────
def _insert_runs(
    db: Session,
    result: dict,
    strategy: Strategy,
    account: MT5Account,
    timeframe: str,
    date_from,
    date_to,
) -> list[BacktestRun]:
    """Wipe runs ของ (strategy, timeframe) เดิม แล้ว INSERT ผลใหม่ — atomic ใน 1 transaction.

    เรียก function นี้ "หลัง" VPS return success เท่านั้น — ถ้า VPS fail
    (caller raise HTTPException) จะไม่ถึงตรงนี้ → ของเก่าใน DB อยู่ครบ
    """
    engine_version = result.get("engine_version", "unknown")

    # 1. ลบ runs เก่าของ (strategy, timeframe) นี้ — keep TF อื่นไว้ให้เปรียบเทียบได้
    deleted = (
        db.query(BacktestRun)
        .filter(
            BacktestRun.strategy_id == strategy.id,
            BacktestRun.timeframe == timeframe,
        )
        .delete(synchronize_session=False)
    )
    if deleted:
        logger.info(
            "wiped %d old runs strategy=%s tf=%s ก่อน insert ใหม่",
            deleted, strategy.code, timeframe,
        )

    # 2. INSERT runs ใหม่ (ทุก symbol + ทุก combo) — flush เพื่อ get IDs โดยยังไม่ commit
    rows: list[BacktestRun] = []
    for run in result.get("runs", []):
        stats = run.get("stats", {})
        symbol = run.get("symbol", "")
        params = run.get("params", {})
        # trades: VPS engine (เวอร์ชันใหม่) จะส่ง list มาด้วย — ของเก่าจะไม่มี key นี้ → เก็บ NULL
        trades = run.get("trades")
        row = BacktestRun(
            strategy_id=strategy.id,
            mt5_login_used=account.login,
            symbol=symbol,
            timeframe=timeframe,
            params=params,
            date_from=date_from,
            date_to=date_to,
            net_profit=stats.get("net_profit", 0.0),
            profit_pct=stats.get("profit_pct", 0.0),
            total_trades=stats.get("total_trades", 0),
            win_trades=stats.get("win_trades", 0),
            loss_trades=stats.get("loss_trades", 0),
            win_rate=stats.get("win_rate", 0.0),
            sharpe_ratio=stats.get("sharpe_ratio"),
            max_drawdown=stats.get("max_drawdown", 0.0),
            recovery_factor=stats.get("recovery_factor"),
            trades_json=trades,  # NULL ถ้า VPS ยังไม่ส่ง (legacy engine)
            engine_version=engine_version,
            run_code=run_code.encode(timeframe, strategy.code, symbol, params),
        )
        db.add(row)
        rows.append(row)

    # 3. Commit ครั้งเดียว — DELETE + INSERT ใน transaction เดียว (atomic)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


# ─── Endpoints ────────────────────────────────────────────────────────
@router.get("/mt5-accounts", response_model=list[AdminMT5AccountResponse])
def list_all_accounts(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """ลิสต์บัญชี MT5 ทั้งหมดของทุก user — ไม่ filter user_id"""
    return db.query(MT5Account).order_by(MT5Account.created_at.desc()).all()


@router.post("/optimize/run", response_model=OptimizeRunResult)
async def trigger_optimize(
    body: OptimizeRunRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """รัน backtest sweep ผ่าน VPS แล้วบันทึกผลทุก combo ลง DB (NO AUTH)"""
    account = db.query(MT5Account).filter(MT5Account.id == body.mt5_account_id).first()
    if not account:
        raise HTTPException(404, "ไม่พบบัญชี MT5 นี้")

    strategy = db.query(Strategy).filter(Strategy.code == body.strategy_code).first()
    if not strategy:
        raise HTTPException(404, f"ไม่พบ strategy code={body.strategy_code}")

    # Sweep 4 มิติ — fast × slow × rr_ratio × sl_lookback (risk คงที่จาก form)
    # 5×4×3×3 = 180, กรอง fast<slow → 162 combos × 7 symbols = 1,134 runs
    params_sweep = [
        {
            "fast": f, "slow": s,
            "sl_lookback": sl,
            "rr_ratio": rr,
            "risk_per_trade_usd": body.risk_per_trade_usd,
        }
        for f in [5, 10, 15, 20, 25]
        for s in [20, 30, 50, 100]
        for rr in [1.5, 2.0, 3.0]
        for sl in [10, 20, 30]
        if f < s
    ]
    today = datetime.now(timezone.utc).date()
    # Sweep window: 1 ปี (ขยายจาก 180 วัน → 365 วัน) — ให้ statistical significance สูงขึ้น
    date_from = today - timedelta(days=365)
    date_to = today

    # decrypt password เก็บใน local var เท่านั้น — ห้าม log
    plain_pw = encryption.decrypt(account.encrypted_password)

    result = await vps_client.run_backtest(
        login=account.login,
        password=plain_pw,
        server=account.server,
        strategy_code=body.strategy_code,
        symbols=MAJOR_SYMBOLS,
        timeframe=body.timeframe,
        date_from=date_from,
        date_to=date_to,
        params_sweep=params_sweep,
    )

    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "backtest failed"))

    rows = _insert_runs(
        db, result, strategy, account, body.timeframe, date_from, date_to,
    )
    return OptimizeRunResult(ok=True, inserted=len(rows), runs=rows)


@router.get("/majors")
def list_majors(
    _admin: User = Depends(require_admin),
) -> dict:
    """Return the major pairs configured for optimize sweeps."""
    return {"symbols": MAJOR_SYMBOLS}


@router.post("/bot/trigger-cycle")
async def admin_trigger_bot_cycle(
    _admin: User = Depends(require_admin),
) -> dict:
    """รัน bot cycle ทันที (manual trigger สำหรับ debug / test) — proxy ไปยัง VPS

    ไม่ต้องรอ scheduler 15 นาที — เหมาะกับการทดสอบ end-to-end
    Returns: {ok, summary: {ts, subscriptions_total, ports_iterated,
                            signals_detected, orders_sent, ...}}
    """
    return await vps_client.trigger_bot_cycle()


@router.post("/tv-cache/refresh")
def admin_trigger_tv_refresh(
    job: str = "all",
    _admin: User = Depends(require_admin),
) -> dict:
    """Trigger TV cache refresh ทันที (ไม่ต้องรอ cron).

    Args:
      job: "h1" | "d1" | "set100" | "all"
        - "h1"     → 11 G8 pairs × H1 (~30s)
        - "d1"     → 11 G8 pairs × D1 (~30s)
        - "set100" → 100 SET stocks × D1 (~5-7 นาที)
        - "all"    → ทั้ง 3 sequential (~6-8 นาที)

    เรียกหลัง deploy ครั้งแรกเพื่อ initial fill — widgets จะมีข้อมูลทันที
    """
    from services import tv_scheduler

    job = job.lower().strip()
    if job not in ("h1", "d1", "set100", "all"):
        raise HTTPException(400, f"invalid job: {job}")

    results = {}
    if job in ("h1", "all"):
        results["h1"] = tv_scheduler.refresh_all("H1")
    if job in ("d1", "all"):
        results["d1"] = tv_scheduler.refresh_all("D1")
    if job in ("set100", "all"):
        results["set100"] = tv_scheduler.refresh_set100()

    return {"ok": True, "results": results}
