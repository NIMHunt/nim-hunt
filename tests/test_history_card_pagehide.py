from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path


class HistoryCardPagehideRegressionTest(unittest.TestCase):
    def test_pagehide_marker_survives_unreliable_history_signals(self):
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
            const misleadingPerformance = {{
                getEntriesByType: () => [{{ type: 'navigate' }}],
            }};

            const cleanup = lifecycle.installHistoryCardRestoreGuard({{
                windowObj,
                documentObj,
                performanceObj: misleadingPerformance,
                MutationObserverClass: FakeMutationObserver,
            }});

            assert.equal(typeof handlers.get('pagehide'), 'function');
            assert.equal(typeof handlers.get('pageshow'), 'function');

            handlers.get('pagehide')({{ persisted: true }});
            assert.equal(backdrop.hidden, true);
            assert.equal(stored.get('nimhunt:card-navigation:/my-spots'), '1');

            // The embedded browser now supplies neither conventional history
            // signal. The marker written for this exact page must still win.
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
