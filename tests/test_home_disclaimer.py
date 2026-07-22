from __future__ import annotations

import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import constants as const


class HomeDisclaimerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.environment = Environment(
            loader=FileSystemLoader(self.root / "templates"),
            autoescape=True,
        )

    def render_home(self, *, enabled: bool) -> str:
        return self.environment.get_template("home.html").render(
            project_disclaimer_enabled=enabled,
        )

    def test_server_switch_is_a_plain_boolean(self):
        self.assertIs(type(const.SHOW_PROJECT_DISCLAIMER), bool)

    def test_enabled_disclaimer_matches_orange_action_card_structure(self):
        rendered = self.render_home(enabled=True)
        self.assertIn('id="project-disclaimer-title"', rendered)
        self.assertIn(
            'class="project-disclaimer-note nq-button gold action-button primary-action"',
            rendered,
        )
        self.assertIn('class="action-title project-disclaimer-title"', rendered)
        self.assertIn('class="action-detail"', rendered)
        self.assertEqual(rendered.count("#nq-alert-triangle"), 2)
        self.assertIn(
            "This project is new and may still have some issues. Please use this mini-app cautiously, and never spend more than you can afford to lose. Have fun!",
            rendered,
        )

    def test_disabled_disclaimer_is_not_rendered(self):
        rendered = self.render_home(enabled=False)
        self.assertNotIn("project-disclaimer-note", rendered)

    def test_disclaimer_uses_orange_action_overrides_and_wraps_text(self):
        public_source = (self.root / "public_html.py").read_text(encoding="utf-8")
        css_source = (
            self.root / "static" / "disclaimer_button.css"
        ).read_text(encoding="utf-8")
        banner_source = (
            self.root / "static" / "network_mode_banner.js"
        ).read_text(encoding="utf-8")

        self.assertIn("project_disclaimer_enabled", public_source)
        self.assertIn("SHOW_PROJECT_DISCLAIMER", public_source)
        self.assertIn("background: var(--nh-warning)", css_source)
        self.assertIn("height: auto", css_source)
        self.assertNotIn("min-height: 112px", css_source)
        self.assertIn("white-space: normal", css_source)
        self.assertIn("overflow-wrap: anywhere", css_source)
        self.assertNotIn("linear-gradient", css_source)
        self.assertIn("shell.prepend(createNetworkModeBanner(label))", banner_source)


if __name__ == "__main__":
    unittest.main()
