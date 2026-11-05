import unittest
from html.parser import HTMLParser
from pathlib import Path


class InputNameParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.input_names = set()
        self.select_names = set()

    def handle_starttag(self, tag, attrs):
        if tag not in {"input", "select"}:
            return
        values = dict(attrs)
        name = values.get("name")
        if name:
            if tag == "input":
                self.input_names.add(name)
            else:
                self.select_names.add(name)


class FrontendSettingsTest(unittest.TestCase):
    def test_analyzer_confidence_controls_are_not_exposed(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)

        self.assertNotIn("confirmation_mode", parser.select_names)
        self.assertNotIn("analyzer_min_confidence_pct", parser.input_names)
        self.assertNotIn("analyzer_realtime_before_open", parser.input_names)

    def test_pre_start_has_independent_initial_profit_and_loss_roi_controls(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertIn("pre_start_take_profit_roi_pct", parser.input_names)
        self.assertIn("pre_start_stop_loss_roi_pct", parser.input_names)

    def test_pre_start_form_exposes_sustained_volume_controls(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertTrue({
            "pre_start_long_enabled", "pre_start_volume_auto_trade_enabled",
            "pre_start_volume_15m_min_usdt",
            "pre_start_volume_relative_min", "pre_start_volume_growth_min",
            "pre_start_volume_taker_min",
        }.issubset(parser.input_names))

    def test_directional_pools_share_candidate_controls(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertTrue({
            "shared_pool_max_candidates", "shared_pool_24h_min_pct",
            "shared_pool_24h_max_pct", "shared_pool_min_quote_volume_usdt",
        }.issubset(parser.input_names))
        self.assertIn("shared_pool_priority", parser.select_names)
        self.assertFalse({
            "pre_start_max_candidates", "pre_start_volume_24h_min_pct",
            "pre_start_volume_24h_max_pct", "pre_start_volume_24h_min_usdt",
            "old_rebound_short_max_candidates", "old_rebound_short_24h_min_pct",
            "old_rebound_short_24h_max_pct", "old_rebound_short_min_quote_volume_usdt",
            "old_rebound_short_max_quote_volume_usdt",
        } & parser.input_names)

    def test_settings_form_exposes_system_score_threshold(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        self.assertIn("signal_score_threshold", parser.input_names)

    def test_settings_form_exposes_strong_long_24h_gain_floor(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        self.assertIn("strong_long_24h_min_change_pct", parser.input_names)

    def test_settings_form_exposes_strong_short_toggle(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        self.assertIn("strong_short_enabled", parser.input_names)

    def test_settings_form_exposes_strong_long_toggle(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertIn("strong_long_enabled", parser.input_names)

    def test_settings_form_exposes_reverse_orders_toggle(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        self.assertIn("reverse_orders_enabled", parser.input_names)

    def test_removed_risk_and_strategy_controls_are_not_displayed(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertFalse(any(name.startswith("high_pullback_short_") for name in parser.input_names))
        self.assertTrue({"max_open_positions", "max_exposure_pct", "stop_loss_cooldown_minutes"}.issubset(parser.input_names))
        for removed in ("max_daily_profit_usdt", "max_daily_loss_usdt", "previous_day_equity_usdt",
                        "equity_take_profit_usdt", "max_daily_profit_enabled", "max_daily_loss_enabled",
                        "equity_take_profit_enabled"):
            self.assertNotIn(removed, parser.input_names)
        self.assertNotIn("dailyPnlText", html)

    def test_settings_form_exposes_margin_type_selector(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        self.assertIn("margin_type", parser.select_names)
        self.assertIn("pre_start_margin_type", parser.select_names)
        self.assertIn("old_rebound_short_margin_type", parser.select_names)

    def test_settings_form_exposes_independent_protection_trigger_selectors(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)

        self.assertIn("take_profit_trigger_price_type", parser.select_names)
        self.assertIn("stop_loss_trigger_price_type", parser.select_names)

    def test_active_positions_expose_strategy_reason_column(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        js = Path("web/app.js").read_text(encoding="utf-8")
        render_positions = js[js.index("function renderPositions") : js.index("function renderEvents")]

        self.assertGreaterEqual(html.count("<th>策略归因</th>"), 2)
        self.assertIn("function strategyReasonDisplay(row)", js)
        self.assertIn('Array.isArray(row.decision_steps)', js)
        self.assertIn("const strategy = strategyReasonDisplay(row);", render_positions)
        self.assertIn('title="${titleText(strategy.title)}"', render_positions)
        self.assertIn('${escapeHtml(strategy.text)}', render_positions)


if __name__ == "__main__":
    unittest.main()
