import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class MarginTypeSettingTest(unittest.TestCase):
    def test_ensure_exchange_settings_uses_global_margin_type(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text('{"margin_type": "CROSSED"}', encoding="utf-8")
        bot = server.PaperBot(config_path, state_path)
        calls = []
        bot.signed_exchange_request = lambda method, path, payload: calls.append((method, path, payload)) or {}

        bot.ensure_exchange_settings("BTCUSDT", target_leverage=5)

        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "/fapi/v1/marginType")
        self.assertEqual(calls[0][2]["marginType"], "CROSSED")
        self.assertEqual(calls[1][1], "/fapi/v1/leverage")
        self.assertEqual(calls[1][2]["leverage"], 5)


class ReverseOrdersTest(unittest.TestCase):
    def make_bot(self) -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text("{}", encoding="utf-8")
        return server.PaperBot(config_path, state_path)

    def make_signal(self) -> server.Signal:
        return server.Signal(
            symbol="BTCUSDT",
            side="LONG",
            score=8.0,
            price=100.0,
            quote_volume=50_000_000.0,
            change_24h_pct=10.0,
            change_4h_pct=2.0,
            change_1h_pct=1.0,
            short_move_pct=0.8,
            volume_ratio=2.0,
            open_interest_usdt=1_000_000.0,
            open_interest_24h_pct=5.0,
            open_interest_4h_pct=2.0,
            open_interest_1h_pct=1.0,
            funding_rate_pct=0.01,
            long_short_ratio=1.1,
            volume_4h_ratio=1.5,
            taker_long_short_ratio=1.1,
            oi_volume_ratio=0.2,
            signal_mode="strong_momentum",
            signal_heat_level="normal",
            market_regime_score=8.0,
            market_regime_label="正常",
            market_regime_reason="",
            risk_level="正常",
            analyzer_direction="市场确认",
            analyzer_confidence_pct=80.0,
            analyzer_reason="",
            trigger_reason="测试信号",
            reject_reason="",
            decision_summary="待开仓",
            decision_steps=[],
            priority="高",
        )

    def test_reverse_orders_setting_can_be_updated(self):
        bot = self.make_bot()

        bot.update_settings({"reverse_orders_enabled": "true"})

        self.assertTrue(bot.config["reverse_orders_enabled"])

    def test_reverse_orders_flip_entry_and_protection_order_sides(self):
        bot = self.make_bot()
        bot.config["reverse_orders_enabled"] = True
        bot.get_available_balance = lambda: 100.0
        bot.ensure_exchange_settings = lambda symbol, target_leverage: target_leverage
        bot.exposure_pct = lambda extra_notional=0.0: 0.0
        calls = []

        def submit(method, path, params, signal_side):
            calls.append((method, path, params, signal_side))
            if path == "/fapi/v1/order":
                return {"orderId": "entry-1"}
            return {"algoId": f"algo-{len(calls)}"}

        bot.signed_exchange_request_position_aware = submit
        signal = self.make_signal()
        symbol_info = {
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            ]
        }

        record = bot.exchange_open_position_with_sizing(
            signal,
            symbol_info,
            {"margin_usdt": 10.0, "target_leverage": 2, "tier": "fixed", "margin_multiplier": 1.0, "reason": "test"},
        )

        self.assertEqual(calls[0][2]["side"], "SELL")
        self.assertEqual(calls[0][3], "SHORT")
        self.assertEqual(calls[1][2]["side"], "BUY")
        self.assertEqual(calls[2][2]["side"], "BUY")
        self.assertEqual(record["side"], "SHORT")
        self.assertEqual(record["original_side"], "LONG")
        self.assertTrue(record["reverse_order"])
        self.assertGreater(float(record["stop_price"]), signal.price)
        self.assertLess(float(record["take_profit"]), signal.price)


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
    def make_bot(self) -> server.PaperBot:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text('{"market_base_url": "https://fapi.binance.com"}', encoding="utf-8")
        return server.PaperBot(config_path, state_path)

    def test_fetch_tickers_only_returns_coin_contracts_and_keeps_existing_position_quotes(self):
        bot = self.make_bot()
        bot.state["positions"] = [{"symbol": "AAPLUSDT"}]
        bot.config["pre_start_long_enabled"] = True
        bot.config["old_rebound_short_enabled"] = True
        bot.config["high_pullback_short_enabled"] = True
        ticker_payload = [
            {"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "10", "quoteVolume": "50000000"},
            {"symbol": "AAPLUSDT", "lastPrice": "200", "priceChangePercent": "10", "quoteVolume": "50000000"},
            {"symbol": "0700USDT", "lastPrice": "300", "priceChangePercent": "10", "quoteVolume": "50000000"},
            {"symbol": "UNKNOWNUSDT", "lastPrice": "400", "priceChangePercent": "10", "quoteVolume": "50000000"},
        ]
        exchange_info = {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "COIN", "contractType": "PERPETUAL"},
            {"symbol": "AAPLUSDT", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "EQUITY", "contractType": "TRADIFI_PERPETUAL"},
            {"symbol": "0700USDT", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "HK_EQUITY", "contractType": "TRADIFI_PERPETUAL"},
            {"symbol": "STOCKLIKEUSDT", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "COIN", "underlyingSubType": ["TradFi"], "contractType": "TRADIFI_PERPETUAL"},
            {"symbol": "MISSINGTYPEUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
        ]}

        with patch.object(server, "http_get_json", side_effect=[ticker_payload, exchange_info]):
            tickers = bot.fetch_tickers()

        self.assertEqual({item["symbol"] for item in tickers}, {"BTCUSDT", "AAPLUSDT"})
        self.assertEqual(bot.crypto_symbols, {"BTCUSDT"})
        self.assertEqual([row["item"]["symbol"] for row in bot.build_candidate_pool(tickers)], ["BTCUSDT"])

    def test_fetch_tickers_fails_closed_when_contract_classification_is_unavailable(self):
        bot = self.make_bot()
        bot.state["positions"] = [{"symbol": "AAPLUSDT"}]

        with patch.object(server, "http_get_json", side_effect=[
            [{"symbol": "BTCUSDT", "lastPrice": "100"}, {"symbol": "AAPLUSDT", "lastPrice": "200"}],
            RuntimeError("exchangeInfo unavailable"),
        ]):
            tickers = bot.fetch_tickers()

        self.assertEqual([item["symbol"] for item in tickers], ["AAPLUSDT"])
        self.assertFalse(tickers[0]["_crypto_candidate_allowed"])
        self.assertIn("exchangeInfo unavailable", bot.crypto_classification_error)

    def test_fetch_tickers_scan_failure_does_not_build_or_open_signals(self):
        bot = self.make_bot()
        bot.state["positions"] = [{"symbol": "AAPLUSDT", "last_price": 100.0}]
        bot.update_signal_future_returns = lambda price_map: None
        bot.sync_exchange_state = lambda: None
        bot.build_signals = lambda tickers, open_symbols: self.fail("signals must not be built without contract classification")
        bot.open_from_signals = lambda signals: self.fail("orders must not be opened without contract classification")

        with patch.object(server, "http_get_json", side_effect=[
            [{"symbol": "AAPLUSDT", "lastPrice": "200"}],
            RuntimeError("metadata timeout"),
        ]):
            bot.scan_once()

        self.assertEqual(bot.state["market_source"], "binance_public")
        self.assertIn("metadata timeout", bot.state["last_error"])
        self.assertEqual(bot.state["positions"][0]["last_price"], 200.0)

    def test_open_from_signals_rejects_non_crypto_contract_as_final_guard(self):
        bot = self.make_bot()
        bot.get_exchange_symbols = lambda: {
            "AAPLUSDT": {
                "symbol": "AAPLUSDT",
                "status": "TRADING",
                "quoteAsset": "USDT",
                "underlyingType": "EQUITY",
                "contractType": "TRADIFI_PERPETUAL",
            }
        }
        bot.get_market_exchange_symbols = bot.get_exchange_symbols
        bot.exchange_open_position = lambda signal, symbol_info: self.fail("stock contract must not reach exchange order")
        signal = ReverseOrdersTest().make_signal()
        signal.symbol = "AAPLUSDT"

        self.assertEqual(bot.open_from_signals([signal]), 0)
        self.assertIn("非加密货币合约", signal.reject_reason)

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

    def kline(self, close: float, quote_volume: float = 1000.0, close_time: int = 0) -> list[object]:
        return [0, "0", "0", "0", str(close), "0", close_time, str(quote_volume)]

    def test_old_rebound_requests_enough_closed_1h_bars_and_keeps_separate_trends(self):
        bot = self.make_bot()
        bot.config["kline_1h_limit"] = 8
        calls = []
        future = int(server.time.time() * 1000) + 60_000

        def fetch(symbol, interval, limit):
            calls.append((interval, limit))
            if interval == "1h":
                return [self.kline(120 - i) for i in range(30)] + [self.kline(99, close_time=future)]
            return [self.kline(100 + i * .2) for i in range(40)] + [self.kline(109, close_time=future)]

        bot.fetch_klines_interval = fetch
        metrics = bot.kline_short_metrics("OLDUSDT", 100, 12, old_rebound=True)
        self.assertGreaterEqual(dict(calls)["1h"], 31)
        self.assertEqual(metrics["closed_1h_count"], 30)
        self.assertEqual(metrics["trend_1h"], "down")
        self.assertEqual(metrics["trend_15m"], "up")

    def test_old_rebound_flat_candle_needs_closed_15m_break(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "trend_15m": "neutral", "trend_1h": "neutral", "closed_15m_previous_low": 9.8,
                   "price": 10.0}
        reason = bot.old_rebound_short_kline_reject_reason("OLDUSDT", 0.0, -0.2, 1.1, "neutral", metrics)
        self.assertIn("15m", reason)
        self.assertIn("转弱", reason)

    def test_old_rebound_no_1h_klines_does_not_use_24h_fallback(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 0, "price": 10,
                   "closed_15m_previous_low": 11}
        reason = bot.old_rebound_short_kline_reject_reason("OLDUSDT", -1, -1, 1.1, "down", metrics)
        self.assertIn("1h", reason)
        self.assertIn("不足", reason)

    def test_old_rebound_rejects_waterfall_but_allows_fresh_break(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30, "trend_15m": "down",
                   "trend_1h": "neutral", "closed_15m_previous_low": 10.1, "price": 10.0}
        def reason(move15, move1h, price=10, high=12, low=8):
            return bot.old_rebound_short_kline_reject_reason(
                "OLDUSDT", move15, move1h, 1.1, "down", {**metrics, "price": price},
                high_24h=high, low_24h=low)
        self.assertEqual(reason(-0.8, -1), "")
        self.assertIn("追空", reason(-5, -1))
        self.assertIn("追空", reason(-0.8, -9))
        self.assertIn("24h高点", reason(-0.8, -1, high=20))
        self.assertIn("24h低点", reason(-0.8, -1, low=9.9))

    def test_old_rebound_uses_real_closed_lows_for_break_check(self):
        bot = self.make_bot()
        future = int(server.time.time() * 1000) + 60_000
        def candle(close, low, close_time=0):
            return [0, "0", "0", str(low), str(close), "0", close_time, "1000"]
        def fetch(symbol, interval, limit):
            if interval == "15m":
                return [candle(10.5, 10.2) for _ in range(36)] + [
                    candle(10.4, 10.2), candle(10.3, 10.15), candle(10.2, 10.1),
                    candle(10.0, 9.9), candle(9.95, 9.8, future)]
            return [candle(10.5, 10.2) for _ in range(30)] + [candle(10.5, 10.2, future)]
        bot.fetch_klines_interval = fetch
        metrics = bot.kline_short_metrics("OLDUSDT", 10, 10, old_rebound=True)
        self.assertEqual(metrics["closed_15m_previous_low"], 10.1)
        self.assertEqual(metrics["closed_15m_count"], 40)
        bot.has_symbol_history = lambda symbol, days: True
        self.assertEqual(bot.old_rebound_short_kline_reject_reason("OLDUSDT", metrics["short_move"], 0, 1, metrics["trend"], metrics), "")

    def test_old_rebound_checks_live_ticker_location_not_only_closed_price(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "closed_15m_previous_low": 10.1, "price": 10.0, "trend_1h": "neutral"}
        reason = bot.old_rebound_short_kline_reject_reason(
            "OLDUSDT", -0.8, -1.0, 1.1, "down", metrics,
            high_24h=12.0, low_24h=8.0, current_price=8.2)
        self.assertIn("24h低点", reason)

    def test_old_rebound_requires_valid_24h_high_and_low(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "closed_15m_previous_low": 10.1, "price": 10.0, "trend_1h": "neutral"}
        reason = bot.old_rebound_short_kline_reject_reason(
            "OLDUSDT", -0.8, -1.0, 1.1, "down", metrics, high_24h=12.0, low_24h=0.0)
        self.assertIn("24h", reason)
        self.assertIn("缺失", reason)
        no_extremes = bot.old_rebound_short_kline_reject_reason(
            "OLDUSDT", -0.8, -1.0, 1.1, "down", metrics, current_price=10.0)
        self.assertIn("24h", no_extremes)

    def test_old_rebound_rejects_ticker_reclaim_after_closed_break(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "closed_15m_previous_low": 10.1, "price": 10.0, "trend_1h": "neutral"}
        reason = bot.old_rebound_short_kline_reject_reason(
            "OLDUSDT", -0.8, -1.0, 1.1, "down", metrics,
            high_24h=12.0, low_24h=8.0, current_price=10.2)
        self.assertIn("站回破位线", reason)

    def test_old_rebound_requires_closed_down_candle_even_if_low_comparison_passes(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "closed_15m_previous_low": 10.1, "price": 10.0, "trend_1h": "neutral"}
        reason = bot.old_rebound_short_kline_reject_reason(
            "OLDUSDT", 0.1, -1.0, 1.1, "down", metrics)
        self.assertIn("15m", reason)
        self.assertIn("转弱", reason)

    def test_old_rebound_first_closed_candle_can_break_but_does_not_wait_for_retest(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "closed_15m_previous_low": 10.1, "price": 10.0, "trend_1h": "neutral"}
        reason = bot.old_rebound_short_kline_reject_reason(
            "OLDUSDT", -0.8, -1.0, 1.1, "down", metrics,
            high_24h=12.0, low_24h=8.0, current_price=10.0)
        self.assertEqual(reason, "")

    def test_old_rebound_rejects_live_waterfall_after_mild_closed_break(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "closed_15m_previous_low": 10.1, "price": 10.0, "trend_1h": "neutral"}
        reason = bot.old_rebound_short_kline_reject_reason(
            "OLDUSDT", -0.8, -1.0, 1.1, "down", metrics,
            high_24h=12.0, low_24h=8.0, current_price=9.5)
        self.assertIn("实时", reason)
        self.assertIn("追空", reason)

    def test_old_rebound_upward_1h_trend_and_live_reclaim_block_entry(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True
        metrics = {"closed_15m_count": 40, "closed_1h_count": 30,
                   "trend_15m": "down", "trend_1h": "up", "closed_15m_previous_low": 10.1,
                   "price": 10.0}
        reason = bot.old_rebound_short_kline_reject_reason("OLDUSDT", -0.8, -1, 1.1, "neutral", metrics)
        self.assertIn("1h", reason)
        self.assertIn("上行", reason)
        metrics["trend_1h"] = "neutral"
        metrics["live_short_move"] = 2.0
        reason = bot.old_rebound_short_kline_reject_reason("OLDUSDT", -0.8, -1, 1.1, "neutral", metrics)
        self.assertIn("反抽", reason)

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

    def test_short_metrics_ignore_unclosed_1h_kline_for_structure(self):
        bot = self.make_bot()
        future_close_time = int(server.time.time() * 1000) + 30 * 60 * 1000

        def fetch_interval(symbol, interval, limit):
            if interval == "15m":
                return [self.kline(100.0, 1000.0) for _ in range(39)] + [self.kline(101.0, 1500.0)]
            if interval == "1h":
                return [
                    self.kline(100.0, 1000.0),
                    self.kline(101.0, 1000.0),
                    self.kline(102.0, 1000.0),
                    self.kline(103.0, 1000.0),
                    self.kline(104.0, 1000.0),
                    self.kline(98.0, 5000.0, close_time=future_close_time),
                ]
            raise AssertionError(interval)

        bot.fetch_klines_interval = fetch_interval

        metrics = bot.kline_short_metrics("LIVE1HUSDT", 100.0, 8.0)

        self.assertAlmostEqual(metrics["change_1h"], server.pct(104.0, 103.0), places=2)
        self.assertAlmostEqual(metrics["change_4h"], server.pct(104.0, 100.0), places=2)
        self.assertEqual(metrics["price"], 101.0)

    def test_short_metrics_ignore_unclosed_15m_kline_for_short_move(self):
        bot = self.make_bot()
        future_close_time = int(server.time.time() * 1000) + 8 * 60 * 1000

        def fetch_interval(symbol, interval, limit):
            if interval == "15m":
                return [
                    self.kline(100.0, 1000.0),
                    self.kline(102.0, 1200.0),
                    self.kline(96.0, 8000.0, close_time=future_close_time),
                ]
            if interval == "1h":
                return [self.kline(100.0, 1000.0) for _ in range(5)]
            raise AssertionError(interval)

        bot.fetch_klines_interval = fetch_interval

        metrics = bot.kline_short_metrics("LIVE15MUSDT", 100.0, 8.0)

        self.assertAlmostEqual(metrics["short_move"], server.pct(102.0, 100.0), places=2)
        self.assertEqual(metrics["price"], 102.0)

    def test_short_metrics_expose_unclosed_klines_as_live_auxiliary_only(self):
        bot = self.make_bot()
        future_close_time = int(server.time.time() * 1000) + 8 * 60 * 1000

        def fetch_interval(symbol, interval, limit):
            if interval == "15m":
                return [
                    self.kline(100.0, 1000.0),
                    self.kline(102.0, 1200.0),
                    self.kline(98.0, 8000.0, close_time=future_close_time),
                ]
            if interval == "1h":
                return [
                    self.kline(100.0, 1000.0),
                    self.kline(101.0, 1000.0),
                    self.kline(102.0, 1000.0),
                    self.kline(104.0, 1000.0),
                    self.kline(99.0, 5000.0, close_time=future_close_time),
                ]
            raise AssertionError(interval)

        bot.fetch_klines_interval = fetch_interval

        metrics = bot.kline_short_metrics("LIVEAUXUSDT", 100.0, 8.0)

        self.assertAlmostEqual(metrics["short_move"], server.pct(102.0, 100.0), places=2)
        self.assertAlmostEqual(metrics["change_1h"], server.pct(104.0, 102.0), places=2)
        self.assertAlmostEqual(metrics["live_short_move"], server.pct(98.0, 102.0), places=2)
        self.assertAlmostEqual(metrics["live_change_1h"], server.pct(99.0, 104.0), places=2)

    def test_old_rebound_short_live_drop_cannot_override_unclosed_main_confirmation(self):
        bot = self.make_bot()
        bot.has_symbol_history = lambda symbol, days: True

        reject = bot.old_rebound_short_kline_reject_reason(
            "LIVEONLYUSDT",
            short_move=1.0,
            change_1h=1.2,
            volume_ratio=2.0,
            trend="up",
            live_metrics={"live_short_move": -2.0, "live_change_1h": -1.5,
                          "closed_15m_count": 40, "closed_1h_count": 30, "price": 10,
                          "closed_15m_previous_low": 9.5},
        )

        self.assertIn("未转弱", reject)
        self.assertIn("实时转弱观察中", reject)

    def test_old_rebound_short_live_auxiliary_adjusts_confirmation_score(self):
        bot = self.make_bot()
        stats = {
            "taker_long_short_ratio": 0.88,
            "open_interest_1h_pct": 2.0,
            "funding_rate_pct": 0.03,
            "long_short_ratio": 1.8,
        }

        continuing = bot.old_rebound_short_confirmation_score(
            change_24h=18.0,
            change_1h=-0.8,
            short_move=-0.6,
            volume_ratio=2.0,
            trend="down",
            stats=stats,
            live_metrics={"live_short_move": -0.7, "live_change_1h": -0.4},
        )
        rebounding = bot.old_rebound_short_confirmation_score(
            change_24h=18.0,
            change_1h=-0.8,
            short_move=-0.6,
            volume_ratio=2.0,
            trend="down",
            stats=stats,
            live_metrics={"live_short_move": 0.8, "live_change_1h": 0.7},
        )

        self.assertGreater(continuing["score"], rebounding["score"])
        self.assertIn("实时继续下压", continuing["reason"])
        self.assertIn("实时反抽", rebounding["reason"])

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

    def test_strong_long_candidates_respect_configured_24h_gain_floor(self):
        bot = self.make_bot()
        bot.config["trade_direction_mode"] = "long_only"
        bot.config["strong_long_24h_min_change_pct"] = 20.0

        candidates = bot.build_candidate_pool(
            [
                {"symbol": "LOWUSDT", "lastPrice": "10", "priceChangePercent": "18", "quoteVolume": "50000000"},
                {"symbol": "HIGHUSDT", "lastPrice": "10", "priceChangePercent": "25", "quoteVolume": "50000000"},
            ]
        )

        symbols = {candidate["item"]["symbol"] for candidate in candidates}
        self.assertNotIn("LOWUSDT", symbols)
        self.assertIn("HIGHUSDT", symbols)

    def test_strong_long_gain_floor_does_not_filter_pre_start_pool(self):
        bot = self.make_bot()
        bot.config["trade_direction_mode"] = "long_only"
        bot.config["pre_start_long_enabled"] = True
        bot.config["strong_long_24h_min_change_pct"] = 20.0

        candidates = bot.build_candidate_pool(
            [{"symbol": "CALMUSDT", "lastPrice": "10", "priceChangePercent": "5", "quoteVolume": "50000000"}]
        )

        pre_start = next(candidate for candidate in candidates if candidate["item"]["symbol"] == "CALMUSDT")
        self.assertEqual(pre_start["signal_mode"], "pre_start_long")

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

    def test_high_pullback_short_candidates_are_added_when_enabled(self):
        bot = self.make_bot()
        bot.config["high_pullback_short_enabled"] = True
        bot.config["high_pullback_short_max_candidates"] = 5
        bot.config["trade_direction_mode"] = "short_only"

        candidates = bot.build_candidate_pool(
            [
                {
                    "symbol": "PULLUSDT",
                    "lastPrice": "1.10",
                    "highPrice": "1.50",
                    "lowPrice": "0.90",
                    "priceChangePercent": "18",
                    "quoteVolume": "50000000",
                },
                {
                    "symbol": "FLATPUUSDT",
                    "lastPrice": "1.42",
                    "highPrice": "1.50",
                    "lowPrice": "0.90",
                    "priceChangePercent": "18",
                    "quoteVolume": "50000000",
                },
            ]
        )

        high_pullback = next(candidate for candidate in candidates if candidate["item"]["symbol"] == "PULLUSDT")
        self.assertEqual(high_pullback["signal_mode"], "high_pullback_short")
        self.assertNotIn("FLATPUUSDT", {candidate["item"]["symbol"] for candidate in candidates})

    def test_high_pullback_short_skips_candidates_too_close_to_24h_low(self):
        bot = self.make_bot()
        bot.config["high_pullback_short_enabled"] = True
        bot.config["high_pullback_short_min_low_distance_pct"] = 5.0
        bot.config["trade_direction_mode"] = "short_only"

        candidates = bot.build_candidate_pool(
            [
                {
                    "symbol": "LOWTAILUSDT",
                    "lastPrice": "0.93",
                    "highPrice": "1.50",
                    "lowPrice": "0.90",
                    "priceChangePercent": "18",
                    "quoteVolume": "50000000",
                }
            ]
        )

        self.assertNotIn("LOWTAILUSDT", {candidate["item"]["symbol"] for candidate in candidates})

    def test_strong_short_candidates_can_be_disabled(self):
        bot = self.make_bot()
        bot.config["strong_short_enabled"] = False
        bot.config["trade_direction_mode"] = "short_only"

        candidates = bot.build_candidate_pool(
            [
                {
                    "symbol": "DROPUSDT",
                    "lastPrice": "1",
                    "priceChangePercent": "-28",
                    "quoteVolume": "50000000",
                }
            ]
        )

        self.assertEqual(candidates, [])

    def test_high_pullback_short_has_priority_over_old_rebound_short_for_same_symbol(self):
        bot = self.make_bot()
        bot.config["high_pullback_short_enabled"] = True
        bot.config["old_rebound_short_enabled"] = True

        candidates = bot.build_candidate_pool(
            [
                {
                    "symbol": "OVERLAPUSDT",
                    "lastPrice": "1.10",
                    "highPrice": "1.50",
                    "lowPrice": "0.90",
                    "priceChangePercent": "18",
                    "quoteVolume": "25000000",
                }
            ]
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["signal_mode"], "high_pullback_short")

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

        def enrich(symbol, price, change, old_rebound=False):
            calls.append(symbol)
            return {
                "short_move": -0.8,
                "change_1h": -1.2,
                "change_4h": 3.0,
                "volume_ratio": 2.0,
                "trend": "down",
                "price": price,
                "enriched": True,
                "closed_15m_count": 40,
                "closed_1h_count": 30,
                "closed_15m_previous_low": price * 1.01,
                "trend_15m": "down",
                "trend_1h": "neutral",
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
                    "lowPrice": "8",
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

    def test_position_plan_uses_high_pullback_short_margin_and_leverage(self):
        bot = self.make_bot(
            '{"margin_per_trade_usdt": 9, "leverage": 5, "high_pullback_short_margin_per_trade_usdt": 2.5, "high_pullback_short_leverage": 2}'
        )
        signal = self.make_signal()
        signal.signal_mode = "high_pullback_short"

        plan = bot.position_plan(signal, available=20.0)

        self.assertEqual(plan["margin_usdt"], 2.5)
        self.assertEqual(plan["target_leverage"], 2)
        self.assertEqual(plan["tier"], "high_pullback_short_fixed")

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

    def test_high_pullback_short_margin_and_leverage_can_be_updated(self):
        bot = self.make_bot("{}")

        bot.update_settings({"high_pullback_short_margin_per_trade_usdt": 2.5, "high_pullback_short_leverage": 2})

        self.assertEqual(bot.config["high_pullback_short_margin_per_trade_usdt"], 2.5)
        self.assertEqual(bot.config["high_pullback_short_leverage"], 2)

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
