import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import paper_dashboard_server as server
import test_equity_take_profit


class OldReboundLimitEntryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.bot = server.PaperBot(root / "config.json", root / "state.json")
        self.bot.update_settings({"old_rebound_short_entry_type": "LIMIT",
                                  "old_rebound_short_limit_offset_pct": "5"})
        self.bot.latest_contract_price = Mock(return_value=0.5)
        self.bot.get_available_balance = Mock(return_value=100)
        self.bot.ensure_exchange_settings = Mock(return_value=5)
        self.bot.exposure_pct = Mock(return_value=5)
        self.bot.config.update({"take_profit_roi_pct": 50, "stop_loss_roi_pct": 50})
        self.bot.signed_exchange_request_position_aware = Mock(return_value={
            "orderId": 10, "status": "NEW", "executedQty": "0"})
        self.bot.mark_signal_review_opened = Mock()
        self.bot.publish_square_open_position_async = Mock()
        self.signal = test_equity_take_profit.ReverseOrdersTest().make_signal()
        self.signal.side = "SHORT"
        self.signal.price = 0.5
        self.signal.signal_mode = "old_rebound_short"
        self.info = {"filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.001"},
                                 {"filterType": "LOT_SIZE", "stepSize": "1"}]}
        self.sizing = {"margin_usdt": 3, "target_leverage": 5}

    def submit(self):
        return self.bot.exchange_open_position_with_sizing(self.signal, self.info, self.sizing)

    def test_gtc_entry_is_not_opened_or_expired_while_unfilled(self):
        self.submit()
        request = self.bot.signed_exchange_request_position_aware
        method, path, params, side = request.call_args.args
        self.assertEqual((method, path, side), ("POST", "/fapi/v1/order", "SHORT"))
        self.assertEqual((params["type"], params["price"], params["timeInForce"]), ("LIMIT", "0.525", "GTC"))
        self.assertEqual(request.call_count, 1)
        self.assertFalse(self.bot.state["open_order_history"])
        self.bot.signed_exchange_request = Mock(return_value={"orderId": 10, "status": "NEW", "executedQty": "0"})
        self.bot.reconcile_limit_entries()
        self.assertEqual(self.bot.signed_exchange_request.call_args.args[0], "GET")
        self.assertIn("已挂单", self.bot.snapshot()["state"]["candidates"][0]["decision_summary"])
        self.assertEqual(request.call_count, 1)

    def test_partial_fill_cancels_remainder_and_protects_at_limit_price_after_restart(self):
        self.submit()
        restarted = server.PaperBot(self.bot.config_path, self.bot.state_path)
        restarted.signed_exchange_request = Mock(side_effect=[
            {"orderId": 10, "status": "PARTIALLY_FILLED", "executedQty": "2", "avgPrice": "0.54"},
            {"orderId": 10, "status": "CANCELED", "executedQty": "3", "avgPrice": "0.54"},
            [{"symbol": self.signal.symbol, "positionAmt": "-3"}],
        ])
        restarted.signed_exchange_request_position_aware = Mock(side_effect=[{"algoId": 31}, {"algoId": 32}])
        restarted.mark_signal_review_opened = Mock()
        restarted.publish_square_open_position_async = Mock()
        restarted.reconcile_limit_entries()
        self.assertEqual([call.args[0] for call in restarted.signed_exchange_request.call_args_list], ["GET", "DELETE", "GET"])
        protection = [call.args[2]["triggerPrice"] for call in restarted.signed_exchange_request_position_aware.call_args_list]
        self.assertEqual(protection, ["0.577", "0.472"])
        record = restarted.state["open_order_history"][0]
        self.assertEqual((record["quantity"], record["entry_price"]), ("3", 0.54))

    def test_reverse_and_other_strategies_keep_market_entry(self):
        for mode, reverse in (("strong_momentum", False), ("old_rebound_short", True)):
            with self.subTest(mode=mode, reverse=reverse):
                self.signal.signal_mode = mode
                self.bot.config["reverse_orders_enabled"] = reverse
                self.bot.signed_exchange_request_position_aware.reset_mock()
                self.submit()
                self.assertEqual(self.bot.signed_exchange_request_position_aware.call_args_list[0].args[2]["type"], "MARKET")
        self.bot.latest_contract_price.assert_not_called()

    def test_pending_symbol_does_not_reach_order_checks_again(self):
        self.submit()
        ticker = {"symbol": self.signal.symbol, "lastPrice": "0.5",
                  "priceChangePercent": "15", "quoteVolume": "50000000"}
        self.assertEqual(self.bot.build_candidate_pool([ticker]), [])
        self.bot.get_exchange_symbols = Mock(return_value={self.signal.symbol: self.info})
        self.bot.get_market_exchange_symbols = Mock(return_value={})
        self.bot.exchange_open_position = Mock()
        self.assertEqual(self.bot.open_from_signals([self.signal]), 0)
        self.bot.exchange_open_position.assert_not_called()
        self.assertIn("已挂单", self.signal.reject_reason)

    def test_exchange_cancellation_releases_unfilled_symbol(self):
        self.submit()
        self.bot.signed_exchange_request = Mock(return_value={
            "orderId": 10, "status": "CANCELED", "executedQty": "0"})
        self.bot.reconcile_limit_entries()
        self.assertEqual(self.bot.tracked_limit_entries(), [])
        self.assertEqual(self.bot.snapshot()["state"]["candidates"], [])
        self.assertFalse(self.bot.state["open_order_history"])
        self.assertEqual(self.bot.signed_exchange_request_position_aware.call_count, 1)

    def test_limit_price_rounds_up_to_exchange_tick(self):
        self.bot.update_settings({"old_rebound_short_limit_offset_pct": "5.01"})
        self.submit()
        params = self.bot.signed_exchange_request_position_aware.call_args.args[2]
        self.assertEqual(params["price"], "0.526")

    def test_protection_timeout_is_looked_up_before_retry(self):
        self.submit()
        filled = {"orderId": 10, "status": "FILLED", "executedQty": "3", "avgPrice": "0.54"}
        position = [{"symbol": self.signal.symbol, "positionAmt": "-3"}]
        self.bot.signed_exchange_request = Mock(side_effect=[filled, position, filled, position, {"algoId": 31}])
        requests = self.bot.signed_exchange_request_position_aware
        requests.reset_mock()
        requests.side_effect = [RuntimeError("timed out"), {"algoId": 32}]
        self.bot.reconcile_limit_entries()
        self.assertFalse(self.bot.state["open_order_history"])
        self.bot.reconcile_limit_entries()
        self.assertEqual(self.bot.signed_exchange_request.call_args.args[:2], ("GET", "/fapi/v1/algoOrder"))
        self.assertEqual([call.args[2]["type"] for call in requests.call_args_list],
                         ["STOP_MARKET", "TAKE_PROFIT_MARKET"])
        self.assertEqual(self.bot.state["open_order_history"][0]["stop_algo_id"], 31)

    def test_already_closed_fill_does_not_send_orphan_protection(self):
        self.submit()
        self.bot.signed_exchange_request = Mock(side_effect=[
            {"orderId": 10, "status": "FILLED", "executedQty": "3", "avgPrice": "0.54",
             "updateTime": int(server.time.time() * 1000) - 30_000},
            [{"symbol": self.signal.symbol, "positionAmt": "0"}],
        ])
        self.bot.reconcile_limit_entries()
        self.assertEqual(self.bot.signed_exchange_request_position_aware.call_count, 1)
        self.assertEqual(self.bot.state["open_order_history"][0]["quantity"], "3")

    def test_immediate_trigger_attempts_reduce_only_exit_for_filled_quantity(self):
        self.submit()
        self.bot.position_mode = Mock(return_value="one_way")
        self.bot.signed_exchange_request = Mock(side_effect=[
            {"orderId": 10, "status": "FILLED", "executedQty": "3", "avgPrice": "0.54"},
            [{"symbol": self.signal.symbol, "positionAmt": "-5"}],
        ])
        requests = self.bot.signed_exchange_request_position_aware
        requests.reset_mock()
        requests.side_effect = [RuntimeError("-2021 Order would immediately trigger"), {"orderId": 11}]
        self.bot.reconcile_limit_entries()
        self.assertEqual([call.args[2]["type"] for call in requests.call_args_list], ["STOP_MARKET", "MARKET"])
        params = requests.call_args.args[2]
        self.assertEqual((params["side"], params["reduceOnly"], params["quantity"]), ("BUY", "true", "3"))
        self.assertFalse(self.bot.state["open_order_history"])
