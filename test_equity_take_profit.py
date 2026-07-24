import tempfile
import unittest
from pathlib import Path

import paper_dashboard_server as server


class EquityTakeProfitRetryTest(unittest.TestCase):
    def make_bot(self) -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text(
            '{"equity_take_profit_enabled": true, "previous_day_equity_usdt": 100, "equity_take_profit_usdt": 10}',
            encoding="utf-8",
        )
        bot = server.PaperBot(config_path, state_path)
        bot.config["equity_take_profit_enabled"] = True
        bot.config["previous_day_equity_usdt"] = 100.0
        bot.config["equity_take_profit_usdt"] = 10.0
        bot.state["exchange_account_mode"] = bot.exchange_mode()
        bot.state["account_equity_usdt"] = 120.0
        bot.state["positions"] = [{"symbol": "BTCUSDT", "side": "LONG", "quantity": 0.01}]
        return bot

    def test_retries_full_close_after_equity_take_profit_was_already_triggered(self):
        bot = self.make_bot()
        calls = []
        bot.exchange_close_position = lambda pos: calls.append(dict(pos))
        bot.sync_exchange_state = lambda: None

        self.assertTrue(bot.enforce_equity_take_profit())
        self.assertEqual(len(calls), 1)

        bot.state["positions"] = [{"symbol": "BTCUSDT", "side": "LONG", "quantity": 0.01}]
        self.assertTrue(bot.enforce_equity_take_profit())
        self.assertEqual(len(calls), 2)


class HedgeModeCloseOrderTest(unittest.TestCase):
    def test_hedge_mode_close_order_omits_reduce_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text("{}", encoding="utf-8")
        bot = server.PaperBot(config_path, state_path)
        bot.position_mode_cache = {"mode": "hedge", "synced_at_ms": 9999999999999}
        calls = []
        bot.signed_exchange_request = lambda method, path, payload: calls.append((method, path, payload)) or {}

        bot.exchange_close_position({"symbol": "BTCUSDT", "side": "LONG", "quantity": 0.01})

        self.assertEqual(calls[0][1], "/fapi/v1/order")
        self.assertEqual(calls[0][2]["positionSide"], "LONG")
        self.assertNotIn("reduceOnly", calls[0][2])


class ClosedTradeCooldownTest(unittest.TestCase):
    def make_bot(self, take_profit_cooldown_enabled=True) -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text(
            (
                '{"stop_loss_cooldown_enabled": true, '
                '"stop_loss_cooldown_minutes": 60, '
                f'"take_profit_cooldown_enabled": {str(take_profit_cooldown_enabled).lower()}'
                '}'
            ),
            encoding="utf-8",
        )
        return server.PaperBot(config_path, state_path)

    def test_profitable_close_trade_enters_cooldown_when_enabled(self):
        bot = self.make_bot(take_profit_cooldown_enabled=True)
        bot.state["trade_history"] = [
            {
                "symbol": "BTCUSDT",
                "closed_at": server.iso_now(),
                "close_reason": "take profit close",
                "pnl_usdt": 12.0,
                "roi_pct": 30.0,
            }
        ]

        reason = bot.stop_loss_cooldown_reason("BTCUSDT")

        self.assertTrue(reason)

    def test_profitable_close_trade_skips_cooldown_when_disabled(self):
        bot = self.make_bot(take_profit_cooldown_enabled=False)
        bot.state["trade_history"] = [
            {
                "symbol": "BTCUSDT",
                "closed_at": server.iso_now(),
                "close_reason": "take profit close",
                "pnl_usdt": 12.0,
                "roi_pct": 30.0,
            }
        ]

        reason = bot.stop_loss_cooldown_reason("BTCUSDT")

        self.assertEqual(reason, "")

    def test_losing_close_trade_still_enters_cooldown_when_take_profit_disabled(self):
        bot = self.make_bot(take_profit_cooldown_enabled=False)
        bot.state["trade_history"] = [
            {
                "symbol": "BTCUSDT",
                "closed_at": server.iso_now(),
                "close_reason": "stop loss close",
                "pnl_usdt": -5.0,
                "roi_pct": -12.0,
            }
        ]

        reason = bot.stop_loss_cooldown_reason("BTCUSDT")

        self.assertTrue(reason)

    def test_cooldown_minutes_can_be_updated_from_settings(self):
        bot = self.make_bot()

        bot.update_settings({"stop_loss_cooldown_minutes": 15})

        self.assertEqual(bot.config["stop_loss_cooldown_minutes"], 15)


