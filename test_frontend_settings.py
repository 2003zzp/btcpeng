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


if __name__ == "__main__":
    unittest.main()
