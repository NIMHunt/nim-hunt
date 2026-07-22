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

    def test_enabled_disclaimer_reuses_primary_action_classes(self):
        rendered = self.render_home(enabled=True)
        self.assertIn('id="project-disclaimer-title"', rendered)
        self.assertIn(
            'class="project-disclaimer-note nq-button green action-button primary-action"',
            rendered,
        )
        self.assertIn('class="action-title"', rendered)
        self.assertIn('class="action-detail"', rendered)
        self.assertIn("never spend more than you can afford to lose", rendered)
        self.assertNotIn("#nq-alert-triangle", rendered)
        self.assertNotIn('class="project-disclaimer"', rendered)

    def test_disabled_disclaimer_is_not_rendered(self):
        rendered = self.render_home(enabled=False)
        self.assertNotIn("project-disclaimer-note", rendered)

    def test_only_noninteractive_overrides_are_custom(self):
        public_source = (self.root / "public_html.py").read_text(encoding="utf-8")
        css_source = (
            self.root / "static" / "disclaimer_button.css"
        ).read_text(encoding="utf-8")
        banner_source = (
            self.root / "static" / "network_mode_banner.js"
        ).read_text(encoding="utf-8")

        self.assertIn("project_disclaimer_enabled", public_source)
        self.assertIn("SHOW_PROJECT_DISCLAIMER", public_source)
        self.assertIn("pointer-events: none", css_source)
        self.assertIn("cursor: default", css_source)
        self.assertNotIn("linear-gradient", css_source)
        self.assertNotIn("box-shadow", css_source)
        self.assertNotIn("background:", css_source)
        self.assertIn("shell.prepend(createNetworkModeBanner(label))", banner_source)


if __name__ == "__main__":
    unittest.main()
