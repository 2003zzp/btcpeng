#!/usr/bin/env python3
"""
Local dashboard for Binance USD-M futures testnet signals.

This service reads public Binance USD-M futures market data, then submits
orders only to Binance USD-M Futures Testnet when signals pass the risk gate.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import hmac
import http.client
import json
import math
import mimetypes
import os
import random
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
CONFIG_PATH = ROOT / "paper_dashboard_config.json"
STATE_PATH = ROOT / "data" / "paper_dashboard_state.json"
SIGNAL_REVIEW_PATH = ROOT / "data" / "signal_review.jsonl"
SIGNAL_REVIEW_STATE_PATH = ROOT / "data" / "signal_review_state.json"
FAST_CONFIRM_PATH = ROOT / "fast_signal_confirmer.py"
FAST_CONFIRM_MODULE: Any | None = None
FAST_CONFIRM_SOURCE = "fast_signal_confirmer_v2"
SQUARE_POST_SKILL_DIR = Path(os.environ.get("BINANCE_SQUARE_SKILL_DIR", r"C:\Users\Administrator\.codex\skills\square-post"))
REPORTS_DIR = ROOT / "reports"
DAILY_PNL_REPORT_PATH = REPORTS_DIR / "daily_realized_pnl.xlsx"
RETRYABLE_HTTP_EXCEPTIONS = (
    TimeoutError,
    socket.timeout,
    ssl.SSLError,
    urllib.error.URLError,
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    ConnectionResetError,
    ConnectionAbortedError,
)


DEFAULT_CONFIG: dict[str, Any] = {
    "initial_equity_usdt": 100.0,
    "execution_mode": "binance_futures_testnet",
    "live_trading_enabled": False,
    "scan_interval_sec": 20,
    "max_scan_runtime_sec": 180,
    "market_base_url": "https://fapi.binance.com",
    "request_timeout_sec": 10,
    "quote_asset": "USDT",
    "min_quote_volume_usdt": 20_000_000,
    "min_abs_24h_change_pct": 6.0,
    "strong_long_24h_min_change_pct": 0.0,
    "strong_short_enabled": True,
    "long_24h_change_pct": 7.0,
    "short_24h_change_pct": -7.0,
    "short_term_move_pct": 1.2,
    "volume_spike_ratio": 1.5,
    "trade_direction_mode": "both",
    "reverse_orders_enabled": False,
    "signal_score_threshold": 4.0,
    "confirmation_mode": "market_score",
    "market_confirm_min_score": 7.0,
    "short_market_confirm_min_score": 7.5,
    "short_max_24h_high_drawdown_pct": 75.0,
    "short_structure_confirmation_enabled": True,
    "short_structure_min_score": 4.0,
    "long_structure_confirmation_enabled": False,
    "long_structure_min_score": 2.5,
    "market_regime_filter_enabled": True,
    "market_regime_min_score": 4.5,
    "analyzer_confirmation_enabled": True,
    "analyzer_min_confidence_pct": 90.0,
    "analyzer_timeout_sec": 12.0,
    "analyzer_cache_ttl_sec": 60,
    "analyzer_failed_cache_ttl_sec": 120,
    "analyzer_realtime_before_open": True,
    "analyzer_max_workers": 2,
    "pending_open_queue_enabled": True,
    "pending_open_queue_ttl_sec": 180,
    "max_candidates_per_scan": 18,
    "pre_start_long_enabled": False,
    "pre_start_max_candidates": 8,
    "pre_start_24h_min_pct": 1.0,
    "pre_start_24h_max_pct": 12.0,
    "pre_start_15m_min_pct": 0.4,
    "pre_start_15m_max_pct": 4.0,
    "pre_start_1h_min_pct": 0.8,
    "pre_start_1h_max_pct": 8.0,
    "pre_start_volume_ratio_min": 1.5,
    "pre_start_volume_ratio_max": 5.0,
    "pre_start_taker_ratio_min": 1.05,
    "pre_start_oi_1h_min_pct": 0.0,
    "pre_start_funding_abs_max_pct": 0.06,
    "pre_start_long_short_max": 2.5,
    "pre_start_market_confirm_min_score": 6.5,
    "pre_start_margin_per_trade_usdt": 3.0,
    "pre_start_leverage": 2,
    "old_rebound_short_enabled": False,
    "old_rebound_short_max_candidates": 8,
    "old_rebound_short_24h_min_pct": 8.0,
    "old_rebound_short_24h_max_pct": 35.0,
    "old_rebound_short_min_quote_volume_usdt": 5_000_000,
    "old_rebound_short_max_quote_volume_usdt": 80_000_000,
    "old_rebound_short_min_history_days": 365,
    "old_rebound_short_15m_max_pct": 0.3,
    "old_rebound_short_1h_max_pct": 0.5,
    "old_rebound_short_15m_min_pct": -3.0,
    "old_rebound_short_1h_min_pct": -6.0,
    "old_rebound_short_15m_break_min_pct": 0.2,
    "old_rebound_short_max_high_drawdown_pct": 35.0,
    "old_rebound_short_min_low_distance_pct": 5.0,
    "old_rebound_short_volume_ratio_min": 0.8,
    "old_rebound_short_volume_ratio_max": 6.0,
    "old_rebound_short_taker_ratio_max": 0.95,
    "old_rebound_short_long_short_min": 1.2,
    "old_rebound_short_oi_1h_min_pct": -5.0,
    "old_rebound_short_funding_abs_max_pct": 0.08,
    "old_rebound_short_confirm_min_score": 6.8,
    "old_rebound_short_margin_per_trade_usdt": 3.0,
    "old_rebound_short_leverage": 2,
    "high_pullback_short_enabled": False,
    "high_pullback_short_max_candidates": 5,
    "high_pullback_short_24h_min_pct": 8.0,
    "high_pullback_short_24h_max_pct": 60.0,
    "high_pullback_short_min_drawdown_pct": 18.0,
    "high_pullback_short_max_drawdown_pct": 45.0,
    "high_pullback_short_min_low_distance_pct": 5.0,
    "high_pullback_short_15m_max_pct": 0.3,
    "high_pullback_short_1h_max_pct": 0.5,
    "high_pullback_short_volume_ratio_min": 0.8,
    "high_pullback_short_volume_ratio_max": 6.0,
    "high_pullback_short_taker_ratio_max": 0.95,
    "high_pullback_short_oi_1h_min_pct": -5.0,
    "high_pullback_short_funding_abs_max_pct": 0.08,
    "high_pullback_short_confirm_min_score": 7.0,
    "high_pullback_short_margin_per_trade_usdt": 3.0,
    "high_pullback_short_leverage": 2,
    "max_kline_enriched_candidates": 8,
    "kline_15m_limit": 40,
    "kline_1h_limit": 10,
    "max_new_positions_per_scan": 2,
    "margin_per_trade_usdt": 9.0,
    "leverage": 3,
    "margin_type": "ISOLATED",
    "max_open_positions": 15,
    "max_exposure_pct": 200.0,
    "max_daily_profit_enabled": True,
    "max_daily_profit_usdt": 30.0,
    "max_daily_loss_enabled": True,
    "max_daily_loss_usdt": 20.0,
    "equity_take_profit_enabled": False,
    "previous_day_equity_usdt": 0.0,
    "equity_take_profit_usdt": 100.0,
    "stop_loss_cooldown_enabled": True,
    "stop_loss_cooldown_minutes": 60,
    "take_profit_cooldown_enabled": True,
    "take_profit_roi_pct": 90.0,
    "stop_loss_roi_pct": 86.0,
    "telegram_enabled": True,
    "telegram_hourly_status_enabled": True,
    "telegram_hourly_status_interval_minutes": 60,
    "square_publish_enabled": True,
    "square_publish_on_open": True,
    "square_publish_disclaimer": "本人观点仅供参考，不构成投资建议。",
    "daily_excel_report_enabled": True,
    "daily_excel_report_path": str(DAILY_PNL_REPORT_PATH),
    "exclude_symbols": ["USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "EURUSDT"],
    "blocked_symbols": [],
    "manual_watchlist": [],
    "futures_testnet": {
        "base_url": "https://testnet.binancefuture.com",
        "recv_window": 5000,
        "margin_type": "ISOLATED",
        "working_type": "MARK_PRICE",
        "quantity_safety_factor": 0.98,
    },
    "futures_live": {
        "base_url": "https://fapi.binance.com",
        "recv_window": 5000,
        "margin_type": "ISOLATED",
        "working_type": "MARK_PRICE",
        "quantity_safety_factor": 0.98,
    },
}


@dataclass
class Signal:
    symbol: str
    side: str
    score: float
    price: float
    quote_volume: float
    change_24h_pct: float
    change_4h_pct: float
    change_1h_pct: float
    short_move_pct: float
    volume_ratio: float
    open_interest_usdt: float
    open_interest_24h_pct: float
    open_interest_4h_pct: float
    open_interest_1h_pct: float
    funding_rate_pct: float
    long_short_ratio: float
    volume_4h_ratio: float
    taker_long_short_ratio: float
    oi_volume_ratio: float
    signal_mode: str
    signal_heat_level: str
    market_regime_score: float
    market_regime_label: str
    market_regime_reason: str
    risk_level: str
    analyzer_direction: str
    analyzer_confidence_pct: float
    analyzer_reason: str
    trigger_reason: str
    reject_reason: str
    decision_summary: str
    decision_steps: list[str]
    priority: str
    planned_stop_price: float = 0.0
    planned_take_profit_price: float = 0.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_time_ms(value: Any) -> int:
    number = fnum(value)
    if number > 10_000_000_000:
        return int(number)
    if number > 1_000_000_000:
        return int(number * 1000)
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            return int(datetime.fromisoformat(raw).timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def closed_klines(klines: list[list[Any]], now_ms: int | None = None) -> list[list[Any]]:
    cutoff = now_ms if now_ms is not None else int(time.time() * 1000)
    closed: list[list[Any]] = []
    for kline in klines:
        close_time = parse_time_ms(kline[6] if len(kline) > 6 else 0)
        if close_time <= 0 or close_time <= cutoff:
            closed.append(kline)
    return closed


def latest_unclosed_kline(klines: list[list[Any]], now_ms: int | None = None) -> list[Any] | None:
    if not klines:
        return None
    cutoff = now_ms if now_ms is not None else int(time.time() * 1000)
    latest = klines[-1]
    close_time = parse_time_ms(latest[6] if len(latest) > 6 else 0)
    return latest if close_time > cutoff else None


def local_datetime(value: Any) -> datetime | None:
    millis = parse_time_ms(value)
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000).astimezone()


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return default


def first_present(item: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if key in item and item.get(key) not in {None, ""}:
            return item.get(key)
    return default


def normalize_symbol_list(value: Any, quote_asset: str = "USDT") -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace("，", ",").replace("\n", ",").replace(" ", ",").split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    symbols = []
    seen = set()
    quote = quote_asset.upper()
    for item in raw_items:
        symbol = str(item).strip().upper().replace("/", "").replace("-", "")
        if not symbol:
            continue
        if not symbol.endswith(quote):
            symbol = f"{symbol}{quote}"
        if symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def price_text(value: Any) -> str:
    number = fnum(value)
    if number >= 100:
        return f"{number:.2f}"
    if number >= 1:
        return f"{number:.4f}"
    return f"{number:.8f}"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_json_lenient(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return load_json(path, fallback)
    except UnicodeDecodeError:
        try:
            raw = path.read_bytes()
            return json.loads(raw.decode("utf-8-sig", errors="replace"))
        except Exception:
            corrupt_path = path.with_suffix(path.suffix + ".corrupt")
            try:
                path.replace(corrupt_path)
            except Exception:
                pass
            return fallback
    except json.JSONDecodeError:
        corrupt_path = path.with_suffix(path.suffix + ".corrupt")
        try:
            path.replace(corrupt_path)
        except Exception:
            pass
        return fallback


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def decode_http_body(resp: Any) -> str:
    raw = resp.read()
    try:
        charset = resp.headers.get_content_charset() or "utf-8"
    except Exception:
        charset = "utf-8"
    try:
        return raw.decode(charset)
    except UnicodeDecodeError:
        return raw.decode(charset, errors="replace")


def deep_update(target: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value
    return target


def load_fast_confirmer() -> Any:
    global FAST_CONFIRM_MODULE
    if FAST_CONFIRM_MODULE is not None:
        return FAST_CONFIRM_MODULE
    if not FAST_CONFIRM_PATH.exists():
        raise RuntimeError(f"找不到快速确认脚本：{FAST_CONFIRM_PATH}")
    import fast_signal_confirmer

    FAST_CONFIRM_MODULE = fast_signal_confirmer
    return FAST_CONFIRM_MODULE


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http_get_json(base_url: str, path: str, params: dict[str, Any] | None = None, timeout: float = 6) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "paper-futures-dashboard/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(decode_http_body(resp))
        except RETRYABLE_HTTP_EXCEPTIONS as exc:
            last_error = exc
            if attempt >= 2:
                break
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"HTTP GET failed after retries: {last_error}") from last_error


def http_request_json(
    method: str,
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 6,
    error_label: str = "HTTP",
) -> Any:
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{base_url.rstrip('/')}{path}"
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "testnet-futures-dashboard/1.0",
        **(headers or {}),
    }
    if method.upper() in {"POST", "DELETE", "PUT"}:
        data = query.encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = decode_http_body(resp)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{error_label} HTTP {exc.code}: {body}") from exc
        except RETRYABLE_HTTP_EXCEPTIONS as exc:
            last_error = exc
            if attempt >= 2:
                break
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"Binance request failed after retries: {last_error}") from last_error


def quantize_down(value: float, step: str) -> str:
    step_dec = Decimal(str(step))
    if step_dec <= 0:
        return str(value)
    value_dec = Decimal(str(value))
    units = (value_dec / step_dec).to_integral_value(rounding=ROUND_DOWN)
    result = units * step_dec
    return format(result.normalize(), "f")


def pct(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100.0


def ema(values: list[float]) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (len(values) + 1.0)
    result = values[0]
    for value in values[1:]:
        result = value * alpha + result * (1.0 - alpha)
    return result


def infer_trend(closes: list[float]) -> str:
    if len(closes) < 30:
        return "neutral"
    fast = ema(closes[-12:])
    slow = ema(closes[-30:])
    last = closes[-1]
    if last > fast > slow:
        return "up"
    if last < fast < slow:
        return "down"
    return "neutral"


class PaperBot:
    def __init__(self, config_path: Path, state_path: Path) -> None:
        self.config_path = config_path
        self.state_path = state_path
        self.lock = threading.RLock()
        self.config = self.load_config()
        self.state = self.load_state()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.exchange_time_offsets: dict[str, dict[str, float]] = {}
        self.position_mode_cache: dict[str, Any] = {"mode": "", "synced_at_ms": 0}
        self.exchange_symbols_cache: dict[str, Any] = {"symbols": {}, "synced_at_ms": 0}
        self.market_exchange_symbols_cache: dict[str, Any] = {"symbols": {}, "synced_at_ms": 0}
        self.crypto_symbols: set[str] = set()
        self.crypto_classification_error = ""
        self.analyzer_cache: dict[str, dict[str, Any]] = dict(self.state.get("analyzer_cache", {}))
        stale_errors = [
            symbol
            for symbol, result in self.analyzer_cache.items()
            if result.get("source") != FAST_CONFIRM_SOURCE or "分析器超时" in str(result.get("reason", ""))
        ]
        for symbol in stale_errors:
            self.analyzer_cache.pop(symbol, None)
            self.state.get("analyzer_cache", {}).pop(symbol, None)
        self.analyzer_inflight: set[str] = set()
        self.analyzer_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(self.config.get("analyzer_max_workers", 2)))
        )
        self.daily_report_thread = threading.Thread(target=self.daily_excel_report_loop, name="daily-excel-report", daemon=True)
        self.daily_report_thread.start()
        self.hourly_status_thread = threading.Thread(target=self.hourly_status_report_loop, name="hourly-status-report", daemon=True)
        self.hourly_status_thread.start()

    def load_config(self) -> dict[str, Any]:
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        if self.config_path.exists():
            deep_update(config, load_json(self.config_path, {}))
        return config

    def load_state(self) -> dict[str, Any]:
        state = load_json(self.state_path, {})
        state["running"] = False
        state["next_scan_at"] = None
        state["scan_in_progress"] = False
        state.setdefault("started_at", None)
        state.setdefault("test_started_at", state.get("started_at"))
        state.setdefault("last_scan_at", None)
        state.setdefault("last_scan_finished_at", None)
        state.setdefault("last_scan_duration_sec", 0.0)
        state.setdefault("scan_stage", "idle")
        state.setdefault("scan_count", 0)
        state["mode"] = self.exchange_mode()
        state.setdefault("market_source", "unknown")
        state.setdefault("exchange_status", "unknown")
        state.setdefault("realized_pnl_usdt", 0.0)
        state.setdefault("equity_high_watermark_usdt", float(self.config.get("initial_equity_usdt", 100.0)))
        state.setdefault("max_drawdown_pct", 0.0)
        state.setdefault("take_profit_count", 0)
        state.setdefault("stop_loss_count", 0)
        state.setdefault("positions", [])
        state.setdefault("exchange_open_positions", [])
        state.setdefault("reconciled_close_ids", [])
        state.setdefault("pending_open_signals", {})
        state.setdefault("stop_loss_cooldown_overrides", {})
        state.setdefault("last_hourly_status_report_at", "")
        if "telegram_close_notified_ids" not in state:
            notified_ids: list[str] = []
            for item in state.get("trade_history", []):
                if not item.get("closed_at"):
                    continue
                notified_ids.extend(self.close_notify_ids(item))
            state["telegram_close_notified_ids"] = list(dict.fromkeys([item for item in notified_ids if item]))[:1000]
        if self.exchange_mode() in {"binance_futures_testnet", "binance_futures_live"}:
            state["positions"] = []
        state.setdefault("candidates", [])
        state.setdefault("trade_history", [])
        state.setdefault("open_order_history", [])
        open_records = [item for item in state.get("trade_history", []) if item.get("close_reason") == "Binance Testnet 开仓记录"]
        if open_records:
            merged = [*open_records, *state.get("open_order_history", [])]
            seen_open_ids = set()
            open_history = []
            for item in merged:
                key = str(item.get("entry_order_id") or f"{item.get('symbol')}-{item.get('time')}")
                if key in seen_open_ids:
                    continue
                seen_open_ids.add(key)
                open_history.append(item)
            state["open_order_history"] = open_history[:500]
        state["trade_history"] = [
            item
            for item in state.get("trade_history", [])
            if self.record_belongs_to_current_exchange(item)
            and not str(item.get("id", "")).startswith(f"{self.exchange_mode()}-income-")
        ]
        state.setdefault("events", [])
        state.setdefault("analyzer_cache", {})
        state.setdefault("last_error", "")
        return state

    def persist(self) -> None:
        save_json(self.state_path, self.state)

    def event(self, level: str, message: str) -> None:
        self.state.setdefault("events", []).insert(0, {"time": iso_now(), "level": level, "message": message})
        self.state["events"] = self.state["events"][:80]

    def is_live_trading(self) -> bool:
        return bool(self.config.get("live_trading_enabled", False))

    def exchange_mode(self) -> str:
        return "binance_futures_live" if self.is_live_trading() else "binance_futures_testnet"

    def exchange_label(self) -> str:
        return "Binance Futures 实盘" if self.is_live_trading() else "Binance Futures Testnet"

    def exchange_record_prefix(self) -> str:
        return "Binance Live" if self.is_live_trading() else "Binance Testnet"

    def exchange_config(self) -> dict[str, Any]:
        key = "futures_live" if self.is_live_trading() else "futures_testnet"
        futures = dict(self.config.get(key, {}))
        margin_type = str(self.config.get("margin_type", futures.get("margin_type", "ISOLATED"))).upper()
        futures["margin_type"] = margin_type if margin_type in {"ISOLATED", "CROSSED"} else "ISOLATED"
        return futures

    def exchange_base_url(self) -> str:
        futures = self.exchange_config()
        return futures.get("base_url", "https://fapi.binance.com" if self.is_live_trading() else "https://testnet.binancefuture.com")

    def exchange_time_offset_ms(self, force: bool = False) -> int:
        mode = self.exchange_mode()
        now_ms = int(time.time() * 1000)
        cached = self.exchange_time_offsets.get(mode, {})
        if not force and cached and now_ms - int(cached.get("synced_at_ms", 0)) < 5 * 60 * 1000:
            return int(cached.get("offset_ms", 0))
        try:
            started_ms = int(time.time() * 1000)
            payload = http_get_json(
                self.exchange_base_url(),
                "/fapi/v1/time",
                timeout=min(5.0, float(self.config.get("request_timeout_sec", 6))),
            )
            finished_ms = int(time.time() * 1000)
            server_time_ms = int(fnum(payload.get("serverTime")))
            local_midpoint_ms = (started_ms + finished_ms) // 2
            offset_ms = server_time_ms - local_midpoint_ms
            self.exchange_time_offsets[mode] = {"offset_ms": offset_ms, "synced_at_ms": finished_ms}
            return offset_ms
        except Exception:
            return int(cached.get("offset_ms", 0)) if cached else 0

    def record_belongs_to_current_exchange(self, record: dict[str, Any]) -> bool:
        mode = str(record.get("execution_mode", ""))
        if mode:
            return mode == self.exchange_mode()
        return not self.is_live_trading()

    def load_signal_review_state(self) -> dict[str, Any]:
        return load_json_lenient(SIGNAL_REVIEW_STATE_PATH, {"signals": {}, "open_orders": {}})

    def save_signal_review_state(self, review_state: dict[str, Any]) -> None:
        signals = dict(review_state.get("signals", {}))
        if len(signals) > 2000:
            trimmed = sorted(signals.items(), key=lambda item: str(item[1].get("scan_time", "")), reverse=True)[:2000]
            review_state["signals"] = dict(trimmed)
        open_orders = dict(review_state.get("open_orders", {}))
        if len(open_orders) > 1000:
            review_state["open_orders"] = dict(list(open_orders.items())[-1000:])
        save_json(SIGNAL_REVIEW_STATE_PATH, review_state)

    def signal_review_id(self, scan_id: int, symbol: str) -> str:
        return f"scan-{scan_id}-{symbol}"

    def signal_review_payload(self, signal: Signal, scan_id: int, scan_time: str) -> dict[str, Any]:
        return {
            "id": self.signal_review_id(scan_id, signal.symbol),
            "scan_id": scan_id,
            "scan_time": scan_time,
            "symbol": signal.symbol,
            "side": signal.side,
            "score": signal.score,
            "price": signal.price,
            "quote_volume": signal.quote_volume,
            "change_24h_pct": signal.change_24h_pct,
            "change_4h_pct": signal.change_4h_pct,
            "change_1h_pct": signal.change_1h_pct,
            "volume_ratio": signal.volume_ratio,
            "open_interest_24h_pct": signal.open_interest_24h_pct,
            "funding_rate_pct": signal.funding_rate_pct,
            "long_short_ratio": signal.long_short_ratio,
            "taker_long_short_ratio": signal.taker_long_short_ratio,
            "signal_mode": signal.signal_mode,
            "signal_heat_level": signal.signal_heat_level,
            "market_regime_score": signal.market_regime_score,
            "market_regime_label": signal.market_regime_label,
            "market_regime_reason": signal.market_regime_reason,
            "risk_level": signal.risk_level,
            "analyzer_direction": signal.analyzer_direction,
            "analyzer_confidence_pct": signal.analyzer_confidence_pct,
            "analyzer_reason": signal.analyzer_reason,
            "trigger_reason": signal.trigger_reason,
            "reject_reason": signal.reject_reason,
            "decision_summary": signal.decision_summary,
            "decision_steps": list(signal.decision_steps),
            "opened": False,
            "open_order_id": "",
            "opened_at": "",
            "entry_price": 0.0,
            "closed": False,
            "closed_at": "",
            "close_reason": "",
            "pnl_usdt": 0.0,
            "roi_pct": 0.0,
            "future_return_15m_pct": None,
            "future_return_60m_pct": None,
            "future_return_240m_pct": None,
        }

    def record_signal_review_scan(self, signals: list[Signal]) -> None:
        scan_id = int(self.state.get("scan_count", 0))
        scan_time = str(self.state.get("last_scan_at") or iso_now())
        review_state = self.load_signal_review_state()
        signals_state = review_state.setdefault("signals", {})
        for signal in signals:
            payload = self.signal_review_payload(signal, scan_id, scan_time)
            signals_state[payload["id"]] = payload
            append_jsonl(SIGNAL_REVIEW_PATH, {"type": "signal_scan", **payload})
        self.save_signal_review_state(review_state)

    def mark_signal_review_opened(self, signal: Signal, record: dict[str, Any]) -> None:
        review_id = self.signal_review_id(int(self.state.get("scan_count", 0)), signal.symbol)
        review_state = self.load_signal_review_state()
        item = review_state.setdefault("signals", {}).get(review_id)
        if not item:
            item = self.signal_review_payload(signal, int(self.state.get("scan_count", 0)), str(self.state.get("last_scan_at") or iso_now()))
            review_state["signals"][review_id] = item
        item.update(
            {
                "opened": True,
                "open_order_id": str(record.get("entry_order_id") or ""),
                "opened_at": record.get("time") or iso_now(),
                "entry_price": fnum(record.get("entry_price"), signal.price),
                "original_side": record.get("original_side", signal.side),
                "executed_side": record.get("side", signal.side),
                "reverse_order": bool(record.get("reverse_order", False)),
                "decision_summary": signal.decision_summary,
                "decision_steps": list(signal.decision_steps),
            }
        )
        review_state.setdefault("open_orders", {})[str(record.get("entry_order_id") or review_id)] = review_id
        append_jsonl(SIGNAL_REVIEW_PATH, {"type": "signal_opened", **item})
        self.save_signal_review_state(review_state)

    def mark_signal_review_closed(self, open_record: dict[str, Any], trade_row: dict[str, Any]) -> None:
        review_id = str(open_record.get("signal_review_id") or "")
        entry_order_id = str(open_record.get("entry_order_id") or "")
        review_state = self.load_signal_review_state()
        if not review_id and entry_order_id:
            review_id = str(review_state.get("open_orders", {}).get(entry_order_id, ""))
        if not review_id:
            return
        item = review_state.setdefault("signals", {}).get(review_id)
        if not item:
            return
        item.update(
            {
                "closed": True,
                "closed_at": trade_row.get("closed_at") or iso_now(),
                "close_reason": trade_row.get("close_reason", ""),
                "pnl_usdt": fnum(trade_row.get("pnl_usdt")),
                "roi_pct": fnum(trade_row.get("roi_pct")),
            }
        )
        append_jsonl(SIGNAL_REVIEW_PATH, {"type": "signal_closed", **item})
        self.save_signal_review_state(review_state)

    def update_signal_future_returns(self, price_map: dict[str, float]) -> None:
        review_state = self.load_signal_review_state()
        signals_state = review_state.setdefault("signals", {})
        now_ms = int(time.time() * 1000)
        changed = False
        horizons = [(15, "future_return_15m_pct"), (60, "future_return_60m_pct"), (240, "future_return_240m_pct")]
        for item in signals_state.values():
            symbol = str(item.get("symbol", ""))
            entry_price = fnum(item.get("price"))
            scan_ms = parse_time_ms(item.get("scan_time"))
            current_price = price_map.get(symbol)
            if not symbol or entry_price <= 0 or not current_price or scan_ms <= 0:
                continue
            for minutes, field in horizons:
                if item.get(field) is not None:
                    continue
                if now_ms - scan_ms < minutes * 60 * 1000:
                    continue
                value = pct(current_price, entry_price)
                item[field] = round(value, 4)
                append_jsonl(SIGNAL_REVIEW_PATH, {"type": "future_return", "id": item.get("id"), "symbol": symbol, "field": field, "value": item[field]})
                changed = True
        if changed:
            self.save_signal_review_state(review_state)

    def send_telegram_message(self, text: str) -> bool:
        if not self.config.get("telegram_enabled", True):
            return False
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            self.event("warn", "Telegram 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，已跳过通知")
            return False
        try:
            http_request_json(
                "POST",
                f"https://api.telegram.org/bot{token}",
                "/sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": "true",
                },
                timeout=8,
            )
            return True
        except Exception as exc:
            self.event("warn", f"Telegram 通知发送失败：{exc}")
            return False

    def notify_open_position(self, signal: Signal, record: dict[str, Any]) -> None:
        side = self.side_cn(str(record.get("side") or signal.side))
        analyzer_text = "-"
        if signal.analyzer_direction:
            analyzer_text = f"{signal.analyzer_direction} {signal.analyzer_confidence_pct:.1f}%"
        message = "\n".join(
            [
                f"{self.exchange_label()} 开仓成功",
                f"币种：{signal.symbol} {side}",
                f"开仓价：{price_text(signal.price)}",
                f"保证金：{fnum(record.get('margin_usdt')):.2f} U",
                f"名义价值：{fnum(record.get('notional_usdt')):.2f} U",
                f"止盈：{record.get('take_profit', '-')}",
                f"止损：{record.get('stop_price', '-')}",
                f"反向订单：{'是' if record.get('reverse_order') else '否'}",
                f"系统分：{signal.score:.2f}",
                f"分析器：{analyzer_text}",
                f"触发：{signal.trigger_reason}",
                f"时间：{iso_now()}",
            ]
        )
        self.send_telegram_message(message)

    def notify_close_position(self, trade: dict[str, Any]) -> bool:
        side = self.side_cn(str(trade.get("side", "")))
        pnl = fnum(trade.get("pnl_usdt"))
        exit_price = self.display_exit_price(trade)
        message = "\n".join(
            [
                f"{self.exchange_label()} 平仓完成",
                f"币种：{trade.get('symbol', '-')} {side}",
                f"平仓原因：{trade.get('close_reason', '-')}",
                f"开仓价：{price_text(fnum(trade.get('entry_price')))}",
                f"平仓价：{price_text(exit_price)}",
                f"数量：{trade.get('quantity', '-')}",
                f"保证金：{fnum(trade.get('margin_usdt')):.2f} U",
                f"已实现盈亏：{pnl:+.2f} U",
                f"ROI：{fnum(trade.get('roi_pct')):+.2f}%",
                f"开仓理由：{trade.get('trigger_reason', '-')}",
                f"时间：{trade.get('closed_at') or iso_now()}",
            ]
        )
        return self.send_telegram_message(message)

    def notify_new_closed_trades(self, trades: list[dict[str, Any]]) -> None:
        if not trades:
            return
        notified = [str(item) for item in self.state.get("telegram_close_notified_ids", []) if str(item)]
        notified_set = set(notified)
        added = []
        for trade in sorted(trades, key=lambda item: str(item.get("closed_at", ""))):
            close_ids = self.close_notify_ids(trade)
            if not close_ids or any(item in notified_set for item in close_ids):
                continue
            if not self.notify_close_position(trade):
                continue
            for item in close_ids:
                notified_set.add(item)
                added.append(item)
        if added:
            self.state["telegram_close_notified_ids"] = list(dict.fromkeys([*added, *notified]))[:1000]

    def build_hourly_status_message(self) -> str:
        with self.lock:
            positions = json.loads(json.dumps(self.state.get("positions", []), ensure_ascii=False))
            total_equity = self.equity()
            quote_asset = str(self.config.get("quote_asset", "USDT")).upper()
            max_open = int(self.config.get("max_open_positions", 0))
        lines = [
            "📊 状态报告",
            f"持仓 ({len(positions)}/{max_open}):",
        ]
        if positions:
            for pos in sorted(positions, key=lambda item: str(item.get("symbol", ""))):
                symbol = str(pos.get("symbol", "-"))
                side = str(pos.get("side", "-"))
                leverage = int(fnum(pos.get("leverage"), self.config.get("leverage", 1)))
                entry = price_text(fnum(pos.get("entry_price")))
                roi = fnum(pos.get("roi_pct"))
                lines.append(f"{symbol} {side} {leverage}x | 入场 {entry} | ROI {roi:+.2f}%")
        else:
            lines.append("暂无持仓")
        lines.append(f"合约总资产: {total_equity:.2f} {quote_asset}")
        return "\n".join(lines)

    def send_hourly_status_report(self) -> bool:
        if not self.config.get("telegram_hourly_status_enabled", True):
            return False
        try:
            self.sync_exchange_state()
        except Exception as exc:
            self.event("warn", f"发送小时状态报告前同步失败，使用本地状态：{exc}")
        message = self.build_hourly_status_message()
        if not self.send_telegram_message(message):
            return False
        with self.lock:
            self.state["last_hourly_status_report_at"] = iso_now()
            self.persist()
        return True

    def hourly_status_report_loop(self) -> None:
        while True:
            try:
                interval_minutes = max(1, int(float(self.config.get("telegram_hourly_status_interval_minutes", 60))))
                interval_sec = interval_minutes * 60
                last_ms = parse_time_ms(self.state.get("last_hourly_status_report_at"))
                now_ms = int(time.time() * 1000)
                if last_ms > 0 and now_ms - last_ms < interval_sec * 1000:
                    sleep_sec = max(5.0, interval_sec - (now_ms - last_ms) / 1000)
                    time.sleep(min(sleep_sec, 300.0))
                    continue
                self.send_hourly_status_report()
                time.sleep(interval_sec)
            except Exception as exc:
                self.event("error", f"小时状态报告发送失败：{exc}")
                time.sleep(60)

    def close_notify_ids(self, trade: dict[str, Any]) -> list[str]:
        trade_id = str(trade.get("id", "")).strip()
        ids = [trade_id] if trade_id else []
        ids.extend(self.close_notify_semantic_ids(trade))
        return list(dict.fromkeys([item for item in ids if item]))

    def close_notify_semantic_ids(self, trade: dict[str, Any]) -> list[str]:
        symbol = str(trade.get("symbol", "")).strip()
        side = str(trade.get("side", "")).strip()
        if not symbol:
            return []
        mode = str(trade.get("execution_mode") or self.exchange_mode())
        quantity = f"{fnum(trade.get('quantity')):.8f}"
        pnl = f"{fnum(trade.get('pnl_usdt')):.2f}"
        closed_ms = parse_time_ms(trade.get("closed_at"))
        close_order_id = str(trade.get("close_order_id", "")).strip()
        ids: list[str] = []
        if close_order_id:
            ids.append(f"closed-order:{mode}:{symbol}:{side}:{close_order_id}")
        if closed_ms > 0:
            ids.append(f"closed-pnl:{mode}:{symbol}:{side}:{closed_ms // 1000}:{quantity}:{pnl}")
        ids.append(self.close_notify_semantic_id(trade))
        return ids

    def close_notify_semantic_id(self, trade: dict[str, Any]) -> str:
        symbol = str(trade.get("symbol", ""))
        side = str(trade.get("side", ""))
        entry_order_id = str(trade.get("entry_order_id", ""))
        opened_at = str(trade.get("opened_at", ""))
        quantity = f"{fnum(trade.get('quantity')):.8f}"
        entry = f"{fnum(trade.get('entry_price')):.12f}"
        if not symbol:
            return ""
        mode = str(trade.get("execution_mode") or self.exchange_mode())
        return f"closed:{mode}:{symbol}:{side}:{entry_order_id or opened_at}:{quantity}:{entry}"

    def trades_for_local_date(self, report_date: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for trade in self.state.get("trade_history", []):
            closed_dt = local_datetime(trade.get("closed_at"))
            if not closed_dt or closed_dt.date().isoformat() != report_date:
                continue
            rows.append(trade)
        rows.sort(key=lambda item: str(item.get("closed_at", "")))
        return rows

    def export_daily_realized_pnl_excel(self, report_date: str | None = None) -> Path:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        if report_date is None:
            report_date = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
        report_path = Path(str(self.config.get("daily_excel_report_path") or DAILY_PNL_REPORT_PATH))
        if not report_path.is_absolute():
            report_path = ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            trades = json.loads(json.dumps(self.trades_for_local_date(report_date), ensure_ascii=False))
        total_pnl = sum(fnum(item.get("pnl_usdt")) for item in trades)
        wins = [item for item in trades if fnum(item.get("pnl_usdt")) > 0]
        losses = [item for item in trades if fnum(item.get("pnl_usdt")) < 0]
        win_rate = len(wins) / len(trades) * 100.0 if trades else 0.0
        take_profit_count = sum(1 for item in trades if fnum(item.get("pnl_usdt")) > 0 or "止盈" in str(item.get("close_reason", "")))
        stop_loss_count = sum(1 for item in trades if fnum(item.get("pnl_usdt")) < 0 or "止损" in str(item.get("close_reason", "")))

        if report_path.exists():
            wb = load_workbook(report_path)
        else:
            wb = Workbook()
            wb.active.title = "每日汇总"
            wb.create_sheet("交易明细")
        for sheet_name in ["每日汇总", "交易明细"]:
            if sheet_name not in wb.sheetnames:
                wb.create_sheet(sheet_name)
        summary = wb["每日汇总"]
        detail = wb["交易明细"]
        summary_headers = ["日期", "平仓笔数", "止盈次数", "止损次数", "胜率%", "已实现盈亏USDT", "盈利合计USDT", "亏损合计USDT", "生成时间"]
        detail_headers = ["日期", "平仓时间", "币种", "方向", "开仓价", "平仓价", "数量", "已实现盈亏USDT", "平仓原因", "订单/记录ID"]
        if summary.max_row == 1 and not summary.cell(1, 1).value:
            for index, header in enumerate(summary_headers, 1):
                summary.cell(1, index, header)
        if detail.max_row == 1 and not detail.cell(1, 1).value:
            for index, header in enumerate(detail_headers, 1):
                detail.cell(1, index, header)
        for row in range(summary.max_row, 1, -1):
            if str(summary.cell(row, 1).value) == report_date:
                summary.delete_rows(row, 1)
        for row in range(detail.max_row, 1, -1):
            if str(detail.cell(row, 1).value) == report_date:
                detail.delete_rows(row, 1)
        summary.append([
            report_date,
            len(trades),
            take_profit_count,
            stop_loss_count,
            round(win_rate, 2),
            round(total_pnl, 6),
            round(sum(fnum(item.get("pnl_usdt")) for item in wins), 6),
            round(sum(fnum(item.get("pnl_usdt")) for item in losses), 6),
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ])
        for item in trades:
            closed_dt = local_datetime(item.get("closed_at"))
            detail.append([
                report_date,
                closed_dt.isoformat(timespec="seconds") if closed_dt else str(item.get("closed_at", "")),
                item.get("symbol", ""),
                item.get("side", ""),
                fnum(item.get("entry_price")),
                self.display_exit_price(item),
                fnum(item.get("quantity")),
                round(fnum(item.get("pnl_usdt")), 6),
                item.get("close_reason", ""),
                item.get("id") or item.get("entry_order_id") or "",
            ])
        for ws in [summary, detail]:
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E78")
            ws.freeze_panes = "A2"
            for col in range(1, ws.max_column + 1):
                max_len = max(len(str(ws.cell(row, col).value or "")) for row in range(1, ws.max_row + 1))
                ws.column_dimensions[get_column_letter(col)].width = min(max(max_len + 2, 10), 36)
        wb.save(report_path)
        with self.lock:
            self.state["last_daily_excel_report_date"] = report_date
            self.state["last_daily_excel_report_path"] = str(report_path)
            self.persist()
        self.event("info", f"每日已实现盈亏 Excel 已生成：{report_path}")
        return report_path

    def refresh_previous_day_equity_baseline(self, baseline_date: str | None = None) -> float:
        if baseline_date is None:
            baseline_date = datetime.now().astimezone().date().isoformat()
        try:
            self.sync_exchange_state()
        except Exception as exc:
            self.event("warn", f"更新昨日合约总账户余额前同步失败，使用本地账户资产：{exc}")
        equity = self.equity()
        with self.lock:
            self.config["previous_day_equity_usdt"] = round(equity, 6)
            self.state["last_equity_baseline_date"] = baseline_date
            self.state["last_equity_baseline_at"] = iso_now()
            self.state["equity_take_profit_triggered"] = False
            self.state["equity_take_profit_triggered_at"] = ""
            save_json(self.config_path, self.config)
            self.event("info", f"已更新昨日合约总账户余额为 {equity:.2f} U，作为 {baseline_date} 的总资产止盈基准")
            self.persist()
        return equity

    def daily_excel_report_loop(self) -> None:
        while True:
            try:
                now = datetime.now().astimezone()
                next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
                time.sleep(max(1.0, (next_midnight - now).total_seconds()))
                report_date = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
                baseline_date = datetime.now().astimezone().date().isoformat()
                self.refresh_previous_day_equity_baseline(baseline_date)
                if self.config.get("daily_excel_report_enabled", True):
                    self.export_daily_realized_pnl_excel(report_date)
            except Exception as exc:
                self.event("error", f"每日维护任务失败：{exc}")
                time.sleep(60)

    def build_square_open_post(self, signal: Signal, record: dict[str, Any]) -> str:
        execution_side = str(record.get("side") or signal.side)
        side = self.side_cn(execution_side)
        side_view = "\u770b\u591a" if execution_side == "LONG" else "\u770b\u7a7a"
        analyzer_text = "-"
        if signal.analyzer_direction:
            analyzer_text = f"{signal.analyzer_direction} {signal.analyzer_confidence_pct:.1f}%"
        mode_text = self.signal_mode_label(signal.signal_mode)
        tier_label = {
            "excellent": "\u9ad8\u8d28\u91cf\u4fe1\u53f7\uff0c\u6309\u5f3a\u5316\u4ed3\u5904\u7406",
            "strong": "\u5f3a\u4fe1\u53f7\uff0c\u7565\u5fae\u52a0\u4ed3\u5904\u7406",
            "standard": "\u8d28\u91cf\u8fbe\u6807\uff0c\u6309\u6807\u51c6\u4ed3\u5904\u7406",
            "low": "\u8fb9\u7f18\u4fe1\u53f7\uff0c\u8f7b\u4ed3\u8bd5\u63a2",
            "fixed": "\u56fa\u5b9a\u4ed3\u4f4d\u6267\u884c",
        }.get(str(record.get("sizing_tier") or ""), "")
        entry = price_text(signal.price)
        take_profit = str(record.get("take_profit", "-"))
        stop_price = str(record.get("stop_price", "-"))
        reason = str(signal.trigger_reason or "-").strip()
        confirm = str(signal.analyzer_reason or "").strip()
        disclaimer = str(self.config.get("square_publish_disclaimer", "")).strip()
        square_symbol = signal.symbol
        quote_asset = str(self.config.get("quote_asset", "USDT")).upper()
        if square_symbol.upper().endswith(quote_asset):
            square_symbol = square_symbol[: -len(quote_asset)]
        seed = f"{record.get('entry_order_id') or ''}-{signal.symbol}-{record.get('time') or iso_now()}"
        template_index = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % 5
        templates = [
            [
                f"${square_symbol} \u8fd9\u6ce2\u6211\u504f{side_view}\uff0c\u5148\u8bb0\u5f55\u4e00\u7b14{side}\u5355\u3002",
                f"\u5165\u573a\uff1a{entry}\uff5c\u6b62\u76c8\uff1a{take_profit}\uff5c\u6b62\u635f\uff1a{stop_price}",
                f"\u4fe1\u53f7\uff1a{mode_text}\uff5c\u7cfb\u7edf\u5206 {signal.score:.2f}\uff5c\u786e\u8ba4 {analyzer_text}",
                f"\u7406\u7531\uff1a{reason}",
            ],
            [
                f"${square_symbol} \u8fd9\u91cc\u5f00\u59cb\u6709\u70b9\u610f\u601d\u4e86\uff0c\u6211\u73b0\u5728\u504f{side_view}\u3002",
                f"\u8fd9\u5355\u770b\u7684\u662f\u77ed\u7ebf\u5ef6\u7eed\uff0c\u5165\u573a {entry}\uff0c\u9632\u5b88 {stop_price}\uff0c\u76ee\u6807\u5148\u770b {take_profit}\u3002",
                f"{mode_text}\uff0c\u7cfb\u7edf\u5206 {signal.score:.2f}\uff0c\u786e\u8ba4 {analyzer_text}\u3002",
                f"\u89e6\u53d1\u70b9\uff1a{reason}",
            ],
            [
                f"${square_symbol} \u5f53\u524d{side_view}\u7ed3\u6784\u6bd4\u8f83\u987a\uff0c\u8bb0\u5f55\u4e00\u7b14{side}\u5355\u3002",
                f"\u6a21\u5f0f\uff1a{mode_text}\uff5c\u7cfb\u7edf\u5206\uff1a{signal.score:.2f}\uff5c\u786e\u8ba4\uff1a{analyzer_text}",
                f"\u70b9\u4f4d\uff1a\u5165\u573a {entry}\uff0c\u6b62\u76c8 {take_profit}\uff0c\u6b62\u635f {stop_price}",
                f"\u4e3b\u8981\u903b\u8f91\uff1a{reason}",
            ],
            [
                f"${square_symbol} \u6ce2\u52a8\u8d77\u6765\u4e86\uff0c\u8fd9\u79cd\u7968\u6211\u53ea\u770b\u8282\u594f\uff0c\u4e0d\u604b\u6218\u3002",
                f"\u65b9\u5411\uff1a{side_view}\uff5c\u5165\u573a\uff1a{entry}\uff5c\u6b62\u76c8\uff1a{take_profit}\uff5c\u6b62\u635f\uff1a{stop_price}",
                f"\u4fe1\u53f7\u5171\u632f\uff1a{mode_text}\uff0c\u7cfb\u7edf\u5206 {signal.score:.2f}\uff0c\u786e\u8ba4 {analyzer_text}",
                f"\u5982\u679c\u653e\u91cf\u4e0d\u80fd\u5ef6\u7eed\uff0c\u5c31\u6309\u6b62\u635f\u7eaa\u5f8b\u5904\u7406\u3002",
            ],
            [
                f"${square_symbol} \u7ed9\u5230\u4e00\u7b14{side}\u673a\u4f1a\uff0c\u6211\u5148\u8ddf\u968f\u4fe1\u53f7\u8bb0\u5f55\u3002",
                f"\u5165\u573a {entry}\uff0c\u6b62\u76c8 {take_profit}\uff0c\u6b62\u635f {stop_price}\u3002",
                f"\u4f9d\u636e\uff1a{reason}\uff1b\u4e8c\u6b21\u786e\u8ba4 {analyzer_text}\u3002",
            ],
        ]
        lines = templates[template_index]
        if tier_label:
            lines.append(f"\u4ed3\u4f4d\u8ba1\u5212\uff1a{tier_label}")
        if confirm:
            lines.append(f"\u786e\u8ba4\uff1a{confirm}")
        if disclaimer:
            lines.append(disclaimer)
        return "\n".join(lines)

    def publish_square_text(self, text: str) -> dict[str, Any]:
        if not self.config.get("square_publish_enabled", True):
            return {"ok": False, "skipped": True, "error": "Binance Square 自动发布未启用"}
        api_key = os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip()
        if not api_key:
            raise RuntimeError("未配置 BINANCE_SQUARE_OPENAPI_KEY")
        payload = {"contentType": 1, "bodyTextOnly": text}
        raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
            data=raw_body,
            method="POST",
            headers={
                "X-Square-OpenAPI-Key": api_key,
                "Content-Type": "application/json",
                "clienttype": "binanceSkill",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                response_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")
            if exc.code == 504:
                return {"ok": True, "id": "unavailable", "link": "unavailable", "output": "Square returned 504 after submission"}
            raise RuntimeError(f"Binance Square HTTP {exc.code}: {response_text}") from exc
        except Exception as exc:
            raise RuntimeError(f"Binance Square 请求失败：{exc}") from exc
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Binance Square 返回非 JSON：{response_text[:300]}") from exc
        if data.get("code") != "000000":
            raise RuntimeError(f"Binance Square API error [{data.get('code')}]: {data.get('message')}")
        result = data.get("data") or {}
        return {"ok": True, "id": result.get("id") or "", "link": result.get("shareLink") or "", "output": response_text}

    def record_square_publish_result(self, entry_order_id: Any, payload: dict[str, Any]) -> None:
        entry_id = str(entry_order_id or "")
        if not entry_id:
            return
        with self.lock:
            for item in self.state.get("open_order_history", []):
                if str(item.get("entry_order_id") or "") == entry_id:
                    item.update(payload)
                    break
            self.persist()

    def publish_square_open_position_async(self, signal: Signal, record: dict[str, Any]) -> None:
        if not self.config.get("square_publish_enabled", True) or not self.config.get("square_publish_on_open", True):
            return
        entry_order_id = record.get("entry_order_id")
        text = self.build_square_open_post(signal, record)
        record["square_post_status"] = "pending"
        record["square_post_text"] = text
        self.record_square_publish_result(entry_order_id, {"square_post_status": "pending", "square_post_text": text})

        def worker() -> None:
            try:
                result = self.publish_square_text(text)
                payload = {
                    "square_post_status": "posted",
                    "square_post_id": result.get("id") or "",
                    "square_post_link": result.get("link") or "",
                    "square_post_error": "",
                }
                record.update(payload)
                self.record_square_publish_result(entry_order_id, payload)
                link_text = f"：{payload['square_post_link']}" if payload.get("square_post_link") and payload.get("square_post_link") != "unavailable" else ""
                with self.lock:
                    self.event("info", f"{signal.symbol} 开仓信息已发布到 Binance Square{link_text}")
                    self.persist()
            except Exception as exc:
                payload = {"square_post_status": "failed", "square_post_error": str(exc)}
                record.update(payload)
                self.record_square_publish_result(entry_order_id, payload)
                with self.lock:
                    self.event("warn", f"{signal.symbol} Binance Square 自动发布失败：{exc}")
                    self.persist()

        threading.Thread(target=worker, name=f"square-post-{signal.symbol}", daemon=True).start()

    def start(self) -> None:
        with self.lock:
            if self.state.get("running"):
                return
            self.state["running"] = True
            self.state["mode"] = self.exchange_mode()
            self.state["started_at"] = self.state.get("started_at") or iso_now()
            self.stop_event.clear()
            self.event("info", f"机器人已启动，当前执行层为 {self.exchange_label()}")
            self.persist()
            self.thread = threading.Thread(target=self.run_loop, name="paper-dashboard-bot", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        with self.lock:
            self.state["running"] = False
            self.state["next_scan_at"] = None
            self.stop_event.set()
            self.event("info", "机器人已停止")
            self.persist()

    def close_all_positions(self) -> dict[str, Any]:
        closed = 0
        try:
            self.sync_exchange_state()
            with self.lock:
                positions = list(self.state.get("positions", []))
            for pos in positions:
                self.exchange_close_position(pos)
                closed += 1
            with self.lock:
                self.event("trade", f"已提交 {self.exchange_label()} 全部平仓 {closed} 笔")
                self.persist()
        except Exception as exc:
            with self.lock:
                self.state["last_error"] = str(exc)
                self.event("error", f"全部平仓失败：{exc}")
                self.persist()
        self.sync_exchange_state()
        return self.snapshot()

    def equity_profit_from_previous_day(self) -> float:
        base = fnum(self.config.get("previous_day_equity_usdt"))
        if base <= 0:
            return 0.0
        return self.equity() - base

    def equity_take_profit_reason(self) -> str:
        if not self.config.get("equity_take_profit_enabled", False):
            return ""
        base = fnum(self.config.get("previous_day_equity_usdt"))
        threshold = fnum(self.config.get("equity_take_profit_usdt"), 100.0)
        if base <= 0 or threshold <= 0:
            return ""
        equity = self.equity()
        profit = equity - base
        if profit >= threshold:
            return f"合约总资产 {equity:.2f} U - 昨日余额 {base:.2f} U = {profit:+.2f} U，达到总资产止盈阈值 {threshold:.2f} U"
        return ""

    def equity_take_profit_block_reason(self) -> str:
        if not self.config.get("equity_take_profit_enabled", False):
            return ""
        if self.state.get("equity_take_profit_triggered"):
            return "账户总资产止盈已触发，本轮不再开新仓"
        return self.equity_take_profit_reason()

    def enforce_equity_take_profit(self) -> bool:
        reason = self.equity_take_profit_reason()
        with self.lock:
            already_triggered = bool(self.state.get("equity_take_profit_triggered"))
            positions_snapshot = list(self.state.get("positions", []))
        if not reason and not already_triggered:
            return False
        with self.lock:
            if not self.state.get("equity_take_profit_triggered"):
                self.state["equity_take_profit_triggered"] = True
                self.state["equity_take_profit_triggered_at"] = iso_now()
                self.event("risk", f"{reason}，停止新增开仓并提交全部平仓")
                self.persist()
            elif positions_snapshot:
                self.event("risk", f"账户总资产止盈已触发，检测到仍有持仓 {len(positions_snapshot)} 笔，继续尝试全部平仓")
                self.persist()
            else:
                return True
        closed = 0
        try:
            positions = positions_snapshot
            for pos in positions:
                self.exchange_close_position(pos)
                closed += 1
            with self.lock:
                self.event("trade", f"账户总资产止盈已触发，已提交 {self.exchange_label()} 全部平仓 {closed} 笔")
                self.persist()
            self.sync_exchange_state()
        except Exception as exc:
            with self.lock:
                self.state["last_error"] = str(exc)
                self.event("error", f"账户总资产止盈平仓失败：{exc}")
                self.persist()
        return True

    def sync_exchange_snapshot(self) -> dict[str, Any]:
        try:
            self.sync_exchange_state()
        except Exception as exc:
            with self.lock:
                self.state["exchange_status"] = "error"
                self.state["last_error"] = str(exc)
                self.event("error", f"{self.exchange_label()} 同步失败：{exc}")
                self.persist()
        return self.snapshot()

    def reset_test_state(self) -> dict[str, Any]:
        try:
            self.sync_exchange_state()
        except Exception as exc:
            with self.lock:
                self.state["exchange_status"] = "error"
                self.state["last_error"] = str(exc)
                self.event("error", f"重置前同步 {self.exchange_label()} 失败：{exc}")
                self.persist()
            return {"ok": False, "error": f"重置前同步失败：{exc}", **self.snapshot()}
        with self.lock:
            if self.state.get("positions"):
                return {"ok": False, "error": f"{self.exchange_label()} 仍有持仓，请先全部平仓后再重置。", **self.snapshot()}
            self.stop_event.set()
            self.state["running"] = False
            self.state["next_scan_at"] = None
            self.state["scan_in_progress"] = False
            self.state["started_at"] = None
            self.state["test_started_at"] = iso_now()
            self.state["last_scan_at"] = None
            self.state["last_scan_finished_at"] = None
            self.state["last_scan_duration_sec"] = 0.0
            self.state["scan_count"] = 0
            self.state["realized_pnl_usdt"] = 0.0
            self.state["equity_high_watermark_usdt"] = float(self.config.get("initial_equity_usdt", 100.0))
            self.state["max_drawdown_pct"] = 0.0
            self.state["take_profit_count"] = 0
            self.state["stop_loss_count"] = 0
            self.state["positions"] = []
            self.state["exchange_open_positions"] = []
            self.state["candidates"] = []
            self.state["trade_history"] = []
            self.state["open_order_history"] = []
            self.state["events"] = []
            self.state["analyzer_cache"] = {}
            self.state["reconciled_close_ids"] = []
            self.state["telegram_close_notified_ids"] = []
            self.state["last_error"] = ""
            self.analyzer_cache.clear()
            self.analyzer_inflight.clear()
            self.event("info", "测试数据已清空，已重新开始。")
            self.persist()
        return {"ok": True, **self.snapshot()}

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "margin_per_trade_usdt": float,
            "leverage": int,
            "take_profit_roi_pct": float,
            "stop_loss_roi_pct": float,
            "take_profit_cooldown_enabled": str,
            "stop_loss_cooldown_minutes": int,
            "scan_interval_sec": int,
            "max_scan_runtime_sec": int,
            "max_candidates_per_scan": int,
            "pre_start_long_enabled": str,
            "pre_start_max_candidates": int,
            "pre_start_24h_min_pct": float,
            "pre_start_24h_max_pct": float,
            "pre_start_15m_min_pct": float,
            "pre_start_15m_max_pct": float,
            "pre_start_1h_min_pct": float,
            "pre_start_1h_max_pct": float,
            "pre_start_volume_ratio_min": float,
            "pre_start_volume_ratio_max": float,
            "pre_start_taker_ratio_min": float,
            "pre_start_oi_1h_min_pct": float,
            "pre_start_funding_abs_max_pct": float,
            "pre_start_long_short_max": float,
            "pre_start_market_confirm_min_score": float,
            "pre_start_margin_per_trade_usdt": float,
            "pre_start_leverage": int,
            "old_rebound_short_enabled": str,
            "old_rebound_short_max_candidates": int,
            "old_rebound_short_24h_min_pct": float,
            "old_rebound_short_24h_max_pct": float,
            "old_rebound_short_min_quote_volume_usdt": float,
            "old_rebound_short_max_quote_volume_usdt": float,
            "old_rebound_short_min_history_days": int,
            "old_rebound_short_15m_max_pct": float,
            "old_rebound_short_1h_max_pct": float,
            "old_rebound_short_volume_ratio_min": float,
            "old_rebound_short_volume_ratio_max": float,
            "old_rebound_short_taker_ratio_max": float,
            "old_rebound_short_long_short_min": float,
            "old_rebound_short_oi_1h_min_pct": float,
            "old_rebound_short_funding_abs_max_pct": float,
            "old_rebound_short_confirm_min_score": float,
            "old_rebound_short_margin_per_trade_usdt": float,
            "old_rebound_short_leverage": int,
            "high_pullback_short_enabled": str,
            "high_pullback_short_max_candidates": int,
            "high_pullback_short_24h_min_pct": float,
            "high_pullback_short_24h_max_pct": float,
            "high_pullback_short_min_drawdown_pct": float,
            "high_pullback_short_max_drawdown_pct": float,
            "high_pullback_short_min_low_distance_pct": float,
            "high_pullback_short_15m_max_pct": float,
            "high_pullback_short_1h_max_pct": float,
            "high_pullback_short_volume_ratio_min": float,
            "high_pullback_short_volume_ratio_max": float,
            "high_pullback_short_taker_ratio_max": float,
            "high_pullback_short_oi_1h_min_pct": float,
            "high_pullback_short_funding_abs_max_pct": float,
            "high_pullback_short_confirm_min_score": float,
            "high_pullback_short_margin_per_trade_usdt": float,
            "high_pullback_short_leverage": int,
            "kline_15m_limit": int,
            "kline_1h_limit": int,
            "max_open_positions": int,
            "max_exposure_pct": float,
            "max_daily_profit_enabled": str,
            "max_daily_profit_usdt": float,
            "max_daily_loss_enabled": str,
            "max_daily_loss_usdt": float,
            "equity_take_profit_enabled": str,
            "previous_day_equity_usdt": float,
            "equity_take_profit_usdt": float,
            "signal_score_threshold": float,
            "strong_long_24h_min_change_pct": float,
            "strong_short_enabled": str,
            "margin_type": str,
            "confirmation_mode": str,
            "market_confirm_min_score": float,
            "short_market_confirm_min_score": float,
            "short_max_24h_high_drawdown_pct": float,
            "short_structure_confirmation_enabled": str,
            "short_structure_min_score": float,
            "long_structure_confirmation_enabled": str,
            "long_structure_min_score": float,
            "market_regime_filter_enabled": str,
            "market_regime_min_score": float,
            "analyzer_min_confidence_pct": float,
            "analyzer_realtime_before_open": str,
            "max_new_positions_per_scan": int,
            "trade_direction_mode": str,
            "reverse_orders_enabled": str,
            "blocked_symbols": str,
            "live_trading_enabled": str,
            "telegram_hourly_status_enabled": str,
            "telegram_hourly_status_interval_minutes": int,
            "square_publish_enabled": str,
            "square_publish_on_open": str,
        }
        with self.lock:
            previous_mode = self.exchange_mode()
            for key, caster in allowed.items():
                if key not in patch:
                    continue
                if key in {"square_publish_enabled", "square_publish_on_open", "max_daily_profit_enabled", "max_daily_loss_enabled", "equity_take_profit_enabled", "live_trading_enabled", "telegram_hourly_status_enabled", "analyzer_realtime_before_open", "market_regime_filter_enabled", "long_structure_confirmation_enabled", "short_structure_confirmation_enabled", "pre_start_long_enabled", "old_rebound_short_enabled", "high_pullback_short_enabled", "strong_short_enabled", "take_profit_cooldown_enabled", "reverse_orders_enabled"}:
                    raw_value = str(patch[key]).strip().lower()
                    value = raw_value in {"1", "true", "yes", "on"}
                else:
                    value = caster(patch[key])
                if key == "trade_direction_mode":
                    value = str(value)
                    if value not in {"both", "long_only", "short_only"}:
                        value = "both"
                if key == "confirmation_mode":
                    value = str(value)
                    if value not in {"market_score", "analyzer"}:
                        value = "market_score"
                if key == "margin_type":
                    value = str(value).upper()
                    if value not in {"ISOLATED", "CROSSED"}:
                        value = "ISOLATED"
                if key == "blocked_symbols":
                    value = normalize_symbol_list(value, str(self.config.get("quote_asset", "USDT")))
                if key in {"leverage", "pre_start_leverage", "old_rebound_short_leverage", "high_pullback_short_leverage", "scan_interval_sec", "max_scan_runtime_sec", "max_candidates_per_scan", "pre_start_max_candidates", "old_rebound_short_max_candidates", "high_pullback_short_max_candidates", "old_rebound_short_min_history_days", "kline_15m_limit", "kline_1h_limit", "max_open_positions", "max_new_positions_per_scan", "telegram_hourly_status_interval_minutes"}:
                    value = max(1, int(value))
                if key == "previous_day_equity_usdt":
                    value = max(0.0, float(value))
                if key in {
                    "margin_per_trade_usdt",
                    "pre_start_margin_per_trade_usdt",
                    "old_rebound_short_margin_per_trade_usdt",
                    "high_pullback_short_margin_per_trade_usdt",
                    "take_profit_roi_pct",
                    "stop_loss_roi_pct",
                    "signal_score_threshold",
                    "strong_long_24h_min_change_pct",
                    "market_confirm_min_score",
                    "short_market_confirm_min_score",
                    "pre_start_market_confirm_min_score",
                    "short_max_24h_high_drawdown_pct",
                    "short_structure_min_score",
                    "long_structure_min_score",
                    "market_regime_min_score",
                    "analyzer_min_confidence_pct",
                    "pre_start_24h_min_pct",
                    "pre_start_24h_max_pct",
                    "pre_start_15m_min_pct",
                    "pre_start_15m_max_pct",
                    "pre_start_1h_min_pct",
                    "pre_start_1h_max_pct",
                    "pre_start_volume_ratio_min",
                    "pre_start_volume_ratio_max",
                    "pre_start_taker_ratio_min",
                    "pre_start_funding_abs_max_pct",
                    "pre_start_long_short_max",
                    "old_rebound_short_24h_min_pct",
                    "old_rebound_short_24h_max_pct",
                    "old_rebound_short_min_quote_volume_usdt",
                    "old_rebound_short_max_quote_volume_usdt",
                    "old_rebound_short_15m_max_pct",
                    "old_rebound_short_1h_max_pct",
                    "old_rebound_short_volume_ratio_min",
                    "old_rebound_short_volume_ratio_max",
                    "old_rebound_short_taker_ratio_max",
                    "old_rebound_short_long_short_min",
                    "old_rebound_short_funding_abs_max_pct",
                    "old_rebound_short_confirm_min_score",
                    "high_pullback_short_24h_min_pct",
                    "high_pullback_short_24h_max_pct",
                    "high_pullback_short_min_drawdown_pct",
                    "high_pullback_short_max_drawdown_pct",
                    "high_pullback_short_min_low_distance_pct",
                    "high_pullback_short_15m_max_pct",
                    "high_pullback_short_1h_max_pct",
                    "high_pullback_short_volume_ratio_min",
                    "high_pullback_short_volume_ratio_max",
                    "high_pullback_short_taker_ratio_max",
                    "high_pullback_short_funding_abs_max_pct",
                    "high_pullback_short_confirm_min_score",
                    "max_exposure_pct",
                    "max_daily_profit_usdt",
                    "max_daily_loss_usdt",
                    "equity_take_profit_usdt",
                }:
                    value = max(0.01, float(value))
                if key == "pre_start_oi_1h_min_pct":
                    value = float(value)
                if key == "old_rebound_short_oi_1h_min_pct":
                    value = float(value)
                if key == "high_pullback_short_oi_1h_min_pct":
                    value = float(value)
                self.config[key] = value
            save_json(self.config_path, self.config)
            if any(key in patch for key in {"equity_take_profit_enabled", "previous_day_equity_usdt", "equity_take_profit_usdt"}):
                self.state["equity_take_profit_triggered"] = False
                self.state["equity_take_profit_triggered_at"] = ""
            current_mode = self.exchange_mode()
            self.state["mode"] = current_mode
            if current_mode != previous_mode:
                for key in {
                    "available_balance_usdt",
                    "account_equity_usdt",
                    "wallet_balance_usdt",
                    "account_unrealized_pnl_usdt",
                    "exchange_account_mode",
                }:
                    self.state.pop(key, None)
                self.state["positions"] = []
                self.state["exchange_open_positions"] = []
                self.state["trade_history"] = []
                self.state["exchange_status"] = "unknown"
                self.state["last_error"] = ""
                self.state["equity_high_watermark_usdt"] = float(self.config.get("initial_equity_usdt", 100.0))
                self.state["max_drawdown_pct"] = 0.0
            self.event("info", "参数已保存")
            self.persist()
            return self.snapshot()

    def run_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                running = bool(self.state.get("running"))
            if not running:
                return
            self.scan_once()
            interval = max(2, int(self.config.get("scan_interval_sec", 20)))
            with self.lock:
                self.state["next_scan_at"] = time.time() + interval
                self.persist()
            self.stop_event.wait(interval)

    def update_scan_stage(self, stage: str) -> None:
        with self.lock:
            self.state["scan_stage"] = stage

    def scan_once(self) -> None:
        started = time.monotonic()
        def set_scan_stage(stage: str) -> None:
            self.update_scan_stage(stage)

        with self.lock:
            if self.state.get("scan_in_progress"):
                max_runtime = max(60, int(self.config.get("max_scan_runtime_sec", 180)))
                last_scan_ms = parse_time_ms(self.state.get("last_scan_at"))
                elapsed_sec = (int(time.time() * 1000) - last_scan_ms) / 1000 if last_scan_ms > 0 else 0
                if last_scan_ms > 0 and elapsed_sec > max_runtime:
                    self.event("error", f"上一轮扫描超过 {max_runtime} 秒未结束，已自动释放扫描状态")
                    self.state["scan_in_progress"] = False
                    self.state["scan_stage"] = "timeout"
                    self.state["last_scan_finished_at"] = iso_now()
                    self.state["last_scan_duration_sec"] = round(elapsed_sec, 2)
                else:
                    self.event("warn", "已有扫描正在进行，已跳过重复触发")
                    self.persist()
                    return
            self.state["scan_in_progress"] = True
            self.state["scan_stage"] = "starting"
            self.state["scan_count"] = int(self.state.get("scan_count", 0)) + 1
            self.state["last_scan_at"] = iso_now()
            self.state["last_error"] = ""
        try:
            try:
                set_scan_stage("fetch_tickers")
                tickers = self.fetch_tickers()
                with self.lock:
                    self.state["market_source"] = "binance_public"
            except Exception as exc:
                with self.lock:
                    self.state["market_source"] = "binance_public_error"
                    self.state["last_error"] = str(exc)
                    self.event("error", f"Binance 真实合约公开行情读取失败，本轮停止扫描且不会开仓：{exc}")
                    self.persist()
                return

            price_map = {item["symbol"]: fnum(item.get("lastPrice")) for item in tickers}
            set_scan_stage("update_future_returns")
            self.update_signal_future_returns(price_map)
            try:
                set_scan_stage("sync_exchange")
                self.sync_exchange_state()
            except Exception as exc:
                with self.lock:
                    self.state["exchange_status"] = "error"
                    self.state["last_error"] = str(exc)
                    self.event("error", f"{self.exchange_label()} 同步失败：{exc}")
                    self.persist()
                return
            equity_take_profit_active = self.enforce_equity_take_profit()
            with self.lock:
                for pos in self.state.get("positions", []):
                    if pos.get("symbol") in price_map:
                        pos["last_price"] = price_map[pos["symbol"]]
                open_symbols = {p["symbol"] for p in self.state.get("positions", [])}
            set_scan_stage("build_signals")
            if self.crypto_classification_error:
                signals = []
                with self.lock:
                    self.state["last_error"] = self.crypto_classification_error
                    self.event("error", f"加密合约分类数据不可用，本轮只更新持仓行情、不生成新开仓信号：{self.crypto_classification_error}")
            else:
                signals = self.build_signals(tickers, open_symbols)
            if equity_take_profit_active:
                equity_tp_reason = self.equity_take_profit_block_reason()
                if equity_tp_reason:
                    for signal in signals:
                        if not signal.reject_reason:
                            self.update_signal_decision(signal, equity_tp_reason, f"开仓前风控：{equity_tp_reason}")
            with self.lock:
                self.state["candidates"] = [signal.__dict__ for signal in signals]
                self.update_pending_open_signals(signals)
                self.record_signal_review_scan(signals)
                self.persist()
            set_scan_stage("open_positions")
            opened = 0 if equity_take_profit_active or self.crypto_classification_error else self.open_from_signals(signals)
            with self.lock:
                if opened:
                    self.event("trade", f"本轮 {self.exchange_label()} 开仓 {opened} 笔")
                    self.persist()
            if opened:
                self.sync_exchange_state()
        finally:
            pending_signals: list[Signal] = []
            with self.lock:
                self.update_risk_state()
                self.state["scan_in_progress"] = False
                self.state["scan_stage"] = "idle"
                self.state["last_scan_finished_at"] = iso_now()
                self.state["last_scan_duration_sec"] = round(time.monotonic() - started, 2)
                if not self.crypto_classification_error:
                    pending_signals = self.collect_pending_open_signals()
                self.persist()
            pending_opened = self.open_from_signals(pending_signals) if pending_signals and not self.crypto_classification_error else 0
            if pending_opened:
                self.sync_exchange_state()

    def fetch_tickers(self) -> list[dict[str, Any]]:
        timeout = float(self.config.get("request_timeout_sec", 6))
        payload = http_get_json(self.config["market_base_url"], "/fapi/v1/ticker/24hr", timeout=timeout)
        quote = str(self.config.get("quote_asset", "USDT"))
        excluded = set(self.config.get("exclude_symbols", []))
        try:
            exchange_symbols = self.get_market_exchange_symbols()
            self.crypto_classification_error = ""
        except Exception as exc:
            self.crypto_symbols = set()
            self.crypto_classification_error = str(exc)
            existing_symbols = {
                str(position.get("symbol", "")).upper()
                for position in self.state.get("positions", [])
                if str(position.get("symbol", "")).strip()
            }
            return [
                {**item, "_crypto_candidate_allowed": False}
                for item in payload
                if item.get("symbol") in existing_symbols
                and str(item.get("symbol", "")).endswith(quote)
                and item.get("symbol") not in excluded
                and fnum(item.get("lastPrice")) > 0
            ]
        self.crypto_symbols = {
            symbol for symbol, info in exchange_symbols.items() if self.is_crypto_contract(info)
        }
        existing_symbols = {
            str(position.get("symbol", "")).upper()
            for position in self.state.get("positions", [])
            if str(position.get("symbol", "")).strip()
        }
        result = []
        for item in payload:
            symbol = item.get("symbol", "")
            symbol_info = exchange_symbols.get(symbol)
            if (
                symbol.endswith(quote)
                and symbol not in excluded
                and fnum(item.get("lastPrice")) > 0
                and symbol in exchange_symbols
                and (symbol in self.crypto_symbols or symbol in existing_symbols)
            ):
                enriched = dict(item)
                enriched["_crypto_candidate_allowed"] = symbol in self.crypto_symbols
                enriched["_contract_underlying_type"] = str(symbol_info.get("underlyingType", ""))
                enriched["_contract_type"] = str(symbol_info.get("contractType", ""))
                result.append(enriched)
        return result

    def signed_exchange_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.is_live_trading():
            api_key = os.environ.get("BINANCE_LIVE_API_KEY", "").strip()
            api_secret = os.environ.get("BINANCE_LIVE_API_SECRET", "").strip()
            key_hint = "BINANCE_LIVE_API_KEY 或 BINANCE_LIVE_API_SECRET"
        else:
            api_key = os.environ.get("BINANCE_API_KEY", "").strip()
            api_secret = os.environ.get("BINANCE_API_SECRET", "").strip()
            key_hint = "BINANCE_API_KEY 或 BINANCE_API_SECRET"
        if not api_key or not api_secret:
            raise RuntimeError(f"缺少 {key_hint}，请在 .env 配置 {self.exchange_label()} key")
        futures = self.exchange_config()

        def send(force_time_sync: bool = False) -> Any:
            payload = dict(params or {})
            payload.setdefault("recvWindow", max(60000, int(futures.get("recv_window", 5000))))
            payload["timestamp"] = int(time.time() * 1000) + self.exchange_time_offset_ms(force=force_time_sync)
            query = urllib.parse.urlencode(payload, doseq=True)
            signature = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
            signed = dict(payload)
            signed["signature"] = signature
            return http_request_json(
                method,
                self.exchange_base_url(),
                path,
                signed,
                headers={"X-MBX-APIKEY": api_key},
                timeout=float(self.config.get("request_timeout_sec", 6)),
                error_label=self.exchange_label(),
            )

        try:
            return send()
        except RuntimeError as exc:
            if '"code":-1021' in str(exc) or "outside of the recvWindow" in str(exc):
                self.exchange_time_offsets.pop(self.exchange_mode(), None)
                time.sleep(0.3)
                try:
                    return send(force_time_sync=True)
                except RuntimeError as retry_exc:
                    if '"code":-1021' in str(retry_exc) or "outside of the recvWindow" in str(retry_exc):
                        self.exchange_time_offsets.pop(self.exchange_mode(), None)
                        time.sleep(0.8)
                        return send(force_time_sync=True)
                    raise
            raise

    def unsigned_exchange_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        futures = self.exchange_config()
        return http_get_json(
            self.exchange_base_url(),
            path,
            params,
            timeout=float(self.config.get("request_timeout_sec", 6)),
        )

    def get_exchange_symbols(self) -> dict[str, dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        cache_ttl_ms = max(5, int(self.config.get("exchange_info_cache_sec", 30))) * 1000
        cached_at = int(self.exchange_symbols_cache.get("synced_at_ms", 0))
        cached_symbols = self.exchange_symbols_cache.get("symbols")
        if cached_at > 0 and now_ms - cached_at < cache_ttl_ms and isinstance(cached_symbols, dict):
            return dict(cached_symbols)
        payload = self.unsigned_exchange_request("/fapi/v1/exchangeInfo")
        result: dict[str, dict[str, Any]] = {}
        for item in payload.get("symbols", []):
            symbol = item.get("symbol")
            if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT":
                result[symbol] = item
        self.exchange_symbols_cache = {"symbols": result, "synced_at_ms": now_ms}
        return result

    def get_market_exchange_symbols(self) -> dict[str, dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        cache_ttl_ms = max(5, int(self.config.get("exchange_info_cache_sec", 30))) * 1000
        cached_at = int(self.market_exchange_symbols_cache.get("synced_at_ms", 0))
        cached_symbols = self.market_exchange_symbols_cache.get("symbols")
        if cached_at > 0 and now_ms - cached_at < cache_ttl_ms and isinstance(cached_symbols, dict):
            return dict(cached_symbols)
        payload = http_get_json(
            self.config["market_base_url"],
            "/fapi/v1/exchangeInfo",
            timeout=float(self.config.get("request_timeout_sec", 6)),
        )
        result = {
            str(item.get("symbol")): item
            for item in payload.get("symbols", [])
            if item.get("symbol") and item.get("status") == "TRADING"
        }
        if not result:
            raise RuntimeError("Binance exchangeInfo 未返回可用交易对，停止筛选")
        self.market_exchange_symbols_cache = {"symbols": result, "synced_at_ms": now_ms}
        return result

    @staticmethod
    def is_crypto_contract(symbol_info: dict[str, Any]) -> bool:
        underlying_type = str(symbol_info.get("underlyingType", "")).upper()
        contract_type = str(symbol_info.get("contractType", "")).upper()
        underlying_subtypes = {
            str(item).upper() for item in symbol_info.get("underlyingSubType", [])
        }
        if underlying_type != "COIN":
            return False
        if contract_type.startswith("TRADIFI"):
            return False
        return not underlying_subtypes or "TRADFI" not in underlying_subtypes

    @staticmethod
    def get_symbol_filter(symbol_info: dict[str, Any], filter_type: str) -> dict[str, Any]:
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == filter_type:
                return item
        return {}

    def round_quantity(self, symbol_info: dict[str, Any], qty: float) -> str:
        lot_filter = self.get_symbol_filter(symbol_info, "LOT_SIZE")
        return quantize_down(qty, lot_filter.get("stepSize", "0.00000001"))

    def round_price(self, symbol_info: dict[str, Any], price: float) -> str:
        price_filter = self.get_symbol_filter(symbol_info, "PRICE_FILTER")
        return quantize_down(price, price_filter.get("tickSize", "0.00000001"))

    def get_available_balance(self) -> float:
        payload = self.signed_exchange_request("GET", "/fapi/v2/balance")
        for item in payload:
            if item.get("asset") == "USDT":
                return fnum(item.get("availableBalance"))
        return 0.0

    def get_account_balance_snapshot(self) -> dict[str, float]:
        payload = self.signed_exchange_request("GET", "/fapi/v2/account")
        assets = payload.get("assets", []) if isinstance(payload, dict) else []
        quote_asset = str(self.config.get("quote_asset", "USDT")).upper()
        asset_row = next((item for item in assets if item.get("asset") == quote_asset), {})
        return {
            "equity_usdt": fnum(payload.get("totalMarginBalance") or asset_row.get("marginBalance")),
            "available_balance_usdt": fnum(payload.get("availableBalance") or asset_row.get("availableBalance")),
            "wallet_balance_usdt": fnum(payload.get("totalWalletBalance") or asset_row.get("walletBalance")),
            "unrealized_pnl_usdt": fnum(payload.get("totalUnrealizedProfit") or asset_row.get("unrealizedProfit")),
        }

    def sync_exchange_state(self) -> None:
        payload = self.signed_exchange_request("GET", "/fapi/v2/positionRisk")
        with self.lock:
            open_history = [item for item in self.state.get("open_order_history", []) if self.record_belongs_to_current_exchange(item)]
            previous_positions = [dict(item) for item in self.state.get("positions", [])]
            reconciled_ids = {str(item) for item in self.state.get("reconciled_close_ids", [])}
        positions = []
        for item in payload:
            amount = fnum(item.get("positionAmt"))
            if abs(amount) <= 0:
                continue
            entry = fnum(item.get("entryPrice"))
            mark = fnum(item.get("markPrice"))
            unrealized = fnum(item.get("unRealizedProfit"))
            margin = abs(fnum(item.get("isolatedMargin"))) or max(0.01, abs(entry * amount) / max(1.0, float(self.config.get("leverage", 3))))
            side = "LONG" if amount > 0 else "SHORT"
            open_record = self.find_open_record_for_position(str(item.get("symbol", "")), side, open_history)
            positions.append(
                {
                    "id": f"binance-{item.get('symbol')}",
                    "symbol": item.get("symbol"),
                    "side": side,
                    "entry_price": entry,
                    "last_price": mark,
                    "quantity": abs(amount),
                    "margin_usdt": margin,
                    "leverage": fnum(item.get("leverage"), float(self.config.get("leverage", 3))),
                    "notional_usdt": abs(mark * amount),
                    "trigger_reason": f"{self.exchange_label()} 持仓同步",
                    "opened_at": item.get("updateTime") or iso_now(),
                    "unrealized_pnl_usdt": unrealized,
                    "roi_pct": unrealized / max(0.01, margin) * 100.0,
                    "liquidation_price": fnum(item.get("liquidationPrice")),
                    "entry_order_id": open_record.get("entry_order_id", ""),
                    "stop_algo_id": open_record.get("stop_algo_id", ""),
                    "stop_price": open_record.get("stop_price", ""),
                    "take_profit_algo_id": open_record.get("take_profit_algo_id", ""),
                    "take_profit": open_record.get("take_profit", ""),
                    "stop_lift_stage": open_record.get("stop_lift_stage", ""),
                    "stop_lift_roi_pct": open_record.get("stop_lift_roi_pct", 0.0),
                    "signal_review_id": open_record.get("signal_review_id", ""),
                    "score": fnum(open_record.get("score")),
                    "analyzer_direction": open_record.get("analyzer_direction", ""),
                    "analyzer_confidence_pct": fnum(open_record.get("analyzer_confidence_pct")),
                    "decision_summary": open_record.get("decision_summary", ""),
                    "decision_steps": open_record.get("decision_steps", []),
                }
            )
        with self.lock:
            closed_records = self.reconcile_exchange_closed_positions(previous_positions, positions, reconciled_ids)
            for record in closed_records:
                self.record_exchange_closed_position(record["position"], record["summary"], record["close_id"])
            self.state["positions"] = positions
            self.state["exchange_open_positions"] = positions
            self.state["exchange_status"] = "connected"
            self.state["exchange_account_mode"] = self.exchange_mode()
            self.persist()

        position_history: list[dict[str, Any]] | None = None
        try:
            position_history = self.fetch_today_position_history(open_history)
        except Exception as exc:
            with self.lock:
                self.event("warn", f"{self.exchange_label()} 仓位历史同步失败，已保留持仓对账结果：{exc}")

        account_balance = self.get_account_balance_snapshot()
        with self.lock:
            if position_history is not None and (position_history or not closed_records):
                self.notify_new_closed_trades(position_history)
                self.state["trade_history"] = self.merge_trade_history(position_history, self.state.get("trade_history", []))
            self.state["account_equity_usdt"] = account_balance["equity_usdt"]
            self.state["available_balance_usdt"] = account_balance["available_balance_usdt"]
            self.state["wallet_balance_usdt"] = account_balance["wallet_balance_usdt"]
            self.state["account_unrealized_pnl_usdt"] = account_balance["unrealized_pnl_usdt"]
            self.refresh_trade_counters()
            self.update_risk_state()
            self.persist()

    def fetch_recent_user_trades(self, symbol: str, start_time_ms: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": 1000}
        if start_time_ms > 0:
            params["startTime"] = max(0, start_time_ms)
        payload = self.signed_exchange_request("GET", "/fapi/v1/userTrades", params)
        return payload if isinstance(payload, list) else []

    def find_open_record_for_position(self, symbol: str, side: str, open_history: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = [
            record
            for record in open_history
            if str(record.get("symbol", "")) == symbol and str(record.get("side", "")) == side
        ]
        if not candidates:
            return {}
        return max(candidates, key=lambda record: parse_time_ms(record.get("time")))

    def estimate_exit_price_from_pnl(self, open_record: dict[str, Any], pnl: float) -> float:
        entry = fnum(open_record.get("entry_price"))
        qty = abs(fnum(open_record.get("quantity")))
        side = str(open_record.get("side", ""))
        if entry <= 0 or qty <= 0:
            return 0.0
        move = pnl / qty
        if side == "SHORT":
            return max(0.0, entry - move)
        return max(0.0, entry + move)

    def display_exit_price(self, trade: dict[str, Any]) -> float:
        price = fnum(trade.get("exit_price"))
        if price > 0:
            return price
        return self.estimate_exit_price_from_pnl(trade, fnum(trade.get("pnl_usdt")))

    def today_local_range_ms(self) -> tuple[int, int]:
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    def open_history_symbols(self, open_history: list[dict[str, Any]]) -> list[str]:
        symbols = []
        seen = set()
        for record in open_history:
            symbol = str(record.get("symbol", ""))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
        return symbols

    def find_open_record_for_trade_window(
        self,
        symbol: str,
        side: str,
        closed_ms: int,
        open_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidates = [
            record
            for record in open_history
            if str(record.get("symbol", "")) == symbol
            and str(record.get("side", "")) == side
            and parse_time_ms(record.get("time")) <= closed_ms
        ]
        if not candidates:
            return {}
        return max(candidates, key=lambda record: parse_time_ms(record.get("time")))

    def fetch_today_position_history(self, open_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        start_ms, end_ms = self.today_local_range_ms()
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for symbol in self.open_history_symbols(open_history):
            trades = self.fetch_recent_user_trades(symbol, max(0, start_ms - 24 * 60 * 60 * 1000))
            close_groups = self.group_closed_position_trades(symbol, trades, start_ms, end_ms)
            for group in close_groups:
                close_ms = int(group.get("time_ms", 0))
                side = str(group.get("side", ""))
                open_record = self.find_open_record_for_trade_window(symbol, side, close_ms, open_history)
                row = self.closed_position_row_from_trade_group(symbol, group, open_record)
                trade_id = str(row.get("id", ""))
                if not trade_id or trade_id in seen_ids:
                    continue
                seen_ids.add(trade_id)
                if open_record:
                    self.mark_signal_review_closed(open_record, row)
                rows.append(row)
        rows.sort(key=lambda row: str(row.get("closed_at", "")), reverse=True)
        return rows

    def trade_merge_keys(self, trade: dict[str, Any]) -> list[str]:
        keys = []
        trade_id = str(trade.get("id", "")).strip()
        close_order_id = str(trade.get("close_order_id", "")).strip()
        mode = str(trade.get("execution_mode") or self.exchange_mode())
        symbol = str(trade.get("symbol", "")).strip()
        side = str(trade.get("side", "")).strip()
        closed_ms = parse_time_ms(trade.get("closed_at"))
        qty = f"{fnum(trade.get('quantity')):.8f}"
        if trade_id:
            keys.append(f"id:{trade_id}")
        if close_order_id:
            keys.append(f"close_order:{mode}:{symbol}:{side}:{close_order_id}")
        if symbol and side and closed_ms > 0:
            keys.append(f"semantic:{mode}:{symbol}:{side}:{closed_ms // 1000}:{qty}")
        return keys

    def merge_trade_history(self, exchange_rows: list[dict[str, Any]], current_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        local_by_key: dict[str, dict[str, Any]] = {}
        used_local_ids: set[int] = set()
        for trade in current_history:
            for key in self.trade_merge_keys(trade):
                local_by_key.setdefault(key, trade)

        merged: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        exchange_fields = {
            "id",
            "execution_mode",
            "symbol",
            "side",
            "entry_price",
            "exit_price",
            "quantity",
            "margin_usdt",
            "pnl_usdt",
            "roi_pct",
            "opened_at",
            "closed_at",
            "close_reason",
            "entry_order_id",
            "close_order_id",
        }
        strategy_fields = {
            "trigger_reason",
            "strategy_score",
            "score",
            "analyzer_direction",
            "analyzer_confidence_pct",
            "analyzer_reason",
            "decision_summary",
            "decision_steps",
            "signal_review_id",
            "signal_mode",
            "signal_heat_level",
        }
        for row in exchange_rows:
            keys = self.trade_merge_keys(row)
            local = next((local_by_key[key] for key in keys if key in local_by_key), None)
            if local:
                used_local_ids.add(id(local))
            merged_row = dict(local or {})
            for field in exchange_fields:
                value = row.get(field)
                if value not in (None, ""):
                    merged_row[field] = value
            for field in strategy_fields:
                if not merged_row.get(field) and row.get(field):
                    merged_row[field] = row.get(field)
            merged.append(merged_row)
            seen_keys.update(keys)

        for trade in current_history:
            if id(trade) in used_local_ids:
                continue
            keys = self.trade_merge_keys(trade)
            if keys and any(key in seen_keys for key in keys):
                continue
            merged.append(trade)
            seen_keys.update(keys)

        merged.sort(key=lambda item: str(item.get("closed_at") or item.get("time") or ""), reverse=True)
        return merged[:200]

    def group_closed_position_trades(
        self,
        symbol: str,
        trades: list[dict[str, Any]],
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for trade in sorted(trades, key=lambda item: fnum(item.get("time"))):
            pnl = fnum(first_present(trade, ["realizedPnl", "realizedProfit"]))
            trade_time = int(fnum(trade.get("time")))
            if abs(pnl) <= 0 or trade_time < start_ms or trade_time >= end_ms:
                continue
            position_side = str(trade.get("positionSide", "")).upper()
            trade_side = str(trade.get("side", "")).upper()
            if position_side in {"LONG", "SHORT"}:
                side = position_side
            else:
                side = "LONG" if trade_side == "SELL" else "SHORT"
            qty = abs(fnum(first_present(trade, ["qty", "executedQty"])))
            price = fnum(first_present(trade, ["price"]))
            order_id = str(trade.get("orderId", ""))
            if (
                current
                and current.get("side") == side
                and order_id
                and str(current.get("last_order_id", "")) == order_id
                and trade_time - int(current.get("time_ms", 0)) <= 10 * 60 * 1000
            ):
                current["pnl"] = fnum(current.get("pnl")) + pnl
                current["qty"] = fnum(current.get("qty")) + qty
                current["notional"] = fnum(current.get("notional")) + price * qty
                current["time_ms"] = max(int(current.get("time_ms", 0)), trade_time)
                current.setdefault("trade_ids", []).append(str(trade.get("id", "")))
            else:
                if current:
                    groups.append(current)
                current = {
                    "symbol": symbol,
                    "side": side,
                    "pnl": pnl,
                    "qty": qty,
                    "notional": price * qty,
                    "time_ms": trade_time,
                    "last_order_id": order_id,
                    "trade_ids": [str(trade.get("id", ""))],
                }
        if current:
            groups.append(current)
        return groups

    def closed_position_row_from_trade_group(
        self,
        symbol: str,
        group: dict[str, Any],
        open_record: dict[str, Any],
    ) -> dict[str, Any]:
        pnl = fnum(group.get("pnl"))
        qty = fnum(group.get("qty"))
        exit_price = fnum(group.get("notional")) / qty if qty > 0 else 0.0
        closed_ms = int(fnum(group.get("time_ms")))
        closed_at = datetime.fromtimestamp(closed_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds") if closed_ms > 0 else iso_now()
        margin = fnum(open_record.get("margin_usdt"))
        reason = "止盈平仓" if pnl > 0 else "止损平仓"
        row_id = f"{self.exchange_mode()}-position-{symbol}-{group.get('side')}-{closed_ms}-{group.get('last_order_id')}"
        row = {
            "id": row_id,
            "execution_mode": self.exchange_mode(),
            "symbol": symbol,
            "side": str(group.get("side", "")),
            "entry_price": fnum(open_record.get("entry_price")),
            "exit_price": exit_price,
            "quantity": qty or fnum(open_record.get("quantity")),
            "margin_usdt": margin,
            "pnl_usdt": pnl,
            "roi_pct": pnl / max(0.01, margin) * 100.0 if margin > 0 else 0.0,
            "opened_at": open_record.get("time") or "",
            "closed_at": closed_at,
            "close_reason": f"{self.exchange_label()} {reason}",
            "trigger_reason": open_record.get("trigger_reason", f"{self.exchange_label()} 仓位历史"),
            "entry_order_id": open_record.get("entry_order_id", ""),
            "close_order_id": group.get("last_order_id", ""),
            "signal_review_id": open_record.get("signal_review_id", ""),
            "signal_mode": open_record.get("signal_mode", ""),
            "signal_heat_level": open_record.get("signal_heat_level", ""),
            "strategy_score": fnum(open_record.get("score")),
            "analyzer_direction": open_record.get("analyzer_direction", ""),
            "analyzer_confidence_pct": fnum(open_record.get("analyzer_confidence_pct")),
            "decision_summary": open_record.get("decision_summary", ""),
            "decision_steps": open_record.get("decision_steps", []),
        }
        if fnum(row.get("exit_price")) <= 0:
            row["exit_price"] = self.display_exit_price(row)
        return row

    def summarize_exit_trades(self, pos: dict[str, Any]) -> dict[str, float]:
        symbol = str(pos.get("symbol", ""))
        if not symbol:
            return {"exit_price": fnum(pos.get("last_price"), fnum(pos.get("entry_price"))), "pnl": 0.0, "qty": 0.0, "time_ms": 0.0}
        opened_ms = parse_time_ms(pos.get("opened_at"))
        start_ms = opened_ms or int(max(0, (time.time() - 7 * 24 * 3600) * 1000))
        trades = self.fetch_recent_user_trades(symbol, start_ms)
        close_side = self.close_side(str(pos.get("side", "")))
        target_qty = abs(fnum(pos.get("quantity")))
        exit_trades = []
        collected_qty = 0.0
        for trade in sorted(trades, key=lambda item: fnum(item.get("time"))):
            if str(trade.get("side", "")).upper() != close_side:
                continue
            if opened_ms and fnum(trade.get("time")) < opened_ms:
                continue
            qty = abs(fnum(first_present(trade, ["qty", "executedQty"])))
            if qty <= 0:
                continue
            exit_trades.append(trade)
            collected_qty += qty
            if target_qty > 0 and collected_qty >= target_qty * 0.98:
                break
        if not exit_trades:
            return {"exit_price": fnum(pos.get("last_price"), fnum(pos.get("entry_price"))), "pnl": 0.0, "qty": 0.0, "time_ms": 0.0}

        pnl = sum(fnum(first_present(trade, ["realizedPnl", "realizedProfit"])) for trade in exit_trades)
        qty = sum(abs(fnum(first_present(trade, ["qty", "executedQty"]))) for trade in exit_trades)
        notional = sum(fnum(first_present(trade, ["price"])) * abs(fnum(first_present(trade, ["qty", "executedQty"]))) for trade in exit_trades)
        exit_price = notional / qty if qty > 0 else fnum(pos.get("last_price"), fnum(pos.get("entry_price")))
        time_ms = max(fnum(trade.get("time")) for trade in exit_trades)
        return {"exit_price": exit_price, "pnl": pnl, "qty": qty, "time_ms": time_ms}

    def reconcile_exchange_closed_positions(
        self,
        previous_positions: list[dict[str, Any]],
        current_positions: list[dict[str, Any]],
        reconciled_ids: set[str],
    ) -> list[dict[str, Any]]:
        current_symbols = {str(pos.get("symbol", "")) for pos in current_positions}
        closed_records = []
        for pos in previous_positions:
            symbol = str(pos.get("symbol", ""))
            if not symbol or symbol in current_symbols:
                continue
            close_id = f"{symbol}-{pos.get('side')}-{pos.get('entry_price')}-{pos.get('quantity')}-{pos.get('opened_at')}"
            if close_id in reconciled_ids:
                continue
            try:
                summary = self.summarize_exit_trades(pos)
            except Exception as exc:
                self.event("warn", f"{symbol} 平仓对账失败：{exc}")
                continue
            closed_records.append({"position": pos, "summary": summary, "close_id": close_id})
        return closed_records

    def record_exchange_closed_position(self, pos: dict[str, Any], summary: dict[str, float], close_id: str) -> None:
        pnl = fnum(summary.get("pnl"))
        exit_price = fnum(summary.get("exit_price"), fnum(pos.get("last_price"), fnum(pos.get("entry_price"))))
        margin = max(0.01, fnum(pos.get("margin_usdt")))
        roi = pnl / margin * 100.0
        reason = "止盈平仓" if pnl >= 0 else "止损平仓"
        if pnl >= 0:
            self.state["take_profit_count"] = int(self.state.get("take_profit_count", 0)) + 1
        else:
            self.state["stop_loss_count"] = int(self.state.get("stop_loss_count", 0)) + 1
        self.state["realized_pnl_usdt"] = fnum(self.state.get("realized_pnl_usdt")) + pnl
        closed_at_ms = fnum(summary.get("time_ms"))
        closed_at = datetime.fromtimestamp(closed_at_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds") if closed_at_ms > 0 else iso_now()
        row = {
            "id": close_id,
            "execution_mode": self.exchange_mode(),
            "symbol": pos.get("symbol"),
            "side": pos.get("side"),
            "entry_price": fnum(pos.get("entry_price")),
            "exit_price": exit_price,
            "quantity": fnum(pos.get("quantity")),
            "margin_usdt": margin,
            "pnl_usdt": pnl,
            "roi_pct": roi,
            "opened_at": pos.get("opened_at"),
            "closed_at": closed_at,
            "close_reason": f"{self.exchange_label()} {reason}",
            "trigger_reason": pos.get("trigger_reason", f"{self.exchange_label()} 持仓同步"),
            "entry_order_id": pos.get("entry_order_id", ""),
            "signal_review_id": pos.get("signal_review_id", ""),
            "strategy_score": fnum(pos.get("score")),
            "analyzer_direction": pos.get("analyzer_direction", ""),
            "analyzer_confidence_pct": fnum(pos.get("analyzer_confidence_pct")),
            "decision_summary": pos.get("decision_summary", ""),
            "decision_steps": pos.get("decision_steps", []),
        }
        self.state.setdefault("trade_history", []).insert(0, row)
        self.mark_signal_review_closed(pos, row)
        self.notify_new_closed_trades([row])
        self.state["trade_history"] = self.state["trade_history"][:200]
        reconciled = list(dict.fromkeys([close_id, *[str(item) for item in self.state.get("reconciled_close_ids", [])]]))
        self.state["reconciled_close_ids"] = reconciled[:500]
        self.event("trade", f"{pos.get('symbol')} {self.side_cn(str(pos.get('side', '')))} {self.exchange_label()} {reason}，PnL {pnl:+.2f} U")

    def collect_backfill_closed_trade_records(
        self,
        history: list[dict[str, Any]],
        current_positions: list[dict[str, Any]],
        reconciled_ids: set[str],
    ) -> list[dict[str, Any]]:
        current_symbols = {str(pos.get("symbol", "")) for pos in current_positions}
        closed_ids = {str(item.get("id", "")) for item in history if item.get("closed_at")}
        records = []
        for record in list(history):
            if record.get("closed_at") or record.get("close_reason") != "Binance Testnet 开仓记录":
                continue
            symbol = str(record.get("symbol", ""))
            if not symbol or symbol in current_symbols:
                continue
            close_id = f"backfill-{record.get('entry_order_id') or symbol}-{record.get('time')}"
            if close_id in closed_ids or close_id in reconciled_ids:
                continue
            pos = {
                "symbol": symbol,
                "side": record.get("side"),
                "entry_price": record.get("entry_price") or 0.0,
                "last_price": record.get("entry_price") or 0.0,
                "quantity": record.get("quantity"),
                "margin_usdt": record.get("margin_usdt"),
                "opened_at": record.get("time"),
                "trigger_reason": record.get("trigger_reason"),
            }
            try:
                summary = self.summarize_exit_trades(pos)
            except Exception as exc:
                self.event("warn", f"{symbol} 历史平仓回填失败：{exc}")
                continue
            if fnum(summary.get("qty")) <= 0:
                continue
            records.append({"position": pos, "summary": summary, "close_id": close_id})
        return records

    @staticmethod
    def futures_side(signal_side: str) -> str:
        return "BUY" if signal_side == "LONG" else "SELL"

    def execution_side(self, signal_side: str) -> str:
        original_side = str(signal_side).upper()
        if not self.config.get("reverse_orders_enabled", False):
            return original_side
        return "SHORT" if original_side == "LONG" else "LONG"

    @staticmethod
    def close_side(signal_side: str) -> str:
        return "SELL" if signal_side == "LONG" else "BUY"

    def position_mode(self, force: bool = False) -> str:
        now_ms = int(time.time() * 1000)
        cached_mode = str(self.position_mode_cache.get("mode") or "")
        cached_at = int(self.position_mode_cache.get("synced_at_ms") or 0)
        if not force and cached_mode and now_ms - cached_at < 5 * 60 * 1000:
            return cached_mode
        payload = self.signed_exchange_request("GET", "/fapi/v1/positionSide/dual")
        mode = "hedge" if bool(payload.get("dualSidePosition")) else "one_way"
        self.position_mode_cache = {"mode": mode, "synced_at_ms": now_ms}
        return mode

    def position_side_params(self, side: str, force: bool = False) -> dict[str, str]:
        return {"positionSide": side} if self.position_mode(force=force) == "hedge" else {}

    def market_close_order_params(self, symbol: str, signal_side: str, quantity: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": self.close_side(signal_side),
            "type": "MARKET",
            "quantity": str(quantity),
        }
        if self.position_mode() != "hedge":
            params["reduceOnly"] = "true"
        return params

    def signed_exchange_request_position_aware(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        signal_side: str,
    ) -> Any:
        payload = {**params, **self.position_side_params(signal_side)}
        try:
            return self.signed_exchange_request(method, path, payload)
        except RuntimeError as exc:
            if "-4061" in str(exc) or "position side does not match" in str(exc):
                self.position_mode_cache = {"mode": "", "synced_at_ms": 0}
                payload = {**params, **self.position_side_params(signal_side, force=True)}
                return self.signed_exchange_request(method, path, payload)
            raise

    def ensure_exchange_settings(self, symbol: str, target_leverage: int | None = None) -> int:
        futures = self.exchange_config()
        margin_type = str(futures.get("margin_type", "ISOLATED")).upper()
        try:
            self.signed_exchange_request(
                "POST",
                "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": margin_type},
            )
        except RuntimeError as exc:
            if "No need to change margin type" not in str(exc):
                if self.is_live_trading() and margin_type == "ISOLATED" and ("-4168" in str(exc) or "Multi-Assets mode" in str(exc)):
                    raise RuntimeError(
                        "实盘账户已开启多资产模式，币安不允许在 Multi-Assets mode 下切换逐仓。"
                        "请先在币安合约设置里关闭多资产模式/切回单资产模式，再重新开仓。"
                    ) from exc
                raise
        target_leverage = max(1, int(target_leverage if target_leverage is not None else self.config.get("leverage", 3)))
        last_error = ""
        for leverage in range(target_leverage, 0, -1):
            try:
                self.signed_exchange_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})
                if leverage != target_leverage:
                    self.event("warn", f"{symbol} 不支持 {target_leverage}x 杠杆，已自动降到 {leverage}x")
                return leverage
            except RuntimeError as exc:
                last_error = str(exc)
                if "Leverage" not in last_error and "-4028" not in last_error:
                    raise
        raise RuntimeError(f"{symbol} 无法设置杠杆：{last_error}")

    def position_plan(self, signal: Signal, available: float) -> dict[str, Any]:
        is_pre_start = signal.signal_mode == "pre_start_long"
        is_old_rebound_short = signal.signal_mode == "old_rebound_short"
        is_high_pullback_short = signal.signal_mode == "high_pullback_short"
        if is_pre_start:
            base_margin = float(self.config.get("pre_start_margin_per_trade_usdt", self.config.get("margin_per_trade_usdt", 9.0)))
            target_leverage = max(1, int(self.config.get("pre_start_leverage", self.config.get("leverage", 3))))
            tier = "pre_start_fixed"
        elif is_old_rebound_short:
            base_margin = float(self.config.get("old_rebound_short_margin_per_trade_usdt", self.config.get("margin_per_trade_usdt", 9.0)))
            target_leverage = max(1, int(self.config.get("old_rebound_short_leverage", self.config.get("leverage", 3))))
            tier = "old_rebound_short_fixed"
        elif is_high_pullback_short:
            base_margin = float(self.config.get("high_pullback_short_margin_per_trade_usdt", self.config.get("margin_per_trade_usdt", 9.0)))
            target_leverage = max(1, int(self.config.get("high_pullback_short_leverage", self.config.get("leverage", 3))))
            tier = "high_pullback_short_fixed"
        else:
            base_margin = float(self.config.get("margin_per_trade_usdt", 9.0))
            target_leverage = max(1, int(self.config.get("leverage", 3)))
            tier = "fixed"
        margin = min(base_margin, available)
        reason = f"fixed_margin={margin:.2f}U, target_leverage={target_leverage}x"
        signal.decision_steps.append(f"仓位计划：{reason}")
        return {
            "margin_usdt": margin,
            "target_leverage": target_leverage,
            "margin_multiplier": 1.0,
            "tier": tier,
            "reason": reason,
        }
    def exchange_open_position(self, signal: Signal, symbol_info: dict[str, Any]) -> dict[str, Any] | None:
        sizing = self.position_plan(signal, self.get_available_balance())
        return self.exchange_open_position_with_sizing(signal, symbol_info, sizing)

    def exchange_open_position_with_sizing(self, signal: Signal, symbol_info: dict[str, Any], sizing: dict[str, Any]) -> dict[str, Any] | None:
        available = self.get_available_balance()
        margin = fnum(sizing.get("margin_usdt"))
        if margin <= 0:
            raise RuntimeError(f"{self.exchange_label()} 可用 USDT 余额不足")
        futures = self.exchange_config()
        original_side = str(signal.side).upper()
        execution_side = self.execution_side(original_side)
        reverse_order = execution_side != original_side
        if reverse_order:
            signal.decision_steps.append(
                f"反向订单：原策略{self.side_cn(original_side)}，实际开{self.side_cn(execution_side)}"
            )
        leverage = float(self.ensure_exchange_settings(signal.symbol, int(sizing.get("target_leverage", self.config.get("leverage", 3)))))
        notional = margin * leverage * float(futures.get("quantity_safety_factor", 0.98))
        if self.exposure_pct(extra_notional=notional) > float(self.config.get("max_exposure_pct", 65.0)):
            self.event("risk", "仓位敞口达到上限，本轮停止新增持仓")
            return None
        qty_text = self.round_quantity(symbol_info, notional / signal.price)
        if fnum(qty_text) <= 0:
            raise RuntimeError(f"{signal.symbol} 计算出的下单数量为 0")
        entry_order = self.signed_exchange_request_position_aware(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": signal.symbol,
                "side": self.futures_side(execution_side),
                "type": "MARKET",
                "quantity": qty_text,
            },
            execution_side,
        )

        if not reverse_order and fnum(signal.planned_stop_price) > 0 and fnum(signal.planned_take_profit_price) > 0:
            stop_price = fnum(signal.planned_stop_price)
            take_profit = fnum(signal.planned_take_profit_price)
        else:
            stop_pct = float(self.config.get("stop_loss_roi_pct", 86.0)) / max(1.0, leverage) / 100.0
            take_pct = float(self.config.get("take_profit_roi_pct", 90.0)) / max(1.0, leverage) / 100.0
            if execution_side == "LONG":
                stop_price = signal.price * (1.0 - stop_pct)
                take_profit = signal.price * (1.0 + take_pct)
            else:
                stop_price = signal.price * (1.0 + stop_pct)
                take_profit = signal.price * (1.0 - take_pct)

        stop_order: dict[str, Any] = {}
        take_order: dict[str, Any] = {}
        try:
            stop_order = self.signed_exchange_request_position_aware(
                "POST",
                "/fapi/v1/algoOrder",
                {
                    "algoType": "CONDITIONAL",
                    "symbol": signal.symbol,
                    "side": self.close_side(execution_side),
                    "type": "STOP_MARKET",
                    "triggerPrice": self.round_price(symbol_info, stop_price),
                    "closePosition": "true",
                    "workingType": futures.get("working_type", "MARK_PRICE"),
                },
                execution_side,
            )
            take_order = self.signed_exchange_request_position_aware(
                "POST",
                "/fapi/v1/algoOrder",
                {
                    "algoType": "CONDITIONAL",
                    "symbol": signal.symbol,
                    "side": self.close_side(execution_side),
                    "type": "TAKE_PROFIT_MARKET",
                    "triggerPrice": self.round_price(symbol_info, take_profit),
                    "closePosition": "true",
                    "workingType": futures.get("working_type", "MARK_PRICE"),
                },
                execution_side,
            )
        except Exception as exc:
            self.event("error", f"{signal.symbol} 已开仓但保护单提交失败：{exc}")
            try:
                self.signed_exchange_request_position_aware(
                    "POST",
                    "/fapi/v1/order",
                    self.market_close_order_params(signal.symbol, execution_side, qty_text),
                    execution_side,
                )
                self.event("risk", f"{signal.symbol} 保护单失败，已提交紧急平仓")
            except Exception as close_exc:
                self.event("error", f"{signal.symbol} 紧急平仓失败：{close_exc}")
            raise

        record = {
            "time": iso_now(),
            "execution_mode": self.exchange_mode(),
            "symbol": signal.symbol,
            "side": execution_side,
            "original_side": original_side,
            "reverse_order": reverse_order,
            "entry_order_id": entry_order.get("orderId"),
            "stop_algo_id": stop_order.get("algoId"),
            "take_profit_algo_id": take_order.get("algoId"),
            "quantity": qty_text,
            "margin_usdt": margin,
            "leverage": leverage,
            "sizing_tier": sizing.get("tier"),
            "sizing_margin_multiplier": sizing.get("margin_multiplier"),
            "sizing_reason": sizing.get("reason"),
            "notional_usdt": notional,
            "stop_price": self.round_price(symbol_info, stop_price),
            "take_profit": self.round_price(symbol_info, take_profit),
            "trigger_reason": signal.trigger_reason,
            "score": signal.score,
            "entry_price": signal.price,
            "analyzer_direction": signal.analyzer_direction,
            "analyzer_confidence_pct": signal.analyzer_confidence_pct,
            "analyzer_reason": signal.analyzer_reason,
            "signal_mode": signal.signal_mode,
            "signal_heat_level": signal.signal_heat_level,
            "decision_summary": signal.decision_summary,
            "decision_steps": list(signal.decision_steps),
            "signal_review_id": self.signal_review_id(int(self.state.get("scan_count", 0)), signal.symbol),
        }
        self.state.setdefault("open_order_history", []).insert(0, {**record, "close_reason": f"{self.exchange_record_prefix()} 开仓记录"})
        self.state["open_order_history"] = self.state["open_order_history"][:500]
        return record

    def exchange_close_position(self, pos: dict[str, Any]) -> None:
        symbol = pos["symbol"]
        self.signed_exchange_request_position_aware(
            "POST",
            "/fapi/v1/order",
            self.market_close_order_params(symbol, str(pos.get("side", "")), pos.get("quantity")),
            str(pos.get("side", "")),
        )

    def fetch_klines(self, symbol: str) -> list[list[Any]]:
        timeout = min(2.0, float(self.config.get("request_timeout_sec", 6)))
        return http_get_json(
            self.config["market_base_url"],
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1m", "limit": 45},
            timeout=timeout,
        )

    def fetch_klines_interval(self, symbol: str, interval: str, limit: int) -> list[list[Any]]:
        timeout = min(4.0, float(self.config.get("request_timeout_sec", 6)))
        return http_get_json(
            self.config["market_base_url"],
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
            timeout=timeout,
        )

    @staticmethod
    def ema(values: list[float], period: int) -> float:
        clean = [fnum(item) for item in values if fnum(item) > 0]
        if not clean:
            return 0.0
        k = 2 / (period + 1)
        result = clean[0]
        for value in clean[1:]:
            result = value * k + result * (1 - k)
        return result

    @staticmethod
    def atr(klines: list[list[Any]], period: int = 14) -> float:
        if len(klines) < period + 1:
            return 0.0
        trs: list[float] = []
        prev_close = fnum(klines[0][4])
        for row in klines[1:]:
            high = fnum(row[2])
            low = fnum(row[3])
            close = fnum(row[4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
            prev_close = close
        recent = trs[-period:]
        return sum(recent) / len(recent) if recent else 0.0

    @staticmethod
    def adx(klines: list[list[Any]], period: int = 14) -> float:
        if len(klines) < period + 2:
            return 0.0
        plus_dm: list[float] = []
        minus_dm: list[float] = []
        trs: list[float] = []
        for idx in range(1, len(klines)):
            high = fnum(klines[idx][2])
            low = fnum(klines[idx][3])
            prev_high = fnum(klines[idx - 1][2])
            prev_low = fnum(klines[idx - 1][3])
            prev_close = fnum(klines[idx - 1][4])
            up_move = high - prev_high
            down_move = prev_low - low
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        tr_sum = sum(trs[-period:])
        if tr_sum <= 0:
            return 0.0
        plus_di = 100 * sum(plus_dm[-period:]) / tr_sum
        minus_di = 100 * sum(minus_dm[-period:]) / tr_sum
        total = plus_di + minus_di
        return abs(plus_di - minus_di) / total * 100 if total > 0 else 0.0

    def fetch_signal_market_stats(self, symbol: str, ticker: dict[str, Any]) -> dict[str, float]:
        timeout = min(2.2, float(self.config.get("request_timeout_sec", 6)))
        price = fnum(ticker.get("lastPrice"))
        quote_volume = fnum(ticker.get("quoteVolume"))
        stats = {
            "open_interest_usdt": 0.0,
            "open_interest_24h_pct": 0.0,
            "open_interest_4h_pct": 0.0,
            "open_interest_1h_pct": 0.0,
            "funding_rate_pct": 0.0,
            "long_short_ratio": 1.0,
            "volume_4h_ratio": 1.0,
            "volume_1h_ratio": 1.0,
            "taker_long_short_ratio": 1.0,
            "oi_volume_ratio": 0.0,
        }
        try:
            oi = http_get_json(self.config["market_base_url"], "/fapi/v1/openInterest", {"symbol": symbol}, timeout=timeout)
            open_interest = fnum(oi.get("openInterest"))
            stats["open_interest_usdt"] = open_interest * price
        except Exception:
            stats["open_interest_usdt"] = quote_volume * 0.22

        try:
            premium = http_get_json(self.config["market_base_url"], "/fapi/v1/premiumIndex", {"symbol": symbol}, timeout=timeout)
            stats["funding_rate_pct"] = fnum(premium.get("lastFundingRate")) * 100
        except Exception:
            stats["funding_rate_pct"] = 0.0

        try:
            oi_hist = http_get_json(
                self.config["market_base_url"],
                "/futures/data/openInterestHist",
                {"symbol": symbol, "period": "1h", "limit": 25},
                timeout=timeout,
            )
            if isinstance(oi_hist, list) and oi_hist:
                values = [fnum(row.get("sumOpenInterestValue")) for row in oi_hist]
                latest = values[-1]
                stats["open_interest_usdt"] = latest or stats["open_interest_usdt"]
                if len(values) > 1:
                    stats["open_interest_1h_pct"] = pct(latest, values[-2])
                if len(values) > 4:
                    stats["open_interest_4h_pct"] = pct(latest, values[-5])
                if len(values) > 24:
                    stats["open_interest_24h_pct"] = pct(latest, values[0])
        except Exception:
            change = fnum(ticker.get("priceChangePercent"))
            stats["open_interest_24h_pct"] = change * 0.55
            stats["open_interest_4h_pct"] = change * 0.16
            stats["open_interest_1h_pct"] = change * 0.05

        try:
            ratios = http_get_json(
                self.config["market_base_url"],
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": symbol, "period": "1h", "limit": 1},
                timeout=timeout,
            )
            if isinstance(ratios, list) and ratios:
                stats["long_short_ratio"] = fnum(ratios[-1].get("longShortRatio"), 1.0)
        except Exception:
            stats["long_short_ratio"] = 1.0

        try:
            taker = http_get_json(
                self.config["market_base_url"],
                "/futures/data/takerlongshortRatio",
                {"symbol": symbol, "period": "1h", "limit": 1},
                timeout=timeout,
            )
            if isinstance(taker, list) and taker:
                buy = fnum(taker[-1].get("buyVol"))
                sell = fnum(taker[-1].get("sellVol"))
                stats["taker_long_short_ratio"] = buy / sell if sell > 0 else 1.0
        except Exception:
            stats["taker_long_short_ratio"] = stats["long_short_ratio"]

        stats["volume_4h_ratio"] = max(0.1, min(20.0, fnum(ticker.get("count")) / 120000.0 or 1.0))
        if quote_volume > 0:
            stats["oi_volume_ratio"] = stats["open_interest_usdt"] / quote_volume
        return stats

    def classify_signal_risk(self, score: float, funding_rate_pct: float, oi_change_pct: float, volume_ratio: float) -> str:
        if abs(funding_rate_pct) >= 0.08 or abs(oi_change_pct) >= 35 or volume_ratio >= 8:
            return "危险"
        if score >= 6 and abs(funding_rate_pct) < 0.04 and abs(oi_change_pct) < 25:
            return "正常"
        return "注意"

    def market_confirm_min_score_for_mode(self, signal_mode: str = "", side: str = "") -> float:
        if signal_mode == "pre_start_long":
            return float(self.config.get("pre_start_market_confirm_min_score", 6.5))
        if signal_mode == "old_rebound_short":
            return float(self.config.get("old_rebound_short_confirm_min_score", 6.8))
        if signal_mode == "high_pullback_short":
            return float(self.config.get("high_pullback_short_confirm_min_score", 7.0))
        if side == "SHORT":
            return float(self.config.get("short_market_confirm_min_score", 7.5))
        return float(self.config.get("market_confirm_min_score", 7.0))

    def market_regime_score(self, side: str, tickers: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.config.get("market_regime_filter_enabled", True):
            return {"score": 10.0, "label": "关闭", "reason": "市场环境过滤未启用"}
        score = 5.0
        reasons: list[str] = []
        side = side.upper()
        ticker_by_symbol = {str(item.get("symbol", "")): item for item in tickers}
        btc_change = fnum(ticker_by_symbol.get("BTCUSDT", {}).get("priceChangePercent"))
        eth_change = fnum(ticker_by_symbol.get("ETHUSDT", {}).get("priceChangePercent"))
        if side == "LONG":
            if btc_change > 0:
                score += 0.4
                reasons.append(f"BTC 24h偏强 {btc_change:+.1f}%")
            elif btc_change < -1.8:
                score -= 0.8
                reasons.append(f"BTC 24h拖累 {btc_change:+.1f}%")
            if eth_change > 0:
                score += 0.3
                reasons.append(f"ETH 24h偏强 {eth_change:+.1f}%")
            elif eth_change < -2.5:
                score -= 0.6
                reasons.append(f"ETH 24h拖累 {eth_change:+.1f}%")
        else:
            if btc_change < 0:
                score += 0.4
                reasons.append(f"BTC 24h偏弱 {btc_change:+.1f}%")
            elif btc_change > 1.8:
                score -= 0.8
                reasons.append(f"BTC 24h反弹 {btc_change:+.1f}%")
            if eth_change < 0:
                score += 0.3
                reasons.append(f"ETH 24h偏弱 {eth_change:+.1f}%")
            elif eth_change > 2.5:
                score -= 0.6
                reasons.append(f"ETH 24h反弹 {eth_change:+.1f}%")
        liquid = [
            fnum(item.get("priceChangePercent"))
            for item in tickers
            if fnum(item.get("quoteVolume")) >= float(self.config.get("min_quote_volume_usdt", 20_000_000))
        ]
        if liquid:
            up_count = sum(1 for change in liquid if change > 1.0)
            down_count = sum(1 for change in liquid if change < -1.0)
            total = max(1, len(liquid))
            up_ratio = up_count / total
            down_ratio = down_count / total
            if side == "LONG":
                if up_ratio >= 0.58:
                    score += 0.7
                    reasons.append(f"全市场偏多 {up_ratio:.0%}")
                elif down_ratio >= 0.55:
                    score -= 1.0
                    reasons.append(f"小币退潮 {down_ratio:.0%}")
            else:
                if down_ratio >= 0.55:
                    score += 0.7
                    reasons.append(f"全市场偏空 {down_ratio:.0%}")
                elif up_ratio >= 0.58:
                    score -= 1.0
                    reasons.append(f"小币普涨 {up_ratio:.0%}")
        for symbol, weight in {"BTCUSDT": 0.7, "ETHUSDT": 0.5}.items():
            item = ticker_by_symbol.get(symbol)
            if not item:
                continue
            try:
                price = fnum(item.get("lastPrice"))
                change = fnum(item.get("priceChangePercent"))
                metrics = self.kline_short_metrics(symbol, price, change)
                short_move = fnum(metrics.get("short_move"))
                change_1h = fnum(metrics.get("change_1h"))
                trend = str(metrics.get("trend", "neutral"))
                if side == "LONG":
                    if trend == "up" and short_move > 0.2 and change_1h > 0.2:
                        score += weight
                        reasons.append(f"{symbol[:-4]}短线向上")
                    elif trend == "down" and short_move < -0.4 and change_1h < -0.4:
                        score -= weight + 0.3
                        reasons.append(f"{symbol[:-4]}短线下压")
                else:
                    if trend == "down" and short_move < -0.2 and change_1h < -0.2:
                        score += weight
                        reasons.append(f"{symbol[:-4]}短线向下")
                    elif trend == "up" and short_move > 0.4 and change_1h > 0.4:
                        score -= weight + 0.3
                        reasons.append(f"{symbol[:-4]}短线反弹")
            except Exception:
                continue
        score = max(0.0, min(10.0, score))
        if score >= 6.5:
            label = "顺风"
        elif score >= float(self.config.get("market_regime_min_score", 4.5)):
            label = "中性"
        else:
            label = "逆风"
        return {"score": round(score, 1), "label": label, "reason": " / ".join(reasons[:8]) or "市场环境中性"}

    def short_structure_confirmation(
        self,
        change_4h: float,
        change_1h: float,
        short_move: float,
        trend: str,
        volume_ratio: float,
        stats: dict[str, float],
    ) -> dict[str, Any]:
        score = 0.0
        reasons: list[str] = []
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        long_short = fnum(stats.get("long_short_ratio"), 1.0)
        if trend == "down":
            score += 1.2
            reasons.append("短线均线向下")
        if short_move <= -1.2:
            score += 1.0
            reasons.append(f"15m下破 {short_move:+.1f}%")
        if change_1h <= -1.5:
            score += 1.0
            reasons.append(f"1h转弱 {change_1h:+.1f}%")
        if change_4h <= -1.2:
            score += 0.8
            reasons.append(f"4h结构下行 {change_4h:+.1f}%")
        if taker_ratio <= 0.92:
            score += 1.2
            reasons.append(f"主动卖出增强 {taker_ratio:.2f}")
        elif taker_ratio >= 1.08:
            score -= 1.0
            reasons.append(f"主动买入反抽 {taker_ratio:.2f}")
        if volume_ratio >= 1.5 and short_move < 0 and taker_ratio <= 0.96:
            score += 0.5
            reasons.append(f"放量下跌 {volume_ratio:.1f}x")
        if oi_1h >= 0:
            score += 0.4
            reasons.append(f"OI跟随 {oi_1h:+.1f}%")
        elif oi_1h < -12:
            score -= 0.4
            reasons.append(f"OI快速撤退 {oi_1h:+.1f}%")
        if long_short >= 1.4 and taker_ratio <= 0.96:
            score += 0.4
            reasons.append("多头拥挤被动回撤")
        threshold = float(self.config.get("short_structure_min_score", 4.0))
        passed = score >= threshold
        return {"score": round(max(0.0, min(6.0, score)), 1), "passed": passed, "reason": " / ".join(reasons[:8]) or "未见明确结构破坏"}

    def long_structure_confirmation(
        self,
        change_4h: float,
        change_1h: float,
        short_move: float,
        trend: str,
        volume_ratio: float,
        stats: dict[str, float],
    ) -> dict[str, Any]:
        score = 0.0
        reasons: list[str] = []
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        if trend == "up":
            score += 1.0
            reasons.append("短线均线向上")
        else:
            score -= 1.0
            reasons.append("短线均线转弱")
        if short_move >= 0.4:
            score += 0.8
            reasons.append(f"15m仍在推进 {short_move:+.1f}%")
        elif short_move <= -1.2:
            score -= 1.0
            reasons.append(f"15m回落过深 {short_move:+.1f}%")
        if change_1h >= 0.4:
            score += 0.8
            reasons.append(f"1h延续 {change_1h:+.1f}%")
        elif change_1h <= -1.0:
            score -= 0.8
            reasons.append(f"1h转弱 {change_1h:+.1f}%")
        if change_4h >= 0:
            score += 0.5
            reasons.append(f"4h结构未破 {change_4h:+.1f}%")
        elif change_4h <= -1.5:
            score -= 0.6
            reasons.append(f"4h结构回落 {change_4h:+.1f}%")
        if taker_ratio >= 1.03:
            score += 0.8
            reasons.append(f"主动买入占优 {taker_ratio:.2f}")
        elif taker_ratio <= 0.92:
            score -= 0.8
            reasons.append(f"主动买入不足 {taker_ratio:.2f}")
        if oi_1h >= -5 and short_move >= 0:
            score += 0.3
            reasons.append(f"OI未明显撤退 {oi_1h:+.1f}%")
        elif oi_1h < -12:
            score -= 0.4
            reasons.append(f"OI快速撤退 {oi_1h:+.1f}%")
        if volume_ratio >= 1.2 and short_move <= 0:
            score -= 0.5
            reasons.append(f"放量但价格未推进 {volume_ratio:.1f}x")
        threshold = float(self.config.get("long_structure_min_score", 2.5))
        bounded = round(max(0.0, min(4.0, score)), 1)
        return {"score": bounded, "passed": bounded >= threshold, "reason": " / ".join(reasons[:8]) or "多头结构数据不足"}

    def market_confirmation_score(
        self,
        side: str,
        signal_mode: str,
        change_24h: float,
        change_4h: float,
        change_1h: float,
        short_move: float,
        volume_ratio: float,
        trend: str,
        stats: dict[str, float],
    ) -> dict[str, Any]:
        score = 4.0
        reasons: list[str] = []
        caps: list[float] = []
        warnings: list[str] = []
        is_long = side == "LONG"
        direction_ok = trend == ("up" if is_long else "down")
        if direction_ok:
            score += 0.8
            reasons.append("短线趋势同向")
        else:
            score -= 1.4
            caps.append(5.8)
            reasons.append("短线趋势不同向")

        directional_24h = change_24h if is_long else -change_24h
        directional_4h = change_4h if is_long else -change_4h
        directional_1h = change_1h if is_long else -change_1h
        directional_short = short_move if is_long else -short_move
        one_hour_accel = directional_1h > 16.0
        short_accel = 5.5 < directional_short <= 9.0

        if directional_4h >= 1.2:
            score += 0.6
            reasons.append(f"4h方向延续 {directional_4h:+.1f}%")
        elif directional_4h < -0.8:
            score -= 1.0
            caps.append(5.8)
            reasons.append(f"4h反向 {directional_4h:+.1f}%")

        if 0.5 <= directional_1h <= 10.0:
            score += 0.5
            reasons.append(f"1h动量健康 {directional_1h:+.1f}%")
        elif one_hour_accel:
            reasons.append(f"1h强加速 {directional_1h:+.1f}%")
        elif directional_1h < -0.6:
            score -= 1.0
            caps.append(6.0)
            reasons.append(f"1h转弱 {directional_1h:+.1f}%")

        if 0.4 <= directional_short <= 5.5:
            score += 0.5
            reasons.append(f"短线突破 {directional_short:+.1f}%")
        elif short_accel:
            reasons.append(f"短线强加速 {directional_short:+.1f}%")
        elif directional_short < -0.4:
            score -= 1.2
            caps.append(5.8)
            reasons.append(f"短线反向 {directional_short:+.1f}%")

        funding = fnum(stats.get("funding_rate_pct"))
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        oi_24h = fnum(stats.get("open_interest_24h_pct"))
        long_short = fnum(stats.get("long_short_ratio"), 1.0)
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        funding_aligned = funding if is_long else -funding
        taker_aligned = taker_ratio if is_long else (1.0 / taker_ratio if taker_ratio > 0 else 1.0)
        crowd_ratio = long_short if is_long else (1.0 / long_short if long_short > 0 else 1.0)
        funding_crowded = funding_aligned > 0.08
        crowd_extreme = crowd_ratio > 3.2 or crowd_ratio < 0.55
        oi_spike = oi_1h > 60
        momentum_push = directional_short >= 0.4 and directional_1h >= 0.5 and directional_4h >= 1.2
        taker_strong = taker_aligned >= 1.05
        taker_weak = taker_aligned < 0.92
        volume_state = self.directional_volume_state(side, change_1h, short_move, volume_ratio, stats)

        if bool(volume_state["aligned"]):
            score += 0.6
            reasons.append(f"方向放量 {volume_ratio:.1f}x/1h {fnum(volume_state['volume_1h_ratio']):.1f}x")
        elif bool(volume_state["counter"]):
            score -= 0.6
            caps.append(5.8)
            reasons.append(f"反向放量 {volume_ratio:.1f}x")
        elif volume_ratio > 7.5:
            score -= 0.2
            warnings.append("volume overheated")
            reasons.append(f"量能极强 {volume_ratio:.1f}x")
        elif volume_ratio >= 1.2:
            score -= 0.1
            reasons.append(f"放量待确认 {volume_ratio:.1f}x")
        else:
            score -= 0.7
            reasons.append(f"量能不足 {volume_ratio:.1f}x")

        if one_hour_accel:
            if taker_strong and momentum_push:
                score += 0.2
                reasons.append("1h强加速且主动成交同向")
            elif taker_weak:
                score -= 0.8
                warnings.append("1h加速失速")
                caps.append(6.0)
                reasons.append("1h强加速但主动成交反向")
            else:
                score -= 0.3
                warnings.append("1h加速待确认")
                reasons.append("1h强加速但主动成交不明显")
        if short_accel:
            if taker_strong and momentum_push:
                score += 0.2
                reasons.append("短线强加速且主动成交同向")
            elif taker_weak:
                score -= 0.8
                warnings.append("短线加速失速")
                caps.append(6.0)
                reasons.append("短线强加速但主动成交反向")
            else:
                score -= 0.2
                warnings.append("短线加速待确认")
                reasons.append("短线强加速但主动成交不明显")

        if abs(funding) <= 0.08:
            score += 0.4
            reasons.append(f"资金费率可接受 {funding:+.3f}%")
        elif funding_crowded and taker_strong and momentum_push:
            score += 0.1
            warnings.append("资金费率同向拥挤")
            reasons.append(f"费率拥挤但仍推进 {funding:+.3f}%")
        elif funding_crowded:
            score -= 0.4
            warnings.append("资金费率同向拥挤")
            reasons.append(f"同向资金费率偏热 {funding:+.3f}%")
        else:
            score += 0.2
            reasons.append(f"逆向资金费率不拥挤 {funding:+.3f}%")

        if 0 <= oi_1h <= 35 or 0 <= oi_24h <= 70:
            score += 0.7
            reasons.append(f"OI跟随增加 1h {oi_1h:+.1f}%")
        elif oi_1h < -8:
            score -= 1.0
            caps.append(6.2)
            reasons.append(f"OI承接下降 {oi_1h:+.1f}%")
        elif oi_spike and taker_strong and momentum_push:
            score += 0.2
            warnings.append("OI急增")
            reasons.append(f"OI急增但价格推进 {oi_1h:+.1f}%")
        elif oi_spike:
            score -= 0.3
            warnings.append("OI急增")
            reasons.append(f"OI急增 {oi_1h:+.1f}%")

        if taker_strong:
            score += 0.9
            reasons.append(f"主动成交同向 {taker_ratio:.2f}")
        elif taker_weak:
            score -= 1.2
            caps.append(6.0)
            reasons.append(f"主动成交反向 {taker_ratio:.2f}")
        else:
            score -= 0.2
            caps.append(6.4)
            reasons.append(f"主动成交不明显 {taker_ratio:.2f}")

        if 0.75 <= crowd_ratio <= 2.3:
            score += 0.5
            reasons.append(f"多空拥挤度可接受 {long_short:.2f}")
        elif 2.3 < crowd_ratio <= 3.2:
            if taker_strong and momentum_push:
                score += 0.1
                reasons.append(f"多空偏挤但仍推进 {long_short:.2f}")
            else:
                score -= 0.2
            warnings.append("多空比偏挤")
        else:
            if taker_strong and momentum_push:
                score -= 0.1
                warnings.append("多空比极端")
                reasons.append(f"多空极端但仍推进 {long_short:.2f}")
            else:
                score -= 0.8
                warnings.append("多空比极端")
                reasons.append(f"多空比极端 {long_short:.2f}")

        if directional_24h >= float(self.config.get("min_abs_24h_change_pct", 6.0)):
            score += 0.3
            reasons.append(f"24h强波动确认 {directional_24h:+.1f}%")

        exhausted_crowd = (funding_crowded or crowd_extreme) and (taker_weak or directional_short < 0.4 or directional_1h < 0.5)
        if exhausted_crowd:
            caps.append(6.2)
            reasons.append("拥挤后推进不足")
        if oi_spike and (taker_weak or directional_short < 0.4):
            caps.append(6.0)
            reasons.append("OI急增但成交/价格不配合")
        if funding_crowded and crowd_extreme and not taker_strong:
            caps.append(6.4)
            reasons.append("费率和多空同时拥挤但主动成交不足")
        if len(warnings) >= 3 and not (taker_strong and momentum_push):
            caps.append(6.8)
            reasons.append(f"过热项较多：{'/'.join(warnings[:3])}")
        if caps:
            cap = min(caps)
            if score > cap:
                reasons.append(f"确认分封顶 {cap:.1f}")
                score = cap
        score = max(0.0, min(10.0, score))
        if score >= 7.5:
            label = "强确认"
        elif score >= self.market_confirm_min_score_for_mode(signal_mode, side):
            label = "通过"
        elif score >= 5.0:
            label = "偏弱"
        else:
            label = "不足"
        return {"score": round(score, 1), "label": label, "reason": " / ".join(reasons[:8])}

    def pre_start_confirmation_score(
        self,
        change_24h: float,
        change_1h: float,
        short_move: float,
        volume_ratio: float,
        trend: str,
        stats: dict[str, float],
        price: float,
        high_24h: float,
    ) -> dict[str, Any]:
        score = 0.0
        reasons: list[str] = []
        min_24h = float(self.config.get("pre_start_24h_min_pct", 1.0))
        max_24h = float(self.config.get("pre_start_24h_max_pct", 12.0))
        min_15m = float(self.config.get("pre_start_15m_min_pct", 0.4))
        max_15m = float(self.config.get("pre_start_15m_max_pct", 4.0))
        min_1h = float(self.config.get("pre_start_1h_min_pct", 0.8))
        max_1h = float(self.config.get("pre_start_1h_max_pct", 8.0))
        min_volume = float(self.config.get("pre_start_volume_ratio_min", 1.5))
        max_volume = float(self.config.get("pre_start_volume_ratio_max", 5.0))
        min_taker = float(self.config.get("pre_start_taker_ratio_min", 1.05))
        min_oi = float(self.config.get("pre_start_oi_1h_min_pct", 0.0))
        max_funding = float(self.config.get("pre_start_funding_abs_max_pct", 0.06))
        max_long_short = float(self.config.get("pre_start_long_short_max", 2.5))
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)

        if min_24h <= change_24h <= max_24h:
            score += 1.0
            reasons.append(f"24h温和启动 {change_24h:+.1f}%")
        if min_15m <= short_move <= max_15m:
            score += 1.5
            reasons.append(f"15m温和推进 {short_move:+.1f}%")
        if min_1h <= change_1h <= max_1h:
            score += 1.5
            reasons.append(f"1h温和推进 {change_1h:+.1f}%")
        if trend == "up":
            score += 1.0
            reasons.append("短线趋势向上")
        if min_volume <= volume_ratio <= max_volume and taker_ratio >= min_taker and short_move > 0 and change_1h > 0:
            score += 1.2
            reasons.append(f"量能刚放大 {volume_ratio:.1f}x")

        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        funding = abs(fnum(stats.get("funding_rate_pct")))
        long_short = fnum(stats.get("long_short_ratio"), 1.0)
        if taker_ratio >= min_taker:
            score += 1.2
            reasons.append(f"主动买入转强 {taker_ratio:.2f}")
        if oi_1h >= min_oi:
            score += 0.8
            reasons.append(f"OI跟随 {oi_1h:+.1f}%")
        if funding <= max_funding:
            score += 0.8
            reasons.append(f"资金费率未过热 {funding:.3f}%")
        if 0.75 <= long_short <= max_long_short:
            score += 0.7
            reasons.append(f"多空比未拥挤 {long_short:.2f}")
        if high_24h > 0 and price > 0:
            distance_from_high = max(0.0, -pct(price, high_24h))
            if distance_from_high >= 1.0:
                score += 0.3
                reasons.append(f"距离24h高点 {distance_from_high:.1f}%")

        score = max(0.0, min(10.0, score))
        threshold = float(self.config.get("pre_start_market_confirm_min_score", 6.5))
        if score >= 8.0:
            label = "强确认"
        elif score >= threshold:
            label = "通过"
        elif score >= 5.0:
            label = "偏弱"
        else:
            label = "不足"
        return {"score": round(score, 1), "label": label, "reason": " / ".join(reasons[:8])}

    def old_rebound_short_confirmation_score(
        self,
        change_24h: float,
        change_1h: float,
        short_move: float,
        volume_ratio: float,
        trend: str,
        stats: dict[str, float],
        live_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        score = 0.0
        reasons: list[str] = []
        live_metrics = live_metrics or {}
        min_24h = float(self.config.get("old_rebound_short_24h_min_pct", 8.0))
        max_24h = float(self.config.get("old_rebound_short_24h_max_pct", 35.0))
        max_15m = float(self.config.get("old_rebound_short_15m_max_pct", 0.3))
        max_1h = float(self.config.get("old_rebound_short_1h_max_pct", 0.5))
        min_volume = float(self.config.get("old_rebound_short_volume_ratio_min", 0.8))
        max_volume = float(self.config.get("old_rebound_short_volume_ratio_max", 6.0))
        max_taker = float(self.config.get("old_rebound_short_taker_ratio_max", 0.95))
        min_oi = float(self.config.get("old_rebound_short_oi_1h_min_pct", -5.0))
        max_funding = float(self.config.get("old_rebound_short_funding_abs_max_pct", 0.08))
        min_long_short = float(self.config.get("old_rebound_short_long_short_min", 1.2))
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)

        if min_24h <= change_24h <= max_24h:
            score += 1.0
            reasons.append(f"老币24h反弹 {change_24h:+.1f}%")
        if short_move <= max_15m:
            score += 1.3
            reasons.append(f"15m开始转弱 {short_move:+.1f}%")
        if change_1h <= max_1h:
            score += 1.2
            reasons.append(f"1h反弹衰竭 {change_1h:+.1f}%")
        if trend == "down":
            score += 1.0
            reasons.append("短线趋势转下")
        elif trend != "up":
            score += 0.4
            reasons.append("短线不再上推")
        if "live_short_move" in live_metrics and short_move <= max_15m:
            live_short_move = fnum(live_metrics.get("live_short_move"))
            if live_short_move <= 0:
                score += 0.2
                reasons.append(f"15m实时继续下压 {live_short_move:+.1f}%")
            elif live_short_move > max_15m:
                score -= 0.4
                reasons.append(f"15m实时反抽 {live_short_move:+.1f}%")
        if "live_change_1h" in live_metrics and change_1h <= max_1h:
            live_change_1h = fnum(live_metrics.get("live_change_1h"))
            if live_change_1h <= 0:
                score += 0.2
                reasons.append(f"1h实时继续下压 {live_change_1h:+.1f}%")
            elif live_change_1h > max_1h:
                score -= 0.3
                reasons.append(f"1h实时反抽 {live_change_1h:+.1f}%")
        if min_volume <= volume_ratio <= max_volume and taker_ratio <= max_taker and short_move <= max_15m and change_1h <= max_1h:
            score += 0.6
            reasons.append(f"量能可交易 {volume_ratio:.1f}x")

        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        funding = abs(fnum(stats.get("funding_rate_pct")))
        long_short = fnum(stats.get("long_short_ratio"), 1.0)
        if taker_ratio <= max_taker:
            score += 1.3
            reasons.append(f"主动卖出占优 {taker_ratio:.2f}")
        if oi_1h >= min_oi:
            score += 0.7
            reasons.append(f"OI未快速撤退 {oi_1h:+.1f}%")
        if funding <= max_funding:
            score += 0.6
            reasons.append(f"费率可接受 {funding:.3f}%")
        if long_short >= min_long_short:
            score += 1.0
            reasons.append(f"多头拥挤 {long_short:.2f}")

        score = max(0.0, min(10.0, score))
        threshold = float(self.config.get("old_rebound_short_confirm_min_score", 6.8))
        if score >= 8.0:
            label = "强确认"
        elif score >= threshold:
            label = "通过"
        elif score >= 5.0:
            label = "偏弱"
        else:
            label = "不足"
        return {"score": round(score, 1), "label": label, "reason": " / ".join(reasons[:8])}

    def high_pullback_short_confirmation_score(
        self,
        change_24h: float,
        change_1h: float,
        short_move: float,
        volume_ratio: float,
        trend: str,
        stats: dict[str, float],
        price: float,
        high_24h: float,
        low_24h: float,
    ) -> dict[str, Any]:
        score = 0.0
        reasons: list[str] = []
        min_24h = float(self.config.get("high_pullback_short_24h_min_pct", 8.0))
        max_24h = float(self.config.get("high_pullback_short_24h_max_pct", 60.0))
        min_drawdown = float(self.config.get("high_pullback_short_min_drawdown_pct", 18.0))
        max_drawdown = float(self.config.get("high_pullback_short_max_drawdown_pct", 45.0))
        min_low_distance = float(self.config.get("high_pullback_short_min_low_distance_pct", 5.0))
        max_15m = float(self.config.get("high_pullback_short_15m_max_pct", 0.3))
        max_1h = float(self.config.get("high_pullback_short_1h_max_pct", 0.5))
        min_volume = float(self.config.get("high_pullback_short_volume_ratio_min", 0.8))
        max_volume = float(self.config.get("high_pullback_short_volume_ratio_max", 6.0))
        max_taker = float(self.config.get("high_pullback_short_taker_ratio_max", 0.95))
        min_oi = float(self.config.get("high_pullback_short_oi_1h_min_pct", -5.0))
        max_funding = float(self.config.get("high_pullback_short_funding_abs_max_pct", 0.08))
        drawdown = max(0.0, -pct(price, high_24h)) if high_24h > 0 and price > 0 else 0.0
        low_distance = pct(price, low_24h) if low_24h > 0 and price > 0 else 999.0
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        funding = abs(fnum(stats.get("funding_rate_pct")))

        if min_24h <= change_24h <= max_24h:
            score += 1.0
            reasons.append(f"24h仍上涨 {change_24h:+.1f}%")
        if min_drawdown <= drawdown <= max_drawdown:
            score += 1.5
            reasons.append(f"高点回撤 {drawdown:.1f}%")
        if low_distance >= min_low_distance:
            score += 0.6
            reasons.append(f"远离24h低点 {low_distance:.1f}%")
        if short_move <= max_15m:
            score += 1.1
            reasons.append(f"15m回落/滞涨 {short_move:+.1f}%")
        if change_1h <= max_1h:
            score += 1.1
            reasons.append(f"1h上攻衰竭 {change_1h:+.1f}%")
        if trend == "down":
            score += 1.0
            reasons.append("短线趋势转下")
        elif trend != "up":
            score += 0.5
            reasons.append("短线不再上推")
        if min_volume <= volume_ratio <= max_volume and taker_ratio <= max_taker:
            score += 0.7
            reasons.append(f"量能配合回落 {volume_ratio:.1f}x")
        if taker_ratio <= max_taker:
            score += 1.2
            reasons.append(f"主动卖出占优 {taker_ratio:.2f}")
        if oi_1h >= min_oi:
            score += 0.5
            reasons.append(f"OI未快速撤退 {oi_1h:+.1f}%")
        if funding <= max_funding:
            score += 0.5
            reasons.append(f"费率可接受 {funding:.3f}%")

        score = max(0.0, min(10.0, score))
        threshold = float(self.config.get("high_pullback_short_confirm_min_score", 7.0))
        if score >= 8.0:
            label = "强确认"
        elif score >= threshold:
            label = "通过"
        elif score >= 5.0:
            label = "偏弱"
        else:
            label = "不足"
        return {"score": round(score, 1), "label": label, "reason": " / ".join(reasons[:8])}

    def analyzer_confirmation(self, symbol: str, system_side: str = "", signal_mode: str = "") -> dict[str, Any]:
        if not self.config.get("analyzer_confirmation_enabled", True):
            return {"direction": "关闭", "confidence": 0.0, "reason": "分析器确认未启用"}
        now = time.time()
        cached = self.analyzer_cache.get(symbol)
        ttl = max(30, int(self.config.get("analyzer_cache_ttl_sec", 300)))
        if cached and now - fnum(cached.get("time")) <= ttl:
            return cached
        failed_ttl = max(ttl, int(self.config.get("analyzer_failed_cache_ttl_sec", 120)))
        if cached and now - fnum(cached.get("time")) <= failed_ttl:
            direction = str(cached.get("direction", "等待"))
            confidence = fnum(cached.get("confidence"))
            min_confidence = self.analyzer_min_confidence_for_mode(signal_mode)
            if direction in {"错误", "观望"} or confidence <= min_confidence:
                return cached
            if system_side and self.analyzer_reject_reason(system_side, cached, signal_mode):
                return cached
        self.enqueue_analyzer(symbol)
        return {"direction": "等待", "confidence": 0.0, "reason": "分析器后台分析中"}

    def enqueue_analyzer(self, symbol: str) -> None:
        if symbol in self.analyzer_inflight:
            return
        self.analyzer_inflight.add(symbol)
        self.analyzer_executor.submit(self.run_analyzer_job, symbol)

    def analyze_symbol_now(self, symbol: str) -> dict[str, Any]:
        confirmer = load_fast_confirmer()
        analyzer_config = confirmer.load_config(self.config_path)
        analyzer_config["request_timeout_sec"] = min(
            4.0,
            float(self.config.get("request_timeout_sec", 6)),
        )
        analysis = analyzer_config.setdefault("analysis", {})
        analysis["request_timeout_sec"] = min(
            4.0,
            float(self.config.get("request_timeout_sec", 6)),
        )
        analysis.setdefault("kline_intervals", ["15m", "1h", "4h"])
        analysis.setdefault("kline_limit", 180)
        fast_result = confirmer.analyze_symbol_fast(analyzer_config, symbol)
        return {
            "time": time.time(),
            "source": FAST_CONFIRM_SOURCE,
            "direction": fast_result["direction"],
            "confidence": float(fast_result["confidence"]),
            "reason": fast_result["reason"],
            "long_score": float(fast_result["long_score"]),
            "short_score": float(fast_result["short_score"]),
        }

    def save_analyzer_result(self, symbol: str, result: dict[str, Any]) -> None:
        self.analyzer_cache[symbol] = result
        self.state.setdefault("analyzer_cache", {})[symbol] = result
        for candidate in self.state.get("candidates", []):
            if candidate.get("symbol") == symbol:
                candidate["analyzer_direction"] = result.get("direction", "等待")
                candidate["analyzer_confidence_pct"] = round(fnum(result.get("confidence")), 1)
                candidate["analyzer_reason"] = result.get("reason", "")
                updated_reject = self.analyzer_reject_reason(
                    str(candidate.get("side", "")),
                    result,
                    str(candidate.get("signal_mode", "")),
                )
                current_reject = str(candidate.get("reject_reason", ""))
                if current_reject == "等待分析器确认" or current_reject.startswith("分析器"):
                    candidate["reject_reason"] = updated_reject
                    candidate["decision_summary"] = self.decision_summary(updated_reject)
                    self.sync_candidate_decision_steps(candidate)
        self.try_open_pending_signal(symbol, result)
        self.persist()

    def run_analyzer_job(self, symbol: str) -> None:
        try:
            result = self.analyze_symbol_now(symbol)
        except Exception as exc:
            result = {
                "time": time.time(),
                "source": FAST_CONFIRM_SOURCE,
                "direction": "错误",
                "confidence": 0.0,
                "reason": str(exc),
                "long_score": 0.0,
                "short_score": 0.0,
            }
        with self.lock:
            self.analyzer_inflight.discard(symbol)
            self.save_analyzer_result(symbol, result)

    @staticmethod
    def analyzer_side(direction: str) -> str:
        if direction == "做多":
            return "LONG"
        if direction == "做空":
            return "SHORT"
        return ""

    def analyzer_min_confidence_for_mode(self, signal_mode: str = "") -> float:
        return float(self.config.get("analyzer_min_confidence_pct", 90.0))

    def analyzer_reject_reason(self, system_side: str, result: dict[str, Any], signal_mode: str = "") -> str:
        direction = str(result.get("direction", "等待"))
        confidence = fnum(result.get("confidence"))
        reason = str(result.get("reason", ""))
        analyzer_side = self.analyzer_side(direction)
        min_confidence = self.analyzer_min_confidence_for_mode(signal_mode)
        if direction == "等待":
            return "等待分析器确认"
        if direction == "错误":
            return f"分析器确认失败：{reason}"
        if analyzer_side != system_side:
            return f"分析器{direction}，与系统{self.side_cn(system_side)}不一致"
        if confidence <= min_confidence:
            return f"分析器置信度 {confidence:.1f}% 未超过 {min_confidence:.0f}%"
        return ""

    def refresh_candidate_analyzer_cache(self) -> None:
        now = time.time()
        ttl = max(30, int(self.config.get("analyzer_cache_ttl_sec", 300)))
        failed_ttl = max(ttl, int(self.config.get("analyzer_failed_cache_ttl_sec", 120)))
        for candidate in self.state.get("candidates", []):
            symbol = str(candidate.get("symbol", ""))
            result = self.analyzer_cache.get(symbol)
            if not result:
                continue
            if result.get("source") != FAST_CONFIRM_SOURCE:
                continue
            age = now - fnum(result.get("time"))
            cached_reject = self.analyzer_reject_reason(
                str(candidate.get("side", "")),
                result,
                str(candidate.get("signal_mode", "")),
            )
            valid_ttl = failed_ttl if cached_reject else ttl
            if age > valid_ttl:
                continue
            candidate["analyzer_direction"] = result.get("direction", "等待")
            candidate["analyzer_confidence_pct"] = round(fnum(result.get("confidence")), 1)
            candidate["analyzer_reason"] = result.get("reason", "")
            reject = str(candidate.get("reject_reason", ""))
            if reject == "等待分析器确认" or reject.startswith("分析器"):
                candidate["reject_reason"] = cached_reject
                self.sync_candidate_decision_steps(candidate)

    def update_pending_open_signals(self, signals: list[Signal]) -> None:
        if not self.config.get("pending_open_queue_enabled", True):
            self.state["pending_open_signals"] = {}
            return
        ttl = max(30, int(self.config.get("pending_open_queue_ttl_sec", 180)))
        now = time.time()
        pending = dict(self.state.get("pending_open_signals", {}))
        for symbol, item in list(pending.items()):
            if now - fnum(item.get("queued_at")) > ttl:
                pending.pop(symbol, None)
        for signal in signals:
            if signal.reject_reason != "等待分析器确认":
                continue
            if signal.price <= 0:
                continue
            pending[signal.symbol] = {
                "queued_at": now,
                "signal": signal.__dict__,
            }
        self.state["pending_open_signals"] = pending

    def signal_from_dict(self, payload: dict[str, Any]) -> Signal:
        fields = Signal.__dataclass_fields__
        data = {name: payload.get(name) for name in fields}
        numeric_defaults = {
            "score": 0.0,
            "price": 0.0,
            "quote_volume": 0.0,
            "change_24h_pct": 0.0,
            "change_4h_pct": 0.0,
            "change_1h_pct": 0.0,
            "short_move_pct": 0.0,
            "volume_ratio": 1.0,
            "open_interest_usdt": 0.0,
            "open_interest_24h_pct": 0.0,
            "open_interest_4h_pct": 0.0,
            "open_interest_1h_pct": 0.0,
            "funding_rate_pct": 0.0,
            "long_short_ratio": 1.0,
            "volume_4h_ratio": 0.0,
            "taker_long_short_ratio": 1.0,
            "oi_volume_ratio": 0.0,
            "market_regime_score": 10.0,
            "analyzer_confidence_pct": 0.0,
            "planned_stop_price": 0.0,
            "planned_take_profit_price": 0.0,
        }
        for key, default in numeric_defaults.items():
            data[key] = fnum(data.get(key), default)
        for key in fields:
            if key == "decision_steps":
                data[key] = list(data.get(key) or [])
            elif data.get(key) is None:
                data[key] = ""
        return Signal(**data)

    def try_open_pending_signal(self, symbol: str, analyzer_result: dict[str, Any]) -> None:
        if not self.config.get("pending_open_queue_enabled", True):
            return
        pending = self.state.setdefault("pending_open_signals", {})
        item = pending.get(symbol)
        if not item:
            return
        queued_at = fnum(item.get("queued_at"))
        ttl = max(30, int(self.config.get("pending_open_queue_ttl_sec", 180)))
        if time.time() - queued_at > ttl:
            pending.pop(symbol, None)
            return
        signal_payload = item.get("signal") if isinstance(item, dict) else None
        if not isinstance(signal_payload, dict):
            pending.pop(symbol, None)
            return
        signal = self.signal_from_dict(signal_payload)
        reject = self.analyzer_reject_reason(signal.side, analyzer_result, signal.signal_mode)
        if reject:
            if reject != "等待分析器确认":
                pending.pop(symbol, None)
            return
        pending[symbol] = item

    def collect_pending_open_signals(self) -> list[Signal]:
        if not self.config.get("pending_open_queue_enabled", True):
            self.state["pending_open_signals"] = {}
            return []
        pending = self.state.setdefault("pending_open_signals", {})
        ready: list[Signal] = []
        for symbol in list(pending.keys()):
            result = self.analyzer_cache.get(symbol)
            if not result:
                continue
            item = pending.get(symbol)
            queued_at = fnum(item.get("queued_at")) if isinstance(item, dict) else 0.0
            ttl = max(30, int(self.config.get("pending_open_queue_ttl_sec", 180)))
            if time.time() - queued_at > ttl:
                pending.pop(symbol, None)
                continue
            signal_payload = item.get("signal") if isinstance(item, dict) else None
            if not isinstance(signal_payload, dict):
                pending.pop(symbol, None)
                continue
            signal = self.signal_from_dict(signal_payload)
            reject = self.analyzer_reject_reason(signal.side, result, signal.signal_mode)
            if reject:
                if reject != "等待分析器确认":
                    pending.pop(symbol, None)
                continue
            signal.reject_reason = ""
            signal.decision_summary = self.decision_summary("")
            signal.analyzer_direction = str(result.get("direction", signal.analyzer_direction))
            signal.analyzer_confidence_pct = round(fnum(result.get("confidence")), 1)
            signal.analyzer_reason = str(result.get("reason", signal.analyzer_reason))
            signal.decision_steps.append(
                f"待开仓队列：分析器结果返回，{signal.analyzer_direction} {signal.analyzer_confidence_pct:.1f}%，尝试补开"
            )
            ready.append(signal)
            pending.pop(symbol, None)
        return ready

    def demo_tickers(self) -> list[dict[str, Any]]:
        symbols = ["BLESSUSDT", "BEATUSDT", "SAHARAUSDT", "VELVETUSDT", "OPNUSDT", "EPICUSDT", "ZKJUSDT"]
        minute = int(time.time() // 60)
        result = []
        for idx, symbol in enumerate(symbols):
            rng = random.Random(minute // 2 + idx * 97)
            base = [0.0068, 3.94, 0.015, 0.317, 0.088, 0.48, 0.19][idx]
            drift = math.sin(minute / 7 + idx) * 0.045 + rng.uniform(-0.018, 0.018)
            change = [-13.2, -9.5, 8.4, -16.8, 7.7, -10.3, 6.9][idx] + rng.uniform(-2.0, 2.0)
            result.append(
                {
                    "symbol": symbol,
                    "lastPrice": f"{max(0.000001, base * (1 + drift)):.8f}",
                    "priceChangePercent": f"{change:.2f}",
                    "quoteVolume": f"{(24 + idx * 9 + rng.random() * 16) * 1_000_000:.2f}",
                }
            )
        return result

    def signal_mode_label(self, signal_mode: str) -> str:
        if signal_mode == "high_pullback_short":
            return "高位回落做空"
        if signal_mode == "old_rebound_short":
            return "老币反弹做空"
        if signal_mode == "pre_start_long":
            return "预启动做多"
        return "强波动"

    def fallback_short_metrics(self, change: float) -> dict[str, Any]:
        return {
            "short_move": change / 12.0,
            "change_1h": change / 12.0,
            "change_4h": change / 6.0,
            "volume_ratio": 1.0 + min(2.0, abs(change) / 20.0),
            "trend": "up" if change > 0 else "down",
            "price": 0.0,
            "enriched": False,
        }

    def pre_start_kline_reject_reason(self, short_move: float, change_1h: float, volume_ratio: float, trend: str) -> str:
        min_15m = float(self.config.get("pre_start_15m_min_pct", 0.4))
        max_15m = float(self.config.get("pre_start_15m_max_pct", 4.0))
        min_1h = float(self.config.get("pre_start_1h_min_pct", 0.8))
        max_1h = float(self.config.get("pre_start_1h_max_pct", 8.0))
        min_volume = float(self.config.get("pre_start_volume_ratio_min", 1.5))
        max_volume = float(self.config.get("pre_start_volume_ratio_max", 5.0))
        if trend != "up":
            return "预启动趋势未向上"
        if short_move < min_15m:
            return f"预启动15m涨幅 {short_move:.1f}% 低于 {min_15m:.1f}%"
        if short_move > max_15m:
            return f"预启动15m涨幅 {short_move:.1f}% 高于 {max_15m:.1f}%"
        if change_1h < min_1h:
            return f"预启动1h涨幅 {change_1h:.1f}% 低于 {min_1h:.1f}%"
        if change_1h > max_1h:
            return f"预启动1h涨幅 {change_1h:.1f}% 高于 {max_1h:.1f}%"
        if volume_ratio < min_volume:
            return f"预启动量能 {volume_ratio:.1f}x 低于 {min_volume:.1f}x"
        if volume_ratio > max_volume:
            return f"预启动量能 {volume_ratio:.1f}x 高于 {max_volume:.1f}x"
        return ""

    def pre_start_market_stats_reject_reason(self, stats: dict[str, float]) -> str:
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        funding = abs(fnum(stats.get("funding_rate_pct")))
        long_short = fnum(stats.get("long_short_ratio"), 1.0)
        min_taker = float(self.config.get("pre_start_taker_ratio_min", 1.05))
        min_oi = float(self.config.get("pre_start_oi_1h_min_pct", 0.0))
        max_funding = float(self.config.get("pre_start_funding_abs_max_pct", 0.06))
        max_long_short = float(self.config.get("pre_start_long_short_max", 2.5))
        if taker_ratio < min_taker:
            return f"预启动主动买入不足 {taker_ratio:.2f}/{min_taker:.2f}"
        if oi_1h < min_oi:
            return f"预启动OI未跟随 {oi_1h:+.1f}%/{min_oi:+.1f}%"
        if funding > max_funding:
            return f"预启动资金费率过热 {funding:.3f}%/{max_funding:.3f}%"
        if long_short > max_long_short:
            return f"预启动多空比过热 {long_short:.2f}/{max_long_short:.2f}"
        return ""

    def has_symbol_history(self, symbol: str, min_days: int) -> bool:
        if min_days <= 0:
            return True
        try:
            klines = self.fetch_klines_interval(symbol, "1d", min(1000, max(1, min_days)))
            return len(klines) >= min_days
        except Exception:
            return False

    def old_rebound_short_kline_reject_reason(
        self,
        symbol: str,
        short_move: float,
        change_1h: float,
        volume_ratio: float,
        trend: str,
        live_metrics: dict[str, Any] | None = None,
        high_24h: float = 0.0,
        low_24h: float = 0.0,
        current_price: float = 0.0,
    ) -> str:
        min_days = int(self.config.get("old_rebound_short_min_history_days", 365))
        max_15m = float(self.config.get("old_rebound_short_15m_max_pct", 0.3))
        max_1h = float(self.config.get("old_rebound_short_1h_max_pct", 0.5))
        min_15m = float(self.config.get("old_rebound_short_15m_min_pct", -3.0))
        min_1h = float(self.config.get("old_rebound_short_1h_min_pct", -6.0))
        min_volume = float(self.config.get("old_rebound_short_volume_ratio_min", 0.8))
        max_volume = float(self.config.get("old_rebound_short_volume_ratio_max", 6.0))
        live_metrics = live_metrics or {}
        live_note = ""
        if fnum(live_metrics.get("live_short_move"), 999.0) <= 0 or fnum(live_metrics.get("live_change_1h"), 999.0) <= 0:
            live_parts = []
            if "live_short_move" in live_metrics:
                live_parts.append(f"15m {fnum(live_metrics.get('live_short_move')):+.1f}%")
            if "live_change_1h" in live_metrics:
                live_parts.append(f"1h {fnum(live_metrics.get('live_change_1h')):+.1f}%")
            live_note = f"（实时转弱观察中 {' / '.join(live_parts)}，不单独放行）"
        if not self.has_symbol_history(symbol, min_days):
            return f"老币反弹历史不足 {min_days} 天"
        if int(live_metrics.get("closed_15m_count", 0)) < 30:
            return "老币反弹15m已收盘K线不足，禁止用24h估算开空"
        if int(live_metrics.get("closed_1h_count", 0)) < 30:
            return "老币反弹1h已收盘K线不足，禁止用24h估算开空"
        if short_move < min_15m or change_1h < min_1h:
            return f"老币反弹跌幅过深，禁止追空：15m {short_move:+.1f}% / 1h {change_1h:+.1f}%"
        price = fnum(live_metrics.get("price"))
        if price <= 0:
            return "老币反弹K线价格无效"
        if (current_price > 0 or high_24h > 0 or low_24h > 0) and (high_24h <= 0 or low_24h <= 0):
            return "老币反弹24h高低点数据缺失，暂停开空"
        location_price = current_price if current_price > 0 else price
        if high_24h > 0 and max(0.0, -pct(location_price, high_24h)) >= float(self.config.get("old_rebound_short_max_high_drawdown_pct", 35.0)):
            return "老币反弹距24h高点回撤过深，禁止追空"
        if low_24h > 0 and pct(location_price, low_24h) <= float(self.config.get("old_rebound_short_min_low_distance_pct", 5.0)):
            return "老币反弹太靠近24h低点，禁止追空"
        if current_price > 0 and pct(current_price, price) < min_15m:
            return "老币反弹15m实时跌幅过深，禁止追空"
        if short_move > max_15m:
            return f"老币反弹15m未转弱 {short_move:.1f}%/{max_15m:.1f}%{live_note}"
        if change_1h > max_1h:
            return f"老币反弹1h未转弱 {change_1h:.1f}%/{max_1h:.1f}%{live_note}"
        if short_move >= 0:
            return "老币反弹15m未明确转弱，短暂横盘或微涨不允许开空"
        if live_metrics.get("trend_1h") == "up":
            return "老币反弹1h趋势仍上行，等待转弱"
        previous_low = fnum(live_metrics.get("closed_15m_previous_low"))
        break_min_pct = float(self.config.get("old_rebound_short_15m_break_min_pct", 0.2))
        if previous_low <= 0 or pct(price, previous_low) > -break_min_pct:
            return f"老币反弹15m未确认破位，需跌破前3根已收盘K线低点至少 {break_min_pct:.1f}%"
        if current_price > 0 and current_price >= previous_low:
            return "老币反弹当前价站回破位线，暂停开空"
        if "live_short_move" in live_metrics:
            live_price = price * (1 + fnum(live_metrics["live_short_move"]) / 100)
            if live_price >= previous_low:
                return "老币反弹15m实时反抽站回破位线，暂停开空"
        if volume_ratio < min_volume:
            return f"老币反弹量能不足 {volume_ratio:.1f}x/{min_volume:.1f}x"
        if volume_ratio > max_volume:
            return f"老币反弹量能过热 {volume_ratio:.1f}x/{max_volume:.1f}x"
        return ""

    def high_pullback_short_kline_reject_reason(self, short_move: float, change_1h: float, volume_ratio: float, trend: str) -> str:
        max_15m = float(self.config.get("high_pullback_short_15m_max_pct", 0.3))
        max_1h = float(self.config.get("high_pullback_short_1h_max_pct", 0.5))
        min_volume = float(self.config.get("high_pullback_short_volume_ratio_min", 0.8))
        max_volume = float(self.config.get("high_pullback_short_volume_ratio_max", 6.0))
        if trend == "up" and short_move > 0 and change_1h > 0:
            return "高位回落仍在短线推升"
        if short_move > max_15m:
            return f"高位回落15m未转弱 {short_move:.1f}%/{max_15m:.1f}%"
        if change_1h > max_1h:
            return f"高位回落1h未衰竭 {change_1h:.1f}%/{max_1h:.1f}%"
        if volume_ratio < min_volume:
            return f"高位回落量能不足 {volume_ratio:.1f}x/{min_volume:.1f}x"
        if volume_ratio > max_volume:
            return f"高位回落量能过热 {volume_ratio:.1f}x/{max_volume:.1f}x"
        return ""

    def old_rebound_short_market_stats_reject_reason(self, stats: dict[str, float]) -> str:
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        funding = abs(fnum(stats.get("funding_rate_pct")))
        long_short = fnum(stats.get("long_short_ratio"), 1.0)
        max_taker = float(self.config.get("old_rebound_short_taker_ratio_max", 0.95))
        min_oi = float(self.config.get("old_rebound_short_oi_1h_min_pct", -5.0))
        max_funding = float(self.config.get("old_rebound_short_funding_abs_max_pct", 0.08))
        min_long_short = float(self.config.get("old_rebound_short_long_short_min", 1.2))
        if taker_ratio > max_taker:
            return f"老币反弹主动卖出不足 {taker_ratio:.2f}/{max_taker:.2f}"
        if oi_1h < min_oi:
            return f"老币反弹OI撤退过快 {oi_1h:+.1f}%/{min_oi:+.1f}%"
        if funding > max_funding:
            return f"老币反弹资金费率过热 {funding:.3f}%/{max_funding:.3f}%"
        if long_short < min_long_short:
            return f"老币反弹多头拥挤不足 {long_short:.2f}/{min_long_short:.2f}"
        return ""

    def high_pullback_short_market_stats_reject_reason(self, stats: dict[str, float]) -> str:
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        funding = abs(fnum(stats.get("funding_rate_pct")))
        max_taker = float(self.config.get("high_pullback_short_taker_ratio_max", 0.95))
        min_oi = float(self.config.get("high_pullback_short_oi_1h_min_pct", -5.0))
        max_funding = float(self.config.get("high_pullback_short_funding_abs_max_pct", 0.08))
        if taker_ratio > max_taker:
            return f"高位回落主动卖出不足 {taker_ratio:.2f}/{max_taker:.2f}"
        if oi_1h < min_oi:
            return f"高位回落OI撤退过快 {oi_1h:+.1f}%/{min_oi:+.1f}%"
        if funding > max_funding:
            return f"高位回落费率过热 {funding:.3f}%/{max_funding:.3f}%"
        return ""

    def kline_short_metrics(self, symbol: str, fallback_price: float, change: float, old_rebound: bool = False) -> dict[str, Any]:
        metrics = self.fallback_short_metrics(change)
        metrics["price"] = fallback_price
        try:
            limit_15m = max(30 if old_rebound else 2, int(self.config.get("kline_15m_limit", 40))) + (1 if old_rebound else 0)
            klines15 = self.fetch_klines_interval(symbol, "15m", limit_15m)
            live15 = latest_unclosed_kline(klines15)
            klines15 = closed_klines(klines15)
            closes15 = [fnum(k[4]) for k in klines15]
            volumes15 = [fnum(k[7]) for k in klines15]
            if old_rebound:
                metrics["closed_15m_count"] = len(klines15)
                metrics["trend_15m"] = infer_trend(closes15)
                if len(klines15) >= 4:
                    lows = [fnum(k[3]) for k in klines15[-4:-1]]
                    if all(low > 0 for low in lows):
                        metrics["closed_15m_previous_low"] = min(lows)
            if closes15:
                metrics["price"] = closes15[-1] or metrics["price"] or fallback_price
                metrics["enriched"] = True
            if live15 is not None and closes15:
                live_close = fnum(live15[4])
                if live_close > 0 and closes15[-1] > 0:
                    metrics["live_short_move"] = pct(live_close, closes15[-1])
            if len(closes15) > 1:
                metrics["short_move"] = pct(closes15[-1], closes15[-2])
            trend15 = infer_trend(closes15)
            if trend15 != "neutral":
                metrics["trend"] = trend15
            if len(volumes15) > 21:
                base = sum(volumes15[-21:-1]) / 20
                metrics["volume_ratio"] = volumes15[-1] / base if base > 0 else metrics["volume_ratio"]
        except Exception:
            pass
        try:
            limit_1h = max(30 if old_rebound else 5, int(self.config.get("kline_1h_limit", 10))) + (1 if old_rebound else 0)
            klines1h = self.fetch_klines_interval(symbol, "1h", limit_1h)
            live1h = latest_unclosed_kline(klines1h)
            klines1h = closed_klines(klines1h)
            closes1h = [fnum(k[4]) for k in klines1h]
            volumes1h = [fnum(k[7]) for k in klines1h]
            if old_rebound:
                metrics["closed_1h_count"] = len(klines1h)
                metrics["trend_1h"] = infer_trend(closes1h)
            if closes1h:
                metrics["enriched"] = True
            if live1h is not None and closes1h:
                live_close = fnum(live1h[4])
                if live_close > 0 and closes1h[-1] > 0:
                    metrics["live_change_1h"] = pct(live_close, closes1h[-1])
            if len(closes1h) > 1:
                metrics["change_1h"] = pct(closes1h[-1], closes1h[-2])
            if len(closes1h) > 4:
                metrics["change_4h"] = pct(closes1h[-1], closes1h[-5])
            if len(volumes1h) > 2:
                base_count = min(12, len(volumes1h) - 1)
                base = sum(volumes1h[-base_count - 1 : -1]) / base_count
                metrics["volume_1h_ratio"] = volumes1h[-1] / base if base > 0 else 1.0
            trend1h = infer_trend(closes1h)
            if trend1h != "neutral":
                metrics["trend"] = trend1h
        except Exception:
            pass
        return metrics

    def build_candidate_pool(self, tickers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        watchlist = {str(item).upper() for item in self.config.get("manual_watchlist", [])}
        blocked = set(normalize_symbol_list(self.config.get("blocked_symbols", []), str(self.config.get("quote_asset", "USDT"))))
        min_volume = float(self.config.get("min_quote_volume_usdt", 0))
        min_abs_change = float(self.config.get("min_abs_24h_change_pct", 0))
        strong_long_min_change = float(self.config.get("strong_long_24h_min_change_pct", 0))
        strong_short_enabled = bool(self.config.get("strong_short_enabled", True))
        max_candidates = int(self.config.get("max_candidates_per_scan", 18))
        pre_start_limit = int(self.config.get("pre_start_max_candidates", 8))
        old_rebound_limit = int(self.config.get("old_rebound_short_max_candidates", 8))
        high_pullback_limit = int(self.config.get("high_pullback_short_max_candidates", 5))
        direction_mode = str(self.config.get("trade_direction_mode", "both"))

        def change_allowed(change: float) -> bool:
            if direction_mode == "long_only":
                return change >= max(min_abs_change, strong_long_min_change)
            if direction_mode == "short_only":
                return strong_short_enabled and change <= -min_abs_change
            if change > 0:
                return change >= max(min_abs_change, strong_long_min_change)
            return strong_short_enabled and abs(change) >= min_abs_change

        def is_blocked_candidate(item: dict[str, Any]) -> bool:
            return str(item.get("symbol", "")).upper() in blocked

        def is_non_crypto_candidate(item: dict[str, Any]) -> bool:
            return item.get("_crypto_candidate_allowed") is False

        def is_cooldown_candidate(item: dict[str, Any]) -> bool:
            symbol = str(item.get("symbol", "")).upper()
            return bool(symbol and self.stop_loss_cooldown_reason(symbol))

        def high_pullback_metrics(item: dict[str, Any]) -> tuple[float, float]:
            price = fnum(item.get("lastPrice"))
            high = fnum(item.get("highPrice"))
            low = fnum(item.get("lowPrice"))
            drawdown = max(0.0, -pct(price, high)) if high > 0 and price > 0 else 0.0
            low_distance = pct(price, low) if low > 0 and price > 0 else 999.0
            return drawdown, low_distance

        pool: dict[str, dict[str, Any]] = {}
        if self.config.get("high_pullback_short_enabled", False) and direction_mode != "long_only" and high_pullback_limit > 0:
            hp_min_change = float(self.config.get("high_pullback_short_24h_min_pct", 8.0))
            hp_max_change = float(self.config.get("high_pullback_short_24h_max_pct", 60.0))
            hp_min_drawdown = float(self.config.get("high_pullback_short_min_drawdown_pct", 18.0))
            hp_max_drawdown = float(self.config.get("high_pullback_short_max_drawdown_pct", 45.0))
            hp_min_low_distance = float(self.config.get("high_pullback_short_min_low_distance_pct", 5.0))
            high_pullback = []
            for item in tickers:
                if is_non_crypto_candidate(item) or is_blocked_candidate(item) or is_cooldown_candidate(item):
                    continue
                change = fnum(item.get("priceChangePercent"))
                volume = fnum(item.get("quoteVolume"))
                drawdown, low_distance = high_pullback_metrics(item)
                if (
                    volume >= min_volume
                    and hp_min_change <= change <= hp_max_change
                    and hp_min_drawdown <= drawdown <= hp_max_drawdown
                    and low_distance >= hp_min_low_distance
                ):
                    high_pullback.append((item, drawdown, low_distance))
            high_pullback.sort(key=lambda row: (-row[1], -fnum(row[0].get("quoteVolume"))))
            for item, drawdown, low_distance in high_pullback[:high_pullback_limit]:
                pool[str(item.get("symbol", ""))] = {
                    "item": item,
                    "signal_mode": "high_pullback_short",
                    "mode_reason": f"高位回落做空池；24h高点回撤 {drawdown:.1f}%，距24h低点 {low_distance:.1f}%",
                }

        if self.config.get("old_rebound_short_enabled", False) and direction_mode != "long_only" and old_rebound_limit > 0:
            old_min_change = float(self.config.get("old_rebound_short_24h_min_pct", 8.0))
            old_max_change = float(self.config.get("old_rebound_short_24h_max_pct", 35.0))
            old_min_volume = float(self.config.get("old_rebound_short_min_quote_volume_usdt", 5_000_000))
            old_max_volume = float(self.config.get("old_rebound_short_max_quote_volume_usdt", 80_000_000))
            old_rebound = []
            for item in tickers:
                if is_non_crypto_candidate(item) or is_blocked_candidate(item) or is_cooldown_candidate(item):
                    continue
                change = fnum(item.get("priceChangePercent"))
                volume = fnum(item.get("quoteVolume"))
                if old_min_change <= change <= old_max_change and old_min_volume <= volume <= old_max_volume:
                    old_rebound.append(item)
            old_rebound.sort(
                key=lambda x: (
                    -fnum(x.get("priceChangePercent")),
                    fnum(x.get("quoteVolume")),
                )
            )
            for item in old_rebound[:old_rebound_limit]:
                symbol = str(item.get("symbol", ""))
                if symbol in pool:
                    continue
                pool[symbol] = {"item": item, "signal_mode": "old_rebound_short", "mode_reason": "老币反弹衰竭做空池"}

        strong = []
        for item in tickers:
            symbol = str(item.get("symbol", ""))
            if symbol in pool or is_non_crypto_candidate(item) or is_blocked_candidate(item) or is_cooldown_candidate(item):
                continue
            change = fnum(item.get("priceChangePercent"))
            volume = fnum(item.get("quoteVolume"))
            if symbol in watchlist or (volume >= min_volume and change_allowed(change)):
                strong.append(item)
        if direction_mode == "long_only":
            strong.sort(key=lambda x: (-fnum(x.get("priceChangePercent")), -fnum(x.get("quoteVolume"))))
        elif direction_mode == "short_only":
            strong.sort(key=lambda x: (fnum(x.get("priceChangePercent")), -fnum(x.get("quoteVolume"))))
        else:
            strong.sort(key=lambda x: (-abs(fnum(x.get("priceChangePercent"))), -fnum(x.get("quoteVolume"))))
        for item in strong[:max_candidates]:
            pool[str(item.get("symbol", ""))] = {"item": item, "signal_mode": "strong_momentum", "mode_reason": "24h强波动池"}

        if self.config.get("pre_start_long_enabled", False) and direction_mode != "short_only" and pre_start_limit > 0:
            min_24h = float(self.config.get("pre_start_24h_min_pct", 1.0))
            max_24h = float(self.config.get("pre_start_24h_max_pct", 12.0))
            pre_start = []
            for item in tickers:
                symbol = str(item.get("symbol", ""))
                if symbol in pool or is_non_crypto_candidate(item) or is_blocked_candidate(item) or is_cooldown_candidate(item):
                    continue
                change = fnum(item.get("priceChangePercent"))
                volume = fnum(item.get("quoteVolume"))
                if volume >= min_volume and min_24h <= change <= max_24h:
                    pre_start.append(item)
            pre_start.sort(
                key=lambda x: (
                    -fnum(x.get("quoteVolume")),
                    fnum(x.get("priceChangePercent")),
                )
            )
            for item in pre_start[:pre_start_limit]:
                pool[str(item.get("symbol", ""))] = {"item": item, "signal_mode": "pre_start_long", "mode_reason": "预启动做多池"}

        candidates = list(pool.values())
        candidates.sort(
            key=lambda row: (
                0 if row.get("signal_mode") == "pre_start_long" else 1 if row.get("signal_mode") == "high_pullback_short" else 2 if row.get("signal_mode") == "old_rebound_short" else 3,
                -fnum(row["item"].get("quoteVolume")),
                -abs(fnum(row["item"].get("priceChangePercent"))),
            )
        )
        extra_limit = 0
        if self.config.get("pre_start_long_enabled", False):
            extra_limit += pre_start_limit
        if self.config.get("old_rebound_short_enabled", False):
            extra_limit += old_rebound_limit
        if self.config.get("high_pullback_short_enabled", False):
            extra_limit += high_pullback_limit
        return candidates[: max_candidates + extra_limit]

    def build_signals(self, tickers: list[dict[str, Any]], open_symbols: set[str]) -> list[Signal]:
        signals: list[Signal] = []
        crypto_tickers = [item for item in tickers if item.get("_crypto_candidate_allowed") is not False]
        long_regime = self.market_regime_score("LONG", crypto_tickers)
        short_regime = self.market_regime_score("SHORT", crypto_tickers)
        candidates = self.build_candidate_pool(tickers)
        enrich_limit = int(self.config.get("max_kline_enriched_candidates", 8))
        if enrich_limit <= 0:
            enrich_limit = min(len(candidates), 8)
        enriched_count = 0
        for index, candidate in enumerate(candidates):
            item = candidate["item"]
            signal_mode = str(candidate.get("signal_mode", "strong_momentum"))
            is_pre_start_long = signal_mode == "pre_start_long"
            is_old_rebound_short = signal_mode == "old_rebound_short"
            is_high_pullback_short = signal_mode == "high_pullback_short"
            mode_reason = str(candidate.get("mode_reason", self.signal_mode_label(signal_mode)))
            symbol = item["symbol"]
            price = fnum(item.get("lastPrice"))
            change = fnum(item.get("priceChangePercent"))
            volume = fnum(item.get("quoteVolume"))
            metrics = candidate.get("metrics") or self.fallback_short_metrics(change)
            price = fnum(metrics.get("price")) or price
            short_move = fnum(metrics.get("short_move"))
            change_1h = fnum(metrics.get("change_1h"))
            change_4h = fnum(metrics.get("change_4h"))
            volume_ratio = fnum(metrics.get("volume_ratio"), 1.0)
            volume_1h_ratio = fnum(metrics.get("volume_1h_ratio"), 1.0)
            trend = str(metrics.get("trend", "neutral"))

            long_score, long_reason = self.score_side("LONG", change, short_move, volume_ratio, trend)
            short_score, short_reason = self.score_side("SHORT", change, short_move, volume_ratio, trend)
            if is_pre_start_long:
                side, score, reason = "LONG", long_score, long_reason
            elif is_old_rebound_short or is_high_pullback_short:
                side, score, reason = "SHORT", short_score, short_reason
            elif long_score >= short_score:
                side, score, reason = "LONG", long_score, long_reason
            else:
                side, score, reason = "SHORT", short_score, short_reason

            score_threshold = self.score_threshold_for_mode(signal_mode)
            reject = ""
            if symbol in open_symbols:
                reject = "已有持仓"
            elif self.blocked_symbol_reject_reason(symbol):
                reject = self.blocked_symbol_reject_reason(symbol)
            elif score < score_threshold and not (is_pre_start_long or is_old_rebound_short or is_high_pullback_short):
                reject = f"分数 {score:.1f} 低于阈值 {score_threshold:.1f}"
            elif side == "LONG" and trend == "down":
                reject = "短线趋势反向"
            elif side == "SHORT" and trend == "up" and not (is_old_rebound_short or is_high_pullback_short):
                reject = "短线趋势反向"
            else:
                reject = self.direction_mode_reject_reason(side)

            if not reject and (enriched_count < enrich_limit or is_pre_start_long or is_old_rebound_short or is_high_pullback_short):
                metrics = self.kline_short_metrics(symbol, price, change, old_rebound=True) if is_old_rebound_short else self.kline_short_metrics(symbol, price, change)
                enriched_count += 1
                price = fnum(metrics.get("price")) or price
                short_move = fnum(metrics.get("short_move"))
                change_1h = fnum(metrics.get("change_1h"))
                change_4h = fnum(metrics.get("change_4h"))
                volume_ratio = fnum(metrics.get("volume_ratio"), 1.0)
                volume_1h_ratio = fnum(metrics.get("volume_1h_ratio"), 1.0)
                trend = str(metrics.get("trend", "neutral"))
                long_score, long_reason = self.score_side("LONG", change, short_move, volume_ratio, trend)
                short_score, short_reason = self.score_side("SHORT", change, short_move, volume_ratio, trend)
                if is_pre_start_long:
                    side, score, reason = "LONG", long_score, long_reason
                elif is_old_rebound_short or is_high_pullback_short:
                    side, score, reason = "SHORT", short_score, short_reason
                elif long_score >= short_score:
                    side, score, reason = "LONG", long_score, long_reason
                else:
                    side, score, reason = "SHORT", short_score, short_reason
                if score < score_threshold and not (is_pre_start_long or is_old_rebound_short or is_high_pullback_short):
                    reject = f"分数 {score:.1f} 低于阈值 {score_threshold:.1f}"
                elif side == "LONG" and trend == "down":
                    reject = "短线趋势反向"
                elif side == "SHORT" and trend == "up" and not (is_old_rebound_short or is_high_pullback_short):
                    reject = "短线趋势反向"
                else:
                    reject = self.direction_mode_reject_reason(side)
                if not reject and is_pre_start_long:
                    reject = self.pre_start_kline_reject_reason(short_move, change_1h, volume_ratio, trend)
                if not reject and is_old_rebound_short:
                    reject = self.old_rebound_short_kline_reject_reason(
                        symbol, short_move, change_1h, volume_ratio, trend, metrics,
                        high_24h=fnum(item.get("highPrice")), low_24h=fnum(item.get("lowPrice")),
                        current_price=fnum(item.get("lastPrice")),
                    )
                if not reject and is_high_pullback_short:
                    reject = self.high_pullback_short_kline_reject_reason(short_move, change_1h, volume_ratio, trend)
            priority = "高" if score >= 6 else "中" if score >= 4.5 else "低"
            market_stats = {
                "open_interest_usdt": volume * 0.22,
                "open_interest_24h_pct": change * 0.55,
                "open_interest_4h_pct": change_4h * 0.75,
                "open_interest_1h_pct": change_1h * 0.75,
                "funding_rate_pct": 0.0,
                "long_short_ratio": 1.0,
                "volume_4h_ratio": max(0.1, min(20.0, volume_ratio * 3.2)),
                "volume_1h_ratio": volume_1h_ratio,
                "taker_long_short_ratio": 1.0,
                "oi_volume_ratio": 0.22,
            }
            market_regime = long_regime if side == "LONG" else short_regime
            heat_level = "normal"
            if not reject:
                try:
                    if index >= 6 and not (is_pre_start_long or is_old_rebound_short or is_high_pullback_short):
                        raise RuntimeError("skip slow derivatives enrichment")
                    market_stats = self.fetch_signal_market_stats(symbol, item)
                    market_stats["volume_1h_ratio"] = volume_1h_ratio
                except Exception:
                    pass
                if not reject and is_pre_start_long:
                    reject = self.pre_start_market_stats_reject_reason(market_stats)
                if not reject and is_old_rebound_short:
                    reject = self.old_rebound_short_market_stats_reject_reason(market_stats)
                if not reject and is_high_pullback_short:
                    reject = self.high_pullback_short_market_stats_reject_reason(market_stats)
                if not reject and side == "LONG" and self.config.get("long_structure_confirmation_enabled", False):
                    long_structure = self.long_structure_confirmation(change_4h, change_1h, short_move, trend, volume_ratio, market_stats)
                    if not long_structure.get("passed"):
                        reject = f"做多结构确认不足 {fnum(long_structure.get('score')):.1f}/{float(self.config.get('long_structure_min_score', 2.5)):.1f}"
                    mode_reason = f"{mode_reason}；做多结构：{long_structure.get('reason', '-')}"
                if not reject and side == "SHORT" and self.config.get("short_structure_confirmation_enabled", True):
                    short_structure = self.short_structure_confirmation(change_4h, change_1h, short_move, trend, volume_ratio, market_stats)
                    if not short_structure.get("passed"):
                        reject = f"做空结构破坏不足 {fnum(short_structure.get('score')):.1f}/{float(self.config.get('short_structure_min_score', 4.0)):.1f}"
                    mode_reason = f"{mode_reason}；做空结构：{short_structure.get('reason', '-')}"
                if not reject and side == "SHORT":
                    high_24h = fnum(item.get("highPrice"))
                    max_drawdown = float(self.config.get("short_max_24h_high_drawdown_pct", 75.0))
                    if high_24h > 0 and max_drawdown > 0:
                        drawdown_from_high = max(0.0, -pct(price, high_24h))
                        if drawdown_from_high >= max_drawdown:
                            reject = f"24h高点回撤 {drawdown_from_high:.1f}% 超过 {max_drawdown:.1f}%，禁止追空"
                if (
                    not reject
                    and self.config.get("market_regime_filter_enabled", True)
                    and fnum(market_regime.get("score")) < float(self.config.get("market_regime_min_score", 4.5))
                ):
                    reject = f"市场环境逆风 {fnum(market_regime.get('score')):.1f}/10"
            analyzer_direction = "未分析"
            analyzer_confidence = 0.0
            analyzer_reason = ""
            market_confirm = {"score": 0.0, "label": "未计算", "reason": ""}
            should_update_analyzer = not reject or reject == "已有持仓"
            confirmation_mode = str(self.config.get("confirmation_mode", "market_score"))
            if should_update_analyzer and not reject and confirmation_mode == "market_score":
                if is_pre_start_long:
                    market_confirm = self.pre_start_confirmation_score(
                        change,
                        change_1h,
                        short_move,
                        volume_ratio,
                        trend,
                        market_stats,
                        price,
                        fnum(item.get("highPrice")),
                    )
                elif is_old_rebound_short:
                    market_confirm = self.old_rebound_short_confirmation_score(
                        change,
                        change_1h,
                        short_move,
                        volume_ratio,
                        trend,
                        market_stats,
                        metrics,
                    )
                elif is_high_pullback_short:
                    market_confirm = self.high_pullback_short_confirmation_score(
                        change,
                        change_1h,
                        short_move,
                        volume_ratio,
                        trend,
                        market_stats,
                        price,
                        fnum(item.get("highPrice")),
                        fnum(item.get("lowPrice")),
                    )
                else:
                    market_confirm = self.market_confirmation_score(
                        side,
                        signal_mode,
                        change,
                        change_4h,
                        change_1h,
                        short_move,
                        volume_ratio,
                        trend,
                        market_stats,
                    )
                confirm_score = fnum(market_confirm.get("score"))
                confirm_min = self.market_confirm_min_score_for_mode(signal_mode, side)
                analyzer_direction = "预启动确认" if is_pre_start_long else "市场确认"
                analyzer_confidence = confirm_score * 10
                if is_old_rebound_short:
                    analyzer_direction = "老币反弹确认"
                if is_high_pullback_short:
                    analyzer_direction = "高位回落确认"
                analyzer_reason = str(market_confirm.get("reason", ""))
                if confirm_score < confirm_min:
                    reject = f"{analyzer_direction}分 {confirm_score:.1f} 低于 {confirm_min:.1f}"
            elif should_update_analyzer and confirmation_mode == "analyzer" and self.config.get("analyzer_confirmation_enabled", True):
                original_reject = reject
                try:
                    analyzer_result = self.analyzer_confirmation(symbol, side, signal_mode)
                    analyzer_direction = str(analyzer_result.get("direction", "观望"))
                    analyzer_confidence = fnum(analyzer_result.get("confidence"))
                    analyzer_reason = str(analyzer_result.get("reason", ""))
                    if not original_reject:
                        reject = self.analyzer_reject_reason(side, analyzer_result, signal_mode)
                except Exception as exc:
                    analyzer_direction = "错误"
                    analyzer_reason = str(exc)
                    if not original_reject:
                        reject = f"分析器确认失败：{exc}"
            risk_level = self.classify_signal_risk(
                score,
                market_stats["funding_rate_pct"],
                market_stats["open_interest_24h_pct"],
                volume_ratio,
            )
            decision_steps = self.base_decision_steps(
                symbol,
                side,
                score,
                reason,
                trend,
                change,
                volume,
                reject,
                analyzer_direction,
                analyzer_confidence,
                analyzer_reason,
            )
            decision_steps.insert(-1, f"候选模式：{self.signal_mode_label(signal_mode)}，{mode_reason}")
            decision_steps.insert(-1, f"市场环境：{market_regime.get('label')} {fnum(market_regime.get('score')):.1f}/10，{market_regime.get('reason') or '-'}")
            if confirmation_mode == "market_score":
                decision_steps.insert(
                    -1,
                    f"市场确认：{market_confirm.get('label')} {fnum(market_confirm.get('score')):.1f}/10，{market_confirm.get('reason') or '-'}",
                )
            signals.append(
                Signal(
                    symbol=symbol,
                    side=side,
                    score=round(score, 2),
                    price=price,
                    quote_volume=volume,
                    change_24h_pct=change,
                    change_4h_pct=change_4h,
                    change_1h_pct=change_1h,
                    short_move_pct=short_move,
                    volume_ratio=volume_ratio,
                    open_interest_usdt=round(market_stats["open_interest_usdt"], 2),
                    open_interest_24h_pct=round(market_stats["open_interest_24h_pct"], 2),
                    open_interest_4h_pct=round(market_stats["open_interest_4h_pct"], 2),
                    open_interest_1h_pct=round(market_stats["open_interest_1h_pct"], 2),
                    funding_rate_pct=round(market_stats["funding_rate_pct"], 4),
                    long_short_ratio=round(market_stats["long_short_ratio"], 3),
                    volume_4h_ratio=round(market_stats["volume_4h_ratio"], 2),
                    taker_long_short_ratio=round(market_stats["taker_long_short_ratio"], 3),
                    oi_volume_ratio=round(market_stats["oi_volume_ratio"], 2),
                    signal_mode=signal_mode,
                    signal_heat_level=heat_level,
                    market_regime_score=fnum(market_regime.get("score")),
                    market_regime_label=str(market_regime.get("label", "")),
                    market_regime_reason=str(market_regime.get("reason", "")),
                    risk_level=risk_level,
                    analyzer_direction=analyzer_direction,
                    analyzer_confidence_pct=round(analyzer_confidence, 1),
                    analyzer_reason=analyzer_reason,
                    trigger_reason=reason,
                    reject_reason=reject,
                    decision_summary=self.decision_summary(reject),
                    decision_steps=decision_steps,
                    priority=priority,
                )
            )
        return signals

    def score_threshold_for_mode(self, signal_mode: str) -> float:
        return float(self.config.get("signal_score_threshold", 4.0))

    def directional_volume_state(
        self,
        side: str,
        change_1h: float,
        short_move: float,
        volume_ratio: float,
        stats: dict[str, float],
    ) -> dict[str, float | bool]:
        is_long = side == "LONG"
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        taker_aligned = taker_ratio if is_long else (1.0 / taker_ratio if taker_ratio > 0 else 1.0)
        volume_1h_ratio = fnum(stats.get("volume_1h_ratio"), 1.0)
        directional_1h = change_1h if is_long else -change_1h
        directional_short = short_move if is_long else -short_move
        volume_active = volume_ratio >= 1.2
        one_hour_confirmed = volume_1h_ratio >= 0.8
        price_push = directional_short >= 0.4 and directional_1h >= 0.5
        aligned = volume_active and one_hour_confirmed and price_push and taker_aligned >= 1.05
        counter = volume_active and (taker_aligned < 0.92 or directional_short < -0.4 or directional_1h < -0.6)
        return {
            "aligned": aligned,
            "counter": counter,
            "volume_active": volume_active,
            "one_hour_confirmed": one_hour_confirmed,
            "price_push": price_push,
            "taker_aligned": taker_aligned,
            "volume_1h_ratio": volume_1h_ratio,
        }

    def score_side(self, side: str, change: float, short_move: float, volume_ratio: float, trend: str) -> tuple[float, str]:
        score = 0.0
        reasons = []
        if volume_ratio >= float(self.config.get("volume_spike_ratio", 1.5)):
            score += 1.3
            reasons.append(f"放量 {volume_ratio:.2f}x")
        if side == "LONG":
            if change >= float(self.config.get("long_24h_change_pct", 7.0)):
                score += 1.8
                reasons.append("24h强势")
            if short_move >= float(self.config.get("short_term_move_pct", 1.2)):
                score += 1.7
                reasons.append("短线突破")
            if trend == "up":
                score += 1.8
                reasons.append("均线向上")
        else:
            if change <= float(self.config.get("short_24h_change_pct", -7.0)):
                score += 1.8
                reasons.append("24h弱势")
            if short_move <= -float(self.config.get("short_term_move_pct", 1.2)):
                score += 1.7
                reasons.append("短线下破")
            if trend == "down":
                score += 1.8
                reasons.append("均线向下")
        if not reasons:
            reasons.append("波动不足")
        return score, " / ".join(reasons)

    def direction_mode_reject_reason(self, side: str) -> str:
        mode = str(self.config.get("trade_direction_mode", "both"))
        if mode == "long_only" and side == "SHORT":
            return "方向模式为只做多，跳过空单"
        if mode == "short_only" and side == "LONG":
            return "方向模式为只做空，跳过多单"
        return ""

    def blocked_symbol_reject_reason(self, symbol: str) -> str:
        blocked = set(normalize_symbol_list(self.config.get("blocked_symbols", []), str(self.config.get("quote_asset", "USDT"))))
        if symbol in blocked:
            return "币种在禁止开仓名单中"
        return ""

    def update_signal_decision(self, signal: Signal, summary: str, step: str | None = None) -> None:
        signal.reject_reason = summary
        signal.decision_summary = self.decision_summary(summary)
        if step:
            signal.decision_steps.append(step)
        if not signal.decision_steps or not signal.decision_steps[-1].startswith("最终结论："):
            signal.decision_steps.append(f"最终结论：{signal.decision_summary}")
        else:
            signal.decision_steps[-1] = f"最终结论：{signal.decision_summary}"

    def sync_candidate_decision_steps(self, candidate: dict[str, Any]) -> None:
        steps = candidate.get("decision_steps")
        if not isinstance(steps, list):
            return
        direction = str(candidate.get("analyzer_direction", "未分析"))
        confidence = fnum(candidate.get("analyzer_confidence_pct"))
        reason = str(candidate.get("analyzer_reason", ""))
        analyzer_step = f"分析器：{direction} {confidence:.1f}%，{reason or '-'}"
        replaced = False
        for index, step in enumerate(steps):
            if str(step).startswith("分析器："):
                steps[index] = analyzer_step
                replaced = True
                break
        if not replaced:
            insert_at = max(0, len(steps) - 1)
            steps.insert(insert_at, analyzer_step)
        summary = self.decision_summary(str(candidate.get("reject_reason", "")))
        candidate["decision_summary"] = summary
        if steps and str(steps[-1]).startswith("最终结论："):
            steps[-1] = f"最终结论：{summary}"
        else:
            steps.append(f"最终结论：{summary}")

    def decision_summary(self, reject: str) -> str:
        return reject or "通过候选筛选，等待开仓前实时确认"

    def base_decision_steps(
        self,
        symbol: str,
        side: str,
        score: float,
        reason: str,
        trend: str,
        change: float,
        volume: float,
        reject: str,
        analyzer_direction: str,
        analyzer_confidence: float,
        analyzer_reason: str,
    ) -> list[str]:
        steps = [
            f"候选来源：24h涨跌 {change:+.2f}%，成交额 {volume:.0f} U",
            f"系统方向：{self.side_cn(side)}，系统分 {score:.2f}，依据：{reason}",
            f"短线趋势：{trend}",
        ]
        blocked_reason = self.blocked_symbol_reject_reason(symbol)
        if blocked_reason:
            steps.append(f"禁开名单：{blocked_reason}")
        mode_reason = self.direction_mode_reject_reason(side)
        if mode_reason:
            steps.append(f"方向模式：{mode_reason}")
        if analyzer_direction and analyzer_direction != "未分析":
            steps.append(f"分析器：{analyzer_direction} {analyzer_confidence:.1f}%，{analyzer_reason or '-'}")
        else:
            steps.append("分析器：未进入或等待二次确认")
        steps.append(f"最终结论：{self.decision_summary(reject)}")
        return steps

    def refresh_positions(self, price_map: dict[str, float]) -> None:
        positions = []
        for pos in self.state.get("positions", []):
            price = price_map.get(pos["symbol"], fnum(pos.get("last_price"), pos["entry_price"]))
            self.update_position_pnl(pos, price)
            roi = fnum(pos.get("roi_pct"))
            if roi >= float(self.config.get("take_profit_roi_pct", 90.0)):
                self.close_position(pos, price, "止盈平仓")
                continue
            if roi <= -float(self.config.get("stop_loss_roi_pct", 86.0)):
                self.close_position(pos, price, "止损平仓")
                continue
            positions.append(pos)
        self.state["positions"] = positions

    def update_position_pnl(self, pos: dict[str, Any], price: float) -> None:
        entry = fnum(pos.get("entry_price"))
        qty = fnum(pos.get("quantity"))
        margin = max(0.01, fnum(pos.get("margin_usdt")))
        pnl = (price - entry) * qty if pos.get("side") == "LONG" else (entry - price) * qty
        pos["last_price"] = price
        pos["unrealized_pnl_usdt"] = pnl
        pos["roi_pct"] = pnl / margin * 100.0
        pos["updated_at"] = iso_now()

    def close_position(self, pos: dict[str, Any], price: float, reason: str) -> None:
        self.update_position_pnl(pos, price)
        pnl = fnum(pos.get("unrealized_pnl_usdt"))
        self.state["realized_pnl_usdt"] = fnum(self.state.get("realized_pnl_usdt")) + pnl
        if "止盈" in reason:
            self.state["take_profit_count"] = int(self.state.get("take_profit_count", 0)) + 1
        elif "止损" in reason:
            self.state["stop_loss_count"] = int(self.state.get("stop_loss_count", 0)) + 1
        row = {
            "id": pos.get("id"),
            "execution_mode": self.exchange_mode(),
            "symbol": pos.get("symbol"),
            "side": pos.get("side"),
            "entry_price": fnum(pos.get("entry_price")),
            "exit_price": price,
            "quantity": fnum(pos.get("quantity")),
            "margin_usdt": fnum(pos.get("margin_usdt")),
            "pnl_usdt": pnl,
            "roi_pct": fnum(pos.get("roi_pct")),
            "opened_at": pos.get("opened_at"),
            "closed_at": iso_now(),
            "close_reason": reason,
            "trigger_reason": pos.get("trigger_reason"),
        }
        self.state.setdefault("trade_history", []).insert(0, row)
        self.notify_new_closed_trades([row])
        self.state["trade_history"] = self.state["trade_history"][:200]
        self.event("trade", f"{pos['symbol']} {self.side_cn(pos['side'])} {reason}，PnL {pnl:+.2f} U")

    def stop_loss_cooldown_reason(self, symbol: str) -> str:
        if not self.config.get("stop_loss_cooldown_enabled", True):
            return ""
        cooldown_sec = max(60, int(float(self.config.get("stop_loss_cooldown_minutes", 60)) * 60))
        now_ms = int(time.time() * 1000)
        latest_stop_ms = 0
        for trade in self.state.get("trade_history", []):
            if str(trade.get("symbol", "")) != symbol:
                continue
            if not self.is_cooldown_trade(trade):
                continue
            closed_ms = parse_time_ms(trade.get("closed_at"))
            if closed_ms > latest_stop_ms:
                latest_stop_ms = closed_ms
        if latest_stop_ms <= 0:
            return ""
        overrides = self.state.setdefault("stop_loss_cooldown_overrides", {})
        override_ms = int(fnum(overrides.get(symbol)))
        if override_ms >= latest_stop_ms:
            return ""
        elapsed_sec = max(0, (now_ms - latest_stop_ms) / 1000)
        remaining_sec = cooldown_sec - elapsed_sec
        if remaining_sec <= 0:
            return ""
        remaining_min = math.ceil(remaining_sec / 60)
        return f"平仓冷静期剩余 {remaining_min} 分钟"

    def is_cooldown_trade(self, trade: dict[str, Any]) -> bool:
        reason = str(trade.get("close_reason", ""))
        pnl = fnum(trade.get("pnl_usdt"))
        if "止损" in reason or pnl < 0:
            return True
        if not self.config.get("take_profit_cooldown_enabled", True):
            return False
        return "止盈" in reason or pnl > 0

    def latest_stop_loss_trade_by_symbol(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for trade in self.state.get("trade_history", []):
            symbol = str(trade.get("symbol", ""))
            if not symbol:
                continue
            if not self.is_cooldown_trade(trade):
                continue
            closed_ms = parse_time_ms(trade.get("closed_at"))
            if closed_ms <= 0:
                continue
            current = latest.get(symbol)
            if not current or closed_ms > int(current.get("closed_ms", 0)):
                latest[symbol] = {"trade": trade, "closed_ms": closed_ms}
        return latest

    def active_stop_loss_cooldowns(self) -> list[dict[str, Any]]:
        if not self.config.get("stop_loss_cooldown_enabled", True):
            return []
        cooldown_sec = max(60, int(float(self.config.get("stop_loss_cooldown_minutes", 60)) * 60))
        now_ms = int(time.time() * 1000)
        overrides = self.state.setdefault("stop_loss_cooldown_overrides", {})
        rows: list[dict[str, Any]] = []
        for symbol, item in self.latest_stop_loss_trade_by_symbol().items():
            closed_ms = int(item.get("closed_ms", 0))
            if int(fnum(overrides.get(symbol))) >= closed_ms:
                continue
            remaining_sec = cooldown_sec - max(0, (now_ms - closed_ms) / 1000)
            if remaining_sec <= 0:
                continue
            trade = item.get("trade") or {}
            rows.append({
                "symbol": symbol,
                "remaining_minutes": math.ceil(remaining_sec / 60),
                "remaining_seconds": int(max(0, remaining_sec)),
                "closed_at": trade.get("closed_at", ""),
                "close_reason": trade.get("close_reason", ""),
                "pnl_usdt": fnum(trade.get("pnl_usdt")),
                "roi_pct": fnum(trade.get("roi_pct")),
                "trade_id": trade.get("id") or trade.get("entry_order_id") or "",
            })
        rows.sort(key=lambda row: int(row.get("remaining_seconds", 0)), reverse=True)
        return rows

    def prune_stop_loss_cooldown_overrides(self, state: dict[str, Any] | None = None) -> None:
        target = state if state is not None else self.state
        overrides = target.setdefault("stop_loss_cooldown_overrides", {})
        if not isinstance(overrides, dict):
            target["stop_loss_cooldown_overrides"] = {}
            return
        latest = self.latest_stop_loss_trade_by_symbol() if state is None else {}
        for symbol in list(overrides.keys()):
            if state is not None:
                continue
            latest_ms = int((latest.get(symbol) or {}).get("closed_ms", 0))
            if latest_ms <= 0 or int(fnum(overrides.get(symbol))) < latest_ms:
                continue
            # Keep active overrides; old symbols are harmless and small, trimmed below.
        if len(overrides) > 200:
            items = sorted(overrides.items(), key=lambda item: fnum(item[1]), reverse=True)[:200]
            target["stop_loss_cooldown_overrides"] = dict(items)

    def clear_stop_loss_cooldown(self, symbol: str) -> dict[str, Any]:
        normalized = normalize_symbol_list([symbol], str(self.config.get("quote_asset", "USDT")))
        if not normalized:
            return {"ok": False, "error": "币种不能为空", **self.snapshot()}
        target_symbol = normalized[0]
        error = ""
        with self.lock:
            latest = self.latest_stop_loss_trade_by_symbol().get(target_symbol)
            if not latest:
                error = f"{target_symbol} 当前没有平仓冷静期记录"
            else:
                self.state.setdefault("stop_loss_cooldown_overrides", {})[target_symbol] = int(latest.get("closed_ms", 0))
                self.event("risk", f"已手动解除 {target_symbol} 本次平仓冷静期")
                self.persist()
        if error:
            return {"ok": False, "error": error, **self.snapshot()}
        return {"ok": True, **self.snapshot()}

    def open_from_signals(self, signals: list[Signal]) -> int:
        open_count = len(self.state.get("positions", []))
        max_open = int(self.config.get("max_open_positions", 8))
        capacity = max(0, max_open - open_count)
        if capacity <= 0:
            return 0
        equity_tp_reason = self.equity_take_profit_block_reason()
        if equity_tp_reason:
            self.event("risk", equity_tp_reason)
            return 0
        daily_pnl = self.daily_realized_pnl()
        if self.config.get("max_daily_profit_enabled", True) and daily_pnl >= float(self.config.get("max_daily_profit_usdt", 30.0)):
            self.event("risk", f"今日已实现盈利 {daily_pnl:+.2f} U，达到盈利停止开单阈值，本轮不再开新仓")
            return 0
        if self.config.get("max_daily_loss_enabled", True) and daily_pnl <= -float(self.config.get("max_daily_loss_usdt", 20.0)):
            self.event("risk", f"今日已实现盈亏 {daily_pnl:+.2f} U，达到亏损停止开单阈值，本轮不再开新仓")
            return 0
        if self.config.get("max_daily_loss_enabled", False) and self.daily_realized_pnl() <= -float(self.config.get("max_daily_loss_usdt", 20.0)):
            self.event("risk", "已触发单日最大亏损限制，本轮不再开新仓")
            return 0
        try:
            exchange_symbols = self.get_exchange_symbols()
            market_exchange_symbols = self.get_market_exchange_symbols()
        except Exception as exc:
            self.event("error", f"读取 {self.exchange_label()} 交易规则失败：{exc}")
            return 0
        opened = 0
        for signal in signals:
            if opened >= min(capacity, int(self.config.get("max_new_positions_per_scan", 2))):
                break
            if signal.reject_reason or signal.price <= 0:
                continue
            blocked_reason = self.blocked_symbol_reject_reason(signal.symbol)
            if blocked_reason:
                self.update_signal_decision(signal, blocked_reason, f"开仓前复核：{blocked_reason}")
                self.event("risk", f"{signal.symbol} {blocked_reason}，跳过开仓")
                continue
            mode_reject = self.direction_mode_reject_reason(signal.side)
            if mode_reject:
                self.update_signal_decision(signal, mode_reject, f"开仓前复核：{mode_reject}")
                self.event("risk", f"{signal.symbol} {mode_reject}")
                continue
            cooldown_reason = self.stop_loss_cooldown_reason(signal.symbol)
            if cooldown_reason:
                self.update_signal_decision(signal, cooldown_reason, f"开仓前复核：{cooldown_reason}")
                self.event("risk", f"{signal.symbol} {cooldown_reason}，跳过开仓")
                continue
            symbol_info = exchange_symbols.get(signal.symbol)
            if not symbol_info:
                self.update_signal_decision(signal, f"{self.exchange_label()} 无此交易对", f"开仓前复核：不在 {self.exchange_label()} 交易对中")
                self.event("warn", f"{signal.symbol} 不在 {self.exchange_label()} 交易对中，跳过")
                continue
            market_symbol_info = market_exchange_symbols.get(signal.symbol, {})
            if not self.is_crypto_contract(market_symbol_info):
                reason = "非加密货币合约，禁止新开仓"
                self.update_signal_decision(signal, reason, f"开仓前复核：{reason}")
                self.event("risk", f"{signal.symbol} {reason}，跳过")
                continue
            if (
                str(self.config.get("confirmation_mode", "market_score")) == "analyzer"
                and self.config.get("analyzer_confirmation_enabled", True)
                and self.config.get("analyzer_realtime_before_open", True)
            ):
                try:
                    realtime_result = self.analyze_symbol_now(signal.symbol)
                    with self.lock:
                        self.save_analyzer_result(signal.symbol, realtime_result)
                    reject = self.analyzer_reject_reason(signal.side, realtime_result, signal.signal_mode)
                    if reject:
                        self.update_signal_decision(signal, reject, f"开仓前实时确认：{reject}")
                        self.event("risk", f"{signal.symbol} 开仓前实时确认未通过：{reject}")
                        continue
                    signal.analyzer_direction = str(realtime_result.get("direction", signal.analyzer_direction))
                    signal.analyzer_confidence_pct = round(fnum(realtime_result.get("confidence")), 1)
                    signal.analyzer_reason = str(realtime_result.get("reason", signal.analyzer_reason))
                    signal.decision_steps.append(
                        f"开仓前实时确认：{signal.analyzer_direction} {signal.analyzer_confidence_pct:.1f}%，通过"
                    )
                except Exception as exc:
                    self.update_signal_decision(signal, f"开仓前实时确认失败：{exc}", f"开仓前实时确认失败：{exc}")
                    self.event("error", f"{signal.symbol} 开仓前实时确认失败：{exc}")
                    continue
            try:
                record = self.exchange_open_position(signal, symbol_info)
            except Exception as exc:
                self.update_signal_decision(signal, f"{self.exchange_label()} 开仓失败：{exc}", f"下单结果：失败，{exc}")
                self.event("error", f"{signal.symbol} {self.exchange_label()} 开仓失败：{exc}")
                continue
            if record:
                signal.reject_reason = ""
                signal.decision_summary = f"已提交 {self.exchange_label()} 开仓"
                signal.decision_steps.append(f"下单结果：已开仓，订单 {record.get('entry_order_id') or '-'}")
                record["decision_summary"] = signal.decision_summary
                record["decision_steps"] = list(signal.decision_steps)
                self.mark_signal_review_opened(signal, record)
                self.event(
                    "trade",
                    f"{self.exchange_label()} 开仓 {signal.symbol} {self.side_cn(str(record.get('side') or signal.side))}，保证金 {record['margin_usdt']:.2f} U",
                )
                self.notify_open_position(signal, record)
                self.publish_square_open_position_async(signal, record)
                opened += 1
        return opened

    def available_margin(self) -> float:
        if self.state.get("exchange_account_mode") == self.exchange_mode() and "available_balance_usdt" in self.state:
            return max(0.0, fnum(self.state.get("available_balance_usdt")))
        equity = self.equity()
        used = sum(fnum(pos.get("margin_usdt")) for pos in self.state.get("positions", []))
        return max(0.0, equity - used)

    def gross_exposure(self) -> float:
        return sum(fnum(pos.get("notional_usdt")) for pos in self.state.get("positions", []))

    def exposure_pct(self, extra_notional: float = 0.0) -> float:
        equity = max(0.01, self.equity())
        return (self.gross_exposure() + extra_notional) / equity * 100.0

    def daily_realized_pnl(self) -> float:
        return sum(fnum(trade.get("pnl_usdt")) for trade in self.today_closed_trades())

    def today_closed_trades(self) -> list[dict[str, Any]]:
        today = datetime.now().astimezone().date().isoformat()
        closed = []
        for trade in self.state.get("trade_history", []):
            closed_dt = local_datetime(trade.get("closed_at"))
            if closed_dt and closed_dt.date().isoformat() == today:
                closed.append(trade)
        return closed

    def refresh_trade_counters(self) -> None:
        closed = self.today_closed_trades()
        self.state["take_profit_count"] = sum(1 for trade in closed if fnum(trade.get("pnl_usdt")) > 0)
        self.state["stop_loss_count"] = sum(1 for trade in closed if fnum(trade.get("pnl_usdt")) < 0)
        self.state["realized_pnl_usdt"] = sum(fnum(trade.get("pnl_usdt")) for trade in closed)

    def equity(self) -> float:
        if self.state.get("exchange_account_mode") == self.exchange_mode():
            if "account_equity_usdt" in self.state:
                return fnum(self.state.get("account_equity_usdt"))
            if "available_balance_usdt" in self.state:
                available = fnum(self.state.get("available_balance_usdt"))
                used = sum(fnum(pos.get("margin_usdt")) for pos in self.state.get("positions", []))
                unrealized = sum(fnum(pos.get("unrealized_pnl_usdt")) for pos in self.state.get("positions", []))
                return available + used + unrealized
        initial = float(self.config.get("initial_equity_usdt", 100.0))
        realized = fnum(self.state.get("realized_pnl_usdt"))
        unrealized = sum(fnum(pos.get("unrealized_pnl_usdt")) for pos in self.state.get("positions", []))
        return initial + realized + unrealized

    def update_risk_state(self) -> None:
        equity = self.equity()
        high = max(fnum(self.state.get("equity_high_watermark_usdt")), equity)
        self.state["equity_high_watermark_usdt"] = high
        drawdown = max(0.0, (high - equity) / max(0.01, high) * 100.0)
        self.state["max_drawdown_pct"] = max(fnum(self.state.get("max_drawdown_pct")), drawdown)

    def trade_stats(self) -> dict[str, float]:
        trades = self.today_closed_trades()
        wins = [fnum(t.get("pnl_usdt")) for t in trades if fnum(t.get("pnl_usdt")) > 0]
        losses = [abs(fnum(t.get("pnl_usdt"))) for t in trades if fnum(t.get("pnl_usdt")) < 0]
        closed = len(trades)
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        return {
            "closed_trades": closed,
            "win_rate_pct": len(wins) / closed * 100.0 if closed else 0.0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0),
            "daily_realized_pnl_usdt": self.daily_realized_pnl(),
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self.refresh_candidate_analyzer_cache()
            self.refresh_trade_counters()
            positions = self.state.get("positions", [])
            unrealized = sum(fnum(pos.get("unrealized_pnl_usdt")) for pos in positions)
            self.update_risk_state()
            self.prune_stop_loss_cooldown_overrides()
            self.state["stop_loss_cooldowns"] = self.active_stop_loss_cooldowns()
            stats = self.trade_stats()
            payload = {
                "config": self.config,
                "state": self.state,
                "metrics": {
                    "equity_usdt": self.equity(),
                    "available_margin_usdt": self.available_margin(),
                    "used_margin_usdt": sum(fnum(pos.get("margin_usdt")) for pos in positions),
                    "unrealized_pnl_usdt": unrealized,
                    "total_pnl_usdt": fnum(self.state.get("realized_pnl_usdt")) + unrealized,
                    "open_positions": len(positions),
                    "gross_exposure_usdt": self.gross_exposure(),
                    "exposure_pct": self.exposure_pct(),
                    "max_drawdown_pct": fnum(self.state.get("max_drawdown_pct")),
                    **stats,
                },
                "server_time": iso_now(),
            }
            return json.loads(json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def side_cn(side: str) -> str:
        return "多" if side == "LONG" else "空"


load_dotenv(ROOT / ".env")
BOT = PaperBot(CONFIG_PATH, STATE_PATH)


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperDashboard/1.0"

    def do_GET(self) -> None:
        if self.path.startswith("/api/state"):
            self.write_json(BOT.snapshot())
            return
        self.serve_static()

    def do_POST(self) -> None:
        try:
            if self.path == "/api/start":
                BOT.start()
                self.write_json(BOT.snapshot())
                return
            if self.path == "/api/stop":
                BOT.stop()
                self.write_json(BOT.snapshot())
                return
            if self.path == "/api/scan":
                threading.Thread(target=BOT.scan_once, daemon=True).start()
                self.write_json({"ok": True})
                return
            if self.path == "/api/sync-exchange":
                self.write_json(BOT.sync_exchange_snapshot())
                return
            if self.path == "/api/close-all":
                self.write_json(BOT.close_all_positions())
                return
            if self.path == "/api/reset-test":
                self.write_json(BOT.reset_test_state())
                return
            if self.path == "/api/settings":
                self.write_json(BOT.update_settings(self.read_json_body()))
                return
            if self.path == "/api/clear-cooldown":
                body = self.read_json_body()
                self.write_json(BOT.clear_stop_loss_cooldown(str(body.get("symbol", ""))))
                return
            self.send_error(404)
        except Exception as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=500)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def serve_static(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            path = "/index.html"
        target = (WEB_ROOT / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.exists() or target.is_dir():
            self.send_error(404)
            return
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{iso_now()}] {self.address_string()} {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local paper trading dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Paper trading dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard")
    finally:
        BOT.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
