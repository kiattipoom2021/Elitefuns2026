"""HTTP client → VPS Bot API (one-way: Railway calls VPS, never reverse)"""
import logging
import os
from datetime import date
import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Timeout: 30s เพราะ MT5 login บางทีช้ามาก
_VPS_TIMEOUT = 30.0
# Timeout สำหรับ fetch_rates — นานกว่าเพราะ copy_rates_range อาจช้า
_VPS_FETCH_TIMEOUT = 60.0
# Timeout สำหรับ backtest sweep — 10 นาที เพราะ multi-symbol sweep (~126 runs)
_VPS_BACKTEST_TIMEOUT = 600.0
# Timeout สำหรับ arbitrage scan — 3 นาที เพราะ 20 pairs × 2 symbols + small TF + bars เยอะ
_VPS_ARB_TIMEOUT = 180.0
# Timeout สำหรับ port sync — 60s ตาม spec (balance + PnL + reconcile orders)
_VPS_SYNC_PORT_TIMEOUT = 60.0
# Timeout สำหรับ fetch trades — 60s (history_deals_get อาจช้าถ้า window ใหญ่ + บัญชี trade เยอะ)
_VPS_FETCH_TRADES_TIMEOUT = 60.0


def _get_vps_config() -> tuple[str, str]:
    """อ่าน VPS_API_URL + VPS_API_KEY จาก env — raise ถ้ายังไม่ตั้งค่า"""
    url = os.getenv("VPS_API_URL", "").rstrip("/")
    key = os.getenv("VPS_API_KEY", "")
    if not url or not key:
        raise HTTPException(503, "VPS not reachable")
    return url, key


def is_configured() -> bool:
    """เช็คว่า VPS env vars ครบหรือยัง — ไม่ raise"""
    return bool(os.getenv("VPS_API_URL")) and bool(os.getenv("VPS_API_KEY"))


