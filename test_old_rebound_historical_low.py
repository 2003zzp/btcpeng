import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paper_dashboard_server as server


class OldReboundHistoricalLowTest(unittest.TestCase):
    def make_bot(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bot = server.PaperBot(root / "config.json", root / "state.json")
        bot.config["old_rebound_short_enabled"] = True
        bot.config["shared_pool_24h_min_pct"] = 1
        bot.config["shared_pool_24h_max_pct"] = 100
        return bot

    @staticmethod
    def signal():
        return server.Signal(
            symbol="AKEUSDT", side="SHORT", score=8, price=1.2, quote_volume=10_000_000,
            change_24h_pct=10, change_4h_pct=0, change_1h_pct=0, short_move_pct=0,
            volume_ratio=1, open_interest_usdt=0, open_interest_24h_pct=0,
            open_interest_4h_pct=0, open_interest_1h_pct=0, funding_rate_pct=0,
            long_short_ratio=0, volume_4h_ratio=0, taker_long_short_ratio=0,
            oi_volume_ratio=0, signal_mode="old_rebound_short", signal_heat_level="normal",
            market_regime_score=0, market_regime_label="", market_regime_reason="",
            risk_level="注意", analyzer_direction="", analyzer_confidence_pct=0,
            analyzer_reason="", trigger_reason="", reject_reason="", decision_summary="",
            decision_steps=[], priority="中",
        )

    def prepare_open(self, bot):
        bot.get_exchange_symbols = lambda: {"AKEUSDT": {"symbol": "AKEUSDT", "baseAsset": "AKE"}}
        bot.get_market_exchange_symbols = lambda: {
            "AKEUSDT": {"symbol": "AKEUSDT", "baseAsset": "AKE", "underlyingType": "COIN", "contractType": "PERPETUAL"}
        }
        orders = []
        bot.exchange_open_position = lambda *_: orders.append("open") or {
            "entry_order_id": "123", "side": "SHORT", "margin_usdt": 3.0
        }
        bot.mark_signal_review_opened = lambda *_: None
        bot.publish_square_open_position_async = lambda *_: None
        return orders

    def test_default_threshold_and_frontend_setting(self):
        bot = self.make_bot()
        self.assertEqual(bot.config["old_rebound_short_min_historical_low_distance_pct"], 25.0)
        bot.update_settings({"old_rebound_short_min_historical_low_distance_pct": 30})
        self.assertEqual(bot.config["old_rebound_short_min_historical_low_distance_pct"], 30.0)
        for value in (-1, 101, "nan"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bot.update_settings({"old_rebound_short_min_historical_low_distance_pct": value})

    def test_near_historical_low_blocks_only_old_short_at_final_gate(self):
        bot = self.make_bot()
        orders = self.prepare_open(bot)
        bot.coingecko_historical_low = lambda *_: ({"price": 1.0, "id": "akedo", "date": "2026-07-10"}, "")
        bot.latest_contract_price = lambda *_: 1.2
        signal = self.signal()
        self.assertEqual(bot.open_from_signals([signal]), 0)
        self.assertEqual(orders, [])
        self.assertIn("16.7%", signal.reject_reason)
        self.assertIn("历史低价", " ".join(signal.decision_steps))
        self.assertIn("akedo", " ".join(signal.decision_steps))

    def test_far_historical_low_allows_open(self):
        bot = self.make_bot()
        orders = self.prepare_open(bot)
        bot.coingecko_historical_low = lambda *_: ({"price": 0.1, "id": "akedo", "date": "2026-07-10"}, "")
        bot.latest_contract_price = lambda *_: 1.2
        self.assertEqual(bot.open_from_signals([self.signal()]), 1)
        self.assertEqual(orders, ["open"])

    def test_disabled_threshold_does_not_request_low(self):
        bot = self.make_bot()
        orders = self.prepare_open(bot)
        bot.config["old_rebound_short_min_historical_low_distance_pct"] = 0
        bot.coingecko_historical_low = lambda *_: self.fail("disabled check must not request CoinGecko")
        self.assertEqual(bot.open_from_signals([self.signal()]), 1)
        self.assertEqual(orders, ["open"])

    def test_reversed_old_signal_opens_long_without_short_protection(self):
        bot = self.make_bot()
        orders = self.prepare_open(bot)
        bot.config["reverse_orders_enabled"] = True
        bot.coingecko_historical_low = lambda *_: self.fail("actual long must not use short protection")
        self.assertEqual(bot.open_from_signals([self.signal()]), 1)
        self.assertEqual(orders, ["open"])

    def test_missing_low_or_price_fails_open_with_audit(self):
        for missing in ("low", "price"):
            with self.subTest(missing=missing):
                bot = self.make_bot()
                orders = self.prepare_open(bot)
                bot.coingecko_historical_low = lambda *_: ({"price": 0.1, "id": "akedo"}, "") if missing == "price" else (None, "接口超时")
                bot.latest_contract_price = lambda *_: None
                signal = self.signal()
                self.assertEqual(bot.open_from_signals([signal]), 1)
                self.assertEqual(orders, ["open"])
                self.assertIn("历史低价检查跳过", " ".join(signal.decision_steps))

    def test_unexpected_low_check_error_does_not_stop_order(self):
        bot = self.make_bot()
        orders = self.prepare_open(bot)
        bot.coingecko_historical_low = lambda *_: (_ for _ in ()).throw(RuntimeError("temporary error"))
        signal = self.signal()
        self.assertEqual(bot.open_from_signals([signal]), 1)
        self.assertEqual(orders, ["open"])
        self.assertIn("历史低价检查跳过", " ".join(signal.decision_steps))

    def test_ambiguous_coingecko_symbol_skips_check(self):
        bot = self.make_bot()
        with patch.dict(server.os.environ, {"COINGECKO_API_KEY": "test-key"}):
            with patch.object(server, "http_get_json_once", return_value=[
                {"id": "akedo", "symbol": "ake", "atl": 0.1},
                {"id": "other-ake", "symbol": "ake", "atl": 0.01},
            ]) as request:
                result, reason = bot.coingecko_historical_low({"baseAsset": "AKE"})
        self.assertIsNone(result)
        self.assertIn("不唯一", reason)
        self.assertEqual(request.call_count, 1)

    def test_unique_coingecko_id_and_low_are_cached(self):
        bot = self.make_bot()
        market = [{"id": "akedo", "symbol": "ake", "atl": 0.1,
                   "atl_date": "2026-07-10T12:36:10Z"}]
        with patch.dict(server.os.environ, {"COINGECKO_API_KEY": "test-key"}):
            with patch.object(server, "http_get_json_once", return_value=market) as request:
                first, error = bot.coingecko_historical_low({"baseAsset": "AKE"})
                second, error2 = bot.coingecko_historical_low({"baseAsset": "AKE"})
        self.assertEqual((first, error), ({"price": 0.1, "id": "akedo", "date": "2026-07-10T12:36:10Z"}, ""))
        self.assertEqual((second, error2), (first, ""))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1], "/coins/markets")
        self.assertEqual(request.call_args.args[2]["symbols"], "ake")
        self.assertEqual(request.call_args.args[2]["include_tokens"], "top")

    def test_rare_queries_by_symbol_without_fixed_id(self):
        bot = self.make_bot()
        market = [{"id": "superrare", "name": "SuperRare", "symbol": "rare", "atl": 0.00826656}]
        with patch.dict(server.os.environ, {"COINGECKO_API_KEY": "test-key"}):
            with patch.object(server, "http_get_json_once", return_value=market) as request:
                result, reason = bot.coingecko_historical_low({"baseAsset": "RARE"})
        self.assertEqual((result["price"], reason), (0.00826656, ""))
        self.assertEqual(request.call_args.args[1], "/coins/markets")
        self.assertEqual(request.call_args.args[2].get("symbols"), "rare")
        self.assertEqual(request.call_args.args[2]["include_tokens"], "top")
        self.assertNotIn("ids", request.call_args.args[2])

    def test_market_result_with_different_symbol_skips_check(self):
        bot = self.make_bot()
        wrong_market = [{"id": "other-coin", "symbol": "other", "atl": 0.00020955}]
        with patch.dict(server.os.environ, {"COINGECKO_API_KEY": "test-key"}):
            with patch.object(server, "http_get_json_once", return_value=wrong_market):
                result, reason = bot.coingecko_historical_low({"baseAsset": "RARE"})
        self.assertIsNone(result)
        self.assertIn("没有对应币种", reason)


if __name__ == "__main__":
    unittest.main()
