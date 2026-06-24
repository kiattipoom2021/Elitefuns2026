"""Public read-only endpoints — list strategies + leaderboard runs (NO AUTH)"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.strategy import Strategy
from models.backtest_run import BacktestRun

router = APIRouter(prefix="/api", tags=["optimize-public"])


# ─── Helpers: window stats + sparkline ─────────────────────────────────
def _zero_stats() -> dict:
    """Stats dict ที่ทุก field เป็นศูนย์/None — ใช้กรณี trades ว่าง"""
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "net_profit": 0.0,
        "win_rate": None,
        "max_drawdown": 0.0,
        "profit_factor": None,
        "recovery_factor": None,
    }


def _parse_close_time(raw: str) -> Optional[datetime]:
    """ISO string → tz-aware datetime (UTC). คืน None ถ้า parse ไม่ได้"""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _compute_window_stats(trades: list[dict], days: int) -> dict:
    """กรอง trades ตาม window (วันย้อนหลัง) แล้วคำนวณ aggregate stats

    Window = [now - days, now]. Trades ที่ parse close_time ไม่ได้ถูกข้าม.
    """
    if not trades:
        return _zero_stats()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered: list[dict] = []
    for t in trades:
        close_time = _parse_close_time(t.get("close_time", ""))
        if close_time is not None and close_time >= cutoff:
            filtered.append(t)

    if not filtered:
        return _zero_stats()

    profits = [float(t.get("profit", 0)) for t in filtered]
    total = len(filtered)
    wins = sum(1 for p in profits if p > 0)
    losses = sum(1 for p in profits if p < 0)
    net = sum(profits)
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = sum(p for p in profits if p < 0)

    # Max drawdown จาก cumulative curve (USD จาก peak)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in profits:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "net_profit": round(net, 2),
        "win_rate": round(wins / total, 4) if total else None,
        "max_drawdown": round(max_dd, 2),
        "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss != 0 else None,
        "recovery_factor": round(net / max_dd, 2) if max_dd > 0 else None,
    }


def _compute_sparkline(trades: list[dict], days: int, points: int = 20) -> list[float]:
    """Downsample cumulative profit curve เป็น ~N จุดสำหรับ sparkline

    Returns: list ของ cumulative profit (start จาก 0). [] ถ้าไม่มี trade ใน window.
    Algorithm: sort by close_time → cumulative sum → pick every k-th point
    (force last point = final cumulative เพื่อให้ปลายเส้นถูกต้องเสมอ)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered: list[tuple[datetime, float]] = []
    for t in trades:
        close_time = _parse_close_time(t.get("close_time", ""))
        if close_time is not None and close_time >= cutoff:
            filtered.append((close_time, float(t.get("profit", 0))))

    if not filtered:
        return []

    filtered.sort(key=lambda x: x[0])

    # cumulative
    cum_points: list[float] = []
    running = 0.0
    for _, profit in filtered:
        running += profit
        cum_points.append(running)

    # ถ้าจุดน้อยกว่าหรือเท่ากับ target — คืนเต็ม
    if len(cum_points) <= points:
        return [round(p, 2) for p in cum_points]

    # downsample แบบ stride uniform
    step = len(cum_points) / points
    sampled = [round(cum_points[int(i * step)], 2) for i in range(points)]

    # บังคับจุดสุดท้าย = final cumulative (กัน peak/valley หาย)
    final = round(cum_points[-1], 2)
    if sampled[-1] != final:
        sampled[-1] = final
    return sampled


