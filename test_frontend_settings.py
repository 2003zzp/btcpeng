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
    def test_pre_start_form_exposes_sustained_volume_controls(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertTrue({
            "pre_start_long_enabled", "pre_start_volume_auto_trade_enabled",
            "pre_start_volume_24h_min_pct", "pre_start_volume_24h_max_pct",
            "pre_start_volume_24h_min_usdt", "pre_start_volume_15m_min_usdt",
            "pre_start_volume_relative_min", "pre_start_volume_growth_min",
            "pre_start_volume_taker_min",
        }.issubset(parser.input_names))

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

    def test_settings_form_exposes_high_pullback_short_pool_settings(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        expected = {
            "high_pullback_short_enabled",
            "high_pullback_short_margin_per_trade_usdt",
            "high_pullback_short_leverage",
            "high_pullback_short_max_candidates",
            "high_pullback_short_24h_min_pct",
            "high_pullback_short_24h_max_pct",
            "high_pullback_short_min_drawdown_pct",
            "high_pullback_short_max_drawdown_pct",
            "high_pullback_short_min_low_distance_pct",
            "high_pullback_short_confirm_min_score",
        }
        self.assertTrue(expected.issubset(parser.input_names))

    def test_settings_form_exposes_margin_type_selector(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        self.assertIn("margin_type", parser.select_names)

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
