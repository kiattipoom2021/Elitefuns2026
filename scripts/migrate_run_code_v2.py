"""One-shot migration — convert old compact run_code → new readable format

Old: H1MaEU05x30lk10rr3
New: MA-EURUSD-H1-5x30-SL10-RR3

Usage:
    cd railway && python -m scripts.migrate_run_code_v2

ก่อนรัน:
  1. backup DB:
       docker exec mt5bot-postgres pg_dump -U mt5bot mt5bot > backup.sql
  2. stop bot scheduler ก่อน (ไม่ให้ INSERT/UPDATE ระหว่าง migration)
  3. (recommended) run on staging DB ก่อน

Idempotent:
  รันซ้ำได้โดยไม่มีปัญหา — แถวที่ run_code เป็น new format จะไม่ตรง
  LEGACY_PATTERN → skip naturally.

Tables affected:
  - backtest_runs.run_code
  - user_strategy_subscriptions.run_code

Note about UNIQUE constraint:
  user_strategy_subscriptions มี UNIQUE(user_id, mt5_account_id, run_code).
  Mapping ระหว่าง old↔new code เป็น 1:1 (ไม่มี collision) ดังนั้น
  migration จะไม่ชน constraint — ถ้าเจอ error ระหว่าง commit แสดงว่า
  DB มีข้อมูลผิดปกติ (เช่น manually inserted duplicate) ต้อง investigate
  ก่อน rerun.
"""
from __future__ import annotations

import re
import sys

from sqlalchemy.orm import Session

from database import SessionLocal
# import all models so SQLAlchemy FK relations resolve
from models.user import User  # noqa: F401
from models.mt5_account import MT5Account  # noqa: F401
from models.strategy import Strategy  # noqa: F401
from models.backtest_run import BacktestRun
from models.subscription import UserStrategySubscription
from models.order import Order  # noqa: F401
from services import run_code as svc

# ─── Legacy decode maps (อยู่ที่นี่เพราะ services/run_code.py เลิกใช้แล้ว) ─────
LEGACY_SYM_MAP: dict[str, str] = {
    "EU": "EURUSD",
    "UJ": "USDJPY",
    "GU": "GBPUSD",
    "UH": "USDCHF",
    "AU": "AUDUSD",
    "UC": "USDCAD",
    "NU": "NZDUSD",
    "XU": "XAUUSD",
}

LEGACY_STRAT_MAP: dict[str, str] = {
    "Ma": "ma_cross",
    "Rs": "rsi_reversal",
    "Bb": "bollinger",
    "Gd": "grid",
}

# Pattern: TF + 2-char STRAT + 2-char SYM + FF + 'x' + SS + 'lk' + LK + 'rr' + RR
#   TF = H1|H4|D1|M1|M5|M15|M30|W1|MN1
#   STRAT = Ma|Rs|Bb|Gd
#   SYM = 2 uppercase letters
#   FF/SS/LK = digits, RR = digits + optional dot
LEGACY_PATTERN = re.compile(
    r"^(H1|H4|D1|M1|M5|M15|M30|W1|MN1)(Ma|Rs|Bb|Gd)([A-Z]{2})(\d+)x(\d+)lk(\d+)rr([\d.]+)$"
)


def parse_legacy(old_code: str) -> dict | None:
    """Decode legacy run_code → kwargs for svc.encode().

    Returns None ถ้า pattern ไม่ match หรือ map ไม่ครบ.
    """
    m = LEGACY_PATTERN.match(old_code)
    if not m:
        return None

    tf, st_short, sym_short, fast, slow, lk, rr = m.groups()
    strategy_code = LEGACY_STRAT_MAP.get(st_short)
    symbol = LEGACY_SYM_MAP.get(sym_short)
    if not strategy_code or not symbol:
        return None

    try:
        return {
            "timeframe": tf,
            "strategy_code": strategy_code,
            "symbol": symbol,
            "params": {
                "fast": int(fast),
                "slow": int(slow),
                "sl_lookback": int(lk),
                "rr_ratio": float(rr),
            },
        }
    except (TypeError, ValueError):
        return None


def _convert(old_code: str | None) -> str | None:
    """Return new code, or None ถ้าควร skip (null / new format / parse fail)."""
    if not old_code:
        return None
    if not LEGACY_PATTERN.match(old_code):
        return None  # already new format หรือ unrecognized — skip
    parsed = parse_legacy(old_code)
    if not parsed:
        return None
    return svc.encode(**parsed)


def migrate(db: Session) -> tuple[int, int, int]:
    """Run migration on both tables. Return (bt_updated, sub_updated, skipped)."""
    bt_updated = 0
    sub_updated = 0
    skipped = 0

    # ─── Backtest runs ────────────────────────────────────────────────
    print("─── backtest_runs ─────────────────────────────────────")
    runs = db.query(BacktestRun).all()
    for r in runs:
        if not r.run_code:
            continue
        if not LEGACY_PATTERN.match(r.run_code):
            continue  # already new format
        new_code = _convert(r.run_code)
        if not new_code:
            print(f"  [SKIP] bt_run {r.id[:8]}: parse failed for {r.run_code!r}")
            skipped += 1
            continue
        print(f"  bt_run {r.id[:8]}: {r.run_code}  →  {new_code}")
        r.run_code = new_code
        bt_updated += 1

    # ─── Subscriptions ────────────────────────────────────────────────
    print("\n─── user_strategy_subscriptions ───────────────────────")
    subs = db.query(UserStrategySubscription).all()
    for s in subs:
        if not s.run_code:
            continue
        if not LEGACY_PATTERN.match(s.run_code):
            continue
        new_code = _convert(s.run_code)
        if not new_code:
            print(f"  [SKIP] sub {s.id[:8]}: parse failed for {s.run_code!r}")
            skipped += 1
            continue
        print(f"  sub {s.id[:8]}: {s.run_code}  →  {new_code}")
        s.run_code = new_code
        sub_updated += 1

    db.commit()
    return bt_updated, sub_updated, skipped


def main() -> int:
    db = SessionLocal()
    try:
        bt, sub, skipped = migrate(db)
    except Exception as exc:  # noqa: BLE001 — surface anything to operator
        db.rollback()
        print(f"\n[ERROR] migration failed, rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print("\n═══════════════════════════════════════════════════════")
    print(f"Migrated: backtest_runs={bt}  subscriptions={sub}  skipped={skipped}")
    print("═══════════════════════════════════════════════════════")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
