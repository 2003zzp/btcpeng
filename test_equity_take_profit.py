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


if __name__ == "__main__":
    unittest.main()
