#!/usr/bin/env python3
"""
On-chain meme token automation framework.

Default mode is paper trading. Live execution is delegated to a webhook-based
wallet executor so private keys do not live in this project.
"""

from __future__ import annotations

import argparse
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
    "request_timeout_sec": 10,
    "scan_interval_sec": 20,
    "data_dir": "data",
    "verbose_console": True,
    "networks": ["bsc", "base", "solana"],
    "geckoterminal": {
        "base_url": "https://api.geckoterminal.com/api/v2",
        "new_pools_per_network": 20,
        "include_tokens": True,
    },
    "execution": {
        "mode": "paper",
        "fixed_trade_usd": 25.0,
        "max_slippage_pct": 8.0,
        "webhook_url_env": "ONCHAIN_TRADE_WEBHOOK_URL",
        "webhook_secret_env": "ONCHAIN_TRADE_WEBHOOK_SECRET",
        "require_safety_webhook_for_live": True,
    },
    "paper": {
        "starting_balance_usd": 500.0,
    },
    "filters": {
        "min_pool_age_sec": 120,
        "max_pool_age_sec": 7200,
        "min_liquidity_usd": 20_000.0,
        "min_volume_h1_usd": 8_000.0,
        "min_volume_h24_usd": 20_000.0,
        "min_txns_m5": 12,
        "min_buy_sell_ratio_m5": 1.15,
        "max_buy_sell_ratio_m5": 6.0,
        "max_price_change_m5_pct": 250.0,
        "max_fdv_liquidity_ratio": 80.0,
        "allowed_quote_symbols": ["WBNB", "BNB", "WETH", "ETH", "USDT", "USDC", "SOL", "WSOL"],
        "blocked_tokens": [],
        "blocked_pools": [],
    },
    "scoring": {
        "min_score_to_buy": 75.0,
        "max_new_trades_per_scan": 1,
    },
    "risk": {
        "max_open_positions": 4,
        "max_trades_per_day": 12,
        "max_daily_loss_usd": 50.0,
        "max_daily_loss_pct": 10.0,
        "max_consecutive_losses": 3,
        "cooldown_after_loss_sec": 600,
        "stop_loss_pct": 25.0,
        "take_profit_steps": [
            {"gain_pct": 80.0, "sell_pct": 40.0},
            {"gain_pct": 150.0, "sell_pct": 30.0},
            {"gain_pct": 300.0, "sell_pct": 20.0},
        ],
        "trailing_start_gain_pct": 120.0,
        "trailing_drawdown_pct": 35.0,
        "max_hold_minutes": 60,
        "no_new_high_exit_minutes": 20,
        "liquidity_exit_ratio": 0.45,
    },
    "notifications": {
        "telegram_enabled": True,
    },
}


@dataclass
class Candidate:
    pool_id: str
    network: str
    pool_address: str
    token_address: str
    token_symbol: str
    token_name: str
    quote_symbol: str
    price_usd: float
    liquidity_usd: float
    volume_m5_usd: float
    volume_h1_usd: float
    volume_h24_usd: float
    txns_m5: int
    buys_m5: int
    sells_m5: int
    buy_sell_ratio_m5: float
    price_change_m5_pct: float
    price_change_h1_pct: float
    fdv_usd: float
    market_cap_usd: float
    pool_created_at: str
    pool_age_sec: float
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    rejects: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.network}:{self.pool_address}:{self.token_address}".lower()


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def today_key() -> str:
    return utc_now().strftime("%Y-%m-%d")


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


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        deep_update(config, load_json(path, {}))
    return config


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return default


def fint(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).astimezone(timezone.utc)
    except ValueError:
        return None


def nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def http_request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
    headers: dict[str, str] | None = None,
) -> Any:
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "onchain-dog-bot/0.1",
        **(headers or {}),
    }
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed {url}: {exc}") from exc


def http_get_json(base_url: str, path: str, params: dict[str, Any] | None, timeout: float) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    return http_request_json("GET", url, timeout=timeout)


def append_event(config: dict[str, Any], event: dict[str, Any]) -> None:
    data_dir = Path(str(config.get("data_dir", "data")))
    path = data_dir / "onchain_dog_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {"ts": iso_now(), **event}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n")


