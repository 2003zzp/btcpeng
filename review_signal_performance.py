#!/usr/bin/env python3
"""
Review recorded dashboard signal snapshots.

Reads data/signal_review_state.json and summarizes confidence buckets,
future returns, and opened trade outcomes. This is offline analysis only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT / "data" / "signal_review_state.json"


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number else default
    except (TypeError, ValueError):
        return default


def confidence_bucket(confidence: float) -> str:
    if confidence >= 95:
        return "95-100"
    if confidence >= 90:
        return "90-95"
    if confidence >= 85:
        return "85-90"
    if confidence >= 80:
        return "80-85"
    if confidence >= 70:
        return "70-80"
    if confidence > 0:
        return "0-70"
    return "未确认"


def load_signals(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    signals = data.get("signals", {})
    if isinstance(signals, dict):
        return list(signals.values())
    if isinstance(signals, list):
        return signals
    return []


def signed_return(signal: dict[str, Any], field: str) -> float | None:
    value = signal.get(field)
    if value is None:
        return None
    ret = fnum(value)
    return ret if signal.get("side") == "LONG" else -ret


def summarize_group(rows: list[dict[str, Any]], horizon: str) -> dict[str, Any]:
    returns = [signed_return(row, horizon) for row in rows]
    returns = [value for value in returns if value is not None]
    opened = [row for row in rows if row.get("opened")]
    closed = [row for row in rows if row.get("closed")]
    pnls = [fnum(row.get("pnl_usdt")) for row in closed]
    return {
        "signals": len(rows),
        "future_samples": len(returns),
        "future_win_rate": sum(1 for value in returns if value > 0) / len(returns) * 100 if returns else 0.0,
        "avg_future_return": mean(returns) if returns else 0.0,
        "opened": len(opened),
        "closed": len(closed),
        "trade_win_rate": sum(1 for value in pnls if value > 0) / len(pnls) * 100 if pnls else 0.0,
        "avg_trade_pnl": mean(pnls) if pnls else 0.0,
        "total_trade_pnl": sum(pnls),
    }


def print_table(title: str, groups: dict[str, list[dict[str, Any]]], horizon: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"{'分组':<12} {'信号':>6} {'样本':>6} {'未来胜率':>9} {'未来均值':>9} {'开仓':>6} {'平仓':>6} {'交易胜率':>9} {'总PnL':>10}")
    for key in sorted(groups):
        stats = summarize_group(groups[key], horizon)
        print(
            f"{key:<12} "
            f"{stats['signals']:>6} "
            f"{stats['future_samples']:>6} "
            f"{stats['future_win_rate']:>8.1f}% "
            f"{stats['avg_future_return']:>8.2f}% "
            f"{stats['opened']:>6} "
            f"{stats['closed']:>6} "
            f"{stats['trade_win_rate']:>8.1f}% "
            f"{stats['total_trade_pnl']:>10.2f}"
        )


def threshold_report(signals: list[dict[str, Any]], horizon: str, min_samples: int) -> None:
    print("\n阈值候选")
    print("--------")
    print(f"{'置信度>=':<10} {'样本':>6} {'未来胜率':>9} {'未来均值':>9} {'开仓':>6} {'总PnL':>10}")
    for threshold in [70, 75, 80, 85, 90, 92, 95]:
        rows = [row for row in signals if fnum(row.get("analyzer_confidence_pct")) >= threshold]
        stats = summarize_group(rows, horizon)
        marker = "*" if stats["future_samples"] >= min_samples and stats["avg_future_return"] > 0 else " "
        print(
            f"{marker}{threshold:<9} "
            f"{stats['future_samples']:>6} "
            f"{stats['future_win_rate']:>8.1f}% "
            f"{stats['avg_future_return']:>8.2f}% "
            f"{stats['opened']:>6} "
            f"{stats['total_trade_pnl']:>10.2f}"
        )
    print("\n说明：带 * 的阈值表示样本数足够且该周期方向收益均值为正，不代表一定最优。")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review dashboard signal performance")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="signal_review_state.json path")
    parser.add_argument("--horizon", choices=["15m", "60m", "240m"], default="60m")
    parser.add_argument("--min-samples", type=int, default=20)
    args = parser.parse_args()

    horizon_field = {
        "15m": "future_return_15m_pct",
        "60m": "future_return_60m_pct",
        "240m": "future_return_240m_pct",
    }[args.horizon]
    signals = load_signals(args.state)
    if not signals:
        print(f"没有复盘数据：{args.state}")
        print("先让机器人运行一段时间，生成 data/signal_review_state.json 后再复盘。")
        return 0

    print(f"复盘文件：{args.state}")
    print(f"信号数量：{len(signals)}，周期：{args.horizon}")

    by_confidence: dict[str, list[dict[str, Any]]] = {}
    by_side: dict[str, list[dict[str, Any]]] = {}
    by_decision: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        by_confidence.setdefault(confidence_bucket(fnum(signal.get("analyzer_confidence_pct"))), []).append(signal)
        by_side.setdefault(str(signal.get("side", "-")), []).append(signal)
        decision = "已开仓" if signal.get("opened") else str(signal.get("decision_summary") or signal.get("reject_reason") or "未开仓")
        by_decision.setdefault(decision[:24], []).append(signal)

    print_table("按置信度分桶", by_confidence, horizon_field)
    print_table("按方向", by_side, horizon_field)
    print_table("按最终决策", by_decision, horizon_field)
    threshold_report(signals, horizon_field, args.min_samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
