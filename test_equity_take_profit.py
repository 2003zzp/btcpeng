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
    def test_profitable_close_trade_enters_cooldown(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        state_path = root / "state.json"
        config_path.write_text('{"stop_loss_cooldown_enabled": true, "stop_loss_cooldown_minutes": 60}', encoding="utf-8")
        bot = server.PaperBot(config_path, state_path)
        bot.state["trade_history"] = [
            {
                "symbol": "BTCUSDT",
                "closed_at": server.iso_now(),
                "close_reason": "止盈平仓",
                "pnl_usdt": 12.0,
                "roi_pct": 30.0,
            }
        ]

        reason = bot.stop_loss_cooldown_reason("BTCUSDT")

        self.assertIn("冷静期", reason)


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
                entry_quality_score=10.0,
                entry_quality_label="",
                entry_quality_reason="",
                entry_timing_score=5.0,
                entry_timing_label="",
                entry_timing_reason="",
                entry_type="",
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


if __name__ == "__main__":
    unittest.main()