async def verify_mt5(login: int, password: str, server: str) -> dict:
    """
    เรียก VPS /mt5/verify เพื่อทดสอบ login MT5
    Returns: response dict จาก VPS (ปกติมี balance, equity, leverage, currency)
    Raises: HTTPException(503) ถ้า network/auth/timeout error
    """
    url, key = _get_vps_config()
    endpoint = f"{url}/mt5/verify"
    payload = {"login": login, "password": password, "server": server}
    headers = {"X-API-Key": key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=_VPS_TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        # ห้าม log password — log แค่ login + error type
        logger.error("VPS verify failed (login=%s): %s", login, type(exc).__name__)
        raise HTTPException(503, "VPS not reachable") from exc

    if resp.status_code == 401:
        logger.error("VPS rejected API key")
        raise HTTPException(503, "VPS not reachable")
    if resp.status_code >= 500:
        logger.error("VPS server error: %s", resp.status_code)
        raise HTTPException(503, "VPS not reachable")

    try:
        return resp.json()
    except ValueError as exc:
        logger.error("VPS returned non-JSON response")
        raise HTTPException(503, "VPS not reachable") from exc


async def trigger_bot_cycle() -> dict:
    """เรียก VPS POST /bot/trigger-cycle เพื่อรัน bot_cycle ทันที (manual override scheduler)

    Returns: {ok, summary: {...}} จาก VPS — ใช้แสดงผลใน admin panel
    Raises: HTTPException(503) ถ้า network/auth/timeout error
    """
    url, key = _get_vps_config()
    endpoint = f"{url}/bot/trigger-cycle"
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    logger.info("manual bot cycle trigger requested")

    try:
        # cycle อาจกินเวลานานถ้ามี subscriptions เยอะ — ใช้ backtest timeout (10 นาที)
        async with httpx.AsyncClient(timeout=_VPS_BACKTEST_TIMEOUT) as client:
            resp = await client.post(endpoint, json={}, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        logger.error("bot cycle trigger failed: %s", type(exc).__name__)
        raise HTTPException(503, "VPS not reachable") from exc

    if resp.status_code == 401:
        logger.error("VPS rejected API key")
        raise HTTPException(503, "VPS not reachable")
    if resp.status_code >= 500:
        logger.error("VPS server error: %s", resp.status_code)
        raise HTTPException(503, "VPS not reachable")

    try:
        return resp.json()
    except ValueError as exc:
        logger.error("VPS returned non-JSON response")
        raise HTTPException(503, "VPS not reachable") from exc


async def arb_scan(
    login: int,
    password: str,
    server: str,
    timeframe: str,
    lookback_bars: int,
) -> dict:
    """
    เรียก VPS /arb/scan เพื่อคำนวณ spread Z-score ของ pair universe
    Returns: response dict {ok, scan_at, scans[]} หรือ {ok: False, error}
    Raises: HTTPException(503) ถ้า network/timeout/non-JSON
    NOTE: ห้าม log password — log แค่ login + timeframe + bars
    """
    url, key = _get_vps_config()
    endpoint = f"{url}/arb/scan"
    payload = {
        "login": login,
        "password": password,
        "server": server,
        "timeframe": timeframe,
        "lookback_bars": lookback_bars,
    }
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    logger.info(
        "arb scan login=%s tf=%s bars=%d",
        login, timeframe, lookback_bars,
    )

    try:
        async with httpx.AsyncClient(timeout=_VPS_ARB_TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        logger.error(
            "VPS arb scan failed (login=%s, tf=%s, bars=%d): %s",
            login, timeframe, lookback_bars, type(exc).__name__,
        )
        raise HTTPException(503, "VPS not reachable") from exc

    if resp.status_code == 401:
        logger.error("VPS rejected API key")
        raise HTTPException(503, "VPS not reachable")
    if resp.status_code >= 500:
        logger.error("VPS server error: %s", resp.status_code)
        raise HTTPException(503, "VPS not reachable")

    try:
        return resp.json()
    except ValueError as exc:
        logger.error("VPS returned non-JSON response")
        raise HTTPException(503, "VPS not reachable") from exc


async def safety_cut(
    subscription_id: str,
    symbol: str,
    magic: int,
    login: int,
    password: str,
    server: str,
) -> dict:
    """เรียก VPS POST /bot/safety-cut เพื่อปิด positions ทุกตัวที่ match (symbol, magic)

    ใช้ใน delete subscription flow (stop_mode=immediate หรือ hard&safety_cut=true)
    VPS ต้อง mt5.initialize() ก่อนปิด positions → ต้องส่ง credentials ไปด้วย
    (pattern เดียวกับ /mt5/verify และ /arb/scan)

    Returns: {ok, positions_closed, positions_failed, subscription_id} จาก VPS
    Raises: HTTPException(503) ถ้า network/auth/timeout/non-JSON

    NOTE: caller ใน bots.py ต้อง catch HTTPException แล้ว log warning — ห้าม fail
    การลบเพราะ VPS ไม่ตอบ (สเปก: ลบ row ใน DB เป็นหลัก, VPS sync เป็น best-effort)
    SECURITY: ห้าม log password — log แค่ login + sub_id + symbol + magic
    """
    url, key = _get_vps_config()
    endpoint = f"{url}/bot/safety-cut"
    payload = {
        "subscription_id": subscription_id,
        "symbol": symbol,
        "magic": magic,
        "login": login,
        "password": password,
        "server": server,
    }
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    logger.info(
        "safety_cut request sub=%s symbol=%s magic=%s login=%s",
        subscription_id, symbol, magic, login,
    )

    try:
        async with httpx.AsyncClient(timeout=_VPS_TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        logger.error(
            "VPS safety_cut failed (sub=%s, symbol=%s, login=%s): %s",
            subscription_id, symbol, login, type(exc).__name__,
        )
        raise HTTPException(503, "VPS not reachable") from exc

    if resp.status_code == 401:
        logger.error("VPS rejected API key")
        raise HTTPException(503, "VPS not reachable")
    if resp.status_code >= 500:
        logger.error("VPS server error: %s", resp.status_code)
        raise HTTPException(503, "VPS not reachable")

    try:
        return resp.json()
    except ValueError as exc:
        logger.error("VPS returned non-JSON response")
        raise HTTPException(503, "VPS not reachable") from exc


async def sync_port(
    login: int,
    password: str,
    server: str,
    mt5_account_id: str,
    subscriptions: list[dict],
    open_orders: list[dict],
) -> dict:
    """เรียก VPS POST /bot/sync-port เพื่อ refresh balance + PnL + reconcile orders

    ใช้ใน Port Stats Phase 1 — orchestrate flow:
      1. VPS acquire MT5SessionQueue lock (wait queue ถ้า scheduler รัน)
      2. mt5.account_info() → POST {Railway}/api/internal/mt5/balance-update
      3. loop subscriptions → compute_subscription_pnl() → POST pnl-update
      4. mt5.positions_get() + history_deals_get() → reconcile ↔ Railway snapshot
      5. PATCH orders close สำหรับ ticket ที่ MT5 ปิดไปแล้ว

    Args:
      login, password, server: MT5 credentials (decrypted ก่อนส่งเข้ามาเท่านั้น)
      mt5_account_id: UUID ของ MT5Account row (VPS ใช้ใน balance-update payload)
      subscriptions: [{ subscription_id, magic, symbol }, ...] — VPS ใช้ในการ
                     compute PnL ต่อ subscription (magic + symbol = filter key)
      open_orders: snapshot ของ Railway DB open orders
                   [{ ticket, subscription_id, symbol, magic, type, volume,
                      sl, tp, opened_at }, ...] — VPS เทียบกับ positions_get()

    Returns: {balance_synced, subs_synced, orders_reconciled, errors} จาก VPS
    Raises: HTTPException(503) ถ้า network/auth/timeout/non-JSON

    SECURITY: ห้าม log password — log แค่ login + mt5_account_id + counts
    """
    url, key = _get_vps_config()
    endpoint = f"{url}/bot/sync-port"
    payload = {
        "login": login,
        "password": password,
        "server": server,
        "mt5_account_id": mt5_account_id,
        "subscriptions": subscriptions,
        "open_orders": open_orders,
    }
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    logger.info(
        "sync_port request login=%s mt5_account_id=%s subs=%d orders=%d",
        login, mt5_account_id, len(subscriptions), len(open_orders),
    )

    try:
        async with httpx.AsyncClient(timeout=_VPS_SYNC_PORT_TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        logger.error(
            "VPS sync_port failed (login=%s, mt5_account_id=%s): %s",
            login, mt5_account_id, type(exc).__name__,
        )
        raise HTTPException(503, "VPS not reachable") from exc

    if resp.status_code == 401:
        logger.error("VPS rejected API key")
        raise HTTPException(503, "VPS not reachable")
    if resp.status_code >= 500:
        logger.error("VPS server error: %s", resp.status_code)
        raise HTTPException(503, "VPS not reachable")

    try:
        return resp.json()
    except ValueError as exc:
        logger.error("VPS returned non-JSON response")
        raise HTTPException(503, "VPS not reachable") from exc


async def fetch_trades(
    login: int,
    password: str,
    server: str,
    days_back: int,
    limit: int,
) -> dict:
    """เรียก VPS POST /bot/fetch-trades เพื่อ fetch trade history ของ port

    ใช้ในหน้า port detail (รวม trade manual ที่ user เปิดเองนอก bot ด้วย).
    VPS acquire MT5SessionQueue → history_deals_get + positions_get → group by
    position_id → คืน list ของ trade dict.

    Args:
      login, password, server: MT5 credentials (decrypted ก่อนส่งเข้ามาเท่านั้น)
      days_back: 1..365 — จำนวนวันย้อนหลังที่จะ scan history
      limit:     1..500 — จำนวน trade สูงสุดที่จะ return (sort by open_time DESC)

    Returns:
      {
        "trades": [...],          # list ของ trade dict (ดู VPS schema)
        "total_in_window": int,   # จำนวน trades ที่ match ใน window (ก่อน limit)
      }
    Raises:
      HTTPException(503) ถ้า network/auth/timeout/non-JSON
      (ไม่ swallow เพราะ user รออ่าน data — frontend toast error)

    SECURITY: ห้าม log password — log แค่ login + days_back + limit
    """
    url, key = _get_vps_config()
    endpoint = f"{url}/bot/fetch-trades"
    payload = {
        "login": login,
        "password": password,
        "server": server,
        "days_back": days_back,
        "limit": limit,
    }
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    logger.info(
        "fetch_trades request login=%s days_back=%d limit=%d",
        login, days_back, limit,
    )

    try:
        async with httpx.AsyncClient(timeout=_VPS_FETCH_TRADES_TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        logger.error(
            "VPS fetch_trades failed (login=%s, days_back=%d): %s",
            login, days_back, type(exc).__name__,
        )
        raise HTTPException(503, "VPS not reachable") from exc

    if resp.status_code == 401:
        logger.error("VPS rejected API key")
        raise HTTPException(503, "VPS not reachable")
    if resp.status_code >= 500:
        logger.error("VPS server error: %s", resp.status_code)
        raise HTTPException(503, "VPS not reachable")

    try:
        return resp.json()
    except ValueError as exc:
        logger.error("VPS returned non-JSON response")
        raise HTTPException(503, "VPS not reachable") from exc


async def run_backtest(
    login: int,
    password: str,
    server: str,
    strategy_code: str,
    symbols: list[str],
    timeframe: str,
    date_from: date,
    date_to: date,
    params_sweep: list[dict],
) -> dict:
    """
    เรียก VPS /mt5/backtest/run เพื่อรัน backtest sweep ข้ามหลาย symbols
    Returns: response dict จาก VPS — {ok, engine_version, runs[]} หรือ {ok: False, error}
    Raises: HTTPException(503) ถ้า network/timeout/non-JSON
    NOTE: ห้าม log password — log แค่ login + strategy_code + symbol count
    """
    url, key = _get_vps_config()
    endpoint = f"{url}/mt5/backtest/run"
    payload = {
        "login": login,
        "password": password,
        "server": server,
        "strategy_code": strategy_code,
        "symbols": symbols,
        "timeframe": timeframe,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "params_sweep": params_sweep,
    }
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    logger.info(
        "backtest sweep login=%s strategy=%s symbols=%d",
        login, strategy_code, len(symbols),
    )

    try:
        async with httpx.AsyncClient(timeout=_VPS_BACKTEST_TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        logger.error(
            "VPS backtest failed (login=%s, strategy=%s, symbols=%d): %s",
            login, strategy_code, len(symbols), type(exc).__name__,
        )
        raise HTTPException(503, "VPS not reachable") from exc

    if resp.status_code == 401:
        logger.error("VPS rejected API key")
        raise HTTPException(503, "VPS not reachable")
    if resp.status_code >= 500:
        logger.error("VPS server error: %s", resp.status_code)
        raise HTTPException(503, "VPS not reachable")

    try:
        return resp.json()
    except ValueError as exc:
        logger.error("VPS returned non-JSON response")
        raise HTTPException(503, "VPS not reachable") from exc
