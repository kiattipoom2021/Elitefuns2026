"""TV data reader — อ่าน OHLC จาก DB (cache by scheduler)

Schema: ตาราง tv_ohlc — refreshed โดย services/tv_scheduler.py ทุก ชม.
Public API:
  - get_ohlc(symbol, tf, n_bars) → list[dict]
  - currency_strength(tf) → ranked list
  - market_snapshot(symbols) → list[dict]

ไม่เรียก TV ตรง — ถ้า DB ไม่มีข้อมูล คืน [] / None.
TV fetching อยู่ใน tv_scheduler.py เท่านั้น.
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Optional

from sqlalchemy import desc, select

from database import SessionLocal
from models.tv_ohlc import TVOhlc

logger = logging.getLogger(__name__)


# ─── Symbol mapping ──────────────────────────────────────────────────
# alias → (symbol, exchange) สำหรับ widget API (กรณี user พิมพ์ alias เช่น "XAUUSD")
SYMBOL_MAP: dict[str, tuple[str, str]] = {
    # forex (OANDA) — G8 pairs ที่ scheduler cache อยู่
    "EURUSD": ("EURUSD", "OANDA"),
    "GBPUSD": ("GBPUSD", "OANDA"),
    "AUDUSD": ("AUDUSD", "OANDA"),
    "NZDUSD": ("NZDUSD", "OANDA"),
    "USDJPY": ("USDJPY", "OANDA"),
    "USDCHF": ("USDCHF", "OANDA"),
    "USDCAD": ("USDCAD", "OANDA"),
    "EURGBP": ("EURGBP", "OANDA"),
    "EURJPY": ("EURJPY", "OANDA"),
    "GBPJPY": ("GBPJPY", "OANDA"),
    "AUDJPY": ("AUDJPY", "OANDA"),
}


def _resolve_symbol(alias: str) -> tuple[str, str]:
    key = alias.upper().strip()
    if key in SYMBOL_MAP:
        return SYMBOL_MAP[key]
    return (key, "OANDA")  # forex default


# ─── Public: get OHLC (DB read) ──────────────────────────────────────
def get_ohlc(symbol: str, tf: str, n_bars: int = 30) -> list[dict]:
    """อ่าน OHLC ล่าสุด n_bars แท่งจาก DB. คืน list[dict] เรียงตาม ts ascending."""
    sym, _exch = _resolve_symbol(symbol)
    with SessionLocal() as session:
        rows = session.execute(
            select(TVOhlc)
            .where(TVOhlc.symbol == sym, TVOhlc.tf == tf)
            .order_by(desc(TVOhlc.ts))
            .limit(n_bars)
        ).scalars().all()

    if not rows:
        return []

    # reverse เป็น ascending สำหรับการคำนวณ ROC / ATR
    rows = list(reversed(rows))
    return [
        {
            "ts": r.ts.isoformat() + "Z",
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for r in rows
    ]


# ─── Public: Currency Strength ───────────────────────────────────────
_G8 = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
_STRENGTH_PAIRS: list[tuple[str, str, str]] = [
    # (symbol, base, quote)
    ("EURUSD", "EUR", "USD"),
    ("GBPUSD", "GBP", "USD"),
    ("AUDUSD", "AUD", "USD"),
    ("NZDUSD", "NZD", "USD"),
    ("USDJPY", "USD", "JPY"),
    ("USDCHF", "USD", "CHF"),
    ("USDCAD", "USD", "CAD"),
    ("EURGBP", "EUR", "GBP"),
    ("EURJPY", "EUR", "JPY"),
    ("GBPJPY", "GBP", "JPY"),
    ("AUDJPY", "AUD", "JPY"),
]


def _roc_pct(bars: list[dict], window: int) -> Optional[float]:
    """% change ระหว่าง close ปัจจุบัน กับ close ของ window bars ที่แล้ว"""
    if len(bars) < window + 1:
        return None
    last = bars[-1]["close"]
    base = bars[-1 - window]["close"]
    if base == 0:
        return None
    return ((last - base) / base) * 100.0


def currency_strength(tf: str = "H1") -> list[dict]:
    """คำนวณ G8 currency strength ด้วย ROC averaging.
    Window: H1 → 24 bars (24h), D1 → 7 bars (7d).
    อ่านข้อมูลจาก DB (cache by scheduler).
    """
    if tf not in ("H1", "D1"):
        tf = "H1"
    window = 24 if tf == "H1" else 7
    n_bars = window + 5  # buffer

    contrib: dict[str, list[float]] = {ccy: [] for ccy in _G8}

    for sym, base, quote in _STRENGTH_PAIRS:
        bars = get_ohlc(sym, tf, n_bars=n_bars)
        roc = _roc_pct(bars, window)
        if roc is None:
            continue
        contrib[base].append(roc)
        contrib[quote].append(-roc)

    result = []
    for ccy in _G8:
        rocs = contrib[ccy]
        if not rocs:
            result.append({"ccy": ccy, "roc_pct": 0.0, "samples": 0})
        else:
            avg = sum(rocs) / len(rocs)
            result.append({"ccy": ccy, "roc_pct": round(avg, 3), "samples": len(rocs)})

    result.sort(key=lambda x: x["roc_pct"], reverse=True)
    return result


# ─── Public: Trend Strength Matrix (per-pair SMA distance) ───────────
# Methodology (per babypips):
#   - For each pair: dist = (close − SMA) / SMA × 100
#   - Positive = ราคา above SMA → bullish trend
#   - Negative = ราคา below SMA → bearish
#   - 4-quadrant scatter: x = 200 SMA dist, y = 50 SMA dist
def trend_matrix(tf: str = "H1") -> dict:
    """SMA distance matrix — 11 G8 pairs × {50 SMA, 200 SMA}.

    Returns:
      {
        tf, method: "SMA distance",
        columns: ["50 SMA", "200 SMA"],
        computed_at: ISO,
        rows: [
          { symbol,
            cells: {
              "50 SMA":  {dist_pct, rank, sma, last},
              "200 SMA": {dist_pct, rank, sma, last}
            },
            avg_rank
          }, ...
        ]
      }
    """
    from datetime import datetime, timezone
    computed_at = datetime.now(timezone.utc).isoformat()

    columns = ["50 SMA", "200 SMA"]
    rows: list[dict] = []

    # _CLUSTER_PAIRS = 11 G8 pairs (reuse same universe)
    for sym in _CLUSTER_PAIRS:
        bars = get_ohlc(sym, tf, n_bars=200)
        if len(bars) < 50:
            # ข้อมูลไม่พอแม้แต่ 50 SMA
            rows.append({"symbol": sym, "cells": {c: None for c in columns}})
            continue

        closes = [b["close"] for b in bars]
        last = closes[-1]
        cells: dict[str, Optional[dict]] = {}

        # 50 SMA
        if len(closes) >= 50:
            sma50 = sum(closes[-50:]) / 50
            cells["50 SMA"] = {
                "dist_pct": round((last - sma50) / sma50 * 100, 3),
                "sma": round(sma50, 5),
                "last": round(last, 5),
            }
        else:
            cells["50 SMA"] = None

        # 200 SMA
        if len(closes) >= 200:
            sma200 = sum(closes[-200:]) / 200
            cells["200 SMA"] = {
                "dist_pct": round((last - sma200) / sma200 * 100, 3),
                "sma": round(sma200, 5),
                "last": round(last, 5),
            }
        else:
            cells["200 SMA"] = None

        rows.append({"symbol": sym, "cells": cells})

    # rank per column (1 = most bullish)
    for col in columns:
        scored = [
            (r["symbol"], r["cells"][col]["dist_pct"]) for r in rows if r["cells"][col]
        ]
        scored.sort(key=lambda x: -x[1])
        for rank, (sym, _) in enumerate(scored, start=1):
            for r in rows:
                if r["symbol"] == sym and r["cells"][col] is not None:
                    r["cells"][col]["rank"] = rank

    # avg_rank
    for r in rows:
        ranks = [r["cells"][c]["rank"] for c in columns if r["cells"][c] is not None]
        r["avg_rank"] = round(sum(ranks) / len(ranks), 2) if ranks else 99.0

    rows.sort(key=lambda r: r["avg_rank"])

    return {
        "tf": tf,
        "method": "SMA distance (per pair)",
        "columns": columns,
        "computed_at": computed_at,
        "rows": rows,
    }


# ─── Public: SET100 Trend Template (Minervini) ───────────────────────
_set100_cache: dict[str, tuple[float, dict]] = {}
_set100_lock = threading.Lock()
_SET100_TTL = 60 * 60  # 1 ชั่วโมง


def set100_template(min_score: int = 8) -> dict:
    """Apply Minervini trend template ทั้ง SET100 → return passing stocks.

    Args:
      min_score: minimum criteria ผ่าน (default 8 = ครบทุก criteria ที่ตรวจได้)

    Returns:
      {
        computed_at, total_scanned, passing_count,
        passing: [{symbol, score, criteria, details}, ...] sorted desc by score+pct_from_low,
        near_pass: [{symbol, score, ...}] passing ≥ min_score-1
      }
    """
    cache_key = f"min{min_score}"
    with _set100_lock:
        entry = _set100_cache.get(cache_key)
        if entry and (time.time() - entry[0]) < _SET100_TTL:
            return entry[1]

    from datetime import datetime, timezone
    from services.set100_universe import SET100_STOCKS
    from services.trend_template import check_trend_template, compute_rs_rank

    # โหลด closes ของทุกหุ้นเพื่อคำนวณ RS rank
    closes_map: dict[str, list[float]] = {}
    bars_map: dict[str, list[dict]] = {}
    for sym in SET100_STOCKS:
        bars = get_ohlc(sym, "D1", n_bars=260)
        if len(bars) >= 252:
            bars_map[sym] = bars
            closes_map[sym] = [b["close"] for b in bars]

    rs_ranks = compute_rs_rank(closes_map)

    # ตรวจ trend template
    all_results = []
    for sym, bars in bars_map.items():
        result = check_trend_template(bars, rs_rank=rs_ranks.get(sym))
        result["symbol"] = sym
        # sparkline: 60 closes ล่าสุด (~3 เดือน trading days)
        result["sparkline"] = [round(c, 3) for c in closes_map[sym][-60:]]
        all_results.append(result)

    # filter + sort
    passing = [r for r in all_results if r.get("passes_all")]
    passing.sort(
        key=lambda r: (
            -(r.get("details", {}).get("rs_rank") or 0),
            -r.get("details", {}).get("pct_from_low_52w", 0),
        )
    )

    result = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "total_scanned": len(all_results),
        "passing_count": len(passing),
        "passing": passing[:50],
    }
    with _set100_lock:
        _set100_cache[cache_key] = (time.time(), result)
    return result


# ─── Public: Market Snapshot ─────────────────────────────────────────
def _atr(bars: list[dict], period: int = 14) -> Optional[float]:
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(len(bars) - period, len(bars)):
        h = bars[i]["high"]
        lo = bars[i]["low"]
        prev_close = bars[i - 1]["close"] if i > 0 else bars[i]["open"]
        tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


# ─── Public: Pair Cluster (Engle-Granger cointegration) ──────────────
# 11 G8 pairs → C(11,2)=55 combinations
_CLUSTER_PAIRS = [
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD",
    "USDJPY", "USDCHF", "USDCAD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY",
]

# in-memory cache (cointegration ราคาแพง — 55 tests/ครั้ง)
_cluster_cache: dict[str, tuple[float, dict]] = {}
_cluster_lock = threading.Lock()
_CLUSTER_TTL = 60 * 60  # 1 ชั่วโมง


def pair_cluster(tf: str = "H1", k: int = 3, bars: int = 100) -> dict:
    """K-means clustering of G8 pairs by log-return shape.

    Args:
      tf: "H1" หรือ "D1"
      k: จำนวน cluster (default 3)
      bars: จำนวน bar ที่ใช้คำนวณ (default 100)

    Algorithm:
      1. โหลด close series ของแต่ละ pair จาก DB
      2. คำนวณ log returns: ln(c[t]/c[t-1])
      3. K-means บน matrix (n_pairs, n_returns) — pair = sample, return = feature
      4. จัด pair เข้า cluster ตาม centroid

    Returns:
      {
        tf, k, bars_used, computed_at, missing_symbols,
        clusters: [
          {id, members: [...], size, cum_return_pct, vol_pct, sharpe_approx}
        ]
      }
    """
    if tf not in ("H1", "D1"):
        tf = "H1"
    k = max(2, min(int(k), 5))  # clamp 2-5

    cache_key = f"{tf}:k{k}:{bars}"
    with _cluster_lock:
        entry = _cluster_cache.get(cache_key)
        if entry and (time.time() - entry[0]) < _CLUSTER_TTL:
            return entry[1]

    from datetime import datetime, timezone
    computed_at = datetime.now(timezone.utc).isoformat()

    closes: dict[str, list[float]] = {}
    min_required = max(30, int(bars * 0.5))
    for sym in _CLUSTER_PAIRS:
        b = get_ohlc(sym, tf, n_bars=bars)
        if len(b) >= min_required:
            closes[sym] = [r["close"] for r in b]

    missing = [s for s in _CLUSTER_PAIRS if s not in closes]

    def _cache_and_return(result: dict) -> dict:
        with _cluster_lock:
            _cluster_cache[cache_key] = (time.time(), result)
        return result

    if len(closes) < k:
        return _cache_and_return({
            "tf": tf, "k": k, "bars_used": 0,
            "computed_at": computed_at,
            "missing_symbols": missing,
            "clusters": [],
            "error": f"ข้อมูลไม่พอ ({len(closes)} pairs < k={k}) — รอ scheduler",
        })

    # ตัด series ให้ยาวเท่ากัน
    syms = sorted(closes.keys())
    min_len = min(len(closes[s]) for s in syms)
    aligned = {s: closes[s][-min_len:] for s in syms}

    try:
        import math
        from sklearn.cluster import KMeans  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        logger.error("scikit-learn ไม่ได้ install — pair_cluster ใช้ไม่ได้")
        return _cache_and_return({
            "tf": tf, "k": k, "bars_used": min_len,
            "computed_at": computed_at,
            "missing_symbols": missing,
            "clusters": [],
            "error": "scikit-learn ไม่พบ",
        })

    # คำนวณ log returns + standardize
    feature_matrix = []
    for s in syms:
        prices = aligned[s]
        rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
        # z-score normalize — ให้ K-means เปรียบเทียบ shape ไม่ใช่ magnitude
        mu = sum(rets) / len(rets)
        sigma = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 or 1e-12
        rets_z = [(r - mu) / sigma for r in rets]
        feature_matrix.append(rets_z)

    X = np.array(feature_matrix, dtype=float)
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(X)

    # จัด cluster
    by_cluster: dict[int, list[int]] = {}
    for idx, lbl in enumerate(labels):
        by_cluster.setdefault(int(lbl), []).append(idx)

    clusters = []
    for cid, member_idxs in by_cluster.items():
        members_sym = [syms[i] for i in member_idxs]
        # raw stats (un-normalized) ต่อ pair → averaged ภายใน cluster
        cum_rets = []
        vols = []
        for s in members_sym:
            prices = aligned[s]
            rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
            cum_rets.append(sum(rets))  # cumulative log return
            mu = sum(rets) / len(rets)
            vols.append((sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5)
        avg_cum = sum(cum_rets) / len(cum_rets) if cum_rets else 0.0
        avg_vol = sum(vols) / len(vols) if vols else 0.0
        sharpe = (avg_cum / (avg_vol * (len(rets) ** 0.5))) if avg_vol else 0.0
        clusters.append({
            "id": cid,
            "members": members_sym,
            "size": len(members_sym),
            "cum_return_pct": round((math.exp(avg_cum) - 1) * 100, 3),
            "vol_pct": round(avg_vol * 100, 3),
            "sharpe_approx": round(sharpe, 3),
        })

    # sort: cum_return_pct desc (top trender first)
    clusters.sort(key=lambda c: -c["cum_return_pct"])

    return _cache_and_return({
        "tf": tf,
        "k": k,
        "bars_used": min_len,
        "computed_at": computed_at,
        "missing_symbols": missing,
        "total_pairs": len(syms),
        "clusters": clusters,
    })


def market_snapshot(symbols: list[str]) -> list[dict]:
    """Return [{symbol, last, spread_pips, atr_14, change_pct}] อ่านจาก DB (H1)."""
    out = []
    for raw in symbols[:10]:
        bars = get_ohlc(raw, "H1", n_bars=30)
        if not bars:
            out.append({
                "symbol": raw.upper(),
                "last": None,
                "spread_pips": None,
                "atr_14": None,
                "change_pct": None,
                "error": "no_data_in_cache",
            })
            continue
        last_close = bars[-1]["close"]
        prev_close = bars[-2]["close"] if len(bars) >= 2 else last_close
        change_pct = ((last_close - prev_close) / prev_close * 100.0) if prev_close else 0.0
        atr_14 = _atr(bars, 14)
        spread_est = (atr_14 / 10.0) if atr_14 else None
        out.append({
            "symbol": raw.upper(),
            "last": round(last_close, 5),
            "spread_pips": round(spread_est, 2) if spread_est is not None else None,
            "atr_14": round(atr_14, 5) if atr_14 is not None else None,
            "change_pct": round(change_pct, 3),
        })
    return out