class EquityTakeProfitScanTest(unittest.TestCase):
    def test_scan_still_refreshes_candidates_when_equity_take_profit_is_triggered(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text('{"equity_take_profit_enabled": true}', encoding="utf-8")
        bot = server.PaperBot(config_path, state_path)
        bot.state["running"] = True
        bot.state["equity_take_profit_triggered"] = True
        bot.fetch_tickers = lambda: [{"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "10", "quoteVolume": "50000000"}]
        bot.update_signal_future_returns = lambda price_map: None
        bot.sync_exchange_state = lambda: None
        bot.build_signals = lambda tickers, open_symbols: [
            server.Signal(
                symbol="BTCUSDT",
                side="LONG",
                score=6.0,
                price=100.0,
                quote_volume=50000000.0,
                change_24h_pct=10.0,
                change_4h_pct=0.0,
                change_1h_pct=0.0,
                short_move_pct=0.0,
                volume_ratio=1.0,
                open_interest_usdt=0.0,
                open_interest_24h_pct=0.0,
                open_interest_4h_pct=0.0,
                open_interest_1h_pct=0.0,
                funding_rate_pct=0.0,
                long_short_ratio=1.0,
                volume_4h_ratio=1.0,
                taker_long_short_ratio=1.0,
                oi_volume_ratio=0.0,
                signal_mode="strong_momentum",
                signal_heat_level="normal",
                market_regime_score=10.0,
                market_regime_label="",
                market_regime_reason="",
                risk_level="",
                analyzer_direction="市场确认",
                analyzer_confidence_pct=80.0,
                analyzer_reason="",
                trigger_reason="test",
                reject_reason="",
                decision_summary="待开仓",
                decision_steps=[],
                priority="高",
            )
        ]

        bot.scan_once()

        self.assertEqual(len(bot.state.get("candidates", [])), 1)
        self.assertEqual(bot.state["candidates"][0]["symbol"], "BTCUSDT")
        self.assertIn("止盈", bot.state["candidates"][0]["reject_reason"])


class LongStructureConfirmationTest(unittest.TestCase):
    def make_bot(self) -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text('{"long_structure_min_score": 2.5}', encoding="utf-8")
        return server.PaperBot(config_path, state_path)

    def test_broken_long_structure_fails_threshold(self):
        bot = self.make_bot()

        result = bot.long_structure_confirmation(
            change_4h=-2.0,
            change_1h=-1.5,
            short_move=-1.8,
            trend="down",
            volume_ratio=2.0,
            stats={"taker_long_short_ratio": 0.88, "open_interest_1h_pct": -15.0},
        )

        self.assertFalse(result["passed"])
        self.assertLess(result["score"], 2.5)

    def test_healthy_long_structure_passes_threshold(self):
        bot = self.make_bot()

        result = bot.long_structure_confirmation(
            change_4h=4.0,
            change_1h=2.0,
            short_move=1.5,
            trend="up",
            volume_ratio=2.0,
            stats={"taker_long_short_ratio": 1.12, "open_interest_1h_pct": 5.0},
        )

        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["score"], 2.5)

    def test_short_structure_uses_configured_threshold(self):
        bot = self.make_bot()
        bot.config["short_structure_min_score"] = 5.0

        result = bot.short_structure_confirmation(
            change_4h=-2.0,
            change_1h=-2.0,
            short_move=-1.8,
            trend="down",
            volume_ratio=2.0,
            stats={
                "taker_long_short_ratio": 1.0,
                "open_interest_1h_pct": -2.0,
                "long_short_ratio": 1.0,
            },
        )

        self.assertLess(result["score"], 5.0)
        self.assertFalse(result["passed"])

    def test_short_structure_threshold_can_be_updated_from_settings(self):
        bot = self.make_bot()

        bot.update_settings({"short_structure_min_score": 4.8})

        self.assertEqual(bot.config["short_structure_min_score"], 4.8)

    def test_short_structure_confirmation_toggle_can_be_updated_from_settings(self):
        bot = self.make_bot()

        bot.update_settings({"short_structure_confirmation_enabled": "false"})

        self.assertFalse(bot.config["short_structure_confirmation_enabled"])

    def test_pre_start_settings_can_be_updated(self):
        bot = self.make_bot()

        bot.update_settings(
            {
                "pre_start_long_enabled": "true",
                "pre_start_max_candidates": 6,
                "pre_start_24h_min_pct": 2.0,
                "pre_start_market_confirm_min_score": 6.8,
            }
        )

        self.assertTrue(bot.config["pre_start_long_enabled"])
        self.assertEqual(bot.config["pre_start_max_candidates"], 6)
        self.assertEqual(bot.config["pre_start_24h_min_pct"], 2.0)
        self.assertEqual(bot.config["pre_start_market_confirm_min_score"], 6.8)

    def test_old_rebound_short_settings_can_be_updated(self):
        bot = self.make_bot()

        bot.update_settings(
            {
                "old_rebound_short_enabled": "true",
                "old_rebound_short_max_candidates": 5,
                "old_rebound_short_24h_min_pct": 8.0,
                "old_rebound_short_confirm_min_score": 6.9,
            }
        )

        self.assertTrue(bot.config["old_rebound_short_enabled"])
        self.assertEqual(bot.config["old_rebound_short_max_candidates"], 5)
        self.assertEqual(bot.config["old_rebound_short_24h_min_pct"], 8.0)
        self.assertEqual(bot.config["old_rebound_short_confirm_min_score"], 6.9)


class DirectionalVolumeConfirmationTest(unittest.TestCase):
    def make_bot(self) -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text("{}", encoding="utf-8")
        return server.PaperBot(config_path, state_path)

    def base_stats(self, taker_ratio: float, volume_1h_ratio: float = 1.2) -> dict[str, float]:
        return {
            "funding_rate_pct": 0.0,
            "open_interest_1h_pct": 8.0,
            "open_interest_24h_pct": 20.0,
            "long_short_ratio": 1.2,
            "taker_long_short_ratio": taker_ratio,
            "volume_1h_ratio": volume_1h_ratio,
        }

    def test_market_confirmation_rejects_long_volume_when_taker_is_selling(self):
        bot = self.make_bot()

        result = bot.market_confirmation_score(
            "LONG",
            "strong_momentum",
            change_24h=8.0,
            change_4h=2.0,
            change_1h=2.0,
            short_move=1.0,
            volume_ratio=2.0,
            trend="up",
            stats=self.base_stats(taker_ratio=0.88),
        )

        self.assertLess(result["score"], 6.0)

    def test_market_confirmation_rewards_long_volume_when_taker_and_1h_volume_align(self):
        bot = self.make_bot()

        result = bot.market_confirmation_score(
            "LONG",
            "strong_momentum",
            change_24h=8.0,
            change_4h=2.0,
            change_1h=2.0,
            short_move=1.0,
            volume_ratio=2.0,
            trend="up",
            stats=self.base_stats(taker_ratio=1.12, volume_1h_ratio=1.2),
        )

        self.assertGreaterEqual(result["score"], 7.0)

    def test_short_structure_only_rewards_volume_when_taker_is_selling(self):
        bot = self.make_bot()

        selling = bot.short_structure_confirmation(
            change_4h=-2.0,
            change_1h=-2.0,
            short_move=-1.8,
            trend="down",
            volume_ratio=2.0,
            stats={
                "taker_long_short_ratio": 0.88,
                "open_interest_1h_pct": 5.0,
                "long_short_ratio": 1.6,
            },
        )
        buying = bot.short_structure_confirmation(
            change_4h=-2.0,
            change_1h=-2.0,
            short_move=-1.8,
            trend="down",
            volume_ratio=2.0,
            stats={
                "taker_long_short_ratio": 1.12,
                "open_interest_1h_pct": 5.0,
                "long_short_ratio": 1.6,
            },
        )

        self.assertGreater(selling["score"] - buying["score"], 2.0)


class KlineShortMetricsTest(unittest.TestCase):
    def make_bot(self) -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text('{"max_kline_enriched_candidates": 2}', encoding="utf-8")
        return server.PaperBot(config_path, state_path)

    def kline(self, close: float, quote_volume: float = 1000.0) -> list[object]:
        return [0, "0", "0", "0", str(close), "0", 0, str(quote_volume)]

    def test_stale_scan_in_progress_is_released_on_next_scan(self):
        bot = self.make_bot()
        bot.config["max_scan_runtime_sec"] = 60
        bot.state["scan_in_progress"] = True
        bot.state["last_scan_at"] = (server.utc_now() - server.timedelta(minutes=5)).isoformat(timespec="seconds")
        bot.fetch_tickers = lambda: [{"symbol": "FREEUSDT", "lastPrice": "10", "priceChangePercent": "-12", "quoteVolume": "50000000"}]
        bot.update_signal_future_returns = lambda price_map: None
        bot.sync_exchange_state = lambda: None
        bot.enforce_equity_take_profit = lambda: False
        bot.build_signals = lambda tickers, open_symbols: []
        bot.update_pending_open_signals = lambda signals: None
        bot.record_signal_review_scan = lambda signals: None
        bot.open_from_signals = lambda signals: 0
        bot.update_risk_state = lambda: None

        bot.scan_once()

        self.assertFalse(bot.state["scan_in_progress"])
        self.assertEqual(bot.state["scan_stage"], "idle")
        self.assertIsNotNone(bot.state["last_scan_finished_at"])

    def test_short_metrics_use_real_15m_and_1h_klines(self):
        bot = self.make_bot()
        calls = []
        one_minute_calls = []
        bot.fetch_klines = lambda symbol: one_minute_calls.append(symbol) or []

        def fetch_interval(symbol, interval, limit):
            calls.append((symbol, interval, limit))
            if interval == "15m":
                return [self.kline(100.0, 1000.0) for _ in range(39)] + [self.kline(94.0, 4500.0)]
            if interval == "1h":
                return [self.kline(110.0 - idx * 0.5, 1000.0) for idx in range(30)] + [
                    self.kline(95.0, 1000.0),
                    self.kline(91.0, 1000.0),
                ]
            raise AssertionError(interval)

        bot.fetch_klines_interval = fetch_interval

        metrics = bot.kline_short_metrics("4USDT", 100.0, -12.0)

        self.assertIn(("4USDT", "15m", 40), calls)
        self.assertIn(("4USDT", "1h", 10), calls)
        self.assertEqual(one_minute_calls, [])
        self.assertTrue(metrics["enriched"])
        self.assertAlmostEqual(metrics["short_move"], -6.0, places=2)
        self.assertAlmostEqual(metrics["change_1h"], server.pct(91.0, 95.0), places=2)
        self.assertEqual(metrics["trend"], "down")

    def test_short_metrics_kline_limits_can_be_configured(self):
        bot = self.make_bot()
        bot.config["kline_15m_limit"] = 32
        bot.config["kline_1h_limit"] = 12
        calls = []

        def fetch_interval(symbol, interval, limit):
            calls.append((symbol, interval, limit))
            return [self.kline(100.0, 1000.0) for _ in range(limit)]

        bot.fetch_klines_interval = fetch_interval

        bot.kline_short_metrics("4USDT", 100.0, -12.0)

        self.assertIn(("4USDT", "15m", 32), calls)
        self.assertIn(("4USDT", "1h", 12), calls)

    def test_short_metrics_calculate_1h_volume_ratio_from_real_klines(self):
        bot = self.make_bot()
        bot.config["kline_1h_limit"] = 12

        def fetch_interval(symbol, interval, limit):
            if interval == "15m":
                return [self.kline(100.0, 1000.0) for _ in range(limit)]
            if interval == "1h":
                return [self.kline(100.0 + idx, 1000.0) for idx in range(11)] + [self.kline(112.0, 2400.0)]
            raise AssertionError(interval)

        bot.fetch_klines_interval = fetch_interval

        metrics = bot.kline_short_metrics("VOLUSDT", 100.0, 8.0)

        self.assertAlmostEqual(metrics["volume_1h_ratio"], 2.4, places=2)

    def test_strong_candidates_are_not_enriched_in_candidate_pool(self):
        bot = self.make_bot()
        bot.kline_short_metrics = lambda symbol, price, change: (_ for _ in ()).throw(AssertionError("candidate pool should not fetch Klines"))

        candidates = bot.build_candidate_pool(
            [
                {"symbol": "4USDT", "lastPrice": "91", "priceChangePercent": "-12", "quoteVolume": "50000000"},
                {"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "8", "quoteVolume": "40000000"},
            ]
        )

        first = next(candidate for candidate in candidates if candidate["item"]["symbol"] == "4USDT")
        self.assertEqual(first["signal_mode"], "strong_momentum")
        self.assertNotIn("metrics", first)

    def test_pre_start_long_candidates_are_added_when_enabled(self):
        bot = self.make_bot()
        bot.config["pre_start_long_enabled"] = True
        bot.config["pre_start_max_candidates"] = 8

        candidates = bot.build_candidate_pool(
            [
                {"symbol": "CALMUSDT", "lastPrice": "10", "priceChangePercent": "5", "quoteVolume": "50000000"},
                {"symbol": "HOTUSDT", "lastPrice": "10", "priceChangePercent": "16", "quoteVolume": "60000000"},
            ]
        )

        pre_start = next(candidate for candidate in candidates if candidate["item"]["symbol"] == "CALMUSDT")
        self.assertEqual(pre_start["signal_mode"], "pre_start_long")
        self.assertEqual(pre_start["mode_reason"], "预启动做多池")

    def test_pre_start_long_candidates_are_disabled_by_default(self):
        bot = self.make_bot()

        candidates = bot.build_candidate_pool(
            [{"symbol": "CALMUSDT", "lastPrice": "10", "priceChangePercent": "5", "quoteVolume": "50000000"}]
        )

        self.assertEqual(candidates, [])

    def test_old_rebound_short_candidates_are_added_when_enabled(self):
        bot = self.make_bot()
        bot.config["old_rebound_short_enabled"] = True
        bot.config["old_rebound_short_max_candidates"] = 4

        candidates = bot.build_candidate_pool(
            [
                {"symbol": "OLDUSDT", "lastPrice": "10", "priceChangePercent": "18", "quoteVolume": "25000000"},
                {"symbol": "NEWUSDT", "lastPrice": "10", "priceChangePercent": "55", "quoteVolume": "50000000"},
            ]
        )

        old_short = next(candidate for candidate in candidates if candidate["item"]["symbol"] == "OLDUSDT")
        self.assertEqual(old_short["signal_mode"], "old_rebound_short")
        self.assertEqual(old_short["mode_reason"], "老币反弹衰竭做空池")

    def test_old_rebound_short_candidates_are_disabled_by_default(self):
        bot = self.make_bot()

        candidates = bot.build_candidate_pool(
            [{"symbol": "OLDUSDT", "lastPrice": "10", "priceChangePercent": "18", "quoteVolume": "25000000"}]
        )

        self.assertTrue(candidates)
        self.assertNotIn("old_rebound_short", {candidate["signal_mode"] for candidate in candidates})

    def test_blocked_symbols_are_filtered_before_candidate_pool(self):
        bot = self.make_bot()
        bot.config["pre_start_long_enabled"] = True
        bot.config["old_rebound_short_enabled"] = True
        bot.config["blocked_symbols"] = ["BTCUSDT", "CALMUSDT", "OLDUSDT"]

        candidates = bot.build_candidate_pool(
            [
                {"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "12", "quoteVolume": "90000000"},
                {"symbol": "CALMUSDT", "lastPrice": "10", "priceChangePercent": "5", "quoteVolume": "50000000"},
                {"symbol": "OLDUSDT", "lastPrice": "10", "priceChangePercent": "18", "quoteVolume": "25000000"},
                {"symbol": "FREEUSDT", "lastPrice": "10", "priceChangePercent": "-12", "quoteVolume": "50000000"},
            ]
        )

        symbols = {candidate["item"]["symbol"] for candidate in candidates}
        self.assertNotIn("BTCUSDT", symbols)
        self.assertNotIn("CALMUSDT", symbols)
        self.assertNotIn("OLDUSDT", symbols)
        self.assertIn("FREEUSDT", symbols)

    def test_cooldown_symbols_are_filtered_before_candidate_pool(self):
        bot = self.make_bot()
        bot.config["pre_start_long_enabled"] = True
        bot.config["old_rebound_short_enabled"] = True
        bot.config["stop_loss_cooldown_minutes"] = 60
        bot.state["trade_history"] = [
            {"symbol": "BTCUSDT", "closed_at": server.iso_now(), "close_reason": "止盈平仓", "pnl_usdt": 5.0},
            {"symbol": "CALMUSDT", "closed_at": server.iso_now(), "close_reason": "止盈平仓", "pnl_usdt": 4.0},
            {"symbol": "OLDUSDT", "closed_at": server.iso_now(), "close_reason": "止损平仓", "pnl_usdt": -2.0},
        ]

        candidates = bot.build_candidate_pool(
            [
                {"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "12", "quoteVolume": "90000000"},
                {"symbol": "CALMUSDT", "lastPrice": "10", "priceChangePercent": "5", "quoteVolume": "50000000"},
                {"symbol": "OLDUSDT", "lastPrice": "10", "priceChangePercent": "18", "quoteVolume": "25000000"},
                {"symbol": "FREEUSDT", "lastPrice": "10", "priceChangePercent": "-12", "quoteVolume": "50000000"},
            ]
        )

        symbols = {candidate["item"]["symbol"] for candidate in candidates}
        self.assertNotIn("BTCUSDT", symbols)
        self.assertNotIn("CALMUSDT", symbols)
        self.assertNotIn("OLDUSDT", symbols)
        self.assertIn("FREEUSDT", symbols)

    def test_build_signals_enriches_only_after_coarse_score_passes(self):
        bot = self.make_bot()
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        calls = []

        def enrich(symbol, price, change):
            calls.append(symbol)
            return {
                "short_move": -5.0,
                "change_1h": -4.0,
                "change_4h": -8.0,
                "volume_ratio": 2.0,
                "trend": "down",
                "price": price,
                "enriched": True,
            }

        bot.kline_short_metrics = enrich
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": -5.0,
            "open_interest_4h_pct": -4.0,
            "open_interest_1h_pct": 3.0,
            "funding_rate_pct": 0.0,
            "long_short_ratio": 1.6,
            "volume_4h_ratio": 2.0,
            "taker_long_short_ratio": 0.8,
            "oi_volume_ratio": 0.1,
        }

        signals = bot.build_signals(
            [
                {"symbol": "PASSUSDT", "lastPrice": "91", "priceChangePercent": "-12", "quoteVolume": "50000000"},
                {"symbol": "FAILUSDT", "lastPrice": "100", "priceChangePercent": "6", "quoteVolume": "50000000"},
            ],
            set(),
        )

        self.assertEqual(calls, ["PASSUSDT"])
        passed = next(signal for signal in signals if signal.symbol == "PASSUSDT")
        failed = next(signal for signal in signals if signal.symbol == "FAILUSDT")
        self.assertEqual(passed.change_1h_pct, -4.0)
        self.assertEqual(passed.side, "SHORT")
        self.assertEqual(failed.change_1h_pct, 0.5)

    def test_pre_start_long_enriches_before_score_reject(self):
        bot = self.make_bot()
        bot.config["pre_start_long_enabled"] = True
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        calls = []

        def enrich(symbol, price, change):
            calls.append(symbol)
            return {
                "short_move": 2.0,
                "change_1h": 3.0,
                "change_4h": 5.0,
                "volume_ratio": 2.0,
                "trend": "up",
                "price": price,
                "enriched": True,
            }

        bot.kline_short_metrics = enrich
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": 10.0,
            "open_interest_4h_pct": 5.0,
            "open_interest_1h_pct": 3.0,
            "funding_rate_pct": 0.01,
            "long_short_ratio": 1.2,
            "volume_4h_ratio": 2.0,
            "taker_long_short_ratio": 1.1,
            "oi_volume_ratio": 0.1,
        }

        signals = bot.build_signals(
            [{"symbol": "CALMUSDT", "lastPrice": "10", "priceChangePercent": "5", "quoteVolume": "50000000"}],
            set(),
        )

        signal = next(signal for signal in signals if signal.symbol == "CALMUSDT")
        self.assertEqual(calls, ["CALMUSDT"])
        self.assertEqual(signal.signal_mode, "pre_start_long")
        self.assertEqual(signal.side, "LONG")
        self.assertEqual(signal.reject_reason, "")
        self.assertEqual(signal.analyzer_direction, "预启动确认")

    def test_pre_start_long_does_not_reject_on_strong_pool_score_after_enrichment(self):
        bot = self.make_bot()
        bot.config["pre_start_long_enabled"] = True
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        bot.kline_short_metrics = lambda symbol, price, change: {
            "short_move": 0.5,
            "change_1h": 1.0,
            "change_4h": 1.5,
            "volume_ratio": 1.6,
            "trend": "up",
            "price": price,
            "enriched": True,
        }
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": 5.0,
            "open_interest_4h_pct": 2.0,
            "open_interest_1h_pct": 1.0,
            "funding_rate_pct": 0.01,
            "long_short_ratio": 1.2,
            "volume_4h_ratio": 1.6,
            "taker_long_short_ratio": 1.1,
            "oi_volume_ratio": 0.1,
        }

        signals = bot.build_signals(
            [
                {
                    "symbol": "EARLYSLOWUSDT",
                    "lastPrice": "10",
                    "highPrice": "10.4",
                    "priceChangePercent": "5",
                    "quoteVolume": "50000000",
                }
            ],
            set(),
        )

        signal = next(signal for signal in signals if signal.symbol == "EARLYSLOWUSDT")
        self.assertEqual(signal.signal_mode, "pre_start_long")
        self.assertLess(signal.score, float(bot.config["signal_score_threshold"]))
        self.assertEqual(signal.reject_reason, "")
        self.assertGreaterEqual(signal.analyzer_confidence_pct, 65.0)

    def test_pre_start_long_rejects_overheated_15m_move(self):
        bot = self.make_bot()
        bot.config["pre_start_long_enabled"] = True
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        bot.kline_short_metrics = lambda symbol, price, change: {
            "short_move": 6.0,
            "change_1h": 7.0,
            "change_4h": 8.0,
            "volume_ratio": 2.0,
            "trend": "up",
            "price": price,
            "enriched": True,
        }
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": 10.0,
            "open_interest_4h_pct": 5.0,
            "open_interest_1h_pct": 3.0,
            "funding_rate_pct": 0.01,
            "long_short_ratio": 1.2,
            "volume_4h_ratio": 2.0,
            "taker_long_short_ratio": 1.1,
            "oi_volume_ratio": 0.1,
        }

        signals = bot.build_signals(
            [{"symbol": "FASTUSDT", "lastPrice": "10", "priceChangePercent": "5", "quoteVolume": "50000000"}],
            set(),
        )

        signal = next(signal for signal in signals if signal.symbol == "FASTUSDT")
        self.assertIn("预启动15m涨幅", signal.reject_reason)

    def test_pre_start_long_uses_independent_confirmation_score(self):
        bot = self.make_bot()
        bot.config["pre_start_long_enabled"] = True
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        bot.kline_short_metrics = lambda symbol, price, change: {
            "short_move": 1.5,
            "change_1h": 2.5,
            "change_4h": 3.0,
            "volume_ratio": 2.2,
            "trend": "up",
            "price": price,
            "enriched": True,
        }
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": 8.0,
            "open_interest_4h_pct": 4.0,
            "open_interest_1h_pct": 2.0,
            "funding_rate_pct": 0.01,
            "long_short_ratio": 1.2,
            "volume_4h_ratio": 2.0,
            "taker_long_short_ratio": 1.1,
            "oi_volume_ratio": 0.1,
        }
        bot.market_confirmation_score = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generic market confirmation should be skipped"))

        signals = bot.build_signals(
            [
                {
                    "symbol": "EARLYUSDT",
                    "lastPrice": "10",
                    "highPrice": "10.5",
                    "priceChangePercent": "5",
                    "quoteVolume": "50000000",
                }
            ],
            set(),
        )

        signal = next(signal for signal in signals if signal.symbol == "EARLYUSDT")
        self.assertEqual(signal.reject_reason, "")
        self.assertEqual(signal.analyzer_direction, "预启动确认")
        self.assertGreaterEqual(signal.analyzer_confidence_pct, 65.0)

    def test_short_signal_rejects_late_waterfall_after_large_24h_high_drawdown(self):
        bot = self.make_bot()
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        bot.config["short_max_24h_high_drawdown_pct"] = 75.0
        bot.kline_short_metrics = lambda symbol, price, change: {
            "short_move": -5.0,
            "change_1h": -4.0,
            "change_4h": -8.0,
            "volume_ratio": 2.0,
            "trend": "down",
            "price": price,
            "enriched": True,
        }
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": -5.0,
            "open_interest_4h_pct": -4.0,
            "open_interest_1h_pct": 3.0,
            "funding_rate_pct": 0.0,
            "long_short_ratio": 1.6,
            "volume_4h_ratio": 2.0,
            "taker_long_short_ratio": 0.8,
            "oi_volume_ratio": 0.1,
        }

        signals = bot.build_signals(
            [
                {
                    "symbol": "TACUSDT",
                    "lastPrice": "20",
                    "highPrice": "100",
                    "priceChangePercent": "-80",
                    "quoteVolume": "50000000",
                }
            ],
            set(),
        )

        signal = next(signal for signal in signals if signal.symbol == "TACUSDT")
        self.assertEqual(signal.side, "SHORT")
        self.assertIn("24h", signal.reject_reason)
        self.assertIn("80.0%", signal.reject_reason)

    def test_build_signals_skips_short_structure_when_disabled(self):
        bot = self.make_bot()
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        bot.config["short_structure_confirmation_enabled"] = False
        bot.kline_short_metrics = lambda symbol, price, change: {
            "short_move": -5.0,
            "change_1h": -4.0,
            "change_4h": -8.0,
            "volume_ratio": 2.0,
            "trend": "down",
            "price": price,
            "enriched": True,
        }
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": -5.0,
            "open_interest_4h_pct": -4.0,
            "open_interest_1h_pct": 3.0,
            "funding_rate_pct": 0.0,
            "long_short_ratio": 1.6,
            "volume_4h_ratio": 2.0,
            "taker_long_short_ratio": 0.8,
            "oi_volume_ratio": 0.1,
        }
        bot.short_structure_confirmation = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("short structure should be skipped"))

        signals = bot.build_signals(
            [{"symbol": "DROPUSDT", "lastPrice": "91", "highPrice": "130", "priceChangePercent": "-12", "quoteVolume": "50000000"}],
            set(),
        )

        signal = next(signal for signal in signals if signal.symbol == "DROPUSDT")
        self.assertNotIn("结构破坏", signal.reject_reason)


    def test_old_rebound_short_forces_kline_and_uses_independent_confirmation(self):
        bot = self.make_bot()
        bot.config["old_rebound_short_enabled"] = True
        bot.config["confirmation_mode"] = "market_score"
        bot.config["market_regime_filter_enabled"] = False
        bot.config["short_structure_confirmation_enabled"] = False
        calls = []

        def enrich(symbol, price, change):
            calls.append(symbol)
            return {
                "short_move": -0.8,
                "change_1h": -1.2,
                "change_4h": 3.0,
                "volume_ratio": 2.0,
                "trend": "down",
                "price": price,
                "enriched": True,
            }

        bot.kline_short_metrics = enrich
        bot.fetch_signal_market_stats = lambda symbol, item: {
            "open_interest_usdt": 1000000.0,
            "open_interest_24h_pct": 15.0,
            "open_interest_4h_pct": 6.0,
            "open_interest_1h_pct": 2.0,
            "funding_rate_pct": 0.03,
            "long_short_ratio": 1.8,
            "volume_4h_ratio": 2.0,
            "taker_long_short_ratio": 0.88,
            "oi_volume_ratio": 0.1,
        }
        bot.has_symbol_history = lambda symbol, days: True
        bot.market_confirmation_score = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generic market confirmation should be skipped"))

        signals = bot.build_signals(
            [
                {
                    "symbol": "OLDUSDT",
                    "lastPrice": "10",
                    "highPrice": "12",
                    "priceChangePercent": "18",
                    "quoteVolume": "25000000",
                }
            ],
            set(),
        )

        signal = next(signal for signal in signals if signal.symbol == "OLDUSDT")
        self.assertEqual(calls, ["OLDUSDT"])
        self.assertEqual(signal.signal_mode, "old_rebound_short")
        self.assertEqual(signal.side, "SHORT")
        self.assertEqual(signal.reject_reason, "")
        self.assertEqual(signal.analyzer_direction, "老币反弹确认")