# ─── Schemas ──────────────────────────────────────────────────────────
class StrategyResponse(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None

    class Config:
        from_attributes = True


class BacktestRunTrades(BaseModel):
    """Response สำหรับ /strategies/{code}/runs/{run_id}/trades

    trades = NULL → legacy run ก่อน engine จะ emit trade list.
    Frontend ควรแสดง "ไม่มี trade data — กด re-sweep เพื่อสร้าง"
    """

    run_id: str
    run_code: Optional[str] = None
    symbol: str
    timeframe: str
    trades: Optional[list[dict]] = None  # None = legacy (NULL ใน DB)

    class Config:
        from_attributes = True


class WindowStats(BaseModel):
    """Aggregate stats ของ trades ใน rolling window (1y / 6m)"""

    total_trades: int
    wins: int
    losses: int
    net_profit: float
    win_rate: Optional[float] = None
    max_drawdown: float
    profit_factor: Optional[float] = None
    recovery_factor: Optional[float] = None


class BacktestRunPublic(BaseModel):
    """Public-safe view — EXCLUDES mt5_login_used to avoid leaking customer account numbers."""

    id: str
    strategy_id: int
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

    # ─── Window stats + sparkline (additive, computed from trades_json) ──
    # None = legacy run (trades_json NULL) → frontend fallback ใช้ aggregate columns เดิม
    stats_1y: Optional[WindowStats] = None
    stats_6m: Optional[WindowStats] = None
    sparkline_1y: list[float] = []   # ~20 cumulative profit points
    sparkline_6m: list[float] = []

    class Config:
        from_attributes = True


# ─── Sort key mapping ──────────────────────────────────────────────────
# (column, ascending?) — max_dd น้อย = ดี, ที่เหลือมาก = ดี
_SORT_MAP = {
    "net_profit": (BacktestRun.net_profit, False),
    "sharpe": (BacktestRun.sharpe_ratio, False),
    "win_rate": (BacktestRun.win_rate, False),
    "max_dd": (BacktestRun.max_drawdown, True),
    "recovery": (BacktestRun.recovery_factor, False),  # มาก = ดี (กำไรเทียบ DD)
    "run_at": (BacktestRun.run_at, False),
}


# ─── Endpoints ────────────────────────────────────────────────────────
@router.get("/strategies", response_model=list[StrategyResponse])
def list_strategies(db: Session = Depends(get_db)):
    """ลิสต์ strategy ที่ enabled — ไว้ให้ frontend แสดงให้ user เลือก"""
    return db.query(Strategy).filter(Strategy.enabled.is_(True)).order_by(Strategy.id).all()


@router.get("/strategies/{code}/runs", response_model=list[BacktestRunPublic])
def list_runs(
    code: str,
    symbol: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
    sort: str = Query("net_profit"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """leaderboard ของ strategy เดียว — sort + filter ตาม query params

    แสดงเฉพาะ runs ของ engine_version ล่าสุดของ strategy นี้
    (เก่ายังคงค้างใน DB เพื่อ audit แต่ไม่แสดงในผลลัพธ์ — กันสับสน
    ระหว่าง engine versions ที่ logic ต่างกัน)
    """
    strategy = db.query(Strategy).filter(Strategy.code == code).first()
    if not strategy:
        raise HTTPException(404, f"ไม่พบ strategy code={code}")

    # validate sort — ถ้าไม่รู้จัก fallback เป็น net_profit
    if sort not in _SORT_MAP:
        sort = "net_profit"
    col, ascending = _SORT_MAP[sort]
    order_by = col.asc() if ascending else col.desc()

    # NOTE: lexicographic MAX — ใช้ได้สำหรับ semver "0.X.0" ที่ X เป็นเลขหลักเดียว
    # ถ้าถึง engine v0.10.0+ ต้องเปลี่ยนเป็น semver parse
    latest_version = (
        db.query(func.max(BacktestRun.engine_version))
        .filter(BacktestRun.strategy_id == strategy.id)
        .scalar()
    )
    if latest_version is None:
        return []  # ไม่มี run ใด ๆ

    q = db.query(BacktestRun).filter(
        BacktestRun.strategy_id == strategy.id,
        BacktestRun.engine_version == latest_version,
    )
    if symbol:
        q = q.filter(BacktestRun.symbol == symbol)
    if timeframe:
        q = q.filter(BacktestRun.timeframe == timeframe)
    runs = q.order_by(order_by).limit(limit).all()

    # ─── Augment each row with window stats + sparkline ──────────────
    # trades_json = NULL → legacy run → stats=None, sparkline=[]
    #   Frontend จะ fallback ไปใช้ aggregate columns (net_profit, max_drawdown, ฯลฯ)
    result: list[BacktestRunPublic] = []
    for r in runs:
        trades = r.trades_json or []
        has_trades = bool(trades)
        result.append(
            BacktestRunPublic(
                id=r.id,
                strategy_id=r.strategy_id,
                symbol=r.symbol,
                timeframe=r.timeframe,
                params=r.params,
                date_from=r.date_from,
                date_to=r.date_to,
                net_profit=r.net_profit,
                profit_pct=r.profit_pct,
                total_trades=r.total_trades,
                win_trades=r.win_trades,
                loss_trades=r.loss_trades,
                win_rate=r.win_rate,
                sharpe_ratio=r.sharpe_ratio,
                max_drawdown=r.max_drawdown,
                recovery_factor=r.recovery_factor,
                engine_version=r.engine_version,
                run_code=r.run_code,
                run_at=r.run_at,
                stats_1y=WindowStats(**_compute_window_stats(trades, 365)) if has_trades else None,
                stats_6m=WindowStats(**_compute_window_stats(trades, 180)) if has_trades else None,
                sparkline_1y=_compute_sparkline(trades, 365),
                sparkline_6m=_compute_sparkline(trades, 180),
            )
        )
    return result


@router.get("/strategies/{code}/runs/{run_id}/trades", response_model=BacktestRunTrades)
def get_run_trades(code: str, run_id: str, db: Session = Depends(get_db)):
    """Trade-by-trade detail ของหนึ่ง backtest run — แยก endpoint เพื่อไม่ load payload หนัก
    เข้าไปใน list_runs (trades อาจมี 100+ records ต่อ run)

    Returns trades=None สำหรับ legacy runs (engine version เก่าก่อนรองรับ trade emission)
    Frontend ต้อง handle null gracefully — แสดง "no trade data" หรือชวน user re-sweep
    """
    strategy = db.query(Strategy).filter(Strategy.code == code).first()
    if not strategy:
        raise HTTPException(404, f"ไม่พบ strategy code={code}")

    run = (
        db.query(BacktestRun)
        .filter(
            BacktestRun.id == run_id,
            BacktestRun.strategy_id == strategy.id,
        )
        .first()
    )
    if not run:
        raise HTTPException(404, f"ไม่พบ run id={run_id} ของ strategy {code}")

    return BacktestRunTrades(
        run_id=run.id,
        run_code=run.run_code,
        symbol=run.symbol,
        timeframe=run.timeframe,
        trades=run.trades_json,  # None ถ้า legacy
    )
