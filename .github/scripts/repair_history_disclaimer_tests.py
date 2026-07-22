from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


lifecycle_tests = read("tests/test_page_lifecycle.py")
start_marker = """            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
"""
end_marker = """            const trackedBackdrop = {{ hidden: true }};
"""
start = lifecycle_tests.find(start_marker)
end = lifecycle_tests.find(end_marker, start)
if start < 0 or end < 0:
    raise RuntimeError("could not locate the persisted-false lifecycle fixture")

replacement = """            // WKWebView may report persisted=false even for browser-history
            // restoration. PerformanceNavigationTiming must still trigger repair.
            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
            }}), true);
            assert.equal(openBackdrop.hidden, true);
            assert.equal(reloadCount, 3);

            // A positive persisted signal is also sufficient when navigation
            // timing is missing or misleading.
            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: true }},
                windowObj,
                documentObj,
                performanceObj: {{ getEntriesByType: () => [{{ type: 'navigate' }}] }},
            }}), true);
            assert.equal(openBackdrop.hidden, true);
            assert.equal(reloadCount, 4);

"""
write("tests/test_page_lifecycle.py", lifecycle_tests[:start] + replacement + lifecycle_tests[end:])

history_test = r'''from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path


class HistoryCardPagehideRegressionTest(unittest.TestCase):
    def test_pagehide_persists_card_marker_and_history_restore_reloads(self):
        module_path = Path(__file__).resolve().parents[1] / "static" / "page_lifecycle.js"
        module_url = module_path.as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            const lifecycle = await import({json.dumps(module_url)});

            const attributes = new Map();
            const documentElement = {{
                hasAttribute(name) {{ return attributes.has(name); }},
                setAttribute(name, value) {{ attributes.set(name, String(value)); }},
                removeAttribute(name) {{ attributes.delete(name); }},
            }};
            const backdrop = {{ hidden: false }};
            const documentObj = {{
                body: {{}},
                documentElement,
                querySelectorAll(selector) {{
                    if (selector === '.notice-backdrop') return [backdrop];
                    if (selector === '.notice-backdrop:not([hidden])') {{
                        return backdrop.hidden ? [] : [backdrop];
                    }}
                    return [];
                }},
            }};

            const stored = new Map();
            const handlers = new Map();
            const timers = new Map();
            let nextTimer = 1;
            let reloads = 0;
            const windowObj = {{
                location: {{
                    pathname: '/my-spots',
                    search: '',
                    reload() {{ reloads += 1; }},
                }},
                sessionStorage: {{
                    getItem(key) {{ return stored.has(key) ? stored.get(key) : null; }},
                    setItem(key, value) {{ stored.set(key, String(value)); }},
                    removeItem(key) {{ stored.delete(key); }},
                }},
                addEventListener(name, handler) {{ handlers.set(name, handler); }},
                removeEventListener(name, handler) {{
                    if (handlers.get(name) === handler) handlers.delete(name);
                }},
                setTimeout(callback) {{
                    const id = nextTimer++;
                    timers.set(id, callback);
                    return id;
                }},
                clearTimeout(id) {{ timers.delete(id); }},
            }};
            class FakeMutationObserver {{
                constructor(callback) {{ this.callback = callback; }}
                observe() {{}}
                disconnect() {{}}
            }}
            const backForwardPerformance = {{
                getEntriesByType: () => [{{ type: 'back_forward' }}],
            }};

            const cleanup = lifecycle.installHistoryCardRestoreGuard({{
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
                MutationObserverClass: FakeMutationObserver,
            }});

            assert.equal(typeof handlers.get('pagehide'), 'function');
            assert.equal(typeof handlers.get('pageshow'), 'function');

            handlers.get('pagehide')({{ persisted: true }});
            assert.equal(backdrop.hidden, true);
            assert.equal(stored.get('nimhunt:card-navigation:/my-spots'), '1');

            // This is the reported case: browser Back restores stale page state,
            // but the embedded browser reports pageshow.persisted as false.
            handlers.get('pageshow')({{ persisted: false }});
            assert.equal(reloads, 1);
            assert.equal(stored.has('nimhunt:card-navigation:/my-spots'), false);
            assert.equal(
                documentElement.hasAttribute('data-nimhunt-card-navigation-pending'),
                false,
            );

            cleanup();
            assert.equal(handlers.has('pagehide'), false);
            assert.equal(handlers.has('pageshow'), false);
        """
        env = dict(os.environ)
        env["TZ"] = "UTC"
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=module_path.parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_history_card_pagehide.py", history_test)

home_disclaimer_test = r'''from __future__ import annotations

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

    def test_enabled_disclaimer_renders_with_two_warning_icons(self):
        rendered = self.render_home(enabled=True)
        self.assertIn('id="project-disclaimer-title"', rendered)
        self.assertEqual(rendered.count("#nq-alert-triangle"), 2)
        self.assertIn("never spend more than you can afford to lose", rendered)

    def test_disabled_disclaimer_is_not_rendered(self):
        rendered = self.render_home(enabled=False)
        self.assertNotIn("project-disclaimer", rendered)

    def test_server_context_and_button_like_styling_are_present(self):
        public_source = (self.root / "public_html.py").read_text(encoding="utf-8")
        css_source = (self.root / "static" / "home.css").read_text(encoding="utf-8")
        banner_source = (
            self.root / "static" / "network_mode_banner.js"
        ).read_text(encoding="utf-8")

        self.assertIn("project_disclaimer_enabled", public_source)
        self.assertIn("SHOW_PROJECT_DISCLAIMER", public_source)
        self.assertIn("linear-gradient", css_source)
        self.assertIn(".project-disclaimer", css_source)
        self.assertIn("box-shadow", css_source)
        # The network label is asynchronously prepended, so it remains above
        # the server-rendered disclaimer and the hero.
        self.assertIn("shell.prepend(createNetworkModeBanner(label))", banner_source)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_home_disclaimer.py", home_disclaimer_test)
