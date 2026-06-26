"""TV OHLC Scheduler — ดึงราคาเก็บลง DB ทุกชม. (Railway side)

Jobs:
  - H1: ทุกชั่วโมงที่นาที 05 → fetch 30 bars × 11 pairs upsert
  - D1: ทุกวัน 00:05 UTC → fetch 30 bars × 11 pairs upsert

Symbols: G8 forex (11 pairs ที่ใช้คำนวณ Currency Strength)
Source: tvdatafeed-enhanced (anonymous mode)

หมายเหตุ:
  - Railway free tier server อาจ sleep on idle → scheduler หยุด.
    ใช้ paid plan หรือ external ping ถ้าต้องการ always-on
  - หาก fetch fail (rate limit / TV down) → log + ข้าม pair นั้น, ครั้งหน้าลองใหม่
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import SessionLocal, engine
from models.tv_ohlc import TVOhlc

logger = logging.getLogger(__name__)

# G8 pairs — ตรงกับ _STRENGTH_PAIRS ใน tvdata.py
PAIRS_TO_CACHE: list[tuple[str, str]] = [
    ("EURUSD", "OANDA"),
    ("GBPUSD", "OANDA"),
    ("AUDUSD", "OANDA"),
    ("NZDUSD", "OANDA"),
    ("USDJPY", "OANDA"),
    ("USDCHF", "OANDA"),
    ("USDCAD", "OANDA"),
    ("EURGBP", "OANDA"),
    ("EURJPY", "OANDA"),
    ("GBPJPY", "OANDA"),
    ("AUDJPY", "OANDA"),
]

BARS_TO_FETCH = 200  # H1: ~8 days · D1: ~200 days
# พอสำหรับ:
#   - currency_strength (H1=24 bars, D1=7 bars)
#   - pair_cluster (เคียร์ ≥100 bars สำหรับ K-means clustering)
#   - trend_matrix (50/200 SMA)

# SET100 stocks: ต้องการ ≥252 D1 bars สำหรับ Minervini trend template
SET100_BARS = 260


# ─── Lazy tvDatafeed (เหมือนใน tvdata.py) ────────────────────────────
_tv = None
_tv_failed = False


def _get_tv():
    global _tv, _tv_failed
    if _tv_failed:
        return None
    if _tv is not None:
        return _tv
    try:
        from tvDatafeed import TvDatafeed, Interval  # type: ignore
        _tv = TvDatafeed()
        _tv._Interval = Interval
        logger.info("TV scheduler: tvDatafeed initialized")
        return _tv
    except Exception as exc:
        logger.error("TV scheduler init failed: %s", exc)
        _tv_failed = True
        return None


def _tf_to_interval(tf: str):
    tv = _get_tv()
    if tv is None:
        return None
    return {
        "H1": tv._Interval.in_1_hour,
        "D1": tv._Interval.in_daily,
    }.get(tf)


# ─── Upsert helper (cross-dialect) ───────────────────────────────────
def _upsert_bars(session, rows: list[dict]) -> int:
    """Upsert OHLC bars. Returns inserted/updated count."""
    if not rows:
        return 0
    dialect = engine.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(TVOhlc).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "tf", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
    elif dialect == "sqlite":
        stmt = sqlite_insert(TVOhlc).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "tf", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
    else:
        # generic fallback: delete+insert per (symbol, tf, ts)
        for row in rows:
            session.query(TVOhlc).filter_by(
                symbol=row["symbol"], tf=row["tf"], ts=row["ts"],
            ).delete()
            session.add(TVOhlc(**row))
        return len(rows)

    session.execute(stmt)
    return len(rows)


# ─── Fetch one pair × tf ─────────────────────────────────────────────
def _fetch_and_store(symbol: str, exchange: str, tf: str, n_bars: Optional[int] = None) -> int:
    """Fetch n_bars (default BARS_TO_FETCH) จาก TV → upsert. Return rows written.

    n_bars override สำหรับ SET100 stocks ที่ต้องการ ≥252 D1 bars (ใช้ SET100_BARS=260)
    """
    tv = _get_tv()
    interval = _tf_to_interval(tf)
    if tv is None or interval is None:
        return 0

    bars_count = n_bars if n_bars else BARS_TO_FETCH
    try:
        df = tv.get_hist(
            symbol=symbol, exchange=exchange, interval=interval, n_bars=bars_count,
        )
    except Exception as exc:
        logger.warning("TV fetch %s:%s %s failed: %s", exchange, symbol, tf, exc)
        return 0

    if df is None or df.empty:
        logger.warning("TV returned empty for %s:%s %s", exchange, symbol, tf)
        return 0

    now = datetime.now(timezone.utc)
    rows = []
    for idx, r in df.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        rows.append({
            "symbol": symbol,
            "exchange": exchange,
            "tf": tf,
            "ts": ts.astimezone(timezone.utc).replace(tzinfo=None),  # store naive UTC
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r.get("volume", 0) or 0),
            "fetched_at": now.replace(tzinfo=None),
        })

    with SessionLocal() as session:
        try:
            count = _upsert_bars(session, rows)
            session.commit()
            return count
        except Exception:
            session.rollback()
            logger.exception("upsert %s:%s %s failed", exchange, symbol, tf)
            return 0


# ─── Job: refresh ทุก pair สำหรับ TF เดียว ───────────────────────────
def refresh_all(tf: str) -> dict:
    """Run job: fetch ทุก pair × tf. Return summary."""
    started = datetime.now(timezone.utc)
    ok = 0
    fail = 0
    for symbol, exch in PAIRS_TO_CACHE:
        n = _fetch_and_store(symbol, exch, tf)
        if n > 0:
            ok += 1
        else:
            fail += 1
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    summary = {"tf": tf, "ok": ok, "fail": fail, "duration_s": round(duration, 1)}
    logger.info("TV refresh %s done: %s", tf, summary)
    return summary


# ─── Scheduler lifecycle ─────────────────────────────────────────────
_scheduler: Optional[AsyncIOScheduler] = None


# ─── SET100 stocks daily refresh (Thai Stock Exchange) ──────────────
def refresh_set100() -> dict:
    """Fetch daily bars สำหรับทุก SET100 stock (รัน 17:30 ICT = 10:30 UTC หลัง SET ปิด)"""
    import time
    from services.set100_universe import SET100_STOCKS

    started = datetime.now(timezone.utc)
    ok = 0
    fail = 0
    for sym in SET100_STOCKS:
        n = _fetch_and_store(sym, "SET", "D1", n_bars=SET100_BARS)
        if n > 0:
            ok += 1
        else:
            fail += 1
        time.sleep(0.25)  # rate-limit guard — ~25 stocks/min
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    summary = {"job": "set100_daily", "ok": ok, "fail": fail, "duration_s": round(duration, 1)}
    logger.info("SET100 refresh done: %s", summary)
    return summary


def start() -> None:
    """Bootstrap APScheduler — เรียกตอน FastAPI startup"""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    # H1: ทุกชั่วโมงที่นาที 5 (หลังแท่งปิด)
    _scheduler.add_job(refresh_all, "cron", minute=5, args=["H1"], id="tv_refresh_h1")
    # D1: ทุกวัน 00:05 UTC
    _scheduler.add_job(refresh_all, "cron", hour=0, minute=5, args=["D1"], id="tv_refresh_d1")
    # SET100: ทุกวัน 10:30 UTC (17:30 ICT — หลัง SET ปิด)
    _scheduler.add_job(refresh_set100, "cron", hour=10, minute=30, id="set100_daily")
    _scheduler.start()
    logger.info("TV scheduler started — H1 ทุกชม., D1 รายวัน, SET100 รายวัน 10:30 UTC")


def stop() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("TV scheduler stopped")


def initial_fill_if_empty() -> None:
    """ครั้งแรกที่ start: ถ้า DB ว่าง → fetch ทั้งหมดเลย (sync, blocking)
    เรียกหลัง start() บน startup event
    """
    with SessionLocal() as session:
        any_row = session.scalar(select(TVOhlc.id).limit(1))
        # check แยกระหว่าง forex (มี/ไม่มี EURUSD) vs SET100 (มี/ไม่มี AOT)
        has_forex = session.scalar(
            select(TVOhlc.id).where(TVOhlc.symbol == "EURUSD").limit(1)
        )
        has_set100 = session.scalar(
            select(TVOhlc.id).where(TVOhlc.symbol == "AOT").limit(1)
        )

    if not has_forex:
        logger.info("Forex cache ว่าง — initial fill H1 + D1")
        refresh_all("H1")
        refresh_all("D1")
    if not has_set100:
        logger.info("SET100 cache ว่าง — initial fill (5-7 นาที)")
        refresh_set100()

    if any_row and has_forex and has_set100:
        logger.info("TV cache ครบแล้ว — skip initial fill")
