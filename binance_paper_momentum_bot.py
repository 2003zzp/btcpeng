#!/usr/bin/env python3
"""
Binance USD-M Futures Testnet momentum bot.

The script scans public Binance market data, then submits real orders to
Binance Futures Testnet with stop-loss and take-profit close-position orders.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "market_base_url": "https://fapi.binance.com",
    "request_timeout_sec": 15,
    "telegram_timeout_sec": 6,
    "quote_assets": ["USDT"],
    "scan_interval_sec": 60,
    "verbose_console": True,
    "execution_mode": "binance_futures_testnet",
    "kline_interval": "1m",
    "kline_limit": 80,
    "trend_fast_interval": "5m",
    "trend_slow_interval": "15m",
    "trend_kline_limit": 80,
    "require_trend_confirmation": True,
    "short_term_lookback_candles": 15,
    "recent_volume_candles": 5,
    "baseline_volume_candles": 30,
    "new_symbol_hours": 72,
    "manual_watchlist": [],
    "exclude_symbols": ["USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "EURUSDT"],
    "min_quote_volume_usdt": 20_000_000,
    "long_24h_change_pct": 8.0,
    "short_24h_change_pct": -8.0,
    "short_term_move_pct": 2.0,
    "volume_spike_ratio": 1.8,
    "min_signal_score": 4.0,
    "max_candidates_per_scan": 30,
    "decision_log_top_n": 10,
    "futures_testnet": {
        "base_url": "https://testnet.binancefuture.com",
        "leverage": 3,
        "margin_type": "ISOLATED",
        "margin_per_trade_pct": 5.0,
        "max_position_usdt": 0.0,
        "max_open_positions": 10,
        "stop_loss_pct": 2.5,
        "take_profit_pct": 6.0,
        "recv_window": 5000,
        "working_type": "MARK_PRICE",
        "quantity_safety_factor": 0.98
    },
    "onchain": {
        "enabled": True,
        "provider": "geckoterminal",
        "base_url": "https://api.geckoterminal.com/api/v2",
        "reject_on_missing_mapping": False,
        "min_liquidity_usd": 100_000.0,
        "min_volume_24h_usd": 100_000.0,
        "long_min_buy_sell_ratio_1h": 1.15,
        "short_max_buy_sell_ratio_1h": 0.85,
        "symbols": {}
    },
    "data_dir": "data",
}

ACTIVE_CONFIG: dict[str, Any] = DEFAULT_CONFIG


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


@dataclass
class Signal:
    symbol: str
    side: str
    score: float
    price: float
    quote_volume: float
    change_24h_pct: float
    short_move_pct: float
    volume_ratio: float
    is_new: bool
    reasons: list[str]


@dataclass
class AnalysisResult:
    signal: Signal | None
    symbol: str
    price: float
    long_score: float
    short_score: float
    chosen_side: str
    chosen_score: float
    change_24h_pct: float
    short_move_pct: float
    volume_ratio: float
    quote_volume: float
    is_new: bool
    long_reasons: list[str]
    short_reasons: list[str]
    reject_reason: str | None


@dataclass
class OnchainResult:
    allowed: bool
    reason: str
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    buy_sell_ratio_1h: float = 0.0
    pool_address: str = ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def load_config(path: Path) -> dict[str, Any]:
    user_config = load_json(path, {})
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    deep_update(config, user_config)
    return config


def deep_update(target: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value


def http_get_json(base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "futures-testnet-momentum-bot/0.1",
            "Accept": "application/json",
        },
    )
    try:
        timeout = float(ACTIVE_CONFIG.get("telegram_timeout_sec", 6))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from Binance: {body}") from exc


def http_post_form_json(url: str, params: dict[str, Any]) -> Any:
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "futures-testnet-momentum-bot/0.1",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        timeout = float(ACTIVE_CONFIG.get("request_timeout_sec", 15))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from Telegram: {body}") from exc


def signed_futures_request(
    config: dict[str, Any],
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> Any:
    api_key = os.environ.get("BINANCE_API_KEY", "").strip()
    api_secret = os.environ.get("BINANCE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("缺少 BINANCE_API_KEY 或 BINANCE_API_SECRET，请先在 .env 配置币安合约 Testnet key。")

    futures = config["futures_testnet"]
    payload = dict(params or {})
    payload.setdefault("recvWindow", int(futures["recv_window"]))
    payload["timestamp"] = int(time.time() * 1000)
    query = urllib.parse.urlencode(payload, doseq=True)
    signature = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    signed_query = f"{query}&signature={signature}"
    url = f"{futures['base_url'].rstrip('/')}{path}"
    headers = {
        "User-Agent": "futures-testnet-momentum-bot/0.1",
        "Accept": "application/json",
        "X-MBX-APIKEY": api_key,
    }
    data = None
    if method.upper() in {"POST", "DELETE"}:
        data = signed_query.encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        url = f"{url}?{signed_query}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        timeout = float(config.get("request_timeout_sec", 15))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"币安合约 Testnet HTTP {exc.code}: {body}") from exc


def unsigned_futures_get(config: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{config['futures_testnet']['base_url'].rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "futures-testnet-momentum-bot/0.1", "Accept": "application/json"})
    try:
        timeout = float(config.get("request_timeout_sec", 15))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"币安合约 Testnet HTTP {exc.code}: {body}") from exc


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def telegram_enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send_telegram_message(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    http_post_form_json(
        url,
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        },
    )


def notify_telegram(text: str) -> None:
    try:
        send_telegram_message(text)
    except Exception as exc:
        print(f"[警告] Telegram 通知发送失败: {exc}", file=sys.stderr)


def side_cn(side: str) -> str:
    return {"LONG": "做多", "SHORT": "做空"}.get(side, side)


def exit_reason_cn(reason: str) -> str:
    return {
        "stop_or_trailing_stop": "止损/移动止损",
        "take_profit": "止盈",
        "max_hold_time": "持仓超时",
    }.get(reason, reason)


def signal_reason_cn(reason: str) -> str:
    if reason == "manual_watchlist":
        return "手动关注"
    if reason == "new_symbol":
        return "新上市/新发现"
    if reason == "liquid":
        return "流动性达标"
    if reason == "ema_up":
        return "均线多头"
    if reason == "ema_down":
        return "均线空头"
    if reason.startswith("volume_spike_"):
        return "成交量放大 " + reason.removeprefix("volume_spike_")
    if reason.startswith("24h_up_"):
        return "24小时上涨 " + reason.removeprefix("24h_up_")
    if reason.startswith("24h_down_"):
        return "24小时下跌 " + reason.removeprefix("24h_down_")
    if reason.startswith("short_move_up_"):
        return "短线上涨 " + reason.removeprefix("short_move_up_")
    if reason.startswith("short_move_down_"):
        return "短线下跌 " + reason.removeprefix("short_move_down_")
    if reason == "trend_5m_up":
        return "5m趋势多头确认"
    if reason == "trend_5m_down":
        return "5m趋势空头确认"
    if reason.startswith("trend_15m_"):
        direction = reason.removeprefix("trend_15m_")
        return trend_reason_cn(direction, "15m") + "，未逆势"
    return reason


def signal_reasons_cn(reasons: list[str]) -> str:
    return ", ".join(signal_reason_cn(reason) for reason in reasons) if reasons else "无"


def decision_line(result: AnalysisResult) -> str:
    verdict = "可开仓" if result.signal else f"不开单：{result.reject_reason}"
    return (
        f"[决策] {result.symbol} 价格={result.price:.8g} 24小时={result.change_24h_pct:.2f}% "
        f"短线={result.short_move_pct:.2f}% 量能={result.volume_ratio:.2f}倍 "
        f"多头分={result.long_score:.1f}({signal_reasons_cn(result.long_reasons)}) "
        f"空头分={result.short_score:.1f}({signal_reasons_cn(result.short_reasons)}) "
        f"选择={side_cn(result.chosen_side)} 分数={result.chosen_score:.1f} -> {verdict}"
    )


def trend_direction_from_closes(closes: list[float]) -> str:
    if len(closes) < 60:
        return "neutral"
    last_close = closes[-1]
    ema_fast = ema(closes[-20:])
    ema_slow = ema(closes[-60:])
    if last_close > ema_fast > ema_slow:
        return "up"
    if last_close < ema_fast < ema_slow:
        return "down"
    return "neutral"


def trend_reason_cn(direction: str, interval: str) -> str:
    if direction == "up":
        return f"{interval}趋势多头"
    if direction == "down":
        return f"{interval}趋势空头"
    return f"{interval}趋势中性"


def onchain_filter_signal(config: dict[str, Any], signal: Signal) -> OnchainResult:
    onchain = config.get("onchain", {})
    if not onchain.get("enabled", False):
        return OnchainResult(True, "链上过滤未启用")

    symbol_config = onchain.get("symbols", {}).get(signal.symbol)
    if not symbol_config:
        if onchain.get("reject_on_missing_mapping", False):
            return OnchainResult(False, "未配置链上合约地址，按配置拒绝开仓")
        return OnchainResult(True, "未配置链上合约地址，跳过链上过滤")

    network = symbol_config.get("network")
    token_address = symbol_config.get("token_address")
    if not network or not token_address:
        return OnchainResult(False, "链上配置缺少 network 或 token_address")

    base_url = onchain["base_url"].rstrip("/")
    path = f"/networks/{network}/tokens/{token_address}/pools"
    payload = http_get_json(base_url, path)
    pools = payload.get("data", [])
    if not pools:
        return OnchainResult(False, "GeckoTerminal 未找到该代币池子")

    pool = pools[0]
    attrs = pool.get("attributes", {})
    liquidity_usd = fnum(attrs.get("reserve_in_usd"))
    volume_24h = fnum(attrs.get("volume_usd", {}).get("h24"))
    buys_1h = fnum(attrs.get("transactions", {}).get("h1", {}).get("buys"))
    sells_1h = fnum(attrs.get("transactions", {}).get("h1", {}).get("sells"))
    buy_sell_ratio = buys_1h / max(1.0, sells_1h)
    pool_address = attrs.get("address", "")

    if liquidity_usd < float(onchain["min_liquidity_usd"]):
        return OnchainResult(False, f"DEX流动性不足 {liquidity_usd:.0f} USD", liquidity_usd, volume_24h, buy_sell_ratio, pool_address)
    if volume_24h < float(onchain["min_volume_24h_usd"]):
        return OnchainResult(False, f"DEX 24小时成交额不足 {volume_24h:.0f} USD", liquidity_usd, volume_24h, buy_sell_ratio, pool_address)

    if signal.side == "LONG" and buy_sell_ratio < float(onchain["long_min_buy_sell_ratio_1h"]):
        return OnchainResult(False, f"链上1小时买卖比 {buy_sell_ratio:.2f} 不支持做多", liquidity_usd, volume_24h, buy_sell_ratio, pool_address)
    if signal.side == "SHORT" and buy_sell_ratio > float(onchain["short_max_buy_sell_ratio_1h"]):
        return OnchainResult(False, f"链上1小时买卖比 {buy_sell_ratio:.2f} 不支持做空", liquidity_usd, volume_24h, buy_sell_ratio, pool_address)

    return OnchainResult(
        True,
        "链上过滤通过",
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h,
        buy_sell_ratio_1h=buy_sell_ratio,
        pool_address=pool_address,
    )


def get_exchange_symbols(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = http_get_json(config["market_base_url"], "/fapi/v1/exchangeInfo")
    quote_assets = set(config["quote_assets"])
    exclude = set(config["exclude_symbols"])
    symbols: dict[str, dict[str, Any]] = {}
    for item in payload.get("symbols", []):
        symbol = item.get("symbol")
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") in quote_assets
            and item.get("contractType") == "PERPETUAL"
            and symbol not in exclude
        ):
            symbols[symbol] = item
    return symbols


def get_24h_tickers(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = http_get_json(config["market_base_url"], "/fapi/v1/ticker/24hr")
    return {item["symbol"]: item for item in payload}


def get_klines(config: dict[str, Any], symbol: str) -> list[list[Any]]:
    return http_get_json(
        config["market_base_url"],
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": config["kline_interval"],
            "limit": config["kline_limit"],
        },
    )


def get_futures_market_klines(config: dict[str, Any], symbol: str, interval: str, limit: int) -> list[list[Any]]:
    return http_get_json(
        config["market_base_url"],
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )


def get_futures_exchange_symbols(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = unsigned_futures_get(config, "/fapi/v1/exchangeInfo")
    symbols: dict[str, dict[str, Any]] = {}
    for item in payload.get("symbols", []):
        symbol = item.get("symbol")
        if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT":
            symbols[symbol] = item
    return symbols


def get_futures_positions(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = signed_futures_request(config, "GET", "/fapi/v2/positionRisk")
    positions: dict[str, dict[str, Any]] = {}
    for item in payload:
        amount = fnum(item.get("positionAmt"))
        if abs(amount) > 0:
            positions[item["symbol"]] = item
    return positions


def get_futures_usdt_available_balance(config: dict[str, Any]) -> float:
    payload = signed_futures_request(config, "GET", "/fapi/v2/balance")
    for item in payload:
        if item.get("asset") == "USDT":
            return fnum(item.get("availableBalance"))
    raise RuntimeError("没有在币安合约 Testnet 账户里找到 USDT 可用余额。")


def get_symbol_filter(symbol_info: dict[str, Any], filter_type: str) -> dict[str, Any]:
    for item in symbol_info.get("filters", []):
        if item.get("filterType") == filter_type:
            return item
    return {}


def quantize_down(value: float, step: str) -> str:
    step_dec = Decimal(str(step))
    if step_dec <= 0:
        return str(value)
    value_dec = Decimal(str(value))
    units = (value_dec / step_dec).to_integral_value(rounding=ROUND_DOWN)
    result = units * step_dec
    return format(result.normalize(), "f")


def round_price(symbol_info: dict[str, Any], price: float) -> str:
    price_filter = get_symbol_filter(symbol_info, "PRICE_FILTER")
    tick_size = price_filter.get("tickSize", "0.00000001")
    return quantize_down(price, tick_size)


def round_quantity(symbol_info: dict[str, Any], qty: float) -> str:
    lot_filter = get_symbol_filter(symbol_info, "LOT_SIZE")
    step_size = lot_filter.get("stepSize", "0.00000001")
    return quantize_down(qty, step_size)


def futures_side(signal_side: str) -> str:
    return "BUY" if signal_side == "LONG" else "SELL"


def close_side(signal_side: str) -> str:
    return "SELL" if signal_side == "LONG" else "BUY"


def ensure_futures_settings(config: dict[str, Any], symbol: str) -> None:
    futures = config["futures_testnet"]
    try:
        signed_futures_request(
            config,
            "POST",
            "/fapi/v1/marginType",
            {"symbol": symbol, "marginType": futures["margin_type"]},
        )
    except RuntimeError as exc:
        if "No need to change margin type" not in str(exc):
            raise
    signed_futures_request(
        config,
        "POST",
        "/fapi/v1/leverage",
        {"symbol": symbol, "leverage": int(futures["leverage"])},
    )


def futures_open_position(
    config: dict[str, Any],
    state: dict[str, Any],
    signal: Signal,
    futures_symbols: dict[str, dict[str, Any]],
    futures_positions: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    futures = config["futures_testnet"]
    symbol_info = futures_symbols.get(signal.symbol)
    if not symbol_info:
        log_verbose(config, f"[合约] {signal.symbol} 不在 U 本位合约 Testnet 交易对中，跳过。")
        return None
    if signal.symbol in futures_positions:
        log_verbose(config, f"[合约] {signal.symbol} 已有 Testnet 持仓，跳过重复开仓。")
        return None
    if len(futures_positions) >= int(futures["max_open_positions"]):
        log_verbose(config, f"[合约] Testnet 持仓数量已达到上限 {futures['max_open_positions']}，跳过。")
        return None

    available_balance = get_futures_usdt_available_balance(config)
    if available_balance <= 0:
        raise RuntimeError("币安合约 Testnet 账户 USDT 可用余额为 0，无法按 5% 保证金开仓。")
    margin_pct = float(futures["margin_per_trade_pct"]) / 100.0
    margin_usdt = available_balance * margin_pct
    notional = margin_usdt * float(futures["leverage"])
    max_position_usdt = float(futures.get("max_position_usdt", 0.0))
    if max_position_usdt > 0:
        notional = min(notional, max_position_usdt)
    notional *= float(futures.get("quantity_safety_factor", 1.0))
    if notional <= 5:
        raise RuntimeError(
            f"{signal.symbol} 计算出的名义仓位只有 {notional:.2f} USDT，"
            "请确认合约 Testnet 账户可用余额或提高 margin_per_trade_pct。"
        )
    qty = notional / signal.price
    qty_text = round_quantity(symbol_info, qty)
    if fnum(qty_text) <= 0:
        raise RuntimeError(f"{signal.symbol} 计算出的下单数量为 0，请提高 max_position_usdt。")

    ensure_futures_settings(config, signal.symbol)
    entry_order = signed_futures_request(
        config,
        "POST",
        "/fapi/v1/order",
        {
            "symbol": signal.symbol,
            "side": futures_side(signal.side),
            "type": "MARKET",
            "quantity": qty_text,
        },
    )

    if signal.side == "LONG":
        stop_price = signal.price * (1.0 - stop_loss_pct)
        take_profit = signal.price * (1.0 + float(futures["take_profit_pct"]) / 100.0)
    else:
        stop_price = signal.price * (1.0 + stop_loss_pct)
        take_profit = signal.price * (1.0 - float(futures["take_profit_pct"]) / 100.0)

    stop_order = signed_futures_request(
        config,
        "POST",
        "/fapi/v1/algoOrder",
        {
            "algoType": "CONDITIONAL",
            "symbol": signal.symbol,
            "side": close_side(signal.side),
            "type": "STOP_MARKET",
            "triggerPrice": round_price(symbol_info, stop_price),
            "closePosition": "true",
            "workingType": futures["working_type"],
        },
    )
    take_profit_order = signed_futures_request(
        config,
        "POST",
        "/fapi/v1/algoOrder",
        {
            "algoType": "CONDITIONAL",
            "symbol": signal.symbol,
            "side": close_side(signal.side),
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": round_price(symbol_info, take_profit),
            "closePosition": "true",
            "workingType": futures["working_type"],
        },
    )

    state.setdefault("futures_testnet", {}).setdefault("orders", [])
    record = {
        "time": iso_now(),
        "symbol": signal.symbol,
        "side": signal.side,
        "price": signal.price,
        "quantity": qty_text,
        "available_balance_usdt": available_balance,
        "margin_usdt": margin_usdt,
        "notional_usdt": notional,
        "entry_order_id": entry_order.get("orderId"),
        "stop_algo_id": stop_order.get("algoId"),
        "take_profit_algo_id": take_profit_order.get("algoId"),
        "stop_price": round_price(symbol_info, stop_price),
        "take_profit": round_price(symbol_info, take_profit),
        "score": signal.score,
        "reasons": signal.reasons,
    }
    state["futures_testnet"]["orders"].append(record)
    return record


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass
    return default


def age_hours(first_seen_iso: str) -> float:
    try:
        first_seen = datetime.fromisoformat(first_seen_iso)
    except ValueError:
        return 999999.0
    return max(0.0, (utc_now() - first_seen).total_seconds() / 3600.0)


def update_symbol_registry(
    state: dict[str, Any],
    symbols: dict[str, dict[str, Any]],
    seed_existing: bool,
) -> None:
    state.setdefault("symbols", {})
    now = iso_now()
    old_time = "2000-01-01T00:00:00+00:00"
    for symbol in symbols:
        if symbol not in state["symbols"]:
            state["symbols"][symbol] = {
                "first_seen": old_time if seed_existing else now,
                "base_asset": symbols[symbol].get("baseAsset"),
                "quote_asset": symbols[symbol].get("quoteAsset"),
            }


def make_candidates(
    config: dict[str, Any],
    state: dict[str, Any],
    symbols: dict[str, dict[str, Any]],
    tickers: dict[str, dict[str, Any]],
) -> list[str]:
    watchlist = {symbol.upper() for symbol in config["manual_watchlist"]}
    min_quote_volume = float(config["min_quote_volume_usdt"])
    candidates: list[str] = []
    for symbol in symbols:
        ticker = tickers.get(symbol)
        if not ticker:
            continue
        quote_volume = fnum(ticker.get("quoteVolume"))
        change_24h = fnum(ticker.get("priceChangePercent"))
        is_new = age_hours(state["symbols"][symbol]["first_seen"]) <= float(config["new_symbol_hours"])
        is_hot = abs(change_24h) >= min(abs(float(config["long_24h_change_pct"])), abs(float(config["short_24h_change_pct"])))
        if symbol in watchlist or is_new or (quote_volume >= min_quote_volume and is_hot):
            candidates.append(symbol)
    candidates.sort(
        key=lambda s: (
            s not in watchlist,
            -abs(fnum(tickers.get(s, {}).get("priceChangePercent"))),
            -fnum(tickers.get(s, {}).get("quoteVolume")),
        )
    )
    return candidates[: int(config["max_candidates_per_scan"])]


def log_verbose(config: dict[str, Any], message: str) -> None:
    if config.get("verbose_console", True):
        print(message, flush=True)


def describe_candidate(symbol: str, state: dict[str, Any], ticker: dict[str, Any]) -> str:
    first_seen = state["symbols"].get(symbol, {}).get("first_seen", "")
    new_tag = " 新币" if first_seen and age_hours(first_seen) <= float(ACTIVE_CONFIG["new_symbol_hours"]) else ""
    return (
        f"{symbol}{new_tag} "
        f"24小时涨跌={fnum(ticker.get('priceChangePercent')):.2f}% "
        f"成交额={fnum(ticker.get('quoteVolume')) / 1_000_000:.1f}M USDT"
    )


def analyze_symbol(
    config: dict[str, Any],
    state: dict[str, Any],
    symbol: str,
    ticker: dict[str, Any],
) -> AnalysisResult:
    klines = get_klines(config, symbol)
    if len(klines) < max(20, int(config["short_term_lookback_candles"]) + 2):
        return AnalysisResult(
            signal=None,
            symbol=symbol,
            price=0.0,
            long_score=0.0,
            short_score=0.0,
            chosen_side="LONG",
            chosen_score=0.0,
            change_24h_pct=fnum(ticker.get("priceChangePercent")),
            short_move_pct=0.0,
            volume_ratio=0.0,
            quote_volume=fnum(ticker.get("quoteVolume")),
            is_new=False,
            long_reasons=[],
            short_reasons=[],
            reject_reason="K线数量不足",
        )

    closes = [fnum(k[4]) for k in klines]
    quote_volumes = [fnum(k[7]) for k in klines]
    price = closes[-1]
    lookback = int(config["short_term_lookback_candles"])
    short_move = pct(closes[-1], closes[-1 - lookback])
    change_24h = fnum(ticker.get("priceChangePercent"))
    quote_volume = fnum(ticker.get("quoteVolume"))
    is_new = age_hours(state["symbols"][symbol]["first_seen"]) <= float(config["new_symbol_hours"])

    recent_n = int(config["recent_volume_candles"])
    baseline_n = int(config["baseline_volume_candles"])
    recent_volume = sum(quote_volumes[-recent_n:])
    baseline_slice = quote_volumes[-recent_n - baseline_n : -recent_n]
    baseline_volume = sum(baseline_slice) / max(1, len(baseline_slice)) * recent_n
    volume_ratio = recent_volume / baseline_volume if baseline_volume > 0 else 0.0

    fast_trend = "neutral"
    slow_trend = "neutral"
    if config.get("require_trend_confirmation", True):
        trend_limit = int(config.get("trend_kline_limit", 80))
        fast_klines = get_futures_market_klines(config, symbol, config["trend_fast_interval"], trend_limit)
        slow_klines = get_futures_market_klines(config, symbol, config["trend_slow_interval"], trend_limit)
        fast_trend = trend_direction_from_closes([fnum(k[4]) for k in fast_klines])
        slow_trend = trend_direction_from_closes([fnum(k[4]) for k in slow_klines])

    long_score, long_reasons = score_direction(
        config, symbol, "LONG", is_new, change_24h, short_move, quote_volume, volume_ratio, closes
    )
    short_score, short_reasons = score_direction(
        config, symbol, "SHORT", is_new, change_24h, short_move, quote_volume, volume_ratio, closes
    )

    if config.get("require_trend_confirmation", True):
        if fast_trend == "up":
            long_score += 1.5
            long_reasons.append("trend_5m_up")
        if slow_trend != "down":
            long_score += 1.0
            long_reasons.append(f"trend_15m_{slow_trend}")
        if fast_trend == "down":
            short_score += 1.5
            short_reasons.append("trend_5m_down")
        if slow_trend != "up":
            short_score += 1.0
            short_reasons.append(f"trend_15m_{slow_trend}")

    if long_score >= short_score:
        side = "LONG"
        score = long_score
        reasons = long_reasons
    else:
        side = "SHORT"
        score = short_score
        reasons = short_reasons

    reject_reason = None
    if score < float(config["min_signal_score"]):
        reject_reason = f"最高分 {score:.1f} 低于开仓阈值 {float(config['min_signal_score']):.1f}"
    elif config.get("require_trend_confirmation", True):
        if side == "LONG" and not (fast_trend == "up" and slow_trend != "down"):
            reject_reason = f"多头趋势确认不足：{trend_reason_cn(fast_trend, '5m')}，{trend_reason_cn(slow_trend, '15m')}"
        if side == "SHORT" and not (fast_trend == "down" and slow_trend != "up"):
            reject_reason = f"空头趋势确认不足：{trend_reason_cn(fast_trend, '5m')}，{trend_reason_cn(slow_trend, '15m')}"

    signal = None if reject_reason else Signal(
        symbol=symbol,
        side=side,
        score=score,
        price=price,
        quote_volume=quote_volume,
        change_24h_pct=change_24h,
        short_move_pct=short_move,
        volume_ratio=volume_ratio,
        is_new=is_new,
        reasons=reasons,
    )
    return AnalysisResult(
        signal=signal,
        symbol=symbol,
        price=price,
        long_score=long_score,
        short_score=short_score,
        chosen_side=side,
        chosen_score=score,
        change_24h_pct=change_24h,
        short_move_pct=short_move,
        volume_ratio=volume_ratio,
        quote_volume=quote_volume,
        is_new=is_new,
        long_reasons=long_reasons,
        short_reasons=short_reasons,
        reject_reason=reject_reason,
    )


def score_direction(
    config: dict[str, Any],
    symbol: str,
    side: str,
    is_new: bool,
    change_24h: float,
    short_move: float,
    quote_volume: float,
    volume_ratio: float,
    closes: list[float],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    watchlist = {s.upper() for s in config["manual_watchlist"]}
    min_quote_volume = float(config["min_quote_volume_usdt"])

    if symbol in watchlist:
        score += 1.0
        reasons.append("manual_watchlist")
    if is_new:
        score += 2.0
        reasons.append("new_symbol")
    if quote_volume >= min_quote_volume:
        score += 1.0
        reasons.append("liquid")
    if volume_ratio >= float(config["volume_spike_ratio"]):
        score += 1.5
        reasons.append(f"volume_spike_{volume_ratio:.2f}x")

    last_close = closes[-1]
    ema_fast = ema(closes[-20:])
    ema_slow = ema(closes[-60:]) if len(closes) >= 60 else ema(closes)

    if side == "LONG":
        if change_24h >= float(config["long_24h_change_pct"]):
            score += 1.5
            reasons.append(f"24h_up_{change_24h:.2f}%")
        if short_move >= float(config["short_term_move_pct"]):
            score += 1.5
            reasons.append(f"short_move_up_{short_move:.2f}%")
        if last_close > ema_fast > ema_slow:
            score += 1.0
            reasons.append("ema_up")
    else:
        if change_24h <= float(config["short_24h_change_pct"]):
            score += 1.5
            reasons.append(f"24h_down_{change_24h:.2f}%")
        if short_move <= -float(config["short_term_move_pct"]):
            score += 1.5
            reasons.append(f"short_move_down_{short_move:.2f}%")
        if last_close < ema_fast < ema_slow:
            score += 1.0
            reasons.append("ema_down")

    return score, reasons


def ema(values: list[float]) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (len(values) + 1.0)
    result = values[0]
    for value in values[1:]:
        result = value * alpha + result * (1.0 - alpha)
    return result


def scan_once(config: dict[str, Any], state: dict[str, Any], seed_existing: bool = False) -> None:
    started_at = time.monotonic()
    mode = "binance_futures_testnet"
    log_verbose(config, "[模式] 币安 U 本位合约 Testnet：信号触发后会向币安模拟盘真实下单。")
    log_verbose(config, f"[扫描] {iso_now()} 正在读取币安交易对和行情...")
    symbols = get_exchange_symbols(config)
    update_symbol_registry(state, symbols, seed_existing)
    tickers = get_24h_tickers(config)
    log_verbose(config, f"[扫描] 交易对={len(symbols)} 行情={len(tickers)} 正在检查持仓...")

    futures_symbols = get_futures_exchange_symbols(config)
    futures_positions = get_futures_positions(config)
    if futures_positions:
        pos_preview = "; ".join(
            f"{symbol} 数量={item.get('positionAmt')} 入场={item.get('entryPrice')}"
            for symbol, item in futures_positions.items()
        )
        log_verbose(config, f"[合约持仓] {len(futures_positions)} -> {pos_preview}")
    else:
        log_verbose(config, "[合约持仓] 0 -> 当前币安 Testnet 无持仓")

    candidates = make_candidates(config, state, symbols, tickers)
    if candidates:
        top_preview = "; ".join(
            describe_candidate(symbol, state, tickers[symbol])
            for symbol in candidates[: min(8, len(candidates))]
        )
        log_verbose(config, f"[候选] {len(candidates)} -> {top_preview}")
    else:
        log_verbose(config, "[候选] 0 -> 没有币种通过流动性/动量/新币筛选")

    analysis_results: list[AnalysisResult] = []
    signals: list[Signal] = []
    skipped_open = 0
    skipped_not_futures = 0
    decision_log_top_n = int(config.get("decision_log_top_n", 10))
    for symbol in candidates:
        if symbol in futures_positions:
            skipped_open += 1
            if skipped_open <= decision_log_top_n:
                log_verbose(config, f"[跳过] {symbol} 已有币安 Testnet 持仓，不重复开仓。")
            continue
        if symbol not in futures_symbols:
            skipped_not_futures += 1
            if skipped_not_futures <= decision_log_top_n:
                log_verbose(config, f"[跳过] {symbol} 不在币安 U 本位合约 Testnet 交易对里，不能下合约单。")
            continue
        try:
            result = analyze_symbol(config, state, symbol, tickers[symbol])
        except Exception as exc:
            print(f"[警告] 跳过 {symbol}: {exc}", file=sys.stderr, flush=True)
            continue
        analysis_results.append(result)
        if len(analysis_results) <= decision_log_top_n:
            log_verbose(config, decision_line(result))
        if result.signal:
            signals.append(result.signal)

    signals.sort(key=lambda s: (-s.score, -abs(s.short_move_pct), -s.quote_volume))
    if signals:
        signal_preview = "; ".join(
            f"{s.symbol} {side_cn(s.side)} 分数={s.score:.1f} 短线涨跌={s.short_move_pct:.2f}% 量能={s.volume_ratio:.2f}倍"
            for s in signals[: min(8, len(signals))]
        )
        log_verbose(config, f"[信号] {len(signals)} -> {signal_preview}")
    else:
        details = []
        if skipped_open:
            details.append(f"{skipped_open} 个候选已有持仓")
        if skipped_not_futures:
            details.append(f"{skipped_not_futures} 个候选不支持合约 Testnet")
        below_score = len([r for r in analysis_results if not r.signal])
        if below_score:
            details.append(f"{below_score} 个候选分数不足")
        detail = "；".join(details) if details else f"信号分数低于 {float(config['min_signal_score']):.1f}"
        log_verbose(config, f"[信号] 0 -> {detail}")

    entries = 0
    for signal in signals:
        onchain_result = onchain_filter_signal(config, signal)
        if not onchain_result.allowed:
            log_verbose(config, f"[链上过滤] {signal.symbol} {side_cn(signal.side)} 不开仓：{onchain_result.reason}")
            continue
        log_verbose(
            config,
            f"[链上过滤] {signal.symbol} {side_cn(signal.side)} 通过：{onchain_result.reason} "
            f"流动性={onchain_result.liquidity_usd:.0f}USD 24h量={onchain_result.volume_24h_usd:.0f}USD "
            f"1h买卖比={onchain_result.buy_sell_ratio_1h:.2f}"
        )
        log_verbose(
            config,
            f"[开仓原因] {signal.symbol} {side_cn(signal.side)} 分数={signal.score:.1f} "
            f"触发={signal_reasons_cn(signal.reasons)}；当前无同币种持仓，未超过最大持仓数，链上过滤通过。"
        )
        order = futures_open_position(config, state, signal, futures_symbols, futures_positions)
        if not order:
            continue
        entries += 1
        futures_positions[signal.symbol] = {"positionAmt": order["quantity"], "entryPrice": order["price"]}
        msg = (
            f"[合约开仓] {signal.symbol} {side_cn(signal.side)} 数量={order['quantity']} "
            f"保证金={order['margin_usdt']:.2f} USDT 名义仓位={order['notional_usdt']:.2f} USDT "
            f"止损={order['stop_price']} 止盈={order['take_profit']}"
        )
        print(msg, flush=True)
        notify_telegram(
            "币安合约 Testnet 开仓\n"
            f"币种: {signal.symbol}\n"
            f"方向: {side_cn(signal.side)}\n"
            f"数量: {order['quantity']}\n"
            f"账户可用余额: {order['available_balance_usdt']:.2f} USDT\n"
            f"本单保证金: {order['margin_usdt']:.2f} USDT\n"
            f"名义仓位: {order['notional_usdt']:.2f} USDT\n"
            f"止损: {order['stop_price']}\n"
            f"止盈: {order['take_profit']}\n"
            f"分数: {signal.score:.1f}\n"
            f"链上过滤: {onchain_result.reason}\n"
            f"DEX流动性: {onchain_result.liquidity_usd:.0f} USD\n"
            f"DEX 24h量: {onchain_result.volume_24h_usd:.0f} USD\n"
            f"链上1h买卖比: {onchain_result.buy_sell_ratio_1h:.2f}\n"
            f"触发原因: {signal_reasons_cn(signal.reasons)}\n"
            f"开仓单号: {order['entry_order_id']}"
        )

    open_count = len(futures_positions)
    elapsed = time.monotonic() - started_at
    print(
        f"[状态] {iso_now()} 模式=币安合约Testnet 持仓={open_count} "
        f"候选={len(candidates)} 信号={len(signals)} 新开仓={entries} 耗时={elapsed:.1f}秒",
        flush=True,
    )


def write_example_files() -> None:
    config_path = Path("config.example.json")
    env_path = Path(".env.example")
    if not config_path.exists():
        save_json(config_path, DEFAULT_CONFIG)
    if not env_path.exists():
        env_path.write_text(
            "# Use Binance USD-M Futures Testnet keys only.\n"
            "# Keep keys local only, never paste them into chat.\n"
            "BINANCE_API_KEY=\n"
            "BINANCE_API_SECRET=\n",
            encoding="utf-8",
        )


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="Binance Futures Testnet momentum bot")
    parser.add_argument("--config", default="config.json", help="config JSON path")
    parser.add_argument("--once", action="store_true", help="run one scan and exit")
    parser.add_argument(
        "--seed-existing",
        action="store_true",
        help="mark currently listed symbols as old so future new listings stand out",
    )
    parser.add_argument("--init", action="store_true", help="write config.example.json and .env.example")
    args = parser.parse_args()

    load_dotenv(Path(".env"))
    if args.init:
        write_example_files()
        print("已写入 config.example.json 和 .env.example")
        return 0

    config_path = Path(args.config)
    if not config_path.exists():
        print("没有找到 config.json，将使用内置默认配置。可运行 --init 生成示例配置。")
    config = load_config(config_path)
    global ACTIVE_CONFIG
    ACTIVE_CONFIG = config
    data_dir = Path(config["data_dir"])
    state_path = data_dir / "futures_testnet_state.json"
    state = load_json(state_path, {})

    while True:
        try:
            scan_once(config, state, seed_existing=args.seed_existing)
            save_json(state_path, state)
        except KeyboardInterrupt:
            save_json(state_path, state)
            print("\n已停止，状态已保存。")
            return 0
        except Exception as exc:
            save_json(state_path, state)
            print(f"[错误] {exc}", file=sys.stderr, flush=True)

        if args.once:
            return 0
        time.sleep(float(config["scan_interval_sec"]))


if __name__ == "__main__":
    raise SystemExit(main())
