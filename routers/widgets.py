"""Console Data Dashboard widget endpoints

JWT-protected. Backed by tvdata service (TVdatafeed, anonymous mode).

Endpoints:
  GET /api/widgets/currency-strength?tf=H1|D1
  GET /api/widgets/market-snapshot?symbols=EURUSD,XAUUSD
  GET /api/widgets/news-calendar?impact=all|high|medium&hours=24
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from routers.auth import get_current_user  # noqa: F401  (กำหนด JWT dep)
from services import tvdata

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/widgets", tags=["widgets"])


# ─── Currency Strength ───────────────────────────────────────────────
@router.get("/currency-strength")
def get_currency_strength(
    tf: str = Query("H1", regex="^(H1|D1)$"),
    _user=Depends(get_current_user),
):
    currencies = tvdata.currency_strength(tf)
    return {
        "tf": tf,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "currencies": currencies,
    }


# ─── Trend Strength Matrix ──────────────────────────────────────────
@router.get("/trend-matrix")
def get_trend_matrix(_user=Depends(get_current_user)):
    return tvdata.trend_matrix(tf="H1")


# ─── SET100 Trend Template (Minervini) ──────────────────────────────
@router.get("/set100-template")
def get_set100_template(_user=Depends(get_current_user)):
    return tvdata.set100_template()


# ─── Pair Cluster (Engle-Granger cointegration) ─────────────────────
@router.get("/pair-cluster")
def get_pair_cluster(
    tf: str = Query("H1", regex="^(H1|D1)$"),
    k: int = Query(3, ge=2, le=5),
    bars: int = Query(100, ge=30, le=300),
    _user=Depends(get_current_user),
):
    return tvdata.pair_cluster(tf=tf, k=k, bars=bars)


# ─── Market Snapshot ─────────────────────────────────────────────────
@router.get("/market-snapshot")
def get_market_snapshot(
    symbols: str = Query(..., min_length=1, max_length=200),
    _user=Depends(get_current_user),
):
    raw_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not raw_list:
        raise HTTPException(400, "symbols ว่าง")
    if len(raw_list) > 10:
        raise HTTPException(400, "symbols เกิน 10 ตัว")

    snapshots = tvdata.market_snapshot(raw_list)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "snapshots": snapshots,
    }


# ─── News Calendar (FX Factory XML) ──────────────────────────────────
_FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
_news_cache: dict[str, tuple[float, list[dict]]] = {}
_NEWS_TTL = 15 * 60  # 15 นาที


def _parse_ff_event(elem) -> Optional[dict]:
    """parse FF XML <event> → dict. คืน None ถ้า field สำคัญหาย"""
    def _txt(tag: str) -> Optional[str]:
        node = elem.find(tag)
        return node.text.strip() if node is not None and node.text else None

    title = _txt("title")
    country = _txt("country")
    date = _txt("date")  # e.g. "06-26-2026" (MM-DD-YYYY)
    time_str = _txt("time")  # e.g. "8:30am"
    impact = _txt("impact")  # "High" | "Medium" | "Low" | "Holiday"
    forecast = _txt("forecast")
    previous = _txt("previous")

    if not (title and country and date and time_str and impact):
        return None

    # parse "06-26-2026 8:30am" (US Eastern → UTC ต้องชดเชย; FF เผยแพร่ Eastern)
    try:
        dt_naive = datetime.strptime(f"{date} {time_str}", "%m-%d-%Y %I:%M%p")
        # FF time = US Eastern. Approximate UTC offset: EDT (-04:00) / EST (-05:00).
        # ใช้ -04:00 default (cover summer); accuracy ±1h เพียงพอสำหรับ widget
        dt_utc = dt_naive.replace(tzinfo=timezone(timedelta(hours=-4))).astimezone(timezone.utc)
    except ValueError:
        return None

    return {
        "time_utc": dt_utc.isoformat(),
        "currency": country.upper(),
        "title": title,
        "impact": impact.lower(),  # high/medium/low/holiday
        "forecast": forecast,
        "previous": previous,
    }


def _fetch_ff_events() -> list[dict]:
    """Fetch + parse FX Factory weekly XML. Return all events (no filter)."""
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(_FF_URL)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        events = []
        for ev in root.iter("event"):
            parsed = _parse_ff_event(ev)
            if parsed:
                events.append(parsed)
        return events
    except Exception as exc:
        logger.error("FX Factory fetch failed: %s", exc)
        raise


@router.get("/news-calendar")
def get_news_calendar(
    impact: str = Query("all", regex="^(all|high|medium|low)$"),
    hours: int = Query(24, ge=1, le=168),
    _user=Depends(get_current_user),
):
    cache_key = "ff_all"
    now = time.time()
    cached = _news_cache.get(cache_key)
    events: list[dict] = []
    warning: Optional[str] = None

    if cached and (now - cached[0]) < _NEWS_TTL:
        events = cached[1]
    else:
        try:
            events = _fetch_ff_events()
            _news_cache[cache_key] = (now, events)
        except Exception:
            if cached:
                events = cached[1]
                warning = "ใช้ cached events — fetch ล่าสุดล้มเหลว"
            else:
                raise HTTPException(503, "FX Factory ไม่ตอบ + ไม่มี cache")

    # filter by impact + next N hours
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc + timedelta(hours=hours)

    def _within_window(ev: dict) -> bool:
        try:
            t = datetime.fromisoformat(ev["time_utc"])
        except ValueError:
            return False
        return now_utc <= t <= cutoff

    def _impact_match(ev: dict) -> bool:
        if impact == "all":
            return ev["impact"] in ("high", "medium", "low")
        return ev["impact"] == impact

    filtered = [ev for ev in events if _within_window(ev) and _impact_match(ev)]
    filtered.sort(key=lambda ev: ev["time_utc"])

    out: dict = {"events": filtered}
    if warning:
        out["warning"] = warning
    return out
