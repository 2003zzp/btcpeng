import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import paper_dashboard_server as server
import tradfi_strategy


class TradfiStrategyTest(unittest.TestCase):
    def make_bot(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        return server.PaperBot(root / "config.json", root / "state.json")

    @staticmethod
    def depth_book(bid_qty="1", ask_qty="1", ask_price="100.1"):
        return {"bids": [["100", bid_qty]], "asks": [[ask_price, ask_qty]]}

    def test_only_equity_tradfi_contract_is_eligible(self):
        stock = {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]}
        metal = {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "COMMODITY", "underlyingSubType": ["TradFi"]}
        crypto = {"contractType": "PERPETUAL", "underlyingType": "COIN"}
        self.assertTrue(tradfi_strategy.is_equity_contract(stock))
        self.assertFalse(tradfi_strategy.is_equity_contract(metal))
        self.assertFalse(tradfi_strategy.is_equity_contract(crypto))

    def test_priority_and_fallback_use_one_stock_candidate(self):
        bot = self.make_bot()
        bot.config.update({"tradfi_enabled": True, "tradfi_symbols": ["TSLAUSDT"],
                           "tradfi_priority": "SHORT", "tradfi_max_candidates": 3})
        tickers = [{"symbol": "TSLAUSDT", "lastPrice": "100", "quoteVolume": "10000000", "priceChangePercent": "3"}]
        info = {"TSLAUSDT": {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]}}
        bot.get_market_exchange_symbols = lambda: info
        pool = bot.build_tradfi_candidate_pool(tickers)
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["modes"], ["SHORT", "LONG"])

    def test_tradfi_24h_change_floor_filters_candidate(self):
        bot = self.make_bot()
        bot.config.update({"tradfi_enabled": True, "tradfi_symbols": ["TSLAUSDT"],
                           "tradfi_min_24h_change_pct": -10})
        bot.get_market_exchange_symbols = lambda: {"TSLAUSDT": {
            "contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]}}
        ticker = {"symbol": "TSLAUSDT", "lastPrice": "100", "quoteVolume": "10000000",
                  "priceChangePercent": "-11"}
        self.assertEqual(bot.build_tradfi_candidate_pool([ticker]), [])
        ticker["priceChangePercent"] = "-9"
        self.assertEqual(len(bot.build_tradfi_candidate_pool([ticker])), 1)
        ticker["priceChangePercent"] = "nan"
        self.assertEqual(bot.build_tradfi_candidate_pool([ticker]), [])

    def test_closed_session_pattern_rejects_live_candle_and_accepts_retest(self):
        now = datetime(2026, 7, 6, 16, 45, tzinfo=timezone.utc)
        opens = [datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc).timestamp() * 1000 + i * 900_000 for i in range(9)]
        prices = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
                  (100, 101, 99, 100), (100, 100, 98, 98.5), (98.5, 99.1, 98, 98.4),
                  (98.4, 98.5, 97.5, 97.7), (97.7, 98, 97, 97.5), (97.5, 98, 97, 97.5)]
        klines = []
        for i, (op, hi, lo, close) in enumerate(prices):
            opened = int(opens[i])
            klines.append([opened, str(op), str(hi), str(lo), str(close), "0", opened + 899_999, "100000"])
        result = tradfi_strategy.evaluate(klines[:7], "SHORT", 97.7, now)
        self.assertTrue(result["passed"])
        live_open = int(datetime(2026, 7, 6, 16, 45, tzinfo=timezone.utc).timestamp() * 1000)
        live = [live_open, "97.7", "101", "90", "95", "0", live_open + 899_999, "900000"]
        self.assertTrue(tradfi_strategy.evaluate(klines[:7] + [live], "SHORT", 97.7, now)["passed"])
        self.assertTrue(tradfi_strategy.evaluate(klines[:8], "SHORT", 97.7, now)["passed"])
        self.assertFalse(tradfi_strategy.evaluate(klines[:7], "LONG", 97.7, now)["passed"])
        self.assertFalse(tradfi_strategy.evaluate(klines[:7], "SHORT", 97.7,
                                                  datetime(2026, 7, 6, 17, 30, tzinfo=timezone.utc))["passed"])

    def test_premarket_needs_fresh_contiguous_liquid_candles(self):
        start = int(datetime(2026, 7, 6, 11, 0, tzinfo=timezone.utc).timestamp() * 1000)
        now = datetime(2026, 7, 6, 12, 46, tzinfo=timezone.utc)
        prices = [(100, 101, 99, 100)] * 4 + [(100, 100, 98, 98.5),
                                               (98.5, 99.1, 98, 98.4),
                                               (98.4, 98.5, 97.5, 97.7)]
        bars = [[start + i * 900_000, str(op), str(hi), str(lo), str(close), "0",
                 start + (i + 1) * 900_000 - 1, "100000"]
                for i, (op, hi, lo, close) in enumerate(prices)]
        self.assertTrue(tradfi_strategy.evaluate(bars, "SHORT", 97.7, now, 50_000)["passed"])
        bars[-1][7] = "1000"
        self.assertIn("成交额", tradfi_strategy.evaluate(bars, "SHORT", 97.7, now, 50_000)["reason"])
        bars[-1][7] = "100000"
        bars[-2][0] += 900_000
        self.assertIn("不连续", tradfi_strategy.evaluate(bars, "SHORT", 97.7, now, 50_000)["reason"])

    def test_four_hour_change_uses_sixteen_closed_trading_bars(self):
        start = int(datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        now = datetime(2026, 7, 6, 14, 1, tzinfo=timezone.utc)
        bars = [[start + i * 900_000, "100", "110", "90", "108" if i == 15 else "100",
                 "0", start + (i + 1) * 900_000 - 1, "100000"] for i in range(16)]
        self.assertAlmostEqual(tradfi_strategy.four_hour_change_pct(bars, now), 8.0)

    def test_fetch_tickers_only_adds_whitelisted_equity_tradfi(self):
        bot = self.make_bot()
        bot.config.update({"tradfi_enabled": True, "tradfi_symbols": ["TSLAUSDT", "XAUUSDT"]})
        payload = [{"symbol": name, "lastPrice": "100", "quoteVolume": "10000000"}
                   for name in ("BTCUSDT", "TSLAUSDT", "NVDAUSDT", "XAUUSDT")]
        kinds = {"BTCUSDT": ("PERPETUAL", "COIN"), "TSLAUSDT": ("TRADIFI_PERPETUAL", "EQUITY"),
                 "NVDAUSDT": ("TRADIFI_PERPETUAL", "EQUITY"), "XAUUSDT": ("TRADIFI_PERPETUAL", "COMMODITY")}
        info = {name: {"contractType": contract, "underlyingType": kind,
                       "underlyingSubType": ["TradFi"] if contract.startswith("TRADIFI") else []}
                for name, (contract, kind) in kinds.items()}
        bot.get_market_exchange_symbols = lambda: info
        with patch.object(server, "http_get_json", return_value=payload):
            rows = bot.fetch_tickers()
        self.assertEqual({row["symbol"] for row in rows}, {"BTCUSDT", "TSLAUSDT"})
        self.assertEqual([row["item"]["symbol"] for row in bot.build_candidate_pool(rows)], [])

    def test_short_priority_falls_back_to_long_once(self):
        bot = self.make_bot()
        bot.config.update({"tradfi_enabled": True, "tradfi_auto_trade_enabled": True,
                           "tradfi_symbols": ["TSLAUSDT"], "tradfi_priority": "SHORT"})
        bot.get_market_exchange_symbols = lambda: {"TSLAUSDT": {
            "contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]}}
        now = datetime(2026, 7, 6, 16, 45, tzinfo=timezone.utc)
        start = int(datetime(2026, 7, 6, 12, 45, tzinfo=timezone.utc).timestamp() * 1000)
        prices = [(100, 101, 99, 100)] * 13 + [(100, 102, 99.5, 101.5),
                                               (101.5, 101.8, 101.1, 101.2),
                                               (101.2, 102.4, 101.1, 102.2)]
        bars = [[start + i * 900_000, str(op), str(hi), str(lo), str(close), "0",
                 start + (i + 1) * 900_000 - 1, "100000"]
                for i, (op, hi, lo, close) in enumerate(prices)]
        bot.fetch_klines_interval = lambda symbol, interval, limit: bars
        ticker = {"symbol": "TSLAUSDT", "lastPrice": "102.2", "quoteVolume": "10000000", "priceChangePercent": "2"}
        with patch.object(server, "utc_now", return_value=now):
            signals = bot.build_tradfi_signals([ticker], set())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "LONG")
        self.assertEqual(signals[0].reject_reason, "")
        self.assertIn("SHORT", signals[0].decision_steps[1])
        self.assertEqual(signals[0].planned_stop_price, 0)
        self.assertEqual(signals[0].planned_take_profit_price, 0)
        bars[0][1] = "80"
        with patch.object(server, "utc_now", return_value=now):
            self.assertEqual(bot.build_tradfi_signals([ticker], set()), [])

    def test_tradfi_roi_settings_are_independent(self):
        bot = self.make_bot()
        bot.update_settings({"tradfi_take_profit_roi_pct": 10, "tradfi_stop_loss_roi_pct": 8})
        self.assertEqual(bot.config["tradfi_take_profit_roi_pct"], 10)
        self.assertEqual(bot.config["tradfi_stop_loss_roi_pct"], 8)
        self.assertEqual(bot.config["take_profit_roi_pct"], 90)
        self.assertEqual(bot.config["stop_loss_roi_pct"], 86)

    def test_tradfi_quality_filters_can_be_saved(self):
        bot = self.make_bot()
        bot.update_settings({"tradfi_min_24h_change_pct": -12,
                             "tradfi_max_4h_change_pct": 7,
                             "tradfi_min_15m_quote_volume_usdt": 60_000,
                             "tradfi_max_spread_pct": 0.2,
                             "tradfi_min_depth_multiple": 4})
        self.assertEqual(bot.config["tradfi_min_24h_change_pct"], -12)
        self.assertEqual(bot.config["tradfi_max_4h_change_pct"], 7)
        self.assertEqual(bot.config["tradfi_min_15m_quote_volume_usdt"], 60_000)
        self.assertEqual(bot.config["tradfi_max_spread_pct"], 0.2)
        self.assertEqual(bot.config["tradfi_min_depth_multiple"], 4)
        bot.update_settings({"tradfi_max_4h_change_pct": "nan"})
        self.assertEqual(bot.config["tradfi_max_4h_change_pct"], 10)

    def test_invalid_saved_quality_limits_fail_closed(self):
        bot = self.make_bot()
        bot.config_path.write_text(
            '{"tradfi_max_4h_change_pct": NaN, "tradfi_min_depth_multiple": NaN}',
            encoding="utf-8",
        )
        reloaded = server.PaperBot(bot.config_path, bot.state_path)
        self.assertEqual(reloaded.config["tradfi_max_4h_change_pct"], 10)
        self.assertEqual(reloaded.config["tradfi_min_depth_multiple"], 3)

    def test_tradfi_protection_orders_use_position_roi_not_structural_prices(self):
        for side, expected_stop, expected_take in (("LONG", 98, 102.5), ("SHORT", 102, 97.5)):
            with self.subTest(side=side):
                bot = self.make_bot()
                bot.config.update({"tradfi_take_profit_roi_pct": 10, "tradfi_stop_loss_roi_pct": 8})
                bot.get_available_balance = lambda: 100
                bot.ensure_exchange_settings = lambda symbol, target_leverage, target_margin_type=None: 4
                bot.exposure_pct = lambda extra_notional=0: 0
                calls = []

                def submit(method, path, params, signal_side):
                    calls.append((path, params))
                    return {"orderId": "entry-1"} if path == "/fapi/v1/order" else {"algoId": "protection-1"}

                bot.signed_exchange_request_position_aware = submit
                signal = server.Signal(
                    symbol="TSLAUSDT", side=side, score=9, price=100, quote_volume=1_000_000,
                    change_24h_pct=0, change_4h_pct=0, change_1h_pct=0, short_move_pct=0,
                    volume_ratio=1, open_interest_usdt=0, open_interest_24h_pct=0,
                    open_interest_4h_pct=0, open_interest_1h_pct=0, funding_rate_pct=0,
                    long_short_ratio=0, volume_4h_ratio=0, taker_long_short_ratio=0,
                    oi_volume_ratio=0, signal_mode="tradfi_us_equity", signal_heat_level="normal",
                    market_regime_score=0, market_regime_label="", market_regime_reason="",
                    risk_level="注意", analyzer_direction="", analyzer_confidence_pct=0,
                    analyzer_reason="", trigger_reason="", reject_reason="", decision_summary="",
                    decision_steps=[], priority="中", planned_stop_price=50,
                    planned_take_profit_price=150, signal_model="tradfi_us_v1")
                info = {"filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001"},
                                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"}]}
                record = bot.exchange_open_position_with_sizing(signal, info, {"margin_usdt": 3, "target_leverage": 8})
                self.assertAlmostEqual(float(calls[1][1]["triggerPrice"]), expected_stop, delta=0.011)
                self.assertAlmostEqual(float(calls[2][1]["triggerPrice"]), expected_take, delta=0.011)
                self.assertEqual(record["stop_loss_roi_pct"], 8)
                self.assertEqual(record["take_profit_roi_pct"], 10)
                self.assertEqual(record["leverage"], 4)
                if side == "LONG":
                    bot.config["tradfi_stop_loss_roi_pct"] = 500
                    with self.assertRaisesRegex(RuntimeError, "ROI"):
                        bot.exchange_open_position_with_sizing(signal, info, {"margin_usdt": 3, "target_leverage": 8})
                    self.assertEqual(len(calls), 3)

    def test_tradfi_paper_position_uses_its_opening_roi_snapshot(self):
        bot = self.make_bot()
        pos = {"symbol": "TSLAUSDT", "side": "LONG", "entry_price": 100, "last_price": 100,
               "quantity": 0.06, "margin_usdt": 3, "signal_mode": "tradfi_us_equity",
               "take_profit_roi_pct": 10, "stop_loss_roi_pct": 8}
        bot.state["positions"] = [pos]
        bot.config.update({"tradfi_take_profit_roi_pct": 90, "tradfi_stop_loss_roi_pct": 86})
        closed = []
        bot.close_position = lambda p, price, reason: closed.append(reason)
        bot.refresh_positions({"TSLAUSDT": 104})
        self.assertEqual(closed, [])
        bot.refresh_positions({"TSLAUSDT": 105})
        self.assertEqual(closed, ["止盈平仓"])

    def test_preopen_rechecks_type_spread_and_depth_outside_regular_session(self):
        bot = self.make_bot()
        bot.config.update({"tradfi_enabled": True, "tradfi_auto_trade_enabled": True,
                           "tradfi_symbols": ["TSLAUSDT"]})
        info = {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]}
        signal = server.Signal(
            symbol="TSLAUSDT", side="SHORT", score=9, price=100, quote_volume=1_000_000,
            change_24h_pct=0, change_4h_pct=0, change_1h_pct=0, short_move_pct=0,
            volume_ratio=1, open_interest_usdt=0, open_interest_24h_pct=0,
            open_interest_4h_pct=0, open_interest_1h_pct=0, funding_rate_pct=0,
            long_short_ratio=0, volume_4h_ratio=0, taker_long_short_ratio=0,
            oi_volume_ratio=0, signal_mode="tradfi_us_equity", signal_heat_level="normal",
            market_regime_score=0, market_regime_label="", market_regime_reason="",
            risk_level="注意", analyzer_direction="美股结构确认", analyzer_confidence_pct=90,
            analyzer_reason="", trigger_reason="", reject_reason="", decision_summary="",
            decision_steps=[], priority="中", planned_stop_price=0, planned_take_profit_price=0,
            signal_model="tradfi_us_v1")
        premarket = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        with patch.object(server, "utc_now", return_value=premarket), patch.object(server, "http_get_json", return_value=self.depth_book(ask_price="100.5")):
            self.assertIn("价差", bot.tradfi_open_reject_reason(signal, info))
        with patch.object(server, "utc_now", return_value=premarket), patch.object(server, "http_get_json", return_value=self.depth_book(bid_qty="0.01")):
            self.assertIn("深度", bot.tradfi_open_reject_reason(signal, info))
        broken_book = {"bids": [["100", "1"], None], "asks": [["100.1", "1"]]}
        with patch.object(server, "utc_now", return_value=premarket), patch.object(server, "http_get_json", return_value=broken_book):
            self.assertIn("盘口", bot.tradfi_open_reject_reason(signal, info))
        missing_quantity = {"bids": [["100"]], "asks": [["100.1", "1"]]}
        with patch.object(server, "utc_now", return_value=premarket), patch.object(server, "http_get_json", return_value=missing_quantity):
            self.assertIn("盘口", bot.tradfi_open_reject_reason(signal, info))
        with patch.object(server, "utc_now", return_value=premarket), patch.object(server, "http_get_json", return_value=self.depth_book()):
            self.assertEqual(bot.tradfi_open_reject_reason(signal, info), "")
            self.assertIn("合约类型", bot.tradfi_open_reject_reason(signal, {**info, "underlyingType": "COMMODITY"}))

    def test_open_path_allows_only_verified_equity_signal(self):
        bot = self.make_bot()
        bot.config.update({"tradfi_enabled": True, "tradfi_auto_trade_enabled": True,
                           "tradfi_symbols": ["TSLAUSDT"], "square_publish_on_open": False})
        info = {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY", "underlyingSubType": ["TradFi"]}
        bot.get_exchange_symbols = lambda: {"TSLAUSDT": info}
        bot.get_market_exchange_symbols = lambda: {"TSLAUSDT": info}
        bot.exchange_open_position = lambda signal, symbol_info: {
            "entry_order_id": "123", "side": "SHORT", "margin_usdt": 3.0}
        signal = server.Signal(
            symbol="TSLAUSDT", side="SHORT", score=9, price=100, quote_volume=1_000_000,
            change_24h_pct=0, change_4h_pct=0, change_1h_pct=0, short_move_pct=0,
            volume_ratio=1, open_interest_usdt=0, open_interest_24h_pct=0,
            open_interest_4h_pct=0, open_interest_1h_pct=0, funding_rate_pct=0,
            long_short_ratio=0, volume_4h_ratio=0, taker_long_short_ratio=0,
            oi_volume_ratio=0, signal_mode="tradfi_us_equity", signal_heat_level="normal",
            market_regime_score=0, market_regime_label="", market_regime_reason="",
            risk_level="注意", analyzer_direction="美股结构确认", analyzer_confidence_pct=90,
            analyzer_reason="", trigger_reason="", reject_reason="", decision_summary="",
            decision_steps=[], priority="中", planned_stop_price=0, planned_take_profit_price=0,
            signal_model="tradfi_us_v1")
        after_hours = datetime(2026, 7, 6, 21, 0, tzinfo=timezone.utc)
        with patch.object(server, "utc_now", return_value=after_hours), patch.object(server, "http_get_json", return_value=self.depth_book()):
            self.assertEqual(bot.open_from_signals([signal]), 1)
        signal.reject_reason = ""
        bot.get_market_exchange_symbols = lambda: {"TSLAUSDT": {**info, "underlyingType": "COMMODITY"}}
        with patch.object(server, "utc_now", return_value=after_hours):
            self.assertEqual(bot.open_from_signals([signal]), 0)


if __name__ == "__main__":
    unittest.main()
