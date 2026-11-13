import tempfile
import unittest
from pathlib import Path

import paper_dashboard_server as server


class OldReboundWhitelistTest(unittest.TestCase):
    def make_bot(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bot = server.PaperBot(root / "config.json", root / "state.json")
        bot.config.update({
            "old_rebound_short_enabled": True,
            "old_rebound_whitelist_enabled": True,
            "pre_start_long_enabled": False,
            "strong_long_enabled": False,
            "strong_short_enabled": False,
            "shared_pool_24h_min_pct": 1,
            "shared_pool_24h_max_pct": 40,
            "shared_pool_min_quote_volume_usdt": 5_000_000,
        })
        return bot

    @staticmethod
    def ticker(change, symbol="OLDUSDT", volume=10_000_000):
        return {"symbol": symbol, "lastPrice": "10", "priceChangePercent": str(change),
                "quoteVolume": str(volume), "_crypto_candidate_allowed": True}

    def test_breaking_above_fifty_adds_once_and_bypasses_only_old_pool_range(self):
        bot = self.make_bot()
        self.assertEqual(bot.config["old_rebound_whitelist_min_24h_change_pct"], 50.0)
        self.assertEqual(bot.build_candidate_pool([self.ticker(50)]), [])
        rows = bot.build_candidate_pool([self.ticker(51)])
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], ["OLDUSDT"])
        self.assertEqual([row["signal_modes"] for row in rows], [["old_rebound_short"]])
        self.assertEqual(bot.build_candidate_pool([self.ticker(-20)])[0]["signal_modes"], ["old_rebound_short"])
        bot.build_candidate_pool([self.ticker(75)])
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], ["OLDUSDT"])
        reloaded = server.PaperBot(bot.config_path, bot.state_path)
        self.assertEqual(reloaded.config["old_rebound_whitelist_symbols"], ["OLDUSDT"])

    def test_custom_entry_threshold_controls_addition_and_suppression_reset(self):
        bot = self.make_bot()
        bot.config["shared_pool_24h_max_pct"] = 20
        bot.update_settings({"old_rebound_whitelist_min_24h_change_pct": 30})
        self.assertEqual(bot.build_candidate_pool([self.ticker(30)]), [])
        self.assertEqual(bot.build_candidate_pool([self.ticker(31)])[0]["signal_modes"],
                         ["old_rebound_short"])
        bot.update_old_rebound_whitelist("remove", "OLDUSDT")
        self.assertEqual(bot.build_candidate_pool([self.ticker(31)]), [])
        bot.build_candidate_pool([self.ticker(29)])
        self.assertEqual(bot.build_candidate_pool([self.ticker(31)])[0]["signal_modes"],
                         ["old_rebound_short"])
        self.assertEqual(bot.config["old_rebound_whitelist_min_24h_change_pct"], 30.0)

    def test_entry_threshold_rejects_out_of_range_values(self):
        bot = self.make_bot()
        for value in (-1, 1001, "nan"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bot.update_settings({"old_rebound_whitelist_min_24h_change_pct": value})

    def test_shared_candidate_only_runs_prestart_when_its_range_passes(self):
        bot = self.make_bot()
        bot.config.update({"pre_start_long_enabled": True, "pre_start_volume_auto_trade_enabled": True})
        self.assertEqual(bot.build_candidate_pool([self.ticker(60)])[0]["signal_modes"], ["old_rebound_short"])
        self.assertEqual(bot.build_candidate_pool([self.ticker(20)])[0]["signal_modes"],
                         ["old_rebound_short", "pre_start_long"])

    def test_manual_delete_and_successful_open_wait_for_new_crossing(self):
        bot = self.make_bot()
        bot.build_candidate_pool([self.ticker(60)])
        bot.update_old_rebound_whitelist("remove", "OLDUSDT")
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], [])
        reloaded = server.PaperBot(bot.config_path, bot.state_path)
        reloaded.config.update(bot.config)
        self.assertEqual(reloaded.build_candidate_pool([self.ticker(60)]), [])
        self.assertEqual(bot.build_candidate_pool([self.ticker(60)]), [])
        bot.build_candidate_pool([self.ticker(49)])
        self.assertEqual(bot.build_candidate_pool([self.ticker(60)])[0]["signal_modes"], ["old_rebound_short"])

        signal = server.Signal(
            symbol="OLDUSDT", side="SHORT", score=8, price=10, quote_volume=10_000_000,
            change_24h_pct=60, change_4h_pct=0, change_1h_pct=0, short_move_pct=0,
            volume_ratio=1, open_interest_usdt=0, open_interest_24h_pct=0,
            open_interest_4h_pct=0, open_interest_1h_pct=0, funding_rate_pct=0,
            long_short_ratio=0, volume_4h_ratio=0, taker_long_short_ratio=0,
            oi_volume_ratio=0, signal_mode="old_rebound_short", signal_heat_level="normal",
            market_regime_score=0, market_regime_label="", market_regime_reason="",
            risk_level="注意", analyzer_direction="", analyzer_confidence_pct=0,
            analyzer_reason="", trigger_reason="", reject_reason="", decision_summary="",
            decision_steps=[], priority="中",
        )
        bot.get_exchange_symbols = lambda: {"OLDUSDT": {"symbol": "OLDUSDT"}}
        bot.get_market_exchange_symbols = lambda: {"OLDUSDT": {"underlyingType": "COIN", "contractType": "PERPETUAL"}}
        bot.exchange_open_position = lambda *_: {"entry_order_id": "123", "side": "SHORT", "margin_usdt": 3.0}
        bot.mark_signal_review_opened = lambda *_: None
        bot.publish_square_open_position_async = lambda *_: None
        bot.update_old_rebound_whitelist("remove", "OLDUSDT")
        self.assertEqual(bot.open_from_signals([signal]), 0)
        self.assertIn("涨幅", signal.reject_reason)
        bot.update_old_rebound_whitelist("add", "OLDUSDT")
        signal.reject_reason = ""
        bot.exchange_open_position = lambda *_: None
        self.assertEqual(bot.open_from_signals([signal]), 0)
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], ["OLDUSDT"])
        bot.exchange_open_position = lambda *_: {"entry_order_id": "123", "side": "SHORT", "margin_usdt": 3.0}
        self.assertEqual(bot.open_from_signals([signal]), 1)
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], [])
        self.assertEqual(bot.build_candidate_pool([self.ticker(60)]), [])

    def test_manual_add_overrides_suppression_but_toggle_preserves_list(self):
        bot = self.make_bot()
        bot.update_old_rebound_whitelist("remove", "old")
        bot.update_old_rebound_whitelist("add", "old")
        bot.update_old_rebound_whitelist("add", "OLDUSDT")
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], ["OLDUSDT"])
        self.assertEqual(len(bot.build_candidate_pool([self.ticker(-20)])), 1)
        bot.update_settings({"old_rebound_whitelist_enabled": "false"})
        self.assertEqual(bot.build_candidate_pool([self.ticker(-20)]), [])
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], ["OLDUSDT"])

    def test_high_gainer_must_pass_other_candidate_filters(self):
        bot = self.make_bot()
        bot.config["blocked_symbols"] = ["OLDUSDT"]
        self.assertEqual(bot.build_candidate_pool([self.ticker(60)]), [])
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], [])
        bot.config["blocked_symbols"] = []
        self.assertEqual(bot.build_candidate_pool([self.ticker(60, volume=100)]), [])
        self.assertEqual(bot.config["old_rebound_whitelist_symbols"], [])

    def test_whitelisted_old_rebound_does_not_lose_24h_confirmation_point(self):
        bot = self.make_bot()
        bot.update_old_rebound_whitelist("add", "OLDUSDT")
        stats = {"taker_long_short_ratio": 1, "open_interest_1h_pct": -10,
                 "funding_rate_pct": 1, "long_short_ratio": 0}
        result = bot.old_rebound_short_confirmation_score(60, 2, 2, 0.1, "up", stats,
                                                          symbol="OLDUSDT")
        self.assertEqual(result["score"], 1.0)
        self.assertIn("白名单", result["reason"])


if __name__ == "__main__":
    unittest.main()