class RemovedStrategyControlsTest(unittest.TestCase):
    def make_bot(self, config: str = "{}") -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text(config, encoding="utf-8")
        return server.PaperBot(config_path, state_path)

    def make_signal(self) -> server.Signal:
        return server.Signal(
            symbol="4USDT",
            side="SHORT",
            score=6.0,
            price=100.0,
            quote_volume=50000000.0,
            change_24h_pct=-12.0,
            change_4h_pct=-5.0,
            change_1h_pct=-2.0,
            short_move_pct=-1.5,
            volume_ratio=2.0,
            open_interest_usdt=0.0,
            open_interest_24h_pct=0.0,
            open_interest_4h_pct=0.0,
            open_interest_1h_pct=0.0,
            funding_rate_pct=0.0,
            long_short_ratio=1.0,
            volume_4h_ratio=1.0,
            taker_long_short_ratio=0.8,
            oi_volume_ratio=0.0,
            signal_mode="strong_momentum",
            signal_heat_level="normal",
            market_regime_score=10.0,
            market_regime_label="",
            market_regime_reason="",
            risk_level="",
            analyzer_direction="市场确认",
            analyzer_confidence_pct=80.0,
            analyzer_reason="",
            trigger_reason="test",
            reject_reason="",
            decision_summary="待开仓",
            decision_steps=[],
            priority="高",
        )

    def test_early_breakout_pool_no_longer_adds_candidates(self):
        bot = self.make_bot('{"early_breakout_enabled": true, "max_candidates_per_scan": 18}')
        bot.kline_short_metrics = lambda symbol, price, change: {
            "short_move": 2.0,
            "change_1h": 2.0,
            "change_4h": 4.0,
            "volume_ratio": 2.0,
            "trend": "up",
            "price": price,
            "enriched": True,
        }

        candidates = bot.build_candidate_pool(
            [{"symbol": "TESTUSDT", "lastPrice": "10", "priceChangePercent": "4", "quoteVolume": "50000000"}]
        )

        self.assertEqual(candidates, [])

    def test_position_plan_uses_fixed_margin_without_entry_timing_sizing(self):
        bot = self.make_bot(
            '{"margin_per_trade_usdt": 9, "leverage": 5, "entry_timing_enabled": true, "entry_timing_small_multiplier": 0.1}'
        )

        plan = bot.position_plan(self.make_signal(), available=20.0)

        self.assertEqual(plan["margin_usdt"], 9.0)
        self.assertEqual(plan["target_leverage"], 5)
        self.assertEqual(plan["tier"], "fixed")

    def test_position_plan_uses_pre_start_margin_and_leverage(self):
        bot = self.make_bot(
            '{"margin_per_trade_usdt": 9, "leverage": 5, "pre_start_margin_per_trade_usdt": 3, "pre_start_leverage": 2}'
        )
        signal = self.make_signal()
        signal.signal_mode = "pre_start_long"

        plan = bot.position_plan(signal, available=20.0)

        self.assertEqual(plan["margin_usdt"], 3.0)
        self.assertEqual(plan["target_leverage"], 2)
        self.assertEqual(plan["tier"], "pre_start_fixed")

    def test_position_plan_uses_old_rebound_short_margin_and_leverage(self):
        bot = self.make_bot(
            '{"margin_per_trade_usdt": 9, "leverage": 5, "old_rebound_short_margin_per_trade_usdt": 4, "old_rebound_short_leverage": 2}'
        )
        signal = self.make_signal()
        signal.signal_mode = "old_rebound_short"

        plan = bot.position_plan(signal, available=20.0)

        self.assertEqual(plan["margin_usdt"], 4.0)
        self.assertEqual(plan["target_leverage"], 2)
        self.assertEqual(plan["tier"], "old_rebound_short_fixed")

    def test_pre_start_margin_and_leverage_can_be_updated(self):
        bot = self.make_bot("{}")

        bot.update_settings({"pre_start_margin_per_trade_usdt": 4.5, "pre_start_leverage": 3})

        self.assertEqual(bot.config["pre_start_margin_per_trade_usdt"], 4.5)
        self.assertEqual(bot.config["pre_start_leverage"], 3)

    def test_old_rebound_short_margin_and_leverage_can_be_updated(self):
        bot = self.make_bot("{}")

        bot.update_settings({"old_rebound_short_margin_per_trade_usdt": 4.5, "old_rebound_short_leverage": 3})

        self.assertEqual(bot.config["old_rebound_short_margin_per_trade_usdt"], 4.5)
        self.assertEqual(bot.config["old_rebound_short_leverage"], 3)

    def test_removed_strategy_controls_are_ignored_by_settings_update(self):
        bot = self.make_bot(
            '{"btc_strategy_enabled": false, "early_breakout_enabled": false, "entry_timing_enabled": false}'
        )

        bot.update_settings(
            {
                "btc_strategy_enabled": "true",
                "early_breakout_enabled": "true",
                "entry_timing_enabled": "true",
                "entry_timing_small_multiplier": 0.1,
            }
        )

        self.assertFalse(bot.config.get("btc_strategy_enabled"))
        self.assertFalse(bot.config.get("early_breakout_enabled"))
        self.assertFalse(bot.config.get("entry_timing_enabled"))
        self.assertNotEqual(bot.config.get("entry_timing_small_multiplier"), 0.1)


if __name__ == "__main__":
    unittest.main()
