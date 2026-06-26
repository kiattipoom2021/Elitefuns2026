"""FastAPI app entry — Railway side"""
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from database import engine, Base, auto_migrate, SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Register models ก่อน create_all
import models.user  # noqa: F401
import models.mt5_account  # noqa: F401
import models.strategy  # noqa: F401
import models.backtest_run  # noqa: F401
import models.subscription  # noqa: F401
import models.order  # noqa: F401
import models.arb_scan  # noqa: F401
import models.mt5_trade  # noqa: F401
import models.tv_ohlc  # noqa: F401
import models.user_dashboard  # noqa: F401

from models.strategy import Strategy  # noqa: E402
from models.backtest_run import BacktestRun  # noqa: E402
from services import run_code as run_code_svc  # noqa: E402

Base.metadata.create_all(bind=engine)
auto_migrate()


def _seed_strategies() -> None:
    """seed strategy catalog ครั้งแรก — ถ้ามีแล้วข้าม"""
    with SessionLocal() as db:
        if not db.query(Strategy).filter_by(code="ma_cross").first():
            db.add(Strategy(
                code="ma_cross",
                name="MA Cross",
                description="เส้นค่าเฉลี่ย 2 เส้นตัดกัน — Fast ตัดขึ้น = buy, ตัดลง = sell",
                enabled=True,
            ))
            db.commit()


def _backfill_run_codes() -> None:
    """เติม run_code ให้ rows เก่าที่ค้าง NULL (1 ครั้ง — ไม่ block startup ถ้า fail)"""
    try:
        with SessionLocal() as db:
            pending = db.query(BacktestRun).filter(BacktestRun.run_code.is_(None)).all()
            if not pending:
                return
            updated = 0
            strategy_codes = {s.id: s.code for s in db.query(Strategy).all()}
            for row in pending:
                strat_code = strategy_codes.get(row.strategy_id)
                if not strat_code:
                    continue
                code = run_code_svc.encode(row.timeframe, strat_code, row.symbol, row.params or {})
                if code:
                    row.run_code = code
                    updated += 1
            if updated:
                db.commit()
                logging.info("backfilled run_code for %d rows", updated)
    except Exception:
        logging.exception("backfill_run_codes failed (non-fatal)")


_seed_strategies()
_backfill_run_codes()

# Register routers
from routers import auth, mt5, admin, optimize_public, bots, internal, arbitrage, ports, widgets, dashboards  # noqa: E402

app = FastAPI(title="MT5 Bot Platform", version="0.1.0")
app.include_router(auth.router)
app.include_router(mt5.router)
app.include_router(admin.router)
app.include_router(optimize_public.router)
app.include_router(bots.router)
app.include_router(ports.router)
app.include_router(internal.router)
app.include_router(arbitrage.router)
app.include_router(widgets.router)
app.include_router(dashboards.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# ─── TV cache scheduler (Console Data widgets) ─────────────────────
import threading
from services import tv_scheduler


@app.on_event("startup")
def _start_tv_scheduler() -> None:
    try:
        tv_scheduler.start()
        # initial fill ทำใน thread ต่าง — กัน startup ค้าง (~60s)
        threading.Thread(
            target=tv_scheduler.initial_fill_if_empty, daemon=True
        ).start()
    except Exception:
        logging.exception("TV scheduler start failed — widgets จะไม่มีข้อมูลจนกว่าจะ restart")


@app.on_event("shutdown")
def _stop_tv_scheduler() -> None:
    try:
        tv_scheduler.stop()
    except Exception:
        logging.exception("TV scheduler stop failed (non-fatal)")


# Static files (เสิร์ฟ frontend) — mount หลัง router เพื่อไม่ให้ทับ /auth, /health
app.mount("/", StaticFiles(directory="static", html=True), name="static")
