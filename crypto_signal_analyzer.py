#!/usr/bin/env python3
"""
Crypto futures signal analyzer.

输入币种，例如 BTC 或 BTCUSDT，脚本会读取 Binance USD-M Futures 公开数据，
并在配置了 token 地址时读取 GeckoTerminal 链上 DEX 数据，输出多空方向建议。

注意：这是数据分析脚本，不是投资建议，也不会自动下单。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "market_base_url": "https://fapi.binance.com",
    "request_timeout_sec": 8,
    "analysis": {
        "request_timeout_sec": 4,
        "kline_limit": 180,
        "kline_intervals": ["15m", "1h", "4h"],
        "derivatives_period": "1h",
        "derivatives_limit": 24,
        "agg_trade_limit": 1000,
        "liquidation_enabled": False,
        "liquidation_lookback_min": 60,
        "liquidation_limit": 100,
        "order_book_limit": 100,
        "min_decision_edge": 8.0,
        "signal_log_path": "data/signal_history.jsonl",
    },
    "onchain": {
        "enabled": True,
        "auto_discover": True,
        "provider": "geckoterminal",
        "base_url": "https://api.geckoterminal.com/api/v2",
        "coingecko_demo_base_url": "https://api.coingecko.com/api/v3",
        "coingecko_pro_base_url": "https://pro-api.coingecko.com/api/v3",
        "coingecko_api_plan": "demo",
        "min_liquidity_usd": 100_000.0,
        "min_volume_24h_usd": 100_000.0,
        "default_networks": {
            "BEAT": "bsc",
            "LAB": "bsc"
        },
        "common_networks": ["bsc", "eth", "base", "solana", "arbitrum", "polygon_pos", "avax", "optimism"],
        "symbols": {
            "BEAT": {
                "network": "bsc",
                "token_address": "0xcf3232b85b43bca90e51d38cc06cc8bb8c8a3e36"
            },
            "BEATUSDT": {
                "network": "bsc",
                "token_address": "0xcf3232b85b43bca90e51d38cc06cc8bb8c8a3e36"
            },
            "LAB": {
                "network": "bsc",
                "token_address": "0x7ec43cf65f1663f820427c62a5780b8f2e25593a"
            },
            "LABUSDT": {
                "network": "bsc",
                "token_address": "0x7ec43cf65f1663f820427c62a5780b8f2e25593a"
            }
        },
    },
}


class CriticalDataError(RuntimeError):
    pass


class OnchainRateLimitError(RuntimeError):
    pass


@dataclass
class ScoreBook:
    long: float = 0.0
    short: float = 0.0
    long_reasons: list[str] = field(default_factory=list)
    short_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_long(self, points: float, reason: str) -> None:
        self.long += points
        self.long_reasons.append(f"+{points:g} {reason}")

    def add_short(self, points: float, reason: str) -> None:
        self.short += points
        self.short_reasons.append(f"+{points:g} {reason}")

    def warn(self, reason: str) -> None:
        self.warnings.append(reason)

    def warn_once(self, reason: str) -> None:
        if reason not in self.warnings:
            self.warnings.append(reason)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def deep_update(target: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        deep_update(config, load_json(path, {}))
    default_onchain = DEFAULT_CONFIG.get("onchain", {})
    onchain = config.setdefault("onchain", {})
    onchain.setdefault("default_networks", {})
    onchain["default_networks"] = {
        **default_onchain.get("default_networks", {}),
        **onchain.get("default_networks", {}),
    }
    onchain.setdefault("common_networks", default_onchain.get("common_networks", []))
    onchain.setdefault("symbols", {})
    onchain["symbols"] = {
        **default_onchain.get("symbols", {}),
        **onchain.get("symbols", {}),
    }
    return config


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return default


def pct(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100.0


def fmt_pct(value: float) -> str:
    return f"{value * 100:.4f}%"


def fmt_price(value: float) -> str:
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def fmt_money(value: float) -> str:
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.2f}"


def fmt_time_ms(value: Any) -> str:
    ts = fnum(value)
    if ts <= 0:
        return "-"
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def http_get_json(
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    timeout: float = 15,
    headers: dict[str, str] | None = None,
) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "crypto-signal-analyzer/0.1",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 429:
            raise OnchainRateLimitError(f"HTTP 429 {path}: API 限流") from exc
        raise RuntimeError(f"HTTP {exc.code} {path}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"请求失败 {path}: {exc}") from exc


def normalize_symbol(raw: str, quote: str = "USDT") -> str:
    symbol = raw.strip().upper().replace("/", "").replace("-", "")
    if not symbol:
        raise ValueError("币种不能为空")
    if symbol.endswith(quote):
        return symbol
    return f"{symbol}{quote}"


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    series = values[-period:]
    alpha = 2.0 / (len(series) + 1.0)
    result = series[0]
    for value in series[1:]:
        result = value * alpha + result * (1.0 - alpha)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains = 0.0
    losses = 0.0
    window = values[-period - 1 :]
    for prev, cur in zip(window, window[1:]):
        change = cur - prev
        if change >= 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def atr_from_klines(klines: list[list[Any]], period: int = 14) -> float:
    if len(klines) <= period:
        return 0.0
    true_ranges: list[float] = []
    prev_close = fnum(klines[-period - 1][4])
    for item in klines[-period:]:
        high = fnum(item[2])
        low = fnum(item[3])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = fnum(item[4])
    return average(true_ranges)


def adx_from_klines(klines: list[list[Any]], period: int = 14) -> float:
    if len(klines) < period * 2 + 1:
        return 0.0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    true_ranges: list[float] = []
    window = klines[-period * 2 - 1 :]
    for prev, cur in zip(window, window[1:]):
        prev_high = fnum(prev[2])
        prev_low = fnum(prev[3])
        prev_close = fnum(prev[4])
        high = fnum(cur[2])
        low = fnum(cur[3])

        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    dx_values: list[float] = []
    for idx in range(period, len(true_ranges) + 1):
        tr_sum = sum(true_ranges[idx - period : idx])
        if tr_sum <= 0:
            continue
        plus_di = 100.0 * sum(plus_dm[idx - period : idx]) / tr_sum
        minus_di = 100.0 * sum(minus_dm[idx - period : idx]) / tr_sum
        denom = plus_di + minus_di
        if denom > 0:
            dx_values.append(100.0 * abs(plus_di - minus_di) / denom)
    return average(dx_values[-period:])


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def last_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    return items[-1] if items else {}


def get_market(config: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
    analysis = config.get("analysis", {})
    return http_get_json(
        config["market_base_url"],
        path,
        params,
        timeout=float(analysis.get("request_timeout_sec", config.get("request_timeout_sec", 8))),
    )


def coingecko_api_key() -> str:
    return os.environ.get("COINGECKO_API_KEY", "").strip()


def coingecko_headers(config: dict[str, Any]) -> dict[str, str]:
    key = coingecko_api_key()
    if not key:
        return {}
    plan = str(config.get("onchain", {}).get("coingecko_api_plan", "demo")).lower()
    header_name = "x-cg-pro-api-key" if plan == "pro" else "x-cg-demo-api-key"
    return {header_name: key}


def onchain_base_url(config: dict[str, Any]) -> str:
    onchain = config.get("onchain", {})
    if coingecko_api_key():
        plan = str(onchain.get("coingecko_api_plan", "demo")).lower()
        base = onchain.get("coingecko_pro_base_url") if plan == "pro" else onchain.get("coingecko_demo_base_url")
        return f"{base.rstrip('/')}/onchain"
    return onchain.get("base_url", DEFAULT_CONFIG["onchain"]["base_url"]).rstrip("/")


def onchain_get_json(config: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
    return http_get_json(
        onchain_base_url(config),
        path,
        params,
        timeout=float(config.get("analysis", {}).get("request_timeout_sec", config.get("request_timeout_sec", 8))),
        headers=coingecko_headers(config),
    )


def safe_get(score: ScoreBook, label: str, fn: Any, default: Any) -> Any:
    try:
        return fn()
    except Exception as exc:
        score.warn(f"{label} 获取失败：{exc}")
        return default


def analyze_klines(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    intervals = config["analysis"].get("kline_intervals", ["15m", "1h", "4h"])
    limit = int(config["analysis"].get("kline_limit", 180))
    fetched: dict[str, list[Any]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(intervals))) as executor:
        futures = {
            executor.submit(
                get_market,
                config,
                "/fapi/v1/klines",
                {"symbol": symbol, "interval": interval, "limit": limit},
            ): interval
            for interval in intervals
        }
        for future, interval in futures.items():
            try:
                fetched[interval] = future.result()
            except Exception as exc:
                score.warn(f"{interval} K线 获取失败：{exc}")
                fetched[interval] = []

    for interval in intervals:
        klines = fetched.get(interval, [])
        if len(klines) < 65:
            score.warn(f"{interval} K线数量不足，趋势评分降低")
            continue

        closes = [fnum(k[4]) for k in klines]
        quote_volumes = [fnum(k[7]) for k in klines]
        close = closes[-1]
        ema20 = ema(closes, 20)
        ema60 = ema(closes, 60)
        move_20 = pct(closes[-1], closes[-21])
        rsi14 = rsi(closes, 14)
        atr14 = atr_from_klines(klines, 14)
        atr_pct = atr14 / close * 100.0 if close > 0 else 0.0
        adx14 = adx_from_klines(klines, 14)
        recent_vol = average(quote_volumes[-5:])
        base_vol = average(quote_volumes[-35:-5])
        volume_ratio = recent_vol / base_vol if base_vol > 0 else 0.0

        weight = {"15m": 10.0, "1h": 14.0, "4h": 12.0}.get(interval, 8.0)
        trend_side = "neutral"
        if close > ema20 > ema60:
            trend_side = "long"
            score.add_long(weight, f"{interval} EMA20 > EMA60，价格在均线上方")
        elif close < ema20 < ema60:
            trend_side = "short"
            score.add_short(weight, f"{interval} EMA20 < EMA60，价格在均线下方")
        elif close > ema60:
            trend_side = "long"
            score.add_long(weight * 0.45, f"{interval} 价格站上 EMA60，但结构未完全走强")
        elif close < ema60:
            trend_side = "short"
            score.add_short(weight * 0.45, f"{interval} 价格跌破 EMA60，但结构未完全走弱")

        if interval in {"15m", "1h"}:
            if adx14 >= 28 and trend_side == "long":
                score.add_long(3.0, f"{interval} ADX {adx14:.1f}，趋势强度支持多头延续")
            elif adx14 >= 28 and trend_side == "short":
                score.add_short(3.0, f"{interval} ADX {adx14:.1f}，趋势强度支持空头延续")
            elif adx14 and adx14 < 16:
                score.warn(f"{interval} ADX {adx14:.1f} 偏低，震荡行情不适合追单")

        if interval == "15m" and atr_pct >= 3.0:
            score.warn(f"15m ATR {atr_pct:.2f}% 偏高，止损距离和仓位需要下调")

        if move_20 >= 2.0:
            score.add_long(4.0, f"{interval} 近20根涨幅 {move_20:.2f}%")
        elif move_20 <= -2.0:
            score.add_short(4.0, f"{interval} 近20根跌幅 {move_20:.2f}%")

        if volume_ratio >= 1.6 and move_20 > 0:
            score.add_long(4.0, f"{interval} 放量上涨，量能 {volume_ratio:.2f} 倍")
        elif volume_ratio >= 1.6 and move_20 < 0:
            score.add_short(4.0, f"{interval} 放量下跌，量能 {volume_ratio:.2f} 倍")

        if rsi14 >= 78:
            score.add_short(3.0, f"{interval} RSI {rsi14:.1f} 过热，追多风险升高")
        elif rsi14 <= 22:
            score.add_long(3.0, f"{interval} RSI {rsi14:.1f} 超卖，追空风险升高")

        result[interval] = {
            "close": close,
            "ema20": ema20,
            "ema60": ema60,
            "move_20_pct": move_20,
            "rsi14": rsi14,
            "atr14": atr14,
            "atr_pct": atr_pct,
            "adx14": adx14,
            "volume_ratio": volume_ratio,
        }

    return result


def analyze_ticker(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, Any]:
    try:
        ticker = get_market(config, "/fapi/v1/ticker/24hr", {"symbol": symbol})
    except Exception as exc:
        message = str(exc)
        if "Invalid symbol" in message or "-1121" in message:
            raise CriticalDataError(f"{symbol} 不是 Binance U本位合约有效交易对，请确认币种或交易所。") from exc
        raise CriticalDataError(
            f"核心行情获取失败：{exc}\n"
            "这通常是当前网络访问 Binance Futures API 超时。可以稍后重试，或加长等待时间："
            f" python .\\crypto_signal_analyzer.py {symbol} --timeout 12"
        ) from exc
    if not isinstance(ticker, dict) or fnum(ticker.get("lastPrice")) <= 0:
        raise CriticalDataError(f"{symbol} 没有返回有效价格，请确认它是否为 Binance U本位合约交易对。")

    change_24h = fnum(ticker.get("priceChangePercent"))
    quote_volume = fnum(ticker.get("quoteVolume"))
    if change_24h >= 4:
        score.add_long(5.0, f"24小时涨幅 {change_24h:.2f}%")
    elif change_24h <= -4:
        score.add_short(5.0, f"24小时跌幅 {change_24h:.2f}%")
    if quote_volume < 20_000_000:
        score.warn(f"24小时成交额只有 {fmt_money(quote_volume)} USDT，流动性偏弱")
    else:
        score.add_long(1.5, "合约成交额充足")
        score.add_short(1.5, "合约成交额充足")
    return ticker


def analyze_funding(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, Any]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_premium = executor.submit(get_market, config, "/fapi/v1/premiumIndex", {"symbol": symbol})
        future_history = executor.submit(get_market, config, "/fapi/v1/fundingRate", {"symbol": symbol, "limit": 8})
        try:
            premium = future_premium.result()
        except Exception as exc:
            score.warn(f"资金费率 获取失败：{exc}")
            premium = {}
        try:
            history = future_history.result()
        except Exception as exc:
            score.warn(f"资金费率历史 获取失败：{exc}")
            history = []
    last_rate = fnum(premium.get("lastFundingRate"))
    avg_rate = average([fnum(item.get("fundingRate")) for item in history]) if history else last_rate

    if last_rate >= 0.0005:
        score.add_short(7.0, f"资金费率偏高 {fmt_pct(last_rate)}，多头拥挤")
    elif last_rate <= -0.0005:
        score.add_long(7.0, f"资金费率偏低 {fmt_pct(last_rate)}，空头拥挤")
    elif last_rate > 0:
        score.add_long(2.0, f"资金费率小幅为正 {fmt_pct(last_rate)}")
    elif last_rate < 0:
        score.add_short(2.0, f"资金费率小幅为负 {fmt_pct(last_rate)}")

    if avg_rate >= 0.0004:
        score.add_short(3.0, f"最近资金费率均值偏高 {fmt_pct(avg_rate)}")
    elif avg_rate <= -0.0004:
        score.add_long(3.0, f"最近资金费率均值偏低 {fmt_pct(avg_rate)}")

    return {"premium": premium, "history": history, "last_rate": last_rate, "avg_rate": avg_rate}


def analyze_long_short(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, Any]:
    period = config["analysis"].get("derivatives_period", "1h")
    limit = int(config["analysis"].get("derivatives_limit", 24))
    specs = {
        "global": ("全市场账户多空比", "/futures/data/globalLongShortAccountRatio"),
        "top_account": ("大户账户多空比", "/futures/data/topLongShortAccountRatio"),
        "top_position": ("大户持仓多空比", "/futures/data/topLongShortPositionRatio"),
        "taker": ("主动买卖比", "/futures/data/takerlongshortRatio"),
    }
    datasets: dict[str, list[Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(specs)) as executor:
        futures = {
            executor.submit(get_market, config, path, {"symbol": symbol, "period": period, "limit": limit}): (name, label)
            for name, (label, path) in specs.items()
        }
        for future, (name, label) in futures.items():
            try:
                datasets[name] = future.result()
            except Exception as exc:
                score.warn(f"{label} 获取失败：{exc}")
                datasets[name] = []

    global_ratio = fnum(last_item(datasets["global"]).get("longShortRatio"), 1.0)
    top_account_ratio = fnum(last_item(datasets["top_account"]).get("longShortRatio"), 1.0)
    top_position_ratio = fnum(last_item(datasets["top_position"]).get("longShortRatio"), 1.0)
    taker_ratio = fnum(last_item(datasets["taker"]).get("buySellRatio"), 1.0)

    if global_ratio >= 1.15:
        score.add_long(5.0, f"全市场账户多空比 {global_ratio:.2f} 偏多")
    elif global_ratio <= 0.87:
        score.add_short(5.0, f"全市场账户多空比 {global_ratio:.2f} 偏空")

    if top_account_ratio >= 1.10:
        score.add_long(4.0, f"大户账户多空比 {top_account_ratio:.2f} 偏多")
    elif top_account_ratio <= 0.90:
        score.add_short(4.0, f"大户账户多空比 {top_account_ratio:.2f} 偏空")

    if top_position_ratio >= 1.10:
        score.add_long(6.0, f"大户持仓多空比 {top_position_ratio:.2f} 偏多")
    elif top_position_ratio <= 0.90:
        score.add_short(6.0, f"大户持仓多空比 {top_position_ratio:.2f} 偏空")

    if taker_ratio >= 1.10:
        score.add_long(5.0, f"主动买入/卖出量比 {taker_ratio:.2f} 偏多")
    elif taker_ratio <= 0.90:
        score.add_short(5.0, f"主动买入/卖出量比 {taker_ratio:.2f} 偏空")

    if global_ratio >= 2.5:
        score.add_short(2.0, "全市场多头极度拥挤，存在反向挤压风险")
    elif global_ratio <= 0.4:
        score.add_long(2.0, "全市场空头极度拥挤，存在反向挤压风险")

    return {
        "period": period,
        "global_ratio": global_ratio,
        "top_account_ratio": top_account_ratio,
        "top_position_ratio": top_position_ratio,
        "taker_ratio": taker_ratio,
        "datasets": datasets,
    }


def analyze_open_interest(config: dict[str, Any], symbol: str, latest_price: float, score: ScoreBook) -> dict[str, Any]:
    period = config["analysis"].get("derivatives_period", "1h")
    limit = int(config["analysis"].get("derivatives_limit", 24))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_current = executor.submit(get_market, config, "/fapi/v1/openInterest", {"symbol": symbol})
        future_history = executor.submit(
            get_market,
            config,
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )
        try:
            current = future_current.result()
        except Exception as exc:
            score.warn(f"当前未平仓量 获取失败：{exc}")
            current = {}
        try:
            history = future_history.result()
        except Exception as exc:
            score.warn(f"未平仓量历史 获取失败：{exc}")
            history = []

    oi_now = fnum(current.get("openInterest"))
    oi_value_now = oi_now * latest_price if latest_price > 0 else 0.0
    oi_value_change = 0.0
    if len(history) >= 6:
        recent = fnum(history[-1].get("sumOpenInterestValue"))
        previous = fnum(history[-6].get("sumOpenInterestValue"))
        oi_value_change = pct(recent, previous)

        if oi_value_change >= 5:
            score.add_long(3.0, f"未平仓价值近5周期增加 {oi_value_change:.2f}%")
            score.add_short(3.0, f"未平仓价值近5周期增加 {oi_value_change:.2f}%")
        elif oi_value_change <= -5:
            score.warn(f"未平仓价值近5周期下降 {oi_value_change:.2f}%，行情延续性可能不足")

    return {
        "current": current,
        "history": history,
        "oi_now": oi_now,
        "oi_value_now": oi_value_now,
        "oi_value_change_pct": oi_value_change,
    }


def analyze_order_book(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, float]:
    limit = int(config["analysis"].get("order_book_limit", 100))
    depth = safe_get(
        score,
        "盘口深度",
        lambda: get_market(config, "/fapi/v1/depth", {"symbol": symbol, "limit": limit}),
        {},
    )
    bid_value = sum(fnum(price) * fnum(qty) for price, qty in depth.get("bids", []))
    ask_value = sum(fnum(price) * fnum(qty) for price, qty in depth.get("asks", []))
    total = bid_value + ask_value
    imbalance = (bid_value - ask_value) / total if total > 0 else 0.0

    if imbalance >= 0.12:
        score.add_long(3.0, f"盘口买盘厚度占优，深度不平衡 {imbalance:.2%}")
    elif imbalance <= -0.12:
        score.add_short(3.0, f"盘口卖盘厚度占优，深度不平衡 {imbalance:.2%}")

    return {"bid_value": bid_value, "ask_value": ask_value, "imbalance": imbalance}


def analyze_agg_trades(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, float]:
    limit = int(config["analysis"].get("agg_trade_limit", 1000))
    trades = safe_get(
        score,
        "主动成交",
        lambda: get_market(config, "/fapi/v1/aggTrades", {"symbol": symbol, "limit": limit}),
        [],
    )
    buy_value = 0.0
    sell_value = 0.0
    large_buy_value = 0.0
    large_sell_value = 0.0
    notionals: list[float] = []

    for trade in trades:
        price = fnum(trade.get("p"))
        qty = fnum(trade.get("q"))
        notional = price * qty
        if notional <= 0:
            continue
        notionals.append(notional)
        if trade.get("m"):
            sell_value += notional
        else:
            buy_value += notional

    if notionals:
        sorted_notionals = sorted(notionals)
        threshold = sorted_notionals[max(0, int(len(sorted_notionals) * 0.9) - 1)]
        for trade in trades:
            notional = fnum(trade.get("p")) * fnum(trade.get("q"))
            if notional < threshold:
                continue
            if trade.get("m"):
                large_sell_value += notional
            else:
                large_buy_value += notional

    total = buy_value + sell_value
    delta = buy_value - sell_value
    cvd_ratio = delta / total if total > 0 else 0.0
    large_delta = large_buy_value - large_sell_value
    large_total = large_buy_value + large_sell_value
    large_ratio = large_delta / large_total if large_total > 0 else 0.0

    if cvd_ratio >= 0.12:
        score.add_long(6.0, f"主动成交 CVD 偏多，买卖差 {cvd_ratio:.2%}")
    elif cvd_ratio <= -0.12:
        score.add_short(6.0, f"主动成交 CVD 偏空，买卖差 {cvd_ratio:.2%}")

    if large_ratio >= 0.18:
        score.add_long(4.0, f"大额主动成交偏多，买卖差 {large_ratio:.2%}")
    elif large_ratio <= -0.18:
        score.add_short(4.0, f"大额主动成交偏空，买卖差 {large_ratio:.2%}")

    return {
        "buy_value": buy_value,
        "sell_value": sell_value,
        "cvd_ratio": cvd_ratio,
        "large_buy_value": large_buy_value,
        "large_sell_value": large_sell_value,
        "large_ratio": large_ratio,
        "trade_count": float(len(trades)),
    }


def analyze_liquidations(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, float]:
    if not config["analysis"].get("liquidation_enabled", False):
        return {
            "enabled": 0.0,
            "long_liq_value": 0.0,
            "short_liq_value": 0.0,
            "imbalance": 0.0,
            "count": 0.0,
        }

    now_ms = int(time.time() * 1000)
    lookback_ms = int(config["analysis"].get("liquidation_lookback_min", 60)) * 60 * 1000
    limit = int(config["analysis"].get("liquidation_limit", 100))
    orders = safe_get(
        score,
        "强平订单",
        lambda: get_market(
            config,
            "/fapi/v1/forceOrders",
            {"symbol": symbol, "startTime": now_ms - lookback_ms, "endTime": now_ms, "limit": limit},
        ),
        [],
    )
    long_liq_value = 0.0
    short_liq_value = 0.0
    for order in orders:
        value = fnum(order.get("price")) * fnum(order.get("origQty"))
        side = str(order.get("side", "")).upper()
        if side == "SELL":
            long_liq_value += value
        elif side == "BUY":
            short_liq_value += value

    total = long_liq_value + short_liq_value
    imbalance = (short_liq_value - long_liq_value) / total if total > 0 else 0.0
    if total >= 100_000:
        if imbalance >= 0.35:
            score.add_short(2.0, f"近1小时空头强平较多 {fmt_money(short_liq_value)}，上方挤压可能接近释放")
            score.warn("空头强平偏多，继续追多需要等回踩确认")
        elif imbalance <= -0.35:
            score.add_long(2.0, f"近1小时多头强平较多 {fmt_money(long_liq_value)}，下方恐慌可能接近释放")
            score.warn("多头强平偏多，继续追空需要等反抽确认")

    return {
        "enabled": 1.0,
        "long_liq_value": long_liq_value,
        "short_liq_value": short_liq_value,
        "imbalance": imbalance,
        "count": float(len(orders)),
    }


def symbol_onchain_config(config: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    mappings = config.get("onchain", {}).get("symbols", {})
    base = symbol.removesuffix("USDT")
    return mappings.get(symbol) or mappings.get(base)


def token_address_from_id(token_id: str, network: str) -> str:
    prefix = f"{network}_"
    if token_id.startswith(prefix):
        return token_id[len(prefix) :]
    if "_" in token_id:
        return token_id.split("_", 1)[1]
    return token_id


def discover_onchain_token(
    config: dict[str, Any],
    symbol: str,
    network: str,
    score: ScoreBook,
) -> dict[str, Any] | None:
    base_symbol = symbol.removesuffix("USDT").upper()
    try:
        payload = onchain_get_json(
            config,
            "/search/pools",
            {"query": base_symbol, "network": network},
        )
    except OnchainRateLimitError as exc:
        raise exc
    except Exception as exc:
        score.warn_once(f"链上自动发现 {base_symbol} 获取失败：{exc}")
        return None
    if not isinstance(payload, dict):
        return None

    included = {
        item.get("id"): item
        for item in payload.get("included", [])
        if isinstance(item, dict) and item.get("type") == "token"
    }
    candidates: list[dict[str, Any]] = []
    for pool in payload.get("data", []):
        if not isinstance(pool, dict):
            continue
        pool_id = str(pool.get("id", ""))
        attrs = pool.get("attributes", {})
        if pool_id and not pool_id.startswith(f"{network}_"):
            continue

        token_ids: list[str] = []
        relationships = pool.get("relationships", {})
        for rel_name in ("base_token", "quote_token"):
            token_data = relationships.get(rel_name, {}).get("data", {})
            token_id = token_data.get("id")
            if token_id:
                token_ids.append(token_id)

        for token_id in token_ids:
            token = included.get(token_id, {})
            token_attrs = token.get("attributes", {})
            token_symbol = str(token_attrs.get("symbol", "")).upper().lstrip("$")
            token_name = str(token_attrs.get("name", ""))
            if token_symbol != base_symbol:
                continue
            candidates.append(
                {
                    "network": network,
                    "token_address": token_attrs.get("address") or token_address_from_id(token_id, network),
                    "token_symbol": token_symbol,
                    "token_name": token_name,
                    "pool_address": attrs.get("address") or token_address_from_id(pool_id, network),
                    "liquidity_usd": fnum(attrs.get("reserve_in_usd")),
                    "volume_24h_usd": fnum(attrs.get("volume_usd", {}).get("h24")),
                }
            )

    if not candidates:
        score.warn_once(f"链上自动发现暂未找到 {base_symbol} 的 token 地址")
        return None

    candidates.sort(key=lambda item: (item["liquidity_usd"], item["volume_24h_usd"]), reverse=True)
    best = candidates[0]
    score.warn(
        f"链上自动发现：{base_symbol} 使用 {network} 地址 {best['token_address']}，"
        f"池子流动性 {fmt_money(best['liquidity_usd'])} USD"
    )
    return best


def analyze_onchain(
    config: dict[str, Any],
    symbol: str,
    score: ScoreBook,
    network: str | None = None,
    token_address: str | None = None,
) -> dict[str, Any]:
    onchain = config.get("onchain", {})
    if not onchain.get("enabled", True):
        score.warn("链上分析未启用")
        return {"enabled": False, "status": "disabled"}

    mapping = symbol_onchain_config(config, symbol) or {}
    base_symbol = symbol.removesuffix("USDT").upper()
    default_networks = onchain.get("default_networks", {})
    network = network or mapping.get("network")
    token_address = token_address or mapping.get("token_address")
    if not network:
        network = default_networks.get(symbol) or default_networks.get(base_symbol)
    if network and not token_address and onchain.get("auto_discover", True):
        try:
            discovered = discover_onchain_token(config, symbol, network, score)
            if discovered:
                token_address = discovered.get("token_address")
        except OnchainRateLimitError:
            score.warn_once("链上自动发现触发 GeckoTerminal 限流，本次跳过自动搜索")
    if not network and not token_address and onchain.get("auto_discover", True):
        for candidate_network in onchain.get("common_networks", []):
            try:
                discovered = discover_onchain_token(config, symbol, candidate_network, score)
            except OnchainRateLimitError:
                score.warn_once("链上自动发现触发 GeckoTerminal 限流，本次跳过剩余网络搜索")
                break
            if discovered:
                network = candidate_network
                token_address = discovered.get("token_address")
                break

    if not network or not token_address:
        score.warn("未配置链上 token 地址，已跳过链上 DEX 指标")
        return {"enabled": True, "status": "missing_mapping"}

    payload = safe_get(
        score,
        "链上池子",
        lambda: onchain_get_json(config, f"/networks/{network}/tokens/{token_address}/pools"),
        {},
    )
    pools = payload.get("data", []) if isinstance(payload, dict) else []
    if not pools:
        score.warn("GeckoTerminal 未返回链上池子数据")
        return {"enabled": True, "status": "no_pool"}

    pool = pools[0]
    attrs = pool.get("attributes", {})
    liquidity = fnum(attrs.get("reserve_in_usd"))
    volume_24h = fnum(attrs.get("volume_usd", {}).get("h24"))
    buys_1h = fnum(attrs.get("transactions", {}).get("h1", {}).get("buys"))
    sells_1h = fnum(attrs.get("transactions", {}).get("h1", {}).get("sells"))
    buy_sell_ratio = buys_1h / max(1.0, sells_1h)
    price_change_h1 = fnum(attrs.get("price_change_percentage", {}).get("h1"))
    price_change_h24 = fnum(attrs.get("price_change_percentage", {}).get("h24"))

    if liquidity < float(onchain.get("min_liquidity_usd", 100_000)):
        score.warn(f"DEX 流动性偏低：{fmt_money(liquidity)} USD")
    if volume_24h < float(onchain.get("min_volume_24h_usd", 100_000)):
        score.warn(f"DEX 24小时成交额偏低：{fmt_money(volume_24h)} USD")

    if buy_sell_ratio >= 1.15:
        score.add_long(5.0, f"链上1小时买/卖笔数比 {buy_sell_ratio:.2f} 偏多")
    elif buy_sell_ratio <= 0.85:
        score.add_short(5.0, f"链上1小时买/卖笔数比 {buy_sell_ratio:.2f} 偏空")

    if price_change_h1 >= 2:
        score.add_long(3.0, f"DEX 1小时价格上涨 {price_change_h1:.2f}%")
    elif price_change_h1 <= -2:
        score.add_short(3.0, f"DEX 1小时价格下跌 {price_change_h1:.2f}%")

    return {
        "enabled": True,
        "status": "ok",
        "network": network,
        "token_address": token_address,
        "pool_address": attrs.get("address", ""),
        "liquidity_usd": liquidity,
        "volume_24h_usd": volume_24h,
        "buy_sell_ratio_1h": buy_sell_ratio,
        "price_change_h1": price_change_h1,
        "price_change_h24": price_change_h24,
    }


def decide(score: ScoreBook, min_edge: float) -> tuple[str, float, str]:
    edge = score.long - score.short
    total = max(1.0, score.long + score.short)
    confidence = min(95.0, 50.0 + abs(edge) / total * 50.0)
    if abs(edge) < min_edge:
        return "观望", confidence, f"多空分差 {edge:.1f} 小于阈值 {min_edge:.1f}"
    if edge > 0:
        return "做多", confidence, f"多头分数领先 {edge:.1f}"
    return "做空", confidence, f"空头分数领先 {abs(edge):.1f}"


def print_report(symbol: str, data: dict[str, Any], score: ScoreBook, decision: tuple[str, float, str]) -> None:
    ticker = data["ticker"]
    funding = data["funding"]
    long_short = data["long_short"]
    open_interest = data["open_interest"]
    order_book = data["order_book"]
    agg_trades = data["agg_trades"]
    liquidations = data["liquidations"]
    onchain = data["onchain"]
    direction, confidence, decision_reason = decision
    latest_price = fnum(ticker.get("lastPrice")) or data.get("latest_price", 0.0)

    print(f"\n=== {symbol} 合约/链上综合分析 ===")
    print(f"时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"结论：{direction}  |  置信度：{confidence:.1f}%  |  {decision_reason}")
    print(f"评分：多头 {score.long:.1f} / 空头 {score.short:.1f}")

    print("\n[行情]")
    print(f"价格：{fmt_price(latest_price)}  24h涨跌：{fnum(ticker.get('priceChangePercent')):.2f}%  24h成交额：{fmt_money(fnum(ticker.get('quoteVolume')))} USDT")

    print("\n[K线趋势]")
    for interval, item in data["klines"].items():
        print(
            f"{interval}: 收盘 {fmt_price(item['close'])}, EMA20 {fmt_price(item['ema20'])}, "
            f"EMA60 {fmt_price(item['ema60'])}, 近20根 {item['move_20_pct']:.2f}%, "
            f"RSI {item['rsi14']:.1f}, ADX {item['adx14']:.1f}, "
            f"ATR {item['atr_pct']:.2f}%, 量能 {item['volume_ratio']:.2f}倍"
        )

    print("\n[合约衍生品]")
    print(f"资金费率：最新 {fmt_pct(funding['last_rate'])}, 近8次均值 {fmt_pct(funding['avg_rate'])}, 下次结算 {fmt_time_ms(funding['premium'].get('nextFundingTime'))}")
    print(
        f"多空比({long_short['period']}): 全市场 {long_short['global_ratio']:.2f}, "
        f"大户账户 {long_short['top_account_ratio']:.2f}, 大户持仓 {long_short['top_position_ratio']:.2f}, "
        f"主动买卖 {long_short['taker_ratio']:.2f}"
    )
    print(
        f"未平仓量：{open_interest['oi_now']:.4g} {symbol.removesuffix('USDT')}, "
        f"名义价值约 {fmt_money(open_interest['oi_value_now'])} USDT, "
        f"近5周期变化 {open_interest['oi_value_change_pct']:.2f}%"
    )
    print(
        f"盘口：买盘 {fmt_money(order_book['bid_value'])}, 卖盘 {fmt_money(order_book['ask_value'])}, "
        f"不平衡 {order_book['imbalance']:.2%}"
    )
    print(
        f"主动成交：买入 {fmt_money(agg_trades['buy_value'])}, 卖出 {fmt_money(agg_trades['sell_value'])}, "
        f"CVD {agg_trades['cvd_ratio']:.2%}, 大额CVD {agg_trades['large_ratio']:.2%}"
    )
    if liquidations.get("enabled"):
        print(
            f"强平：多头强平 {fmt_money(liquidations['long_liq_value'])}, "
            f"空头强平 {fmt_money(liquidations['short_liq_value'])}, "
            f"不平衡 {liquidations['imbalance']:.2%}"
        )
    else:
        print("强平：未启用 REST 强平过滤")

    print("\n[链上]")
    if onchain.get("status") == "ok":
        print(
            f"{onchain['network']} 池子 {onchain.get('pool_address') or '-'}  "
            f"流动性 {fmt_money(onchain['liquidity_usd'])} USD, "
            f"24h成交 {fmt_money(onchain['volume_24h_usd'])} USD, "
            f"1h买卖比 {onchain['buy_sell_ratio_1h']:.2f}, "
            f"DEX 1h涨跌 {onchain['price_change_h1']:.2f}%"
        )
    elif onchain.get("status") == "missing_mapping":
        print("未配置 token 地址，链上指标未参与评分。可在 config.json 的 onchain.symbols 里配置，或运行时传 --network 和 --token-address。")
    else:
        print(f"链上状态：{onchain.get('status', 'unknown')}")

    print("\n[多头理由]")
    for reason in score.long_reasons or ["无明显多头理由"]:
        print(f"- {reason}")

    print("\n[空头理由]")
    for reason in score.short_reasons or ["无明显空头理由"]:
        print(f"- {reason}")

    if score.warnings:
        print("\n[风险/缺失]")
        for warning in score.warnings:
            print(f"- {warning}")

    print("\n提示：脚本只做数据打分，不代表确定盈利。实盘前请结合止损、仓位和事件风险。")


def analyze_symbol(
    config: dict[str, Any],
    symbol: str,
    network: str | None = None,
    token_address: str | None = None,
) -> tuple[dict[str, Any], ScoreBook, tuple[str, float, str]]:
    score = ScoreBook()
    ticker = analyze_ticker(config, symbol, score)
    klines = analyze_klines(config, symbol, score)
    latest_price = fnum(ticker.get("lastPrice"))
    if latest_price <= 0 and klines:
        latest_price = list(klines.values())[0]["close"]
    if not klines:
        raise CriticalDataError(
            f"{symbol} 的 K线数据没有成功获取，暂时无法给出开单方向。"
            "请检查网络，或用 --timeout 12 加长接口等待时间。"
        )

    funding = analyze_funding(config, symbol, score)
    long_short = analyze_long_short(config, symbol, score)
    open_interest = analyze_open_interest(config, symbol, latest_price, score)
    order_book = analyze_order_book(config, symbol, score)
    agg_trades = analyze_agg_trades(config, symbol, score)
    liquidations = analyze_liquidations(config, symbol, score)
    onchain = analyze_onchain(config, symbol, score, network, token_address)

    # 未平仓量必须结合价格方向判断，放在 K 线和 OI 都拿到之后再补充。
    primary = klines.get("1h") or next(iter(klines.values()), {})
    price_move = fnum(primary.get("move_20_pct"))
    oi_move = fnum(open_interest.get("oi_value_change_pct"))
    if oi_move >= 5 and price_move >= 1:
        score.add_long(4.0, f"价格上涨且未平仓量增加，趋势资金可能进场")
    elif oi_move >= 5 and price_move <= -1:
        score.add_short(4.0, f"价格下跌且未平仓量增加，空头趋势可能进场")
    elif oi_move <= -5 and abs(price_move) >= 1:
        score.warn("价格波动但未平仓量下降，可能是平仓驱动，追单质量下降")

    min_edge = float(config["analysis"].get("min_decision_edge", 8.0))
    decision = decide(score, min_edge)
    return {
        "ticker": ticker,
        "klines": klines,
        "latest_price": latest_price,
        "funding": funding,
        "long_short": long_short,
        "open_interest": open_interest,
        "order_book": order_book,
        "agg_trades": agg_trades,
        "liquidations": liquidations,
        "onchain": onchain,
    }, score, decision


def write_json_report(path: Path, symbol: str, data: dict[str, Any], score: ScoreBook, decision: tuple[str, float, str]) -> None:
    payload = {
        "symbol": symbol,
        "generated_at": int(time.time() * 1000),
        "decision": {
            "direction": decision[0],
            "confidence": decision[1],
            "reason": decision[2],
            "long_score": score.long,
            "short_score": score.short,
            "long_reasons": score.long_reasons,
            "short_reasons": score.short_reasons,
            "warnings": score.warnings,
        },
        "data": data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def compact_signal_payload(symbol: str, data: dict[str, Any], score: ScoreBook, decision: tuple[str, float, str]) -> dict[str, Any]:
    ticker = data.get("ticker", {})
    funding = data.get("funding", {})
    long_short = data.get("long_short", {})
    open_interest = data.get("open_interest", {})
    agg_trades = data.get("agg_trades", {})
    liquidations = data.get("liquidations", {})
    klines = data.get("klines", {})
    onchain = data.get("onchain", {})
    return {
        "ts": int(time.time() * 1000),
        "symbol": symbol,
        "price": fnum(ticker.get("lastPrice")) or data.get("latest_price", 0.0),
        "direction": decision[0],
        "confidence": decision[1],
        "reason": decision[2],
        "long_score": score.long,
        "short_score": score.short,
        "change_24h_pct": fnum(ticker.get("priceChangePercent")),
        "funding_rate": funding.get("last_rate", 0.0),
        "global_long_short": long_short.get("global_ratio", 1.0),
        "top_position_long_short": long_short.get("top_position_ratio", 1.0),
        "open_interest_change_pct": open_interest.get("oi_value_change_pct", 0.0),
        "cvd_ratio": agg_trades.get("cvd_ratio", 0.0),
        "large_cvd_ratio": agg_trades.get("large_ratio", 0.0),
        "long_liq_value": liquidations.get("long_liq_value", 0.0),
        "short_liq_value": liquidations.get("short_liq_value", 0.0),
        "kline_15m": klines.get("15m", {}),
        "kline_1h": klines.get("1h", {}),
        "onchain": {
            "status": onchain.get("status"),
            "network": onchain.get("network"),
            "buy_sell_ratio_1h": onchain.get("buy_sell_ratio_1h"),
            "price_change_h1": onchain.get("price_change_h1"),
        },
        "warnings": score.warnings,
    }


def append_signal_log(config: dict[str, Any], symbol: str, data: dict[str, Any], score: ScoreBook, decision: tuple[str, float, str]) -> Path | None:
    log_path = config.get("analysis", {}).get("signal_log_path")
    if not log_path:
        return None
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(compact_signal_payload(symbol, data, score, decision), ensure_ascii=False, sort_keys=True) + "\n")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="输入币种，输出合约与链上指标综合开单方向")
    parser.add_argument("symbol", nargs="?", help="币种或交易对，例如 BTC、ETH、SOLUSDT")
    parser.add_argument("--config", default="config.json", help="配置文件路径，默认 config.json")
    parser.add_argument("--quote", default="USDT", help="报价资产，默认 USDT")
    parser.add_argument("--network", help="GeckoTerminal 网络名，例如 eth、bsc、solana")
    parser.add_argument("--token-address", help="链上 token 合约地址；传入后会覆盖 config.json 映射")
    parser.add_argument("--timeout", type=float, help="单个接口超时时间，单位秒；网络慢时可设为 12 或 20")
    parser.add_argument("--with-liquidations", action="store_true", help="尝试读取 Binance REST 强平订单；部分环境可能返回 401")
    parser.add_argument("--json-out", help="把完整结果另存为 JSON 文件")
    parser.add_argument("--no-log", action="store_true", help="不追加写入信号历史日志")
    return parser.parse_args()


def main() -> int:
    configure_console()
    load_dotenv(Path(".env"))
    args = parse_args()
    raw_symbol = args.symbol or input("请输入币种，例如 BTC 或 BTCUSDT：")
    try:
        symbol = normalize_symbol(raw_symbol, args.quote.upper())
        config = load_config(Path(args.config))
        if args.timeout:
            config.setdefault("analysis", {})["request_timeout_sec"] = args.timeout
        if args.with_liquidations:
            config.setdefault("analysis", {})["liquidation_enabled"] = True
        data, score, decision = analyze_symbol(config, symbol, args.network, args.token_address)
        print_report(symbol, data, score, decision)
        if not args.no_log:
            log_path = append_signal_log(config, symbol, data, score, decision)
            if log_path:
                print(f"\n信号日志已追加：{log_path}")
        if args.json_out:
            write_json_report(Path(args.json_out), symbol, data, score, decision)
            print(f"\nJSON 报告已保存：{args.json_out}")
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