def log(config: dict[str, Any], message: str) -> None:
    if config.get("verbose_console", True):
        print(f"{iso_now()} {message}")


def telegram_enabled(config: dict[str, Any]) -> bool:
    notify = config.get("notifications", {})
    if not notify.get("telegram_enabled", True):
        return False
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def notify_telegram(config: dict[str, Any], text: str) -> None:
    if not telegram_enabled(config):
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "onchain-dog-bot/0.1",
        },
        method="POST",
    )
    try:
        timeout = float(config.get("request_timeout_sec", 10))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception as exc:
        print(f"[warn] telegram notification failed: {exc}", file=sys.stderr)


def initial_state(config: dict[str, Any]) -> dict[str, Any]:
    starting_balance = float(config.get("paper", {}).get("starting_balance_usd", 500.0))
    return {
        "created_at": iso_now(),
        "paper_balance_usd": starting_balance,
        "paper_starting_balance_usd": starting_balance,
        "realized_pnl_usd": 0.0,
        "consecutive_losses": 0,
        "last_loss_ts": None,
        "daily": {
            "date": today_key(),
            "realized_pnl_usd": 0.0,
            "trades": 0,
        },
        "seen_pools": {},
        "positions": {},
        "closed_positions": [],
    }


def load_state(config: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    data_dir = Path(str(config.get("data_dir", "data")))
    path = data_dir / "onchain_dog_state.json"
    state = load_json(path, initial_state(config))
    state.setdefault("paper_starting_balance_usd", float(config.get("paper", {}).get("starting_balance_usd", 500.0)))
    state.setdefault("paper_balance_usd", state["paper_starting_balance_usd"])
    state.setdefault("realized_pnl_usd", 0.0)
    state.setdefault("consecutive_losses", 0)
    state.setdefault("last_loss_ts", None)
    state.setdefault("seen_pools", {})
    state.setdefault("positions", {})
    state.setdefault("closed_positions", [])
    state.setdefault("daily", {"date": today_key(), "realized_pnl_usd": 0.0, "trades": 0})
    if state["daily"].get("date") != today_key():
        state["daily"] = {"date": today_key(), "realized_pnl_usd": 0.0, "trades": 0}
    return path, state


def included_token_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tokens: dict[str, dict[str, Any]] = {}
    for item in payload.get("included", []) or []:
        if item.get("type") != "token":
            continue
        token_id = str(item.get("id", ""))
        attrs = item.get("attributes", {}) or {}
        tokens[token_id] = attrs
    return tokens


def relationship_id(item: dict[str, Any], name: str) -> str:
    rel = item.get("relationships", {}).get(name, {}).get("data", {})
    if isinstance(rel, dict):
        return str(rel.get("id", ""))
    return ""


def address_from_pool_id(pool_id: str) -> str:
    if "_" in pool_id:
        return pool_id.split("_", 1)[1]
    return pool_id


def token_address_from_id(token_id: str) -> str:
    if "_" in token_id:
        return token_id.split("_", 1)[1]
    return token_id


def calc_ratio(buys: int, sells: int) -> float:
    if sells <= 0:
        return float(buys) if buys > 0 else 0.0
    return buys / sells


def parse_pool(network: str, item: dict[str, Any], tokens: dict[str, dict[str, Any]]) -> Candidate:
    attrs = item.get("attributes", {}) or {}
    pool_id = str(item.get("id", ""))
    pool_address = str(attrs.get("address") or address_from_pool_id(pool_id))

    base_id = relationship_id(item, "base_token")
    quote_id = relationship_id(item, "quote_token")
    base = tokens.get(base_id, {})
    quote = tokens.get(quote_id, {})

    token_address = str(base.get("address") or attrs.get("base_token_address") or token_address_from_id(base_id))
    token_symbol = str(base.get("symbol") or attrs.get("base_token_symbol") or attrs.get("name") or "UNKNOWN")
    token_name = str(base.get("name") or attrs.get("base_token_name") or token_symbol)
    quote_symbol = str(quote.get("symbol") or attrs.get("quote_token_symbol") or "")

    tx_m5 = attrs.get("transactions", {}).get("m5", {}) or {}
    buys_m5 = fint(tx_m5.get("buys"))
    sells_m5 = fint(tx_m5.get("sells"))
    created_at = str(attrs.get("pool_created_at") or "")
    created_dt = parse_time(created_at)
    age_sec = (utc_now() - created_dt).total_seconds() if created_dt else 0.0

    return Candidate(
        pool_id=pool_id,
        network=network,
        pool_address=pool_address,
        token_address=token_address,
        token_symbol=token_symbol.upper(),
        token_name=token_name,
        quote_symbol=quote_symbol.upper(),
        price_usd=fnum(attrs.get("base_token_price_usd") or attrs.get("token_price_usd")),
        liquidity_usd=fnum(attrs.get("reserve_in_usd")),
        volume_m5_usd=fnum(nested(attrs, "volume_usd", "m5")),
        volume_h1_usd=fnum(nested(attrs, "volume_usd", "h1")),
        volume_h24_usd=fnum(nested(attrs, "volume_usd", "h24")),
        txns_m5=buys_m5 + sells_m5,
        buys_m5=buys_m5,
        sells_m5=sells_m5,
        buy_sell_ratio_m5=calc_ratio(buys_m5, sells_m5),
        price_change_m5_pct=fnum(nested(attrs, "price_change_percentage", "m5")),
        price_change_h1_pct=fnum(nested(attrs, "price_change_percentage", "h1")),
        fdv_usd=fnum(attrs.get("fdv_usd")),
        market_cap_usd=fnum(attrs.get("market_cap_usd")),
        pool_created_at=created_at,
        pool_age_sec=age_sec,
    )


def fetch_new_pools(config: dict[str, Any]) -> list[Candidate]:
    gt = config.get("geckoterminal", {})
    base_url = str(gt.get("base_url", DEFAULT_CONFIG["geckoterminal"]["base_url"]))
    timeout = float(config.get("request_timeout_sec", 10))
    per_network = int(gt.get("new_pools_per_network", 20))
    include_tokens = bool(gt.get("include_tokens", True))
    result: list[Candidate] = []

    for network in config.get("networks", []):
        params: dict[str, Any] = {"page": 1}
        if include_tokens:
            params["include"] = "base_token,quote_token"
        try:
            payload = http_get_json(base_url, f"/networks/{network}/new_pools", params, timeout)
        except RuntimeError:
            payload = http_get_json(base_url, f"/networks/{network}/new_pools", {"page": 1}, timeout)
        tokens = included_token_map(payload)
        for item in (payload.get("data", []) or [])[:per_network]:
            try:
                result.append(parse_pool(str(network), item, tokens))
            except Exception as exc:
                log(config, f"[skip] failed to parse pool on {network}: {exc}")
    return result


def fetch_pool(config: dict[str, Any], network: str, pool_address: str) -> Candidate | None:
    gt = config.get("geckoterminal", {})
    base_url = str(gt.get("base_url", DEFAULT_CONFIG["geckoterminal"]["base_url"]))
    timeout = float(config.get("request_timeout_sec", 10))
    params: dict[str, Any] = {}
    if gt.get("include_tokens", True):
        params["include"] = "base_token,quote_token"
    payload = http_get_json(base_url, f"/networks/{network}/pools/{pool_address}", params, timeout)
    tokens = included_token_map(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    return parse_pool(network, data, tokens)


def evaluate_candidate(config: dict[str, Any], candidate: Candidate) -> Candidate:
    filters = config.get("filters", {})
    allowed_quotes = {str(v).upper() for v in filters.get("allowed_quote_symbols", [])}
    blocked_tokens = {str(v).lower() for v in filters.get("blocked_tokens", [])}
    blocked_pools = {str(v).lower() for v in filters.get("blocked_pools", [])}

    if candidate.token_address.lower() in blocked_tokens:
        candidate.rejects.append("blocked_token")
    if candidate.pool_address.lower() in blocked_pools:
        candidate.rejects.append("blocked_pool")
    if not candidate.token_address:
        candidate.rejects.append("missing_token_address")
    if candidate.price_usd <= 0:
        candidate.rejects.append("missing_price")
    if allowed_quotes and candidate.quote_symbol and candidate.quote_symbol not in allowed_quotes:
        candidate.rejects.append(f"quote_not_allowed:{candidate.quote_symbol}")
    if candidate.pool_age_sec < float(filters.get("min_pool_age_sec", 0)):
        candidate.rejects.append("too_new")
    if candidate.pool_age_sec > float(filters.get("max_pool_age_sec", 10**12)):
        candidate.rejects.append("too_old")
    if candidate.liquidity_usd < float(filters.get("min_liquidity_usd", 0)):
        candidate.rejects.append("low_liquidity")
    if candidate.volume_h1_usd < float(filters.get("min_volume_h1_usd", 0)):
        candidate.rejects.append("low_h1_volume")
    if candidate.volume_h24_usd < float(filters.get("min_volume_h24_usd", 0)):
        candidate.rejects.append("low_h24_volume")
    if candidate.txns_m5 < int(filters.get("min_txns_m5", 0)):
        candidate.rejects.append("low_m5_txns")

    min_ratio = float(filters.get("min_buy_sell_ratio_m5", 0))
    max_ratio = float(filters.get("max_buy_sell_ratio_m5", 10**9))
    if candidate.buy_sell_ratio_m5 < min_ratio:
        candidate.rejects.append("weak_buy_pressure")
    if candidate.buy_sell_ratio_m5 > max_ratio:
        candidate.rejects.append("buy_pressure_too_one_sided")

    max_m5_change = float(filters.get("max_price_change_m5_pct", 10**9))
    if candidate.price_change_m5_pct > max_m5_change:
        candidate.rejects.append("m5_move_too_extended")

    max_fdv_liq = float(filters.get("max_fdv_liquidity_ratio", 0))
    fdv_basis = candidate.fdv_usd or candidate.market_cap_usd
    if max_fdv_liq > 0 and fdv_basis > 0 and candidate.liquidity_usd > 0:
        if fdv_basis / candidate.liquidity_usd > max_fdv_liq:
            candidate.rejects.append("fdv_liquidity_ratio_too_high")

    if candidate.rejects:
        candidate.score = 0.0
        return candidate

    min_liq = max(float(filters.get("min_liquidity_usd", 1)), 1.0)
    min_h1_vol = max(float(filters.get("min_volume_h1_usd", 1)), 1.0)
    min_txns = max(float(filters.get("min_txns_m5", 1)), 1.0)

    score = 30.0
    candidate.reasons.append("hard_filters_passed")

    liquidity_score = min(15.0, candidate.liquidity_usd / min_liq * 7.5)
    volume_score = min(15.0, candidate.volume_h1_usd / min_h1_vol * 7.5)
    tx_score = min(10.0, candidate.txns_m5 / min_txns * 5.0)
    score += liquidity_score + volume_score + tx_score

    if min_ratio <= candidate.buy_sell_ratio_m5 <= max_ratio:
        score += 10.0
        candidate.reasons.append(f"buy_sell_ratio_m5={candidate.buy_sell_ratio_m5:.2f}")
    if candidate.price_change_h1_pct >= 0:
        score += min(10.0, 5.0 + candidate.price_change_h1_pct / 20.0)
        candidate.reasons.append(f"h1_change={candidate.price_change_h1_pct:.2f}%")
    if allowed_quotes and candidate.quote_symbol in allowed_quotes:
        score += 5.0
        candidate.reasons.append(f"quote={candidate.quote_symbol}")
    if fdv_basis > 0 and candidate.liquidity_usd > 0:
        score += 5.0
        candidate.reasons.append(f"fdv_liq={fdv_basis / candidate.liquidity_usd:.1f}")
    if candidate.pool_age_sec <= 1800:
        score += 5.0
        candidate.reasons.append("fresh_pool")

    candidate.score = min(100.0, score)
    return candidate


def position_count(state: dict[str, Any]) -> int:
    return len(state.get("positions", {}))


def risk_block_reason(config: dict[str, Any], state: dict[str, Any]) -> str | None:
    risk = config.get("risk", {})
    execution = config.get("execution", {})
    mode = str(execution.get("mode", "paper")).lower()

    if mode == "webhook" and execution.get("require_safety_webhook_for_live", True):
        webhook_url = os.environ.get(str(execution.get("webhook_url_env", "ONCHAIN_TRADE_WEBHOOK_URL")), "").strip()
        if not webhook_url:
            return "webhook_url_missing"

    if position_count(state) >= int(risk.get("max_open_positions", 1)):
        return "max_open_positions"
    if int(state.get("daily", {}).get("trades", 0)) >= int(risk.get("max_trades_per_day", 999999)):
        return "max_trades_per_day"

    daily_pnl = float(state.get("daily", {}).get("realized_pnl_usd", 0.0))
    max_daily_loss_usd = float(risk.get("max_daily_loss_usd", 0.0))
    if max_daily_loss_usd > 0 and daily_pnl <= -max_daily_loss_usd:
        return "max_daily_loss_usd"

    starting_balance = float(state.get("paper_starting_balance_usd", config.get("paper", {}).get("starting_balance_usd", 500.0)))
    max_daily_loss_pct = float(risk.get("max_daily_loss_pct", 0.0))
    if max_daily_loss_pct > 0 and starting_balance > 0 and daily_pnl <= -(starting_balance * max_daily_loss_pct / 100.0):
        return "max_daily_loss_pct"

    if int(state.get("consecutive_losses", 0)) >= int(risk.get("max_consecutive_losses", 999999)):
        return "max_consecutive_losses"

    last_loss_ts = state.get("last_loss_ts")
    cooldown = float(risk.get("cooldown_after_loss_sec", 0))
    if last_loss_ts and cooldown > 0:
        loss_dt = parse_time(str(last_loss_ts))
        if loss_dt and (utc_now() - loss_dt).total_seconds() < cooldown:
            return "cooldown_after_loss"

    mode = str(config.get("execution", {}).get("mode", "paper")).lower()
    fixed_trade_usd = float(config.get("execution", {}).get("fixed_trade_usd", 0))
    if mode == "paper" and float(state.get("paper_balance_usd", 0.0)) < fixed_trade_usd:
        return "paper_balance_too_low"

    return None


def webhook_headers(config: dict[str, Any]) -> dict[str, str]:
    execution = config.get("execution", {})
    secret = os.environ.get(str(execution.get("webhook_secret_env", "ONCHAIN_TRADE_WEBHOOK_SECRET")), "").strip()
    if not secret:
        return {}
    return {"Authorization": f"Bearer {secret}"}


def execute_order(config: dict[str, Any], action: str, payload: dict[str, Any]) -> dict[str, Any]:
    execution = config.get("execution", {})
    mode = str(execution.get("mode", "paper")).lower()
    if mode == "paper":
        return {"status": "filled", "mode": "paper", "tx_id": f"paper-{int(time.time() * 1000)}"}
    if mode != "webhook":
        raise RuntimeError(f"unsupported execution mode: {mode}")

    webhook_url = os.environ.get(str(execution.get("webhook_url_env", "ONCHAIN_TRADE_WEBHOOK_URL")), "").strip()
    if not webhook_url:
        raise RuntimeError("missing ONCHAIN_TRADE_WEBHOOK_URL")
    body = {
        "action": action,
        "client_order_id": f"{action}-{int(time.time() * 1000)}",
        "max_slippage_pct": float(execution.get("max_slippage_pct", 8.0)),
        **payload,
    }
    timeout = float(config.get("request_timeout_sec", 10))
    response = http_request_json("POST", webhook_url, body, timeout=timeout, headers=webhook_headers(config))
    if str(response.get("status", "")).lower() not in {"accepted", "filled", "ok"}:
        raise RuntimeError(f"webhook rejected order: {response}")
    return response


def open_position(config: dict[str, Any], state: dict[str, Any], candidate: Candidate) -> None:
    amount_usd = float(config.get("execution", {}).get("fixed_trade_usd", 0.0))
    if amount_usd <= 0:
        raise RuntimeError("fixed_trade_usd must be positive")
    if candidate.price_usd <= 0:
        raise RuntimeError("candidate price is missing")

    payload = {
        "network": candidate.network,
        "pool_address": candidate.pool_address,
        "token_address": candidate.token_address,
        "token_symbol": candidate.token_symbol,
        "quote_symbol": candidate.quote_symbol,
        "amount_usd": amount_usd,
        "score": candidate.score,
        "reasons": candidate.reasons,
    }
    result = execute_order(config, "buy", payload)
    fill_price = fnum(result.get("filled_price_usd"), candidate.price_usd)
    quantity = amount_usd / fill_price
    now = iso_now()

    if str(config.get("execution", {}).get("mode", "paper")).lower() == "paper":
        state["paper_balance_usd"] = float(state.get("paper_balance_usd", 0.0)) - amount_usd

    state["positions"][candidate.key] = {
        "key": candidate.key,
        "network": candidate.network,
        "pool_id": candidate.pool_id,
        "pool_address": candidate.pool_address,
        "token_address": candidate.token_address,
        "token_symbol": candidate.token_symbol,
        "token_name": candidate.token_name,
        "quote_symbol": candidate.quote_symbol,
        "entry_time": now,
        "entry_price_usd": fill_price,
        "amount_usd": amount_usd,
        "quantity": quantity,
        "remaining_quantity": quantity,
        "highest_price_usd": fill_price,
        "highest_price_time": now,
        "last_price_usd": fill_price,
        "entry_liquidity_usd": candidate.liquidity_usd,
        "targets_hit": [],
        "score": candidate.score,
        "reasons": candidate.reasons,
        "entry_order": result,
    }
    state["daily"]["trades"] = int(state["daily"].get("trades", 0)) + 1
    state.setdefault("seen_pools", {})[candidate.key] = now

    append_event(config, {"event": "buy", "candidate": payload, "result": result})
    notify_telegram(
        config,
        (
            f"[BUY] {candidate.network} {candidate.token_symbol}\n"
            f"amount={amount_usd:.2f} USD price={fill_price:.10f}\n"
            f"score={candidate.score:.1f} liq={candidate.liquidity_usd:.0f} h1vol={candidate.volume_h1_usd:.0f}\n"
            f"pool={candidate.pool_address}"
        ),
    )
    log(config, f"[buy] {candidate.network} {candidate.token_symbol} score={candidate.score:.1f} amount={amount_usd:.2f}")


def close_position(
    config: dict[str, Any],
    state: dict[str, Any],
    key: str,
    position: dict[str, Any],
    price_usd: float,
    sell_fraction: float,
    reason: str,
) -> None:
    remaining_qty = float(position.get("remaining_quantity", 0.0))
    if remaining_qty <= 0:
        return
    sell_fraction = max(0.0, min(1.0, sell_fraction))
    sell_qty = remaining_qty * sell_fraction
    if sell_qty <= 0:
        return

    proceeds = sell_qty * price_usd
    cost_basis = float(position.get("entry_price_usd", 0.0)) * sell_qty
    pnl = proceeds - cost_basis
    payload = {
        "network": position["network"],
        "pool_address": position["pool_address"],
        "token_address": position["token_address"],
        "token_symbol": position.get("token_symbol", ""),
        "sell_fraction": sell_fraction,
        "quantity": sell_qty,
        "expected_price_usd": price_usd,
        "reason": reason,
    }
    result = execute_order(config, "sell", payload)

    if str(config.get("execution", {}).get("mode", "paper")).lower() == "paper":
        state["paper_balance_usd"] = float(state.get("paper_balance_usd", 0.0)) + proceeds

    position["remaining_quantity"] = remaining_qty - sell_qty
    position["last_exit_reason"] = reason
    position["last_exit_price_usd"] = price_usd
    position.setdefault("exits", []).append(
        {
            "ts": iso_now(),
            "price_usd": price_usd,
            "sell_fraction": sell_fraction,
            "quantity": sell_qty,
            "proceeds_usd": proceeds,
            "pnl_usd": pnl,
            "reason": reason,
            "result": result,
        }
    )

    state["realized_pnl_usd"] = float(state.get("realized_pnl_usd", 0.0)) + pnl
    state["daily"]["realized_pnl_usd"] = float(state["daily"].get("realized_pnl_usd", 0.0)) + pnl
    if pnl < 0:
        state["consecutive_losses"] = int(state.get("consecutive_losses", 0)) + 1
        state["last_loss_ts"] = iso_now()
    elif sell_fraction >= 0.999 or position["remaining_quantity"] <= float(position.get("quantity", 0.0)) * 0.001:
        state["consecutive_losses"] = 0

    append_event(config, {"event": "sell", "position_key": key, "payload": payload, "pnl_usd": pnl, "result": result})
    notify_telegram(
        config,
        (
            f"[SELL] {position.get('network')} {position.get('token_symbol')}\n"
            f"reason={reason} sold={sell_fraction * 100:.1f}% price={price_usd:.10f}\n"
            f"pnl={pnl:.2f} USD"
        ),
    )
    log(config, f"[sell] {position.get('token_symbol')} reason={reason} fraction={sell_fraction:.2f} pnl={pnl:.2f}")

    if position["remaining_quantity"] <= float(position.get("quantity", 0.0)) * 0.001:
        position["closed_at"] = iso_now()
        state["closed_positions"].append(position)
        del state["positions"][key]


def should_exit(config: dict[str, Any], position: dict[str, Any], pool: Candidate) -> list[tuple[float, str, str | None]]:
    risk = config.get("risk", {})
    entry = float(position.get("entry_price_usd", 0.0))
    price = pool.price_usd
    exits: list[tuple[float, str, str | None]] = []
    if entry <= 0 or price <= 0:
        return exits

    gain_pct = (price / entry - 1.0) * 100.0
    stop_loss_pct = float(risk.get("stop_loss_pct", 0.0))
    if stop_loss_pct > 0 and gain_pct <= -stop_loss_pct:
        exits.append((1.0, "stop_loss", None))
        return exits

    liquidity_exit_ratio = float(risk.get("liquidity_exit_ratio", 0.0))
    entry_liquidity = float(position.get("entry_liquidity_usd", 0.0))
    min_liq = float(config.get("filters", {}).get("min_liquidity_usd", 0.0))
    liquidity_basis = entry_liquidity if entry_liquidity > 0 else min_liq
    if liquidity_exit_ratio > 0 and liquidity_basis > 0 and pool.liquidity_usd < liquidity_basis * liquidity_exit_ratio:
        exits.append((1.0, "liquidity_drop", None))
        return exits

    targets_hit = set(position.get("targets_hit", []))
    for step in risk.get("take_profit_steps", []):
        target = float(step.get("gain_pct", 0.0))
        sell_pct = float(step.get("sell_pct", 0.0))
        marker = f"tp_{target:g}"
        if target > 0 and sell_pct > 0 and gain_pct >= target and marker not in targets_hit:
            exits.append((sell_pct / 100.0, "take_profit", marker))
            return exits

    high = max(float(position.get("highest_price_usd", entry)), price)
    high_gain_pct = (high / entry - 1.0) * 100.0
    drawdown_pct = (1.0 - price / high) * 100.0 if high > 0 else 0.0
    if high_gain_pct >= float(risk.get("trailing_start_gain_pct", 0.0)):
        if drawdown_pct >= float(risk.get("trailing_drawdown_pct", 0.0)):
            exits.append((1.0, "trailing_stop", None))
            return exits

    entry_dt = parse_time(str(position.get("entry_time", "")))
    if entry_dt:
        age_min = (utc_now() - entry_dt).total_seconds() / 60.0
        max_hold = float(risk.get("max_hold_minutes", 0.0))
        if max_hold > 0 and age_min >= max_hold:
            exits.append((1.0, "max_hold", None))
            return exits

    high_dt = parse_time(str(position.get("highest_price_time", "")))
    no_high_min = float(risk.get("no_new_high_exit_minutes", 0.0))
    if high_dt and no_high_min > 0 and gain_pct > 0:
        idle_min = (utc_now() - high_dt).total_seconds() / 60.0
        if idle_min >= no_high_min:
            exits.append((1.0, "no_new_high", None))
    return exits


def manage_positions(config: dict[str, Any], state: dict[str, Any]) -> None:
    positions = list(state.get("positions", {}).items())
    for key, position in positions:
        try:
            pool = fetch_pool(config, str(position["network"]), str(position["pool_address"]))
        except Exception as exc:
            log(config, f"[position] {position.get('token_symbol')} price fetch failed: {exc}")
            append_event(config, {"event": "position_price_error", "position_key": key, "error": str(exc)})
            continue
        if pool is None or pool.price_usd <= 0:
            continue
        position["last_price_usd"] = pool.price_usd
        if pool.price_usd > float(position.get("highest_price_usd", 0.0)):
            position["highest_price_usd"] = pool.price_usd
            position["highest_price_time"] = iso_now()

        exits = should_exit(config, position, pool)
        for sell_fraction, reason, marker in exits:
            if marker:
                position.setdefault("targets_hit", []).append(marker)
            close_position(config, state, key, position, pool.price_usd, sell_fraction, reason)
            break


def trim_seen_pools(state: dict[str, Any], max_items: int = 5000) -> None:
    seen = state.get("seen_pools", {})
    if len(seen) <= max_items:
        return
    ordered = sorted(seen.items(), key=lambda item: str(item[1]))
    state["seen_pools"] = dict(ordered[-max_items:])


def scan_once(config: dict[str, Any], state: dict[str, Any]) -> None:
    manage_positions(config, state)

    candidates = fetch_new_pools(config)
    evaluated = [evaluate_candidate(config, c) for c in candidates]
    evaluated.sort(key=lambda c: c.score, reverse=True)

    for candidate in evaluated[:10]:
        status = "reject:" + ",".join(candidate.rejects[:3]) if candidate.rejects else f"score={candidate.score:.1f}"
        log(
            config,
            (
                f"[candidate] {candidate.network} {candidate.token_symbol}/{candidate.quote_symbol} "
                f"liq={candidate.liquidity_usd:.0f} h1vol={candidate.volume_h1_usd:.0f} "
                f"m5tx={candidate.txns_m5} ratio={candidate.buy_sell_ratio_m5:.2f} {status}"
            ),
        )

    min_score = float(config.get("scoring", {}).get("min_score_to_buy", 75.0))
    max_new = int(config.get("scoring", {}).get("max_new_trades_per_scan", 1))
    opened = 0
    for candidate in evaluated:
        if opened >= max_new:
            break
        if candidate.rejects or candidate.score < min_score:
            continue
        if candidate.key in state.get("seen_pools", {}):
            continue
        if candidate.key in state.get("positions", {}):
            continue

        block = risk_block_reason(config, state)
        if block:
            log(config, f"[risk] open blocked: {block}")
            append_event(config, {"event": "open_blocked", "reason": block})
            break

        try:
            open_position(config, state, candidate)
            opened += 1
        except Exception as exc:
            log(config, f"[error] buy failed {candidate.token_symbol}: {exc}")
            append_event(config, {"event": "buy_error", "candidate_key": candidate.key, "error": str(exc)})

    trim_seen_pools(state)


def write_default_config(path: Path) -> None:
    if path.exists():
        raise RuntimeError(f"config already exists: {path}")
    save_json(path, DEFAULT_CONFIG)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automated on-chain meme token scanner and fixed-size trader.")
    parser.add_argument("--config", default="onchain_dog_config.json", help="Path to config JSON.")
    parser.add_argument("--once", action="store_true", help="Run one scan then exit.")
    parser.add_argument("--init-config", action="store_true", help="Write a default config file then exit.")
    parser.add_argument("--mode", choices=["paper", "webhook"], help="Override execution.mode.")
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    cwd = Path.cwd()
    load_dotenv(cwd / ".env")
    config_path = Path(args.config)

    if args.init_config:
        write_default_config(config_path)
        print(f"wrote {config_path}")
        return 0

    config = load_config(config_path)
    if args.mode:
        config.setdefault("execution", {})["mode"] = args.mode

    state_path, state = load_state(config)
    log(config, f"[start] mode={config.get('execution', {}).get('mode')} config={config_path} state={state_path}")

    while True:
        try:
            scan_once(config, state)
            save_json(state_path, state)
        except KeyboardInterrupt:
            save_json(state_path, state)
            print("\nstopped")
            return 0
        except Exception as exc:
            append_event(config, {"event": "scan_error", "error": str(exc)})
            log(config, f"[error] scan failed: {exc}")
            save_json(state_path, state)

        if args.once:
            break
        time.sleep(float(config.get("scan_interval_sec", 20)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
