import unittest
from html.parser import HTMLParser
from pathlib import Path


class InputNameParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.input_names = set()
        self.named_controls = []
        self.input_attrs = {}
        self.select_names = set()
        self.groups = []
        self.settings_tabs = []
        self.settings_panels = []
        self.subsections = []
        self.in_legend = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "button" and "data-settings-tab" in values:
            self.settings_tabs.append(values)
        if tag == "fieldset":
            classes = values.get("class", "").split()
            if "settings-group" in classes:
                self.groups.append({"classes": classes, "legend": ""})
                self.settings_panels.append(values)
        if tag == "h3" and "settings-subsection" in values.get("class", "").split():
            self.subsections.append(values)
        if tag == "legend" and self.groups:
            self.in_legend = True
        if tag not in {"input", "select"}:
            return
        name = values.get("name")
        if name:
            self.named_controls.append(name)
            if tag == "input":
                self.input_names.add(name)
                self.input_attrs[name] = values
            else:
                self.select_names.add(name)

    def handle_data(self, data):
        if self.in_legend:
            self.groups[-1]["legend"] += data

    def handle_endtag(self, tag):
        if tag == "legend":
            self.in_legend = False


class FrontendSettingsTest(unittest.TestCase):
    def test_settings_tabs_target_five_panels_with_only_first_visible(self):
        parser = InputNameParser()
        parser.feed(Path("web/index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(parser.settings_tabs), 5)
        self.assertEqual(len(parser.settings_panels), 5)
        self.assertEqual(
            [tab.get("aria-controls") for tab in parser.settings_tabs],
            [panel.get("id") for panel in parser.settings_panels],
        )
        self.assertEqual([tab.get("aria-selected") for tab in parser.settings_tabs],
                         ["true", "false", "false", "false", "false"])
        self.assertEqual(["hidden" in panel for panel in parser.settings_panels],
                         [False, True, True, True, True])
        self.assertGreaterEqual(len(parser.subsections), 10)

    def test_settings_keep_unique_controls_across_hidden_panels(self):
        parser = InputNameParser()
        parser.feed(Path("web/index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(parser.named_controls), 91)
        self.assertEqual(len(parser.named_controls), len(set(parser.named_controls)))
        self.assertNotIn("old_rebound_short_min_history_days", parser.named_controls)
        self.assertNotIn("trade_direction_mode", parser.named_controls)
        self.assertIn("blocked_symbols", parser.named_controls)

    def test_old_rebound_15m_drop_limit_is_editable(self):
        parser = InputNameParser()
        parser.feed(Path("web/index.html").read_text(encoding="utf-8"))
        attrs = parser.input_attrs["old_rebound_short_15m_min_pct"]
        self.assertEqual((attrs["type"], attrs["min"], attrs["max"], attrs["step"]),
                         ("number", "-50", "0", "0.1"))

    def test_removed_old_rebound_cumulative_limits_not_shown(self):
        parser = InputNameParser()
        parser.feed(Path("web/index.html").read_text(encoding="utf-8"))
        self.assertNotIn("old_rebound_short_1h_min_pct", parser.named_controls)
        self.assertNotIn("old_rebound_short_2h_min_pct", parser.named_controls)

    def test_tradfi_amount_and_roi_inputs_accept_normal_values(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)

        for name in ("tradfi_min_15m_quote_volume_usdt",
                     "tradfi_long_take_profit_roi_pct", "tradfi_long_stop_loss_roi_pct",
                     "tradfi_short_take_profit_roi_pct", "tradfi_short_stop_loss_roi_pct"):
            with self.subTest(name=name):
                attrs = parser.input_attrs[name]
                self.assertEqual(attrs["type"], "number")
                self.assertEqual(attrs["min"], "0.01")
                self.assertEqual(attrs["step"], "any")

    def test_settings_groups_use_stable_responsive_layout(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        css = Path("web/styles.css").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)

        self.assertEqual(
            [group["legend"] for group in parser.groups],
            ["交易执行", "筛选策略", "股票 TradFi 多空", "确认过滤", "风险控制"],
        )
        for group in parser.groups:
            self.assertIn("settings-group--full", group["classes"])
        self.assertNotIn(".settings-group:nth-of-type", css)
        self.assertIn(".settings-group--full", css)

    def test_pool_toggles_use_consistent_checkbox_layout(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        for name in ("old_rebound_short_enabled", "strong_long_enabled", "strong_short_enabled"):
            self.assertIn(
                f'<label class="settings-check">\n              <input name="{name}" type="checkbox" />',
                html,
            )

    def test_old_rebound_whitelist_has_toggle_and_per_symbol_controls(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertIn("old_rebound_whitelist_enabled", parser.input_names)
        self.assertIn("old_rebound_whitelist_min_24h_change_pct", parser.input_names)
        self.assertIn('id="oldReboundWhitelistInput"', html)
        self.assertIn('id="oldReboundWhitelistAdd"', html)
        self.assertIn('id="oldReboundWhitelistItems"', html)

    def test_tradfi_pool_controls_are_exposed(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertTrue({"tradfi_enabled", "tradfi_auto_trade_enabled",
                         "tradfi_max_candidates", "tradfi_min_quote_volume_usdt",
                         "tradfi_long_enabled", "tradfi_short_enabled",
                         "tradfi_min_24h_change_pct", "tradfi_max_4h_change_pct",
                         "tradfi_min_15m_quote_volume_usdt", "tradfi_max_spread_pct",
                         "tradfi_min_depth_multiple"}.issubset(parser.input_names))
        self.assertNotIn("tradfi_symbols", parser.input_names)
        self.assertNotIn("tradfi_max_stop_distance_pct", parser.input_names)
        self.assertNotIn("tradfi_reward_risk", parser.input_names)
        self.assertIn("tradfi_priority", parser.select_names)

    def test_tradfi_directions_have_independent_controls(self):
        parser = InputNameParser()
        parser.feed(Path("web/index.html").read_text(encoding="utf-8"))
        for side in ("long", "short"):
            self.assertIn(f"tradfi_{side}_enabled", parser.input_names)
            self.assertIn(f"tradfi_{side}_margin_type", parser.select_names)
            for suffix in ("margin_per_trade_usdt", "leverage", "take_profit_roi_pct", "stop_loss_roi_pct"):
                self.assertIn(f"tradfi_{side}_{suffix}", parser.input_names)
        for old in ("tradfi_margin_per_trade_usdt", "tradfi_leverage",
                    "tradfi_take_profit_roi_pct", "tradfi_stop_loss_roi_pct"):
            self.assertNotIn(old, parser.input_names)
        self.assertNotIn("tradfi_margin_type", parser.select_names)

    def test_tradfi_candidate_count_has_no_frontend_upper_bound(self):
        parser = InputNameParser()
        parser.feed(Path("web/index.html").read_text(encoding="utf-8"))
        attrs = parser.input_attrs["tradfi_max_candidates"]
        self.assertEqual(attrs.get("min"), "1")
        self.assertNotIn("max", attrs)

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

    def test_old_rebound_historical_low_distance_control_is_exposed(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertIn("old_rebound_short_min_historical_low_distance_pct", parser.input_names)
        self.assertEqual(parser.input_attrs["old_rebound_short_min_historical_low_distance_pct"]["min"], "0")
        self.assertEqual(parser.input_attrs["old_rebound_short_min_historical_low_distance_pct"]["max"], "100")

    def test_old_rebound_24h_low_distance_control_is_exposed(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        parser = InputNameParser()
        parser.feed(html)
        self.assertIn("old_rebound_short_min_low_distance_pct", parser.input_names)

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
