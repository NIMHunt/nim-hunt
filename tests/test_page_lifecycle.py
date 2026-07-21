from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import cache
import constants as const
import database as schema
import db_access


class DraftSpotDefaultsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await cache.force_all_cache_clear()
        await schema.init_db()

    async def asyncTearDown(self):
        await cache.force_all_cache_clear()
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def test_title_only_draft_uses_friendly_form_defaults(self):
        async with schema.get_db() as db:
            user_id = await db_access.create_user(
                db,
                device_id_hash="page-lifecycle-defaults-user",
            )
            spot_id = await db_access.create_spot(
                db,
                created_by=user_id,
                title="Friendly defaults",
            )
            await db.commit()
            spot = await db_access.get_spot(db, spot_id=spot_id)

        self.assertIsNotNone(spot)
        self.assertEqual(
            int(spot[schema.SPOT_RADIUS]),
            const.DEFAULT_DRAFT_SPOT_RADIUS_METRES,
        )
        self.assertEqual(int(spot[schema.SPOT_RADIUS]), 200)
        self.assertEqual(
            int(spot[schema.SPOT_ENDS_AT]),
            const.DEFAULT_DRAFT_SPOT_ENDS_AFTER_SECONDS,
        )
        self.assertEqual(int(spot[schema.SPOT_ENDS_AT]), 24 * 60 * 60)
        # Keep the durable draft meaning of NULL: publish immediately unless the
        # creator saves a specific Starts value. The browser displays the current
        # local time without prematurely persisting a stale timestamp.
        self.assertIsNone(spot[schema.SPOT_STARTS_AT])


class PageLifecycleJavaScriptTest(unittest.TestCase):
    def test_history_restore_and_datetime_helpers(self):
        module_path = Path(__file__).resolve().parents[1] / "static" / "page_lifecycle.js"
        module_url = module_path.as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            const lifecycle = await import({json.dumps(module_url)});

            const openBackdrop = {{ hidden: false }};
            let reloadCount = 0;
            const windowObj = {{
                location: {{ reload() {{ reloadCount += 1; }} }},
                addEventListener() {{}},
                removeEventListener() {{}},
            }};
            const documentObj = {{
                querySelectorAll(selector) {{
                    assert.equal(selector, '.notice-backdrop:not([hidden])');
                    return [openBackdrop];
                }},
            }};
            const backForwardPerformance = {{
                getEntriesByType(kind) {{
                    assert.equal(kind, 'navigation');
                    return [{{ type: 'back_forward' }}];
                }},
            }};

            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: true }},
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
            }}), true);
            assert.equal(openBackdrop.hidden, true);
            assert.equal(reloadCount, 1);

            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
            }}), false);
            assert.equal(openBackdrop.hidden, false);
            assert.equal(reloadCount, 1);

            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: true }},
                windowObj,
                documentObj,
                performanceObj: {{ getEntriesByType: () => [{{ type: 'navigate' }}] }},
            }}), false);
            assert.equal(openBackdrop.hidden, false);
            assert.equal(reloadCount, 1);

            const blankInput = {{ value: '' }};
            assert.equal(
                lifecycle.fillBlankStartInput(blankInput, Date.UTC(2026, 6, 21, 14, 5)),
                true,
            );
            assert.equal(blankInput.value, '2026-07-21T14:05');

            const existingInput = {{ value: '2026-08-01T09:30' }};
            assert.equal(
                lifecycle.fillBlankStartInput(existingInput, Date.UTC(2026, 6, 21, 14, 5)),
                false,
            );
            assert.equal(existingInput.value, '2026-08-01T09:30');

            const observedCard = {{ hidden: true }};
            const observedInput = {{ value: '' }};
            let observerCallback = null;
            let disconnected = false;
            class FakeMutationObserver {{
                constructor(callback) {{ observerCallback = callback; }}
                observe(target, options) {{
                    assert.equal(target, observedCard);
                    assert.deepEqual(options, {{ attributes: true, attributeFilter: ['hidden'] }});
                }}
                disconnect() {{ disconnected = true; }}
            }}
            const cleanup = lifecycle.installCreateSpotStartDefault({{
                documentObj: {{
                    getElementById(id) {{
                        if (id === 'create-spot-card') return observedCard;
                        if (id === 'spot-starts-input') return observedInput;
                        return null;
                    }},
                }},
                MutationObserverClass: FakeMutationObserver,
                now: () => Date.UTC(2026, 6, 21, 16, 45),
            }});
            assert.equal(observedInput.value, '');
            observedCard.hidden = false;
            observerCallback();
            assert.equal(observedInput.value, '2026-07-21T16:45');
            cleanup();
            assert.equal(disconnected, true);
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
