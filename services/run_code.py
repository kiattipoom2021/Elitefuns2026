"""Run code encoder — สร้าง readable identifier จากผล backtest run.

Format ใหม่ (v2): {STRAT}-{SYMBOL}-{TF}-{FAST}x{SLOW}-SL{LOOKBACK}-RR{RR}
Examples:
    "MA-EURUSD-H1-5x30-SL10-RR3"
    "RSI-USDCAD-H1-100x200-SL50-RR3.5"

- STRAT  = uppercase strategy code (MA, RSI, BB, GRID)
- SYMBOL = full symbol name (EURUSD, USDCAD, XAUUSD, ...)
- TF     = timeframe (H1, H4, M15, D1, ...)
- FAST/SLOW = integer ไม่ pad zero (5x30 ไม่ใช่ 05x030)
- SL     = sl_lookback (integer)
- RR     = rr_ratio — drop .0 ถ้า integer (RR2 ไม่ใช่ RR2.0); keep .5 (RR1.5)

─────────────────────────────────────────────────────────────────────
Legacy format (v1) — kept here for decoder reference (parser อยู่ที่
scripts/migrate_run_code_v2.py):
    {TF}{ST2}{SYM2}{FF}x{SS}lk{LK}rr{RR}   เช่น "H1MaEU05x20lk10rr2"

    LEGACY_SYM_MAP = {
        "EURUSD": "EU", "USDJPY": "UJ", "GBPUSD": "GU", "USDCHF": "UH",
        "AUDUSD": "AU", "USDCAD": "UC", "NZDUSD": "NU", "XAUUSD": "XU",
    }
    LEGACY_STRAT_MAP = {
        "ma_cross": "Ma", "rsi_reversal": "Rs",
        "bollinger": "Bb", "grid": "Gd",
    }
─────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

STRAT_MAP: dict[str, str] = {
    "ma_cross": "MA",
    "rsi_reversal": "RSI",
    "bollinger": "BB",
    "grid": "GRID",
}


def _fmt_rr(rr: float) -> str:
    """2.0 → '2', 1.5 → '1.5', 2.25 → '2.25'"""
    if rr == int(rr):
        return str(int(rr))
    return f"{rr:g}"


def encode(
    timeframe: str,
    strategy_code: str,
    symbol: str,
    params: dict,
) -> str | None:
    """สร้าง run code จาก inputs — return None ถ้า map ไม่ครบ.

    Args:
        timeframe: "H1", "M15", "D1", ...
        strategy_code: "ma_cross", "rsi_reversal", ...
        symbol: "EURUSD", "XAUUSD", ...
        params: {"fast": 5, "slow": 20, "sl_lookback": 10, "rr_ratio": 2.0, ...}
    """
    st = STRAT_MAP.get(strategy_code)
    if not st or not symbol or not timeframe:
        return None

    try:
        fast = int(params["fast"])
        slow = int(params["slow"])
        lk = int(params["sl_lookback"])
        rr = float(params["rr_ratio"])
    except (KeyError, TypeError, ValueError):
        return None

    return f"{st}-{symbol}-{timeframe}-{fast}x{slow}-SL{lk}-RR{_fmt_rr(rr)}"
