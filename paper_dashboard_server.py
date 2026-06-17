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
import json
import math
import mimetypes
import os
import random
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


DEFAULT_CONFIG: dict[str, Any] = {
    "initial_equity_usdt": 100.0,
    "execution_mode": "binance_futures_testnet",
    "live_trading_enabled": False,
    "scan_interval_sec": 20,
    "market_base_url": "https://fapi.binance.com",
    "request_timeout_sec": 6,
    "quote_asset": "USDT",
    "min_quote_volume_usdt": 20_000_000,
    "min_abs_24h_change_pct": 6.0,
    "long_24h_change_pct": 7.0,
    "short_24h_change_pct": -7.0,
    "short_term_move_pct": 1.2,
    "volume_spike_ratio": 1.5,
    "trade_direction_mode": "both",
    "signal_score_threshold": 4.0,
    "analyzer_confirmation_enabled": True,
    "analyzer_min_confidence_pct": 90.0,
    "analyzer_timeout_sec": 12.0,
    "analyzer_cache_ttl_sec": 60,
    "analyzer_realtime_before_open": True,
    "analyzer_max_workers": 2,
    "max_candidates_per_scan": 18,
    "max_kline_enriched_candidates": 8,
    "entry_quality_enabled": True,
    "entry_quality_min_score": 6.0,
    "entry_quality_long_only": True,
    "dynamic_position_sizing_enabled": True,
    "dynamic_max_leverage": 6,
    "dynamic_max_margin_multiplier": 1.5,
    "max_new_positions_per_scan": 2,
    "margin_per_trade_usdt": 9.0,
    "leverage": 3,
    "max_open_positions": 15,
    "max_exposure_pct": 65.0,
    "max_daily_profit_enabled": True,
    "max_daily_profit_usdt": 30.0,
    "max_daily_loss_enabled": True,
    "max_daily_loss_usdt": 20.0,
    "stop_loss_cooldown_enabled": True,
    "stop_loss_cooldown_minutes": 60,
    "take_profit_roi_pct": 90.0,
    "stop_loss_roi_pct": 86.0,
    "telegram_enabled": True,
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
    entry_quality_score: float
    entry_quality_label: str
    entry_quality_reason: str
    risk_level: str
    analyzer_direction: str
    analyzer_confidence_pct: float
    analyzer_reason: str
    trigger_reason: str
    reject_reason: str
    decision_summary: str
    decision_steps: list[str]
    priority: str


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
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_request_json(
    method: str,
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 6,
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Binance Testnet HTTP {exc.code}: {body}") from exc


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
        if "telegram_close_notified_ids" not in state:
            state["telegram_close_notified_ids"] = [
                str(item.get("id", ""))
                for item in state.get("trade_history", [])
                if item.get("closed_at") and item.get("id")
            ][:1000]
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
            if str(item.get("id", "")).startswith("income-")
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
        return self.config.get(key, {})

    def record_belongs_to_current_exchange(self, record: dict[str, Any]) -> bool:
        mode = str(record.get("execution_mode", ""))
        if mode:
            return mode == self.exchange_mode()
        return not self.is_live_trading()

    def load_signal_review_state(self) -> dict[str, Any]:
        return load_json(SIGNAL_REVIEW_STATE_PATH, {"signals": {}, "open_orders": {}})

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
            "entry_quality_score": signal.entry_quality_score,
            "entry_quality_label": signal.entry_quality_label,
            "entry_quality_reason": signal.entry_quality_reason,
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

    def send_telegram_message(self, text: str) -> None:
        if not self.config.get("telegram_enabled", True):
            return
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            self.event("warn", "Telegram 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，已跳过通知")
            return
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
        except Exception as exc:
            self.event("warn", f"Telegram 通知发送失败：{exc}")

    def notify_open_position(self, signal: Signal, record: dict[str, Any]) -> None:
        side = self.side_cn(signal.side)
        analyzer_text = "-"
        if signal.analyzer_direction:
            analyzer_text = f"{signal.analyzer_direction} {signal.analyzer_confidence_pct:.1f}%"
        message = "\n".join(
            [
                "Binance Testnet 开仓成功",
                f"币种：{signal.symbol} {side}",
                f"开仓价：{price_text(signal.price)}",
                f"保证金：{fnum(record.get('margin_usdt')):.2f} U",
                f"名义价值：{fnum(record.get('notional_usdt')):.2f} U",
                f"止盈：{record.get('take_profit', '-')}",
                f"止损：{record.get('stop_price', '-')}",
                f"系统分：{signal.score:.2f}",
                f"分析器：{analyzer_text}",
                f"触发：{signal.trigger_reason}",
                f"时间：{iso_now()}",
            ]
        )
        self.send_telegram_message(message)

    def notify_close_position(self, trade: dict[str, Any]) -> None:
        side = self.side_cn(str(trade.get("side", "")))
        pnl = fnum(trade.get("pnl_usdt"))
        message = "\n".join(
            [
                "Binance Testnet 平仓完成",
                f"币种：{trade.get('symbol', '-')} {side}",
                f"平仓原因：{trade.get('close_reason', '-')}",
                f"开仓价：{price_text(fnum(trade.get('entry_price')))}",
                f"平仓价：{price_text(fnum(trade.get('exit_price')))}",
                f"数量：{trade.get('quantity', '-')}",
                f"保证金：{fnum(trade.get('margin_usdt')):.2f} U",
                f"已实现盈亏：{pnl:+.2f} U",
                f"ROI：{fnum(trade.get('roi_pct')):+.2f}%",
                f"开仓理由：{trade.get('trigger_reason', '-')}",
                f"时间：{trade.get('closed_at') or iso_now()}",
            ]
        )
        self.send_telegram_message(message)

    def notify_new_closed_trades(self, trades: list[dict[str, Any]]) -> None:
        if not trades:
            return
        notified = [str(item) for item in self.state.get("telegram_close_notified_ids", []) if str(item)]
        notified_set = set(notified)
        added = []
        for trade in sorted(trades, key=lambda item: str(item.get("closed_at", ""))):
            trade_id = str(trade.get("id", ""))
            if not trade_id or trade_id in notified_set:
                continue
            self.notify_close_position(trade)
            notified_set.add(trade_id)
            added.append(trade_id)
        if added:
            self.state["telegram_close_notified_ids"] = list(dict.fromkeys([*added, *notified]))[:1000]

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
                fnum(item.get("exit_price"), fnum(item.get("last_price"))),
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

    def daily_excel_report_loop(self) -> None:
        while True:
            try:
                now = datetime.now().astimezone()
                next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
                time.sleep(max(1.0, (next_midnight - now).total_seconds()))
                if not self.config.get("daily_excel_report_enabled", True):
                    continue
                report_date = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
                try:
                    self.sync_exchange_state()
                except Exception as exc:
                    self.event("warn", f"生成日报前同步交易记录失败：{exc}")
                self.export_daily_realized_pnl_excel(report_date)
            except Exception as exc:
                self.event("error", f"每日 Excel 报表生成失败：{exc}")
                time.sleep(60)

    def build_square_open_post(self, signal: Signal, record: dict[str, Any]) -> str:
        side = self.side_cn(signal.side)
        side_view = "\u770b\u591a" if signal.side == "LONG" else "\u770b\u7a7a"
        analyzer_text = "-"
        if signal.analyzer_direction:
            analyzer_text = f"{signal.analyzer_direction} {signal.analyzer_confidence_pct:.1f}%"
        quality_text = "-"
        if signal.entry_quality_label:
            quality_text = f"{signal.entry_quality_label} {signal.entry_quality_score:.1f}"
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
                f"\u4fe1\u53f7\uff1a\u7cfb\u7edf\u5206 {signal.score:.2f}\uff5c\u5165\u573a\u8d28\u91cf {quality_text}\uff5c\u4e8c\u6b21\u786e\u8ba4 {analyzer_text}",
                f"\u7406\u7531\uff1a{reason}",
            ],
            [
                f"${square_symbol} \u8fd9\u91cc\u5f00\u59cb\u6709\u70b9\u610f\u601d\u4e86\uff0c\u6211\u73b0\u5728\u504f{side_view}\u3002",
                f"\u8fd9\u5355\u770b\u7684\u662f\u77ed\u7ebf\u5ef6\u7eed\uff0c\u5165\u573a {entry}\uff0c\u9632\u5b88 {stop_price}\uff0c\u76ee\u6807\u5148\u770b {take_profit}\u3002",
                f"\u7cfb\u7edf\u5206 {signal.score:.2f}\uff0c\u5165\u573a\u8d28\u91cf {quality_text}\uff0c\u4e8c\u6b21\u786e\u8ba4 {analyzer_text}\u3002",
                f"\u89e6\u53d1\u70b9\uff1a{reason}",
            ],
            [
                f"${square_symbol} \u5f53\u524d{side_view}\u7ed3\u6784\u6bd4\u8f83\u987a\uff0c\u8bb0\u5f55\u4e00\u7b14{side}\u5355\u3002",
                f"\u5165\u573a\u8d28\u91cf\uff1a{quality_text}\uff5c\u7cfb\u7edf\u5206\uff1a{signal.score:.2f}\uff5c\u4e8c\u6b21\u786e\u8ba4\uff1a{analyzer_text}",
                f"\u70b9\u4f4d\uff1a\u5165\u573a {entry}\uff0c\u6b62\u76c8 {take_profit}\uff0c\u6b62\u635f {stop_price}",
                f"\u4e3b\u8981\u903b\u8f91\uff1a{reason}",
            ],
            [
                f"${square_symbol} \u6ce2\u52a8\u8d77\u6765\u4e86\uff0c\u8fd9\u79cd\u7968\u6211\u53ea\u770b\u8282\u594f\uff0c\u4e0d\u604b\u6218\u3002",
                f"\u65b9\u5411\uff1a{side_view}\uff5c\u5165\u573a\uff1a{entry}\uff5c\u6b62\u76c8\uff1a{take_profit}\uff5c\u6b62\u635f\uff1a{stop_price}",
                f"\u4fe1\u53f7\u5171\u632f\uff1a\u7cfb\u7edf\u5206 {signal.score:.2f}\uff0c\u8d28\u91cf {quality_text}\uff0c\u786e\u8ba4 {analyzer_text}",
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
                self.event("trade", f"已提交 Binance Testnet 全部平仓 {closed} 笔")
                self.persist()
        except Exception as exc:
            with self.lock:
                self.state["last_error"] = str(exc)
                self.event("error", f"全部平仓失败：{exc}")
                self.persist()
        self.sync_exchange_state()
        return self.snapshot()

    def sync_exchange_snapshot(self) -> dict[str, Any]:
        try:
            self.sync_exchange_state()
        except Exception as exc:
            with self.lock:
                self.state["exchange_status"] = "error"
                self.state["last_error"] = str(exc)
                self.event("error", f"Binance Testnet 同步失败：{exc}")
                self.persist()
        return self.snapshot()

    def reset_test_state(self) -> dict[str, Any]:
        try:
            self.sync_exchange_state()
        except Exception as exc:
            with self.lock:
                self.state["exchange_status"] = "error"
                self.state["last_error"] = str(exc)
                self.event("error", f"重置前同步 Binance Testnet 失败：{exc}")
                self.persist()
            return {"ok": False, "error": f"重置前同步失败：{exc}", **self.snapshot()}
        with self.lock:
            if self.state.get("positions"):
                return {"ok": False, "error": "Binance Testnet 仍有持仓，请先全部平仓后再重置。", **self.snapshot()}
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
            "scan_interval_sec": int,
            "max_open_positions": int,
            "max_exposure_pct": float,
            "max_daily_profit_enabled": str,
            "max_daily_profit_usdt": float,
            "max_daily_loss_enabled": str,
            "max_daily_loss_usdt": float,
            "entry_quality_enabled": str,
            "entry_quality_min_score": float,
            "entry_quality_long_only": str,
            "dynamic_position_sizing_enabled": str,
            "dynamic_max_leverage": int,
            "dynamic_max_margin_multiplier": float,
            "signal_score_threshold": float,
            "analyzer_min_confidence_pct": float,
            "max_new_positions_per_scan": int,
            "trade_direction_mode": str,
            "blocked_symbols": str,
            "live_trading_enabled": str,
            "square_publish_enabled": str,
            "square_publish_on_open": str,
        }
        with self.lock:
            for key, caster in allowed.items():
                if key not in patch:
                    continue
                if key in {"square_publish_enabled", "square_publish_on_open", "max_daily_profit_enabled", "max_daily_loss_enabled", "live_trading_enabled", "entry_quality_enabled", "entry_quality_long_only", "dynamic_position_sizing_enabled"}:
                    raw_value = str(patch[key]).strip().lower()
                    value = raw_value in {"1", "true", "yes", "on"}
                else:
                    value = caster(patch[key])
                if key == "trade_direction_mode":
                    value = str(value)
                    if value not in {"both", "long_only", "short_only"}:
                        value = "both"
                if key == "blocked_symbols":
                    value = normalize_symbol_list(value, str(self.config.get("quote_asset", "USDT")))
                if key in {"leverage", "scan_interval_sec", "max_open_positions", "max_new_positions_per_scan", "dynamic_max_leverage"}:
                    value = max(1, int(value))
                if key in {
                    "margin_per_trade_usdt",
                    "take_profit_roi_pct",
                    "stop_loss_roi_pct",
                    "signal_score_threshold",
                    "analyzer_min_confidence_pct",
                    "max_exposure_pct",
                    "max_daily_profit_usdt",
                    "max_daily_loss_usdt",
                    "entry_quality_min_score",
                    "dynamic_max_margin_multiplier",
                }:
                    value = max(0.01, float(value))
                self.config[key] = value
            save_json(self.config_path, self.config)
            self.state["mode"] = self.exchange_mode()
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

    def scan_once(self) -> None:
        started = time.monotonic()
        with self.lock:
            if self.state.get("scan_in_progress"):
                self.event("warn", "已有扫描正在进行，已跳过重复触发")
                self.persist()
                return
            self.state["scan_in_progress"] = True
            self.state["scan_count"] = int(self.state.get("scan_count", 0)) + 1
            self.state["last_scan_at"] = iso_now()
            self.state["last_error"] = ""
        try:
            try:
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
            self.update_signal_future_returns(price_map)
            try:
                self.sync_exchange_state()
            except Exception as exc:
                with self.lock:
                    self.state["exchange_status"] = "error"
                    self.state["last_error"] = str(exc)
                    self.event("error", f"Binance Testnet 同步失败：{exc}")
                    self.persist()
                return
            with self.lock:
                for pos in self.state.get("positions", []):
                    if pos.get("symbol") in price_map:
                        pos["last_price"] = price_map[pos["symbol"]]
                open_symbols = {p["symbol"] for p in self.state.get("positions", [])}
            signals = self.build_signals(tickers, open_symbols)
            with self.lock:
                self.state["candidates"] = [signal.__dict__ for signal in signals]
                opened = self.open_from_signals(signals)
                self.record_signal_review_scan(signals)
                if opened:
                    self.event("trade", f"本轮 Binance Testnet 开仓 {opened} 笔")
            if opened:
                self.sync_exchange_state()
        finally:
            with self.lock:
                self.update_risk_state()
                self.state["scan_in_progress"] = False
                self.state["last_scan_finished_at"] = iso_now()
                self.state["last_scan_duration_sec"] = round(time.monotonic() - started, 2)
                self.persist()

    def fetch_tickers(self) -> list[dict[str, Any]]:
        timeout = float(self.config.get("request_timeout_sec", 6))
        payload = http_get_json(self.config["market_base_url"], "/fapi/v1/ticker/24hr", timeout=timeout)
        quote = str(self.config.get("quote_asset", "USDT"))
        excluded = set(self.config.get("exclude_symbols", []))
        result = []
        for item in payload:
            symbol = item.get("symbol", "")
            if symbol.endswith(quote) and symbol not in excluded and fnum(item.get("lastPrice")) > 0:
                result.append(item)
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
        payload = dict(params or {})
        payload.setdefault("recvWindow", int(futures.get("recv_window", 5000)))
        payload["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(payload, doseq=True)
        signature = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        signed = dict(payload)
        signed["signature"] = signature
        return http_request_json(
            method,
            futures.get("base_url", "https://fapi.binance.com" if self.is_live_trading() else "https://testnet.binancefuture.com"),
            path,
            signed,
            headers={"X-MBX-APIKEY": api_key},
            timeout=float(self.config.get("request_timeout_sec", 6)),
        )

    def unsigned_exchange_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        futures = self.exchange_config()
        return http_get_json(
            futures.get("base_url", "https://fapi.binance.com" if self.is_live_trading() else "https://testnet.binancefuture.com"),
            path,
            params,
            timeout=float(self.config.get("request_timeout_sec", 6)),
        )

    def get_exchange_symbols(self) -> dict[str, dict[str, Any]]:
        payload = self.unsigned_exchange_request("/fapi/v1/exchangeInfo")
        result: dict[str, dict[str, Any]] = {}
        for item in payload.get("symbols", []):
            symbol = item.get("symbol")
            if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT":
                result[symbol] = item
        return result

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

    def sync_exchange_state(self) -> None:
        payload = self.signed_exchange_request("GET", "/fapi/v2/positionRisk")
        with self.lock:
            open_history = [item for item in self.state.get("open_order_history", []) if self.record_belongs_to_current_exchange(item)]
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
                    "trigger_reason": "Binance Testnet 持仓同步",
                    "opened_at": item.get("updateTime") or iso_now(),
                    "unrealized_pnl_usdt": unrealized,
                    "roi_pct": unrealized / max(0.01, margin) * 100.0,
                    "liquidation_price": fnum(item.get("liquidationPrice")),
                    "entry_order_id": open_record.get("entry_order_id", ""),
                    "signal_review_id": open_record.get("signal_review_id", ""),
                    "score": fnum(open_record.get("score")),
                    "analyzer_direction": open_record.get("analyzer_direction", ""),
                    "analyzer_confidence_pct": fnum(open_record.get("analyzer_confidence_pct")),
                    "decision_summary": open_record.get("decision_summary", ""),
                    "decision_steps": open_record.get("decision_steps", []),
                }
            )
        realized_history = self.fetch_realized_pnl_history(open_history)
        available_balance = self.get_available_balance()
        with self.lock:
            self.notify_new_closed_trades(realized_history)
            self.state["trade_history"] = realized_history[:200]
            self.state["positions"] = positions
            self.state["exchange_open_positions"] = positions
            self.state["exchange_status"] = "connected"
            self.state["available_balance_usdt"] = available_balance
            self.refresh_trade_counters()
            self.update_risk_state()
            self.persist()

    def fetch_recent_user_trades(self, symbol: str, start_time_ms: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": 1000}
        if start_time_ms > 0:
            params["startTime"] = max(0, start_time_ms)
        payload = self.signed_exchange_request("GET", "/fapi/v1/userTrades", params)
        return payload if isinstance(payload, list) else []

    def fetch_realized_pnl_income(self, start_time_ms: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"incomeType": "REALIZED_PNL", "limit": 1000}
        if start_time_ms > 0:
            params["startTime"] = max(0, start_time_ms)
        payload = self.signed_exchange_request("GET", "/fapi/v1/income", params)
        return payload if isinstance(payload, list) else []

    def find_open_record_for_income(self, income: dict[str, Any], open_history: list[dict[str, Any]]) -> dict[str, Any]:
        symbol = str(income.get("symbol", ""))
        income_time = fnum(income.get("time"))
        candidates = [
            record
            for record in open_history
            if str(record.get("symbol", "")) == symbol and parse_time_ms(record.get("time")) <= income_time
        ]
        if not candidates:
            return {}
        return max(candidates, key=lambda record: parse_time_ms(record.get("time")))

    def find_open_record_for_position(self, symbol: str, side: str, open_history: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = [
            record
            for record in open_history
            if str(record.get("symbol", "")) == symbol and str(record.get("side", "")) == side
        ]
        if not candidates:
            return {}
        return max(candidates, key=lambda record: parse_time_ms(record.get("time")))

    def fetch_realized_pnl_history(self, open_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        start_ms = parse_time_ms(self.state.get("test_started_at"))
        if not start_ms:
            open_times = [parse_time_ms(record.get("time")) for record in open_history]
            open_times = [value for value in open_times if value > 0]
            if open_times:
                start_ms = min(open_times)
        rows = []
        seen = set()
        for item in self.fetch_realized_pnl_income(start_ms):
            pnl = fnum(item.get("income"))
            if abs(pnl) <= 0:
                continue
            symbol = str(item.get("symbol", ""))
            income_time = fnum(item.get("time"))
            row_id = str(item.get("tranId") or f"{symbol}-{income_time}-{pnl}")
            if row_id in seen:
                continue
            seen.add(row_id)
            open_record = self.find_open_record_for_income(item, open_history)
            reason = "Binance Testnet 止盈平仓" if pnl > 0 else "Binance Testnet 止损平仓"
            row = {
                "id": f"{self.exchange_mode()}-income-{row_id}",
                "execution_mode": self.exchange_mode(),
                "symbol": symbol,
                "side": open_record.get("side", ""),
                "entry_price": fnum(open_record.get("entry_price")),
                "exit_price": 0.0,
                "quantity": fnum(open_record.get("quantity")),
                "margin_usdt": fnum(open_record.get("margin_usdt")),
                "pnl_usdt": pnl,
                "roi_pct": pnl / max(0.01, fnum(open_record.get("margin_usdt"))) * 100.0 if open_record else 0.0,
                "opened_at": open_record.get("time") or "",
                "closed_at": datetime.fromtimestamp(income_time / 1000, tz=timezone.utc).isoformat(timespec="seconds")
                if income_time > 0
                else iso_now(),
                "close_reason": reason,
                "trigger_reason": open_record.get("trigger_reason", "Binance Testnet 资金流水"),
                "entry_order_id": open_record.get("entry_order_id", ""),
                "signal_review_id": open_record.get("signal_review_id", ""),
                "strategy_score": fnum(open_record.get("score")),
                "analyzer_direction": open_record.get("analyzer_direction", ""),
                "analyzer_confidence_pct": fnum(open_record.get("analyzer_confidence_pct")),
                "decision_summary": open_record.get("decision_summary", ""),
                "decision_steps": open_record.get("decision_steps", []),
            }
            self.mark_signal_review_closed(open_record, row)
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("closed_at", "")), reverse=True)
        return rows

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
            "close_reason": f"Binance Testnet {reason}",
            "trigger_reason": pos.get("trigger_reason", "Binance Testnet 持仓同步"),
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
        self.event("trade", f"{pos.get('symbol')} {self.side_cn(str(pos.get('side', '')))} Binance Testnet {reason}，PnL {pnl:+.2f} U")

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

    @staticmethod
    def close_side(signal_side: str) -> str:
        return "SELL" if signal_side == "LONG" else "BUY"

    def ensure_exchange_settings(self, symbol: str, target_leverage: int | None = None) -> int:
        futures = self.exchange_config()
        try:
            self.signed_exchange_request(
                "POST",
                "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": futures.get("margin_type", "ISOLATED")},
            )
        except RuntimeError as exc:
            if "No need to change margin type" not in str(exc):
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
        base_margin = float(self.config.get("margin_per_trade_usdt", 9.0))
        configured_leverage = max(1, int(self.config.get("leverage", 3)))
        if not self.config.get("dynamic_position_sizing_enabled", True):
            margin = min(base_margin, available)
            return {
                "margin_usdt": margin,
                "target_leverage": configured_leverage,
                "margin_multiplier": 1.0,
                "tier": "fixed",
                "reason": "fixed sizing",
            }

        quality = fnum(signal.entry_quality_score)
        tier = "low"
        margin_multiplier = 0.6
        target_leverage = 3
        if quality >= 9.0:
            tier = "excellent"
            margin_multiplier = 1.5
            target_leverage = 6
        elif quality >= 8.0:
            tier = "strong"
            margin_multiplier = 1.2
            target_leverage = 6
        elif quality >= 7.0:
            tier = "standard"
            margin_multiplier = 1.0
            target_leverage = 5

        heat_reasons: list[str] = []
        if signal.funding_rate_pct > 0.04:
            heat_reasons.append("funding")
        if signal.open_interest_1h_pct > 30 or signal.open_interest_24h_pct > 55:
            heat_reasons.append("oi")
        if signal.long_short_ratio > 2.6:
            heat_reasons.append("crowded")
        if signal.short_move_pct > 4.5:
            heat_reasons.append("fast_15m")
        if self.daily_realized_pnl() < 0:
            heat_reasons.append("daily_loss")

        for _ in heat_reasons[:2]:
            if margin_multiplier > 1.2:
                margin_multiplier = 1.2
                target_leverage = min(target_leverage, 6)
            elif margin_multiplier > 1.0:
                margin_multiplier = 1.0
                target_leverage = min(target_leverage, 5)
            else:
                margin_multiplier = 0.6
                target_leverage = min(target_leverage, 3)

        max_multiplier = float(self.config.get("dynamic_max_margin_multiplier", 1.5))
        max_dynamic_leverage = max(1, int(self.config.get("dynamic_max_leverage", 6)))
        target_leverage = max(1, min(target_leverage, configured_leverage, max_dynamic_leverage))
        margin_multiplier = max(0.1, min(margin_multiplier, max_multiplier))
        margin = min(base_margin * margin_multiplier, available)
        reason = f"quality={quality:.1f}, tier={tier}, multiplier={margin_multiplier:.2f}, target_leverage={target_leverage}x"
        if heat_reasons:
            reason += ", heat_downshift=" + ",".join(heat_reasons[:2])
        signal.decision_steps.append(f"仓位计划：{reason}, margin={margin:.2f}U")
        return {
            "margin_usdt": margin,
            "target_leverage": target_leverage,
            "margin_multiplier": margin_multiplier,
            "tier": tier,
            "reason": reason,
        }

    def exchange_open_position(self, signal: Signal, symbol_info: dict[str, Any]) -> dict[str, Any] | None:
        available = self.get_available_balance()
        sizing = self.position_plan(signal, available)
        margin = fnum(sizing.get("margin_usdt"))
        if margin <= 0:
            raise RuntimeError(f"{self.exchange_label()} 可用 USDT 余额不足")
        futures = self.exchange_config()
        leverage = float(self.ensure_exchange_settings(signal.symbol, int(sizing.get("target_leverage", self.config.get("leverage", 3)))))
        notional = margin * leverage * float(futures.get("quantity_safety_factor", 0.98))
        if self.exposure_pct(extra_notional=notional) > float(self.config.get("max_exposure_pct", 65.0)):
            self.event("risk", "仓位敞口达到上限，本轮停止新增持仓")
            return None
        qty_text = self.round_quantity(symbol_info, notional / signal.price)
        if fnum(qty_text) <= 0:
            raise RuntimeError(f"{signal.symbol} 计算出的下单数量为 0")
        entry_order = self.signed_exchange_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": signal.symbol,
                "side": self.futures_side(signal.side),
                "type": "MARKET",
                "quantity": qty_text,
            },
        )

        stop_pct = float(self.config.get("stop_loss_roi_pct", 86.0)) / max(1.0, leverage) / 100.0
        take_pct = float(self.config.get("take_profit_roi_pct", 90.0)) / max(1.0, leverage) / 100.0
        if signal.side == "LONG":
            stop_price = signal.price * (1.0 - stop_pct)
            take_profit = signal.price * (1.0 + take_pct)
        else:
            stop_price = signal.price * (1.0 + stop_pct)
            take_profit = signal.price * (1.0 - take_pct)

        stop_order: dict[str, Any] = {}
        take_order: dict[str, Any] = {}
        try:
            stop_order = self.signed_exchange_request(
                "POST",
                "/fapi/v1/algoOrder",
                {
                    "algoType": "CONDITIONAL",
                    "symbol": signal.symbol,
                    "side": self.close_side(signal.side),
                    "type": "STOP_MARKET",
                    "triggerPrice": self.round_price(symbol_info, stop_price),
                    "closePosition": "true",
                    "workingType": futures.get("working_type", "MARK_PRICE"),
                },
            )
            take_order = self.signed_exchange_request(
                "POST",
                "/fapi/v1/algoOrder",
                {
                    "algoType": "CONDITIONAL",
                    "symbol": signal.symbol,
                    "side": self.close_side(signal.side),
                    "type": "TAKE_PROFIT_MARKET",
                    "triggerPrice": self.round_price(symbol_info, take_profit),
                    "closePosition": "true",
                    "workingType": futures.get("working_type", "MARK_PRICE"),
                },
            )
        except Exception as exc:
            self.event("error", f"{signal.symbol} 已开仓但保护单提交失败：{exc}")
            try:
                self.signed_exchange_request(
                    "POST",
                    "/fapi/v1/order",
                    {
                        "symbol": signal.symbol,
                        "side": self.close_side(signal.side),
                        "type": "MARKET",
                        "quantity": qty_text,
                        "reduceOnly": "true",
                    },
                )
                self.event("risk", f"{signal.symbol} 保护单失败，已提交紧急平仓")
            except Exception as close_exc:
                self.event("error", f"{signal.symbol} 紧急平仓失败：{close_exc}")
            raise

        record = {
            "time": iso_now(),
            "execution_mode": self.exchange_mode(),
            "symbol": signal.symbol,
            "side": signal.side,
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
            "decision_summary": signal.decision_summary,
            "decision_steps": list(signal.decision_steps),
            "signal_review_id": self.signal_review_id(int(self.state.get("scan_count", 0)), signal.symbol),
        }
        self.state.setdefault("open_order_history", []).insert(0, {**record, "close_reason": f"{self.exchange_record_prefix()} 开仓记录"})
        self.state["open_order_history"] = self.state["open_order_history"][:500]
        return record

    def exchange_close_position(self, pos: dict[str, Any]) -> None:
        symbol = pos["symbol"]
        side = self.close_side(pos["side"])
        qty_text = str(pos.get("quantity"))
        self.signed_exchange_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": qty_text,
                "reduceOnly": "true",
            },
        )

    def fetch_klines(self, symbol: str) -> list[list[Any]]:
        timeout = min(2.0, float(self.config.get("request_timeout_sec", 6)))
        return http_get_json(
            self.config["market_base_url"],
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1m", "limit": 45},
            timeout=timeout,
        )

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

    def entry_quality(self, side: str, change_24h: float, change_4h: float, change_1h: float, short_move: float, volume_ratio: float, trend: str, stats: dict[str, float], enriched: bool) -> dict[str, Any]:
        score = 5.0
        reasons: list[str] = []
        if not self.config.get("entry_quality_enabled", True):
            return {"score": 10.0, "label": "关闭", "reason": "入场质量过滤未启用"}
        if self.config.get("entry_quality_long_only", True) and side != "LONG":
            return {"score": 10.0, "label": "跳过", "reason": "入场质量过滤仅作用于做多"}
        if not enriched:
            score -= 2.0
            reasons.append("未取得真实短线K线")
        if trend == "up":
            score += 1.2
            reasons.append("短线趋势向上")
        else:
            score -= 1.6
            reasons.append("短线趋势不顺")
        if 0.35 <= short_move <= 4.5:
            score += 1.4
            reasons.append(f"15m涨幅健康 {short_move:+.2f}%")
        elif short_move > 4.5:
            score -= 1.6
            reasons.append(f"15m拉升过快 {short_move:+.2f}%")
        elif short_move < -0.4:
            score -= 1.4
            reasons.append(f"15m转弱 {short_move:+.2f}%")
        if 0.5 <= change_1h <= 12:
            score += 1.0
            reasons.append(f"1h仍强 {change_1h:+.2f}%")
        elif change_1h > 18:
            score -= 1.4
            reasons.append(f"1h过热 {change_1h:+.2f}%")
        elif change_1h < 0:
            score -= 1.0
            reasons.append(f"1h转弱 {change_1h:+.2f}%")
        if change_24h > 60:
            score -= 1.8
            reasons.append(f"24h涨幅过大 {change_24h:+.2f}%")
        elif 7 <= change_24h <= 45:
            score += 0.8
            reasons.append(f"24h强势未极端 {change_24h:+.2f}%")
        if 1.2 <= volume_ratio <= 5.5:
            score += 1.0
            reasons.append(f"放量合理 {volume_ratio:.2f}x")
        elif volume_ratio > 8:
            score -= 1.0
            reasons.append(f"短线量能过热 {volume_ratio:.2f}x")
        funding = fnum(stats.get("funding_rate_pct"))
        oi_1h = fnum(stats.get("open_interest_1h_pct"))
        oi_24h = fnum(stats.get("open_interest_24h_pct"))
        long_short = fnum(stats.get("long_short_ratio"), 1.0)
        taker_ratio = fnum(stats.get("taker_long_short_ratio"), 1.0)
        if funding > 0.08:
            score -= 1.5
            reasons.append(f"资金费率过热 {funding:+.3f}%")
        elif funding > 0.04:
            score -= 0.6
            reasons.append(f"资金费率偏热 {funding:+.3f}%")
        else:
            score += 0.4
            reasons.append("资金费率未极端")
        if 1 <= oi_1h <= 18:
            score += 0.8
            reasons.append(f"OI温和增加 {oi_1h:+.2f}%")
        elif oi_1h > 30 or oi_24h > 55:
            score -= 1.3
            reasons.append("OI增加过快")
        if taker_ratio >= 1.08:
            score += 0.7
            reasons.append(f"主动买入偏强 {taker_ratio:.2f}")
        elif taker_ratio < 0.92:
            score -= 0.8
            reasons.append(f"主动买入偏弱 {taker_ratio:.2f}")
        if long_short > 2.6:
            score -= 1.0
            reasons.append(f"多空比拥挤 {long_short:.2f}")
        elif 1.05 <= long_short <= 2.2:
            score += 0.4
            reasons.append(f"多空比可接受 {long_short:.2f}")
        score = max(0.0, min(10.0, score))
        if score >= 7.5:
            label = "优秀"
        elif score >= float(self.config.get("entry_quality_min_score", 6.0)):
            label = "合格"
        elif score >= 4.5:
            label = "偏弱"
        else:
            label = "不合格"
        return {"score": round(score, 1), "label": label, "reason": " / ".join(reasons[:7])}

    def analyzer_confirmation(self, symbol: str) -> dict[str, Any]:
        if not self.config.get("analyzer_confirmation_enabled", True):
            return {"direction": "关闭", "confidence": 0.0, "reason": "分析器确认未启用"}
        now = time.time()
        cached = self.analyzer_cache.get(symbol)
        ttl = max(30, int(self.config.get("analyzer_cache_ttl_sec", 300)))
        if cached and now - fnum(cached.get("time")) <= ttl:
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
                if candidate.get("reject_reason") == "等待分析器确认":
                    candidate["reject_reason"] = self.analyzer_reject_reason(str(candidate.get("side", "")), result)
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

    def analyzer_reject_reason(self, system_side: str, result: dict[str, Any]) -> str:
        direction = str(result.get("direction", "等待"))
        confidence = fnum(result.get("confidence"))
        reason = str(result.get("reason", ""))
        analyzer_side = self.analyzer_side(direction)
        min_confidence = float(self.config.get("analyzer_min_confidence_pct", 90.0))
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
        for candidate in self.state.get("candidates", []):
            symbol = str(candidate.get("symbol", ""))
            result = self.analyzer_cache.get(symbol)
            if not result:
                continue
            if result.get("source") != FAST_CONFIRM_SOURCE:
                continue
            if now - fnum(result.get("time")) > ttl:
                continue
            candidate["analyzer_direction"] = result.get("direction", "等待")
            candidate["analyzer_confidence_pct"] = round(fnum(result.get("confidence")), 1)
            candidate["analyzer_reason"] = result.get("reason", "")
            reject = str(candidate.get("reject_reason", ""))
            if reject == "等待分析器确认" or reject.startswith("分析器"):
                candidate["reject_reason"] = self.analyzer_reject_reason(str(candidate.get("side", "")), result)

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

    def build_signals(self, tickers: list[dict[str, Any]], open_symbols: set[str]) -> list[Signal]:
        watchlist = {str(item).upper() for item in self.config.get("manual_watchlist", [])}
        min_volume = float(self.config.get("min_quote_volume_usdt", 0))
        min_abs_change = float(self.config.get("min_abs_24h_change_pct", 0))
        candidates = []
        for item in tickers:
            symbol = item["symbol"]
            change = fnum(item.get("priceChangePercent"))
            volume = fnum(item.get("quoteVolume"))
            if symbol in watchlist or (volume >= min_volume and abs(change) >= min_abs_change):
                candidates.append(item)
        candidates.sort(key=lambda x: (-abs(fnum(x.get("priceChangePercent"))), -fnum(x.get("quoteVolume"))))
        candidates = candidates[: int(self.config.get("max_candidates_per_scan", 18))]

        signals: list[Signal] = []
        enrich_limit = int(self.config.get("max_kline_enriched_candidates", 8))
        for index, item in enumerate(candidates):
            symbol = item["symbol"]
            price = fnum(item.get("lastPrice"))
            change = fnum(item.get("priceChangePercent"))
            volume = fnum(item.get("quoteVolume"))
            short_move = 0.0
            change_1h = 0.0
            change_4h = 0.0
            volume_ratio = 1.0
            trend = "neutral"
            kline_enriched = False
            try:
                if index >= enrich_limit:
                    raise RuntimeError("skip slow kline enrichment")
                klines = self.fetch_klines(symbol)
                closes = [fnum(k[4]) for k in klines]
                volumes = [fnum(k[7]) for k in klines]
                kline_enriched = bool(closes)
                if len(closes) > 16:
                    short_move = pct(closes[-1], closes[-16])
                    change_1h = short_move
                    trend = infer_trend(closes)
                if len(closes) > 40:
                    change_4h = pct(closes[-1], closes[-40])
                if len(volumes) > 25:
                    recent = sum(volumes[-5:])
                    base = sum(volumes[-25:-5]) / 20 * 5
                    volume_ratio = recent / base if base > 0 else 1.0
                    price = closes[-1] or price
            except Exception:
                short_move = change / 12.0
                change_1h = short_move
                change_4h = change / 6.0
                volume_ratio = 1.0 + min(2.0, abs(change) / 20.0)
                trend = "up" if change > 0 else "down"

            long_score, long_reason = self.score_side("LONG", change, short_move, volume_ratio, trend)
            short_score, short_reason = self.score_side("SHORT", change, short_move, volume_ratio, trend)
            if long_score >= short_score:
                side, score, reason = "LONG", long_score, long_reason
            else:
                side, score, reason = "SHORT", short_score, short_reason

            reject = ""
            if symbol in open_symbols:
                reject = "已有持仓"
            elif self.blocked_symbol_reject_reason(symbol):
                reject = self.blocked_symbol_reject_reason(symbol)
            elif score < float(self.config.get("signal_score_threshold", 4.0)):
                reject = f"分数 {score:.1f} 低于阈值"
            elif side == "LONG" and trend == "down":
                reject = "短线趋势反向"
            elif side == "SHORT" and trend == "up":
                reject = "短线趋势反向"
            else:
                reject = self.direction_mode_reject_reason(side)

            priority = "高" if score >= 6 else "中" if score >= 4.5 else "低"
            try:
                if index >= max(6, enrich_limit):
                    raise RuntimeError("skip slow derivatives enrichment")
                market_stats = self.fetch_signal_market_stats(symbol, item)
            except Exception:
                market_stats = {
                    "open_interest_usdt": volume * 0.22,
                    "open_interest_24h_pct": change * 0.55,
                    "open_interest_4h_pct": change_4h * 0.75,
                    "open_interest_1h_pct": change_1h * 0.75,
                    "funding_rate_pct": 0.0,
                    "long_short_ratio": 1.0,
                    "volume_4h_ratio": max(0.1, min(20.0, volume_ratio * 3.2)),
                    "taker_long_short_ratio": 1.0,
                    "oi_volume_ratio": 0.22,
                }
            quality = self.entry_quality(side, change, change_4h, change_1h, short_move, volume_ratio, trend, market_stats, kline_enriched)
            if not reject and self.config.get("entry_quality_enabled", True):
                min_quality = float(self.config.get("entry_quality_min_score", 6.0))
                if fnum(quality.get("score")) < min_quality:
                    reject = f"入场质量 {fnum(quality.get('score')):.1f} 低于 {min_quality:.1f}：{quality.get('reason')}"
            analyzer_direction = "未分析"
            analyzer_confidence = 0.0
            analyzer_reason = ""
            should_update_analyzer = not reject or reject == "已有持仓"
            if should_update_analyzer and self.config.get("analyzer_confirmation_enabled", True):
                original_reject = reject
                try:
                    analyzer_result = self.analyzer_confirmation(symbol)
                    analyzer_direction = str(analyzer_result.get("direction", "观望"))
                    analyzer_confidence = fnum(analyzer_result.get("confidence"))
                    analyzer_reason = str(analyzer_result.get("reason", ""))
                    if not original_reject:
                        reject = self.analyzer_reject_reason(side, analyzer_result)
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
            decision_steps.insert(-1, f"入场质量：{quality.get('label')} {fnum(quality.get('score')):.1f}，{quality.get('reason') or '-'}")
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
                    entry_quality_score=fnum(quality.get("score")),
                    entry_quality_label=str(quality.get("label", "")),
                    entry_quality_reason=str(quality.get("reason", "")),
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
            reason = str(trade.get("close_reason", ""))
            pnl = fnum(trade.get("pnl_usdt"))
            if "止损" not in reason and pnl >= 0:
                continue
            closed_ms = parse_time_ms(trade.get("closed_at"))
            if closed_ms > latest_stop_ms:
                latest_stop_ms = closed_ms
        if latest_stop_ms <= 0:
            return ""
        elapsed_sec = max(0, (now_ms - latest_stop_ms) / 1000)
        remaining_sec = cooldown_sec - elapsed_sec
        if remaining_sec <= 0:
            return ""
        remaining_min = math.ceil(remaining_sec / 60)
        return f"止损冷静期剩余 {remaining_min} 分钟"

    def open_from_signals(self, signals: list[Signal]) -> int:
        open_count = len(self.state.get("positions", []))
        max_open = int(self.config.get("max_open_positions", 8))
        capacity = max(0, max_open - open_count)
        if capacity <= 0:
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
        except Exception as exc:
            self.event("error", f"读取 Binance Testnet 交易规则失败：{exc}")
            return 0
        opened = 0
        for signal in signals:
            if opened >= min(capacity, int(self.config.get("max_new_positions_per_scan", 2))):
                break
            if signal.reject_reason or signal.price <= 0:
                continue
            quality_applies = self.config.get("entry_quality_enabled", True) and (
                not self.config.get("entry_quality_long_only", True) or signal.side == "LONG"
            )
            if quality_applies and fnum(signal.entry_quality_score) < float(self.config.get("entry_quality_min_score", 6.0)):
                quality_reason = f"入场质量 {signal.entry_quality_score:.1f} 未达标：{signal.entry_quality_reason}"
                self.update_signal_decision(signal, quality_reason, f"开仓前复核：{quality_reason}")
                self.event("risk", f"{signal.symbol} {quality_reason}，跳过开仓")
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
                self.update_signal_decision(signal, "Testnet 无此交易对", "开仓前复核：不在 Binance Futures Testnet 交易对中")
                self.event("warn", f"{signal.symbol} 不在 Binance Futures Testnet 交易对中，跳过")
                continue
            if self.config.get("analyzer_confirmation_enabled", True) and self.config.get("analyzer_realtime_before_open", True):
                try:
                    realtime_result = self.analyze_symbol_now(signal.symbol)
                    with self.lock:
                        self.save_analyzer_result(signal.symbol, realtime_result)
                    reject = self.analyzer_reject_reason(signal.side, realtime_result)
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
                self.update_signal_decision(signal, f"Binance Testnet 开仓失败：{exc}", f"下单结果：失败，{exc}")
                self.event("error", f"{signal.symbol} Binance Testnet 开仓失败：{exc}")
                continue
            if record:
                signal.reject_reason = ""
                signal.decision_summary = "已提交 Binance Testnet 开仓"
                signal.decision_steps.append(f"下单结果：已开仓，订单 {record.get('entry_order_id') or '-'}")
                record["decision_summary"] = signal.decision_summary
                record["decision_steps"] = list(signal.decision_steps)
                self.mark_signal_review_opened(signal, record)
                self.event(
                    "trade",
                    f"Binance Testnet 开仓 {signal.symbol} {self.side_cn(signal.side)}，保证金 {record['margin_usdt']:.2f} U",
                )
                self.notify_open_position(signal, record)
                self.publish_square_open_position_async(signal, record)
                opened += 1
        return opened

    def available_margin(self) -> float:
        if "available_balance_usdt" in self.state:
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
        today = utc_now().date().isoformat()
        total = 0.0
        for trade in self.state.get("trade_history", []):
            closed_at = str(trade.get("closed_at", ""))
            if closed_at.startswith(today):
                total += fnum(trade.get("pnl_usdt"))
        return total

    def refresh_trade_counters(self) -> None:
        closed = [trade for trade in self.state.get("trade_history", []) if trade.get("closed_at")]
        self.state["take_profit_count"] = sum(1 for trade in closed if "止盈" in str(trade.get("close_reason", "")))
        self.state["stop_loss_count"] = sum(1 for trade in closed if "止损" in str(trade.get("close_reason", "")))
        self.state["realized_pnl_usdt"] = sum(fnum(trade.get("pnl_usdt")) for trade in closed)

    def equity(self) -> float:
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
        trades = [trade for trade in self.state.get("trade_history", []) if trade.get("closed_at")]
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
