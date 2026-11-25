"""Isolated stock TradFi market-quality and closed-candle entry rules."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any


def is_equity_contract(info: dict[str, Any]) -> bool:
    return (str(info.get("contractType", "")).upper() == "TRADIFI_PERPETUAL"
            and str(info.get("underlyingType", "")).upper() in {"EQUITY", "HK_EQUITY"})


def closed_15m_bars(klines: list[list[Any]], now: datetime) -> list[list[Any]]:
    now_ms = int(now.timestamp() * 1000)
    return [row for row in klines if len(row) > 7 and int(row[6]) < now_ms]


def four_hour_change_pct(klines: list[list[Any]], now: datetime) -> float | None:
    bars = closed_15m_bars(klines, now)
    if len(bars) < 16:
        return None
    first_open, last_close = float(bars[-16][1]), float(bars[-1][4])
    if not math.isfinite(first_open) or not math.isfinite(last_close) or first_open <= 0 or last_close <= 0:
        return None
    return (last_close / first_open - 1) * 100


def evaluate(klines: list[list[Any]], side: str, current_price: float, now: datetime,
             min_15m_quote_volume_usdt: float = 50_000) -> dict[str, Any]:
    result: dict[str, Any] = {"passed": False, "score": 0.0, "reason": ""}
    now_ms = int(now.timestamp() * 1000)
    closed = closed_15m_bars(klines, now)
    if len(closed) < 7:
        result["reason"] = "最近已收盘15m K线不足"
        return result
    if now_ms - int(closed[-1][6]) > 20 * 60 * 1000:
        result["reason"] = "已收盘15m信号过期"
        return result
    recent_open_times = [int(row[0]) for row in closed[-7:]]
    if any(right - left != 900_000 for left, right in zip(recent_open_times, recent_open_times[1:])):
        result["reason"] = "15m K线不连续"
        return result
    prior = closed[-6:-3]
    breakout, retest, confirm = closed[-3:]
    try:
        b_open, b_high, b_low, b_close = (float(breakout[i]) for i in (1, 2, 3, 4))
        r_high, r_low, r_close = (float(retest[i]) for i in (2, 3, 4))
        c_open, c_close = float(confirm[1]), float(confirm[4])
        highs = [float(row[2]) for row in prior]
        lows = [float(row[3]) for row in prior]
        volumes = [float(row[7]) for row in closed[-6:]]
        lead_in_closes = [float(row[4]) for row in closed[-7:-3]] if side == "SHORT" else []
    except (TypeError, ValueError, IndexError):
        result["reason"] = "K线字段无效"
        return result
    values = (highs + lows + volumes + lead_in_closes
              + [b_open, b_high, b_low, b_close, r_high, r_low, r_close, c_open, c_close, current_price])
    if any(not math.isfinite(value) or value <= 0 for value in values):
        result["reason"] = "价格或成交额缺失"
        return result
    if min(volumes[-3], volumes[-1]) < min_15m_quote_volume_usdt:
        result["reason"] = "15m突破或确认成交额不足"
        return result
    volume_ratio = volumes[-1] / (sum(volumes[:-1]) / 5)
    if not 0.6 <= volume_ratio <= 4.0:
        result["reason"] = "15m量能不足或过热"
        return result
    if abs(current_price / c_close - 1) > 0.004:
        result["reason"] = "实时价格偏离已收盘信号"
        return result
    if side == "LONG":
        level = max(highs)
        pattern = (b_close > level * 1.001 and b_close > b_open and
                   r_low <= level * 1.002 and r_close >= level and
                   c_close > r_high and c_close > c_open and current_price > level and
                   (b_close / b_open - 1) <= 0.02 and (c_close / c_open - 1) <= 0.015)
    elif side == "SHORT":
        level = min(lows)
        pattern = (b_close < level * 0.999 and b_close < b_open and
                   r_high >= level * 0.998 and r_close <= level and
                   c_close < r_low and c_close < c_open and current_price < level and
                   (1 - b_close / b_open) <= 0.02 and (1 - c_close / c_open) <= 0.015)
    else:
        result["reason"] = "交易方向无效"
        return result
    if not pattern:
        result["reason"] = "尚未完成破位、回踩和再次确认"
        return result

    if side == "LONG":
        # Use the range before the breakout as the baseline; the retest may be quiet.
        baseline = sum(volumes[:3]) / 3
        breakout_ratio = volumes[-3] / baseline
        confirm_ratio = volumes[-1] / baseline
        volume_detail = f"突破 {breakout_ratio:.2f}x / 再确认 {confirm_ratio:.2f}x"
        if min(breakout_ratio, confirm_ratio) <= 1.0:
            result["reason"] = f"股票做多持续放量不足：{volume_detail}，均须高于突破前3根均额"
            return result
        if c_close <= b_close:
            result["reason"] = "股票做多价格未继续推进：再确认收盘未超过突破收盘价"
            return result
        reason = f"已收盘15m持续放量并回踩确认，{volume_detail}"
    else:
        # Two consecutive higher closes identify a rebound before the breakdown.
        if not any(first < second < third for first, second, third in
                   zip(lead_in_closes, lead_in_closes[1:], lead_in_closes[2:])):
            result["reason"] = "股票做空破位前未见连续两根15m收盘抬高，不按反弹衰竭开空"
            return result
        reason = f"已收盘15m反弹衰竭，跌破后反抽失败再转弱，量比 {volume_ratio:.1f}x"

    result.update({"passed": True, "score": round(min(10.0, 8.0 + volume_ratio * 0.5), 1),
                   "reason": reason,
                   "volume_ratio": volume_ratio})
    return result
