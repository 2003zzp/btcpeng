#!/usr/bin/env python3
"""
Backtest and calibrate crypto_signal_analyzer signal history.

读取 data/signal_history.jsonl，回看信号发出后 15m/1h/4h 的价格表现，
统计方向命中率、平均收益、置信度分桶表现。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_MARKET_BASE_URL = "https://fapi.binance.com"
HORIZONS_MIN = [15, 60, 240]


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return default


def http_get_json(base_url: str, path: str, params: dict[str, Any], timeout: float) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}{path}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "signal-history-backtester/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"请求失败 {path}: {exc}") from exc


def load_signals(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[跳过] 第 {line_no} 行 JSON 解析失败：{exc}", file=sys.stderr)
            continue
        if row.get("direction") in {"做多", "做空"} and fnum(row.get("price")) > 0 and fnum(row.get("ts")) > 0:
            rows.append(row)
    return rows


def get_price_at_or_after(base_url: str, symbol: str, target_ms: int, timeout: float) -> tuple[float, int] | None:
    klines = http_get_json(
        base_url,
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "1m",
            "startTime": target_ms,
            "limit": 1,
        },
        timeout,
    )
    if not klines:
        return None
    kline = klines[0]
    return fnum(kline[4]), int(fnum(kline[0]))


def direction_return_pct(direction: str, entry: float, exit_price: float) -> float:
    if entry <= 0:
        return 0.0
    raw = (exit_price - entry) / entry * 100.0
    return raw if direction == "做多" else -raw


def confidence_bucket(confidence: float) -> str:
    lower = int(confidence // 10 * 10)
    upper = lower + 10
    if lower < 50:
        return "<50"
    if lower >= 90:
        return "90+"
    return f"{lower}-{upper}"


def summarize(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    key = f"return_{horizon}m_pct"
    done = [row for row in rows if key in row]
    wins = [row for row in done if fnum(row[key]) > 0]
    losses = [row for row in done if fnum(row[key]) <= 0]
    returns = [fnum(row[key]) for row in done]
    return {
        "count": len(done),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(done) * 100.0 if done else 0.0,
        "avg_return": sum(returns) / len(returns) if returns else 0.0,
        "best": max(returns) if returns else 0.0,
        "worst": min(returns) if returns else 0.0,
    }


def print_summary(rows: list[dict[str, Any]], horizons: list[int]) -> None:
    print("\n=== 信号历史回测/胜率校准 ===")
    print(f"可评估信号数：{len(rows)}")
    if not rows:
        print("还没有足够信号。先多运行几次 crypto_signal_analyzer.py 积累日志。")
        return

    print("\n[总体表现]")
    for horizon in horizons:
        summary = summarize(rows, horizon)
        print(
            f"{horizon}m: 样本 {summary['count']}, 胜率 {summary['win_rate']:.1f}%, "
            f"平均方向收益 {summary['avg_return']:.2f}%, 最好 {summary['best']:.2f}%, 最差 {summary['worst']:.2f}%"
        )

    print("\n[按方向]")
    for direction in ("做多", "做空"):
        subset = [row for row in rows if row.get("direction") == direction]
        if not subset:
            continue
        parts = []
        for horizon in horizons:
            summary = summarize(subset, horizon)
            parts.append(f"{horizon}m胜率 {summary['win_rate']:.1f}%({summary['count']})")
        print(f"{direction}: " + " | ".join(parts))

    print("\n[按置信度分桶]")
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[confidence_bucket(fnum(row.get("confidence")))].append(row)
    for bucket in sorted(buckets):
        subset = buckets[bucket]
        parts = []
        for horizon in horizons:
            summary = summarize(subset, horizon)
            parts.append(f"{horizon}m {summary['win_rate']:.1f}%/{summary['avg_return']:.2f}%({summary['count']})")
        print(f"{bucket}%: " + " | ".join(parts))

    print("\n[校准解读]")
    print("如果某个置信度桶的真实胜率明显低于显示置信度，说明当前打分过于乐观。")
    print("建议至少积累 50-100 条信号后，再根据分桶胜率调整开单阈值和指标权重。")


def enrich_signals(args: argparse.Namespace, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    enriched: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, 1):
        out = dict(row)
        symbol = str(row.get("symbol", "")).upper()
        ts = int(fnum(row.get("ts")))
        entry = fnum(row.get("price"))
        direction = str(row.get("direction"))
        if not symbol or ts <= 0 or entry <= 0:
            continue

        for horizon in args.horizons:
            return_key = f"return_{horizon}m_pct"
            price_key = f"exit_price_{horizon}m"
            if return_key in out and not args.refresh:
                continue
            target_ms = ts + horizon * 60 * 1000
            if target_ms > now_ms - 60_000:
                continue
            try:
                result = get_price_at_or_after(args.market_base_url, symbol, target_ms, args.timeout)
            except Exception as exc:
                print(f"[跳过] {symbol} #{idx} {horizon}m 价格获取失败：{exc}", file=sys.stderr)
                continue
            if not result:
                continue
            exit_price, exit_ts = result
            out[price_key] = exit_price
            out[f"exit_ts_{horizon}m"] = exit_ts
            out[return_key] = direction_return_pct(direction, entry, exit_price)
        enriched.append(out)
    return enriched


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回测 data/signal_history.jsonl 并校准信号胜率")
    parser.add_argument("--history", default="data/signal_history.jsonl", help="信号历史 JSONL 路径")
    parser.add_argument("--market-base-url", default=DEFAULT_MARKET_BASE_URL, help="Binance Futures base URL")
    parser.add_argument("--timeout", type=float, default=8.0, help="单个 Binance 请求超时秒数")
    parser.add_argument("--horizons", type=int, nargs="+", default=HORIZONS_MIN, help="回看周期，单位分钟")
    parser.add_argument("--refresh", action="store_true", help="重新拉取已存在的未来价格结果")
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    path = Path(args.history)
    rows = load_signals(path)
    if not rows:
        print(f"没有找到可回测信号：{path}")
        return 0
    enriched = enrich_signals(args, rows)
    write_jsonl(path, enriched)
    print_summary(enriched, args.horizons)
    print(f"\n已更新历史文件：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
