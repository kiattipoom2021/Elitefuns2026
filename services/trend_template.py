"""Minervini Trend Template (8 criteria + volume filter)

Reference: "Trade Like a Stock Market Wizard" — Mark Minervini

Criteria:
  1. Current price > 150-day SMA AND > 200-day SMA
  2. 150-day SMA > 200-day SMA
  3. 200-day SMA trending up for ≥1 month (21 trading days)
  4. 50-day SMA > 150-day SMA > 200-day SMA
  5. Current price > 50-day SMA
  6. Current price ≥ +25% above 52-week low
  7. Current price within 25% of 52-week high
  8. RS rank ≥ 70 (relative strength vs universe — computed externally)

Plus volume filter:
  - 50-day avg volume ≥ 100,000 shares (liquidity)
  - Recent (5-day) volume avg ≥ 80% of 50-day avg (not declining)
"""
from __future__ import annotations

from typing import Optional


def check_trend_template(bars: list[dict], rs_rank: Optional[float] = None) -> dict:
    """Apply 8 criteria + volume to one stock's daily bars.

    Args:
      bars: list of {open, high, low, close, volume} — ascending order, daily
      rs_rank: optional pre-computed RS rank (0-100). If None, criterion 8 = None (skip)

    Returns:
      {
        passes_all: bool (ผ่านทุกเกณฑ์รวม vol),
        score: int (จำนวน criteria ที่ผ่าน — 0-9),
        criteria: { c1, c2, c3, c4, c5, c6, c7, c8, vol },
        details: { close, sma50, sma150, sma200, high_52w, low_52w, ... }
      }
    """
    if len(bars) < 252:
        return {
            "passes_all": False, "score": 0,
            "criteria": {},
            "details": {},
            "error": f"ข้อมูลไม่พอ (need 252, got {len(bars)})",
        }

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]

    close = closes[-1]
    sma50 = sum(closes[-50:]) / 50
    sma150 = sum(closes[-150:]) / 150
    sma200 = sum(closes[-200:]) / 200

    # 200 SMA 1 เดือนก่อน (21 trading days)
    # ใช้ closes[-221:-21] ค่าเฉลี่ย 200 bar ก่อน 21 วันที่แล้ว
    sma200_21d_ago = sum(closes[-221:-21]) / 200 if len(closes) >= 221 else sma200

    high_52w = max(highs[-252:])
    low_52w = min(lows[-252:])

    vol_50_avg = sum(volumes[-50:]) / 50 if volumes else 0
    vol_5_avg = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0

    # ─── 8 Criteria ──────────────────────────────────────────────────
    c1 = close > sma150 and close > sma200
    c2 = sma150 > sma200
    c3 = sma200 > sma200_21d_ago  # 200 SMA trending up
    c4 = sma50 > sma150 > sma200
    c5 = close > sma50
    c6 = close >= low_52w * 1.25  # ≥+25% above 52w low
    c7 = close >= high_52w * 0.75  # within 25% of 52w high
    if rs_rank is None:
        c8: Optional[bool] = None
    else:
        c8 = rs_rank >= 70

    # ─── Volume filter ───────────────────────────────────────────────
    cv = vol_50_avg >= 100_000 and vol_5_avg >= vol_50_avg * 0.8

    bools = [c1, c2, c3, c4, c5, c6, c7, cv]
    if c8 is not None:
        bools.append(c8)
    score = sum(1 for b in bools if b)

    # passes_all = ทุก criteria ที่ตรวจได้ต้องผ่าน (c8 ถ้ามี)
    passes_required = c1 and c2 and c3 and c4 and c5 and c6 and c7 and cv
    if c8 is not None:
        passes_required = passes_required and c8

    return {
        "passes_all": passes_required,
        "score": score,
        "max_score": len(bools),
        "criteria": {
            "c1_price_above_150_200": c1,
            "c2_150_above_200": c2,
            "c3_200_trending_up": c3,
            "c4_50_150_200_aligned": c4,
            "c5_price_above_50": c5,
            "c6_above_25_from_52wL": c6,
            "c7_within_25_of_52wH": c7,
            "c8_rs_rank_70": c8,
            "vol_liquidity": cv,
        },
        "details": {
            "close": round(close, 3),
            "sma50": round(sma50, 3),
            "sma150": round(sma150, 3),
            "sma200": round(sma200, 3),
            "sma200_21d_ago": round(sma200_21d_ago, 3),
            "high_52w": round(high_52w, 3),
            "low_52w": round(low_52w, 3),
            "pct_from_low_52w": round((close / low_52w - 1) * 100, 1) if low_52w else 0,
            "pct_from_high_52w": round((close / high_52w - 1) * 100, 1) if high_52w else 0,
            "vol_50_avg": int(vol_50_avg),
            "vol_5_avg": int(vol_5_avg),
            "vol_ratio": round(vol_5_avg / vol_50_avg, 2) if vol_50_avg else 0,
            "rs_rank": rs_rank,
        },
    }


def compute_rs_rank(closes_by_symbol: dict[str, list[float]]) -> dict[str, float]:
    """คำนวณ Relative Strength rank (IBD-style, 12-month weighted).

    Score = 0.4*3mo + 0.2*6mo + 0.2*9mo + 0.2*12mo  (% change)
    Rank = percentile (0-100)

    Args:
      closes_by_symbol: {symbol: [close, ...]} — ascending daily bars

    Returns:
      {symbol: rs_rank}  — rs_rank 0-100 (higher = stronger)
    """
    def _pct_change(arr: list[float], lookback: int) -> Optional[float]:
        if len(arr) < lookback + 1:
            return None
        return (arr[-1] / arr[-lookback - 1] - 1) * 100

    scores: dict[str, float] = {}
    for sym, closes in closes_by_symbol.items():
        c3 = _pct_change(closes, 63)   # ~3 months (63 trading days)
        c6 = _pct_change(closes, 126)
        c9 = _pct_change(closes, 189)
        c12 = _pct_change(closes, 252)
        if None in (c3, c6, c9, c12):
            continue
        scores[sym] = 0.4 * c3 + 0.2 * c6 + 0.2 * c9 + 0.2 * c12

    if not scores:
        return {}

    # rank percentile
    sorted_syms = sorted(scores.items(), key=lambda x: x[1])  # ascending
    n = len(sorted_syms)
    ranks = {}
    for idx, (sym, _) in enumerate(sorted_syms):
        ranks[sym] = round((idx + 1) / n * 100, 1)
    return ranks
