import unittest
from html.parser import HTMLParser
from pathlib import Path


class InputNameParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.input_names = set()

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        values = dict(attrs)
        name = values.get("name")
        if name:
            self.input_names.add(name)


class FrontendSettingsTest(unittest.TestCase):
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

    def test_settings_form_exposes_high_pullback_short_pool_settings(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()

        parser.feed(html)

        expected = {
            "high_pullback_short_enabled",
            "high_pullback_short_max_candidates",
            "high_pullback_short_24h_min_pct",
            "high_pullback_short_24h_max_pct",
            "high_pullback_short_min_drawdown_pct",
            "high_pullback_short_max_drawdown_pct",
            "high_pullback_short_min_low_distance_pct",
            "high_pullback_short_confirm_min_score",
        }
        self.assertTrue(expected.issubset(parser.input_names))


if __name__ == "__main__":
    unittest.main()
