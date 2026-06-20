#!/usr/bin/env python3
"""
Fast secondary signal confirmer for the dashboard.

It keeps the decision style from crypto_signal_analyzer.py:
score long/short, compare the edge, then convert the edge into confidence.
It intentionally skips slow modules such as order book, agg trades,
liquidations, and on-chain data.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "market_base_url": "https://fapi.binance.com",
    "request_timeout_sec": 2.5,
    "quote_asset": "USDT",
    "analysis": {
        "min_decision_edge": 8.0,
        "kline_intervals": ["15m", "1h", "4h"],
        "kline_limit": 180,
        "derivatives_period": "1h",
        "derivatives_limit": 24,
        "agg_trade_limit": 1000,
        "order_book_limit": 100,
    },
}


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


def deep_update(target: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value
    return target


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_config(path: Path | None = None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and path.exists():
        deep_update(config, load_json(path, {}))
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


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


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


def http_get_json(base_url: str, path: str, params: dict[str, Any] | None = None, timeout: float = 2.5) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "close",
            "User-Agent": "crypto-signal-analyzer/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_symbol(raw: str, quote: str = "USDT") -> str:
    symbol = raw.strip().upper().replace("/", "")
    return symbol if symbol.endswith(quote) else f"{symbol}{quote}"


def get_market(config: dict[str, Any], path: str, params: dict[str, Any]) -> Any:
    analysis = config.get("analysis", {})
    return http_get_json(
        str(config.get("market_base_url", "https://fapi.binance.com")),
        path,
        params,
        timeout=float(analysis.get("request_timeout_sec", config.get("request_timeout_sec", 2.5))),
    )


def decide(score: ScoreBook, min_edge: float) -> tuple[str, float, str]:
    edge = score.long - score.short
    total = max(1.0, score.long + score.short)
    confidence = min(95.0, 50.0 + abs(edge) / total * 50.0)
    if abs(edge) < min_edge:
        return "观望", confidence, f"多空分差 {edge:.1f} 小于阈值 {min_edge:.1f}"
    if edge > 0:
        return "做多", confidence, f"多头分数领先 {edge:.1f}"
    return "做空", confidence, f"空头分数领先 {abs(edge):.1f}"


def analyze_ticker(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, Any]:
    ticker = get_market(config, "/fapi/v1/ticker/24hr", {"symbol": symbol})
    change_24h = fnum(ticker.get("priceChangePercent"))
    quote_volume = fnum(ticker.get("quoteVolume"))
    if change_24h >= 4:
        score.add_long(5.0, f"24小时涨幅 {change_24h:.2f}%")
    elif change_24h <= -4:
        score.add_short(5.0, f"24小时跌幅 {change_24h:.2f}%")
    if quote_volume >= 20_000_000:
        score.add_long(1.5, "合约成交额充足")
        score.add_short(1.5, "合约成交额充足")
    else:
        score.warn("24小时成交额偏低")
    return ticker


def score_klines(interval: str, klines: list[list[Any]], score: ScoreBook) -> dict[str, float]:
    if len(klines) < 65:
        score.warn(f"{interval} K线数量不足")
        return {}
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
        score.add_long(weight, f"{interval} EMA20 > EMA60")
    elif close < ema20 < ema60:
        trend_side = "short"
        score.add_short(weight, f"{interval} EMA20 < EMA60")
    elif close > ema60:
        trend_side = "long"
        score.add_long(weight * 0.45, f"{interval} 价格站上 EMA60")
    elif close < ema60:
        trend_side = "short"
        score.add_short(weight * 0.45, f"{interval} 价格跌破 EMA60")

    if interval in {"15m", "1h"}:
        if adx14 >= 28 and trend_side == "long":
            score.add_long(3.0, f"{interval} ADX {adx14:.1f} 支持多头")
        elif adx14 >= 28 and trend_side == "short":
            score.add_short(3.0, f"{interval} ADX {adx14:.1f} 支持空头")
        elif adx14 and adx14 < 16:
            score.warn(f"{interval} ADX {adx14:.1f} 偏低")

    if interval == "15m" and atr_pct >= 3.0:
        score.warn(f"15m ATR {atr_pct:.2f}% 偏高")

    if move_20 >= 2:
        score.add_long(4.0, f"{interval} 近20根涨幅 {move_20:.2f}%")
    elif move_20 <= -2:
        score.add_short(4.0, f"{interval} 近20根跌幅 {move_20:.2f}%")
    if volume_ratio >= 1.6 and move_20 > 0:
        score.add_long(4.0, f"{interval} 放量上涨 {volume_ratio:.2f}x")
    elif volume_ratio >= 1.6 and move_20 < 0:
        score.add_short(4.0, f"{interval} 放量下跌 {volume_ratio:.2f}x")
    if rsi14 >= 78:
        score.add_short(3.0, f"{interval} RSI 过热 {rsi14:.1f}")
    elif rsi14 <= 22:
        score.add_long(3.0, f"{interval} RSI 超卖 {rsi14:.1f}")
    return {
        "close": close,
        "ema20": ema20,
        "ema60": ema60,
        "move_20_pct": move_20,
        "rsi14": rsi14,
        "atr14": atr14,
        "atr_pct": atr_pct,
        "adx14": adx14,
        "volume_ratio": volume_ratio,
        "trend_side": trend_side,
    }


def analyze_klines(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, Any]:
    intervals = config.get("analysis", {}).get("kline_intervals", ["15m", "1h"])
    limit = int(config.get("analysis", {}).get("kline_limit", 80))
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(intervals)) as executor:
        futures = {
            executor.submit(get_market, config, "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}): interval
            for interval in intervals
        }
        for future, interval in futures.items():
            try:
                result[interval] = score_klines(interval, future.result(), score)
            except Exception as exc:
                score.warn(f"{interval} K线失败：{exc}")
    return result


def analyze_fast_derivatives(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, Any]:
    analysis = config.get("analysis", {})
    period = str(analysis.get("derivatives_period", "1h"))
    limit = int(analysis.get("derivatives_limit", 24))
    specs = {
        "premium": ("/fapi/v1/premiumIndex", {"symbol": symbol}),
        "funding_history": ("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 8}),
        "global_ratio": ("/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": period, "limit": limit}),
        "top_account": ("/futures/data/topLongShortAccountRatio", {"symbol": symbol, "period": period, "limit": limit}),
        "top_position": ("/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": period, "limit": limit}),
        "taker": ("/futures/data/takerlongshortRatio", {"symbol": symbol, "period": period, "limit": limit}),
        "oi_hist": ("/futures/data/openInterestHist", {"symbol": symbol, "period": period, "limit": limit}),
    }
    data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(specs)) as executor:
        futures = {executor.submit(get_market, config, path, params): name for name, (path, params) in specs.items()}
        for future, name in futures.items():
            try:
                data[name] = future.result()
            except Exception as exc:
                score.warn(f"{name} 获取失败：{exc}")
                data[name] = [] if name != "premium" else {}

    last_rate = fnum(data.get("premium", {}).get("lastFundingRate"))
    funding_history = data.get("funding_history") or []
    avg_rate = average([fnum(item.get("fundingRate")) for item in funding_history]) if funding_history else last_rate
    if last_rate >= 0.0005:
        score.add_short(7.0, f"资金费率偏高 {last_rate * 100:.4f}%")
    elif last_rate <= -0.0005:
        score.add_long(7.0, f"资金费率偏低 {last_rate * 100:.4f}%")
    elif last_rate > 0:
        score.add_long(2.0, f"资金费率小幅为正 {last_rate * 100:.4f}%")
    elif last_rate < 0:
        score.add_short(2.0, f"资金费率小幅为负 {last_rate * 100:.4f}%")

    if avg_rate >= 0.0004:
        score.add_short(3.0, f"最近资金费率均值偏高 {avg_rate * 100:.4f}%")
    elif avg_rate <= -0.0004:
        score.add_long(3.0, f"最近资金费率均值偏低 {avg_rate * 100:.4f}%")

    ratios = data.get("global_ratio") or []
    global_ratio = fnum(ratios[-1].get("longShortRatio"), 1.0) if ratios else 1.0
    if global_ratio >= 1.15:
        score.add_long(5.0, f"全市场多空比 {global_ratio:.2f} 偏多")
    elif global_ratio <= 0.87:
        score.add_short(5.0, f"全市场多空比 {global_ratio:.2f} 偏空")
    if global_ratio >= 2.5:
        score.add_short(2.0, "多头极度拥挤")
    elif global_ratio <= 0.4:
        score.add_long(2.0, "空头极度拥挤")

    top_accounts = data.get("top_account") or []
    top_account_ratio = fnum(top_accounts[-1].get("longShortRatio"), 1.0) if top_accounts else 1.0
    if top_account_ratio >= 1.10:
        score.add_long(4.0, f"大户账户多空比 {top_account_ratio:.2f} 偏多")
    elif top_account_ratio <= 0.90:
        score.add_short(4.0, f"大户账户多空比 {top_account_ratio:.2f} 偏空")

    top_positions = data.get("top_position") or []
    top_position_ratio = fnum(top_positions[-1].get("longShortRatio"), 1.0) if top_positions else 1.0
    if top_position_ratio >= 1.10:
        score.add_long(6.0, f"大户持仓多空比 {top_position_ratio:.2f} 偏多")
    elif top_position_ratio <= 0.90:
        score.add_short(6.0, f"大户持仓多空比 {top_position_ratio:.2f} 偏空")

    taker = data.get("taker") or []
    taker_ratio = fnum(taker[-1].get("buySellRatio"), 1.0) if taker else 1.0
    if taker_ratio >= 1.10:
        score.add_long(5.0, f"主动买卖比 {taker_ratio:.2f} 偏多")
    elif taker_ratio <= 0.90:
        score.add_short(5.0, f"主动买卖比 {taker_ratio:.2f} 偏空")

    oi_hist = data.get("oi_hist") or []
    oi_value_change = 0.0
    if len(oi_hist) >= 6:
        oi_value_change = pct(fnum(oi_hist[-1].get("sumOpenInterestValue")), fnum(oi_hist[0].get("sumOpenInterestValue")))
        if oi_value_change >= 5:
            score.add_long(3.0, f"OI 增加 {oi_value_change:.2f}%")
            score.add_short(3.0, f"OI 增加 {oi_value_change:.2f}%")
        elif oi_value_change <= -5:
            score.warn(f"OI 下降 {oi_value_change:.2f}%")

    return {
        "funding_rate": last_rate,
        "funding_avg_rate": avg_rate,
        "global_ratio": global_ratio,
        "top_account_ratio": top_account_ratio,
        "top_position_ratio": top_position_ratio,
        "taker_ratio": taker_ratio,
        "open_interest_change_pct": oi_value_change,
    }


def analyze_order_book(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, float]:
    limit = int(config.get("analysis", {}).get("order_book_limit", 100))
    try:
        depth = get_market(config, "/fapi/v1/depth", {"symbol": symbol, "limit": limit})
    except Exception as exc:
        score.warn(f"盘口深度 获取失败：{exc}")
        depth = {}
    bid_value = sum(fnum(price) * fnum(qty) for price, qty in depth.get("bids", []))
    ask_value = sum(fnum(price) * fnum(qty) for price, qty in depth.get("asks", []))
    total = bid_value + ask_value
    imbalance = (bid_value - ask_value) / total if total > 0 else 0.0

    if imbalance >= 0.12:
        score.add_long(3.0, f"盘口买盘厚度占优 {imbalance:.2%}")
    elif imbalance <= -0.12:
        score.add_short(3.0, f"盘口卖盘厚度占优 {imbalance:.2%}")
    return {"bid_value": bid_value, "ask_value": ask_value, "imbalance": imbalance}


def analyze_agg_trades(config: dict[str, Any], symbol: str, score: ScoreBook) -> dict[str, float]:
    limit = int(config.get("analysis", {}).get("agg_trade_limit", 1000))
    try:
        trades = get_market(config, "/fapi/v1/aggTrades", {"symbol": symbol, "limit": limit})
    except Exception as exc:
        score.warn(f"主动成交 获取失败：{exc}")
        trades = []

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
        score.add_long(6.0, f"主动成交 CVD 偏多 {cvd_ratio:.2%}")
    elif cvd_ratio <= -0.12:
        score.add_short(6.0, f"主动成交 CVD 偏空 {cvd_ratio:.2%}")

    if large_ratio >= 0.18:
        score.add_long(4.0, f"大额主动成交偏多 {large_ratio:.2%}")
    elif large_ratio <= -0.18:
        score.add_short(4.0, f"大额主动成交偏空 {large_ratio:.2%}")

    return {
        "buy_value": buy_value,
        "sell_value": sell_value,
        "cvd_ratio": cvd_ratio,
        "large_buy_value": large_buy_value,
        "large_sell_value": large_sell_value,
        "large_ratio": large_ratio,
        "trade_count": float(len(trades)),
    }


def analyze_symbol_fast(config: dict[str, Any], symbol: str) -> dict[str, Any]:
    score = ScoreBook()
    ticker = analyze_ticker(config, symbol, score)
    klines = analyze_klines(config, symbol, score)
    derivatives = analyze_fast_derivatives(config, symbol, score)
    order_book = analyze_order_book(config, symbol, score)
    agg_trades = analyze_agg_trades(config, symbol, score)

    primary = klines.get("1h") or klines.get("15m") or {}
    price_move = fnum(primary.get("move_20_pct"))
    oi_move = fnum(derivatives.get("open_interest_change_pct"))
    if oi_move >= 5 and price_move >= 1:
        score.add_long(4.0, "价格上涨且 OI 增加")
    elif oi_move >= 5 and price_move <= -1:
        score.add_short(4.0, "价格下跌且 OI 增加")

    decision = decide(score, float(config.get("analysis", {}).get("min_decision_edge", 8.0)))
    return {
        "symbol": symbol,
        "direction": decision[0],
        "confidence": round(float(decision[1]), 1),
        "reason": decision[2],
        "long_score": round(score.long, 2),
        "short_score": round(score.short, 2),
        "long_reasons": score.long_reasons,
        "short_reasons": score.short_reasons,
        "warnings": score.warnings,
        "ticker": ticker,
        "klines": klines,
        "derivatives": derivatives,
        "order_book": order_book,
        "agg_trades": agg_trades,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="快速输出做多/做空/观望与置信度")
    parser.add_argument("symbol")
    parser.add_argument("--config", default="paper_dashboard_config.json")
    parser.add_argument("--timeout", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    if args.timeout:
        config["request_timeout_sec"] = args.timeout
    symbol = normalize_symbol(args.symbol, str(config.get("quote_asset", "USDT")))
    result = analyze_symbol_fast(config, symbol)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
