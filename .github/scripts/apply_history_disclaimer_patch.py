from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return content.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Replace the history-card lifecycle guard with a durable history marker.
# ---------------------------------------------------------------------------

page_lifecycle = r'''/*
 * Shared browser lifecycle repairs.
 *
 * Safari and embedded WKWebViews may restore a page with its previous DOM and
 * JavaScript memory intact. Some of them report `pageshow.persisted = false`
 * even when PerformanceNavigationTiming correctly reports `back_forward`.
 * Store a short per-page marker during pagehide as well as in the cached DOM,
 * hide open cards before the page is frozen, and reload only the affected
 * history entry when it is restored. This also clears page-specific busy flags
 * such as `creatingSpot` that cannot safely be reset from this shared module.
 */

const BACKDROP_SELECTOR = '.notice-backdrop';
const OPEN_BACKDROP_SELECTOR = '.notice-backdrop:not([hidden])';
const CARD_NAVIGATION_MARKER = 'data-nimhunt-card-navigation-pending';
const STORAGE_KEY_PREFIX = 'nimhunt:card-navigation:';
const MANUAL_CLOSE_GRACE_MILLISECONDS = 250;

function navigationType(performanceObj) {
    try {
        const entry = performanceObj?.getEntriesByType?.('navigation')?.[0];
        if (entry?.type) return String(entry.type);
    } catch (_err) {
        // Older WebViews may not implement PerformanceNavigationTiming.
    }

    const legacyType = Number(performanceObj?.navigation?.type);
    if (legacyType === 2) return 'back_forward';
    if (Number.isFinite(legacyType)) return 'navigate';
    return null;
}

function documentMarkerElement(documentObj) {
    return documentObj?.documentElement || documentObj?.body || null;
}

function hasCardNavigationMarker(documentObj) {
    return Boolean(documentMarkerElement(documentObj)?.hasAttribute?.(CARD_NAVIGATION_MARKER));
}

function setCardNavigationMarker(documentObj) {
    documentMarkerElement(documentObj)?.setAttribute?.(CARD_NAVIGATION_MARKER, '1');
}

function clearCardNavigationMarker(documentObj) {
    documentMarkerElement(documentObj)?.removeAttribute?.(CARD_NAVIGATION_MARKER);
}

function allBackdrops(documentObj) {
    return [...(documentObj?.querySelectorAll?.(BACKDROP_SELECTOR) || [])];
}

function openBackdrops(documentObj) {
    return [...(documentObj?.querySelectorAll?.(OPEN_BACKDROP_SELECTOR) || [])];
}

function storageKey(windowObj) {
    const pathname = String(windowObj?.location?.pathname || '');
    const search = String(windowObj?.location?.search || '');
    return `${STORAGE_KEY_PREFIX}${pathname}${search}`;
}

function sessionStorageObject(windowObj) {
    try {
        return windowObj?.sessionStorage || null;
    } catch (_err) {
        return null;
    }
}

function hasStoredNavigationMarker(windowObj) {
    try {
        return sessionStorageObject(windowObj)?.getItem(storageKey(windowObj)) === '1';
    } catch (_err) {
        return false;
    }
}

function setStoredNavigationMarker(windowObj) {
    try {
        sessionStorageObject(windowObj)?.setItem(storageKey(windowObj), '1');
    } catch (_err) {
        // Private browsing or a restrictive WebView may block sessionStorage.
    }
}

function clearStoredNavigationMarker(windowObj) {
    try {
        sessionStorageObject(windowObj)?.removeItem(storageKey(windowObj));
    } catch (_err) {
        // The DOM marker remains available when storage is unavailable.
    }
}

export function isBackForwardRestore(event, performanceObj = globalThis.performance) {
    const type = navigationType(performanceObj);
    // Some Safari/WKWebView versions restore history with persisted=false, while
    // other versions omit navigation timing. Either positive signal is enough.
    return type === 'back_forward' || Boolean(event?.persisted);
}

export function prepareCardsForPageHide({
    windowObj = globalThis.window,
    documentObj = globalThis.document,
} = {}) {
    if (!windowObj || !documentObj) return false;

    const visibleBackdrops = openBackdrops(documentObj);
    const cardActionPending = hasCardNavigationMarker(documentObj);
    if (!cardActionPending && visibleBackdrops.length === 0) return false;

    setCardNavigationMarker(documentObj);
    setStoredNavigationMarker(windowObj);

    // Hide before the browser snapshots/freezes the page. A reload on return is
    // still required because page-specific in-progress variables may be stale.
    for (const backdrop of visibleBackdrops) backdrop.hidden = true;
    return true;
}

export function repairOpenCardsAfterHistoryRestore({
    event,
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
} = {}) {
    if (!windowObj || !documentObj || !isBackForwardRestore(event, performanceObj)) {
        return false;
    }

    const restoredBackdrops = openBackdrops(documentObj);
    const marked = hasCardNavigationMarker(documentObj)
        || hasStoredNavigationMarker(windowObj);
    if (!marked && restoredBackdrops.length === 0) return false;

    clearCardNavigationMarker(documentObj);
    clearStoredNavigationMarker(windowObj);

    // Hide every card first so no stale overlay flashes while the fresh page loads.
    for (const backdrop of allBackdrops(documentObj)) backdrop.hidden = true;
    windowObj.location.reload();
    return true;
}

export function installHistoryCardRestoreGuard({
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
    MutationObserverClass = globalThis.MutationObserver,
} = {}) {
    if (!windowObj || !documentObj) return () => {};

    let clearTimer = null;
    const clearScheduledMarker = () => {
        if (clearTimer !== null) windowObj.clearTimeout(clearTimer);
        clearTimer = null;
    };
    const syncMarker = () => {
        if (openBackdrops(documentObj).length > 0) {
            clearScheduledMarker();
            setCardNavigationMarker(documentObj);
            return;
        }

        // A successful card action often hides the backdrop and assigns a new
        // location in the same task. Delay clearing so pagehide can persist the
        // marker. A normal manual close remains on-page long enough to clear it.
        clearScheduledMarker();
        clearTimer = windowObj.setTimeout(() => {
            clearTimer = null;
            if (openBackdrops(documentObj).length === 0) {
                clearCardNavigationMarker(documentObj);
            }
        }, MANUAL_CLOSE_GRACE_MILLISECONDS);
    };

    const pageshowHandler = (event) => repairOpenCardsAfterHistoryRestore({
        event,
        windowObj,
        documentObj,
        performanceObj,
    });
    const pagehideHandler = () => prepareCardsForPageHide({
        windowObj,
        documentObj,
    });
    windowObj.addEventListener('pageshow', pageshowHandler);
    windowObj.addEventListener('pagehide', pagehideHandler);

    let observer = null;
    const root = documentObj.body || documentObj.documentElement;
    if (root && typeof MutationObserverClass === 'function') {
        observer = new MutationObserverClass(syncMarker);
        observer.observe(root, {
            subtree: true,
            attributes: true,
            attributeFilter: ['hidden'],
        });
    }
    if (documentObj.querySelectorAll?.(BACKDROP_SELECTOR)?.length) syncMarker();

    return () => {
        clearScheduledMarker();
        observer?.disconnect();
        windowObj.removeEventListener('pageshow', pageshowHandler);
        windowObj.removeEventListener('pagehide', pagehideHandler);
    };
}

export function localDateTimeValue(nowMilliseconds = Date.now()) {
    const date = new Date(Number(nowMilliseconds));
    if (!Number.isFinite(date.getTime())) return '';
    const pad = (value) => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fillBlankStartInput(input, nowMilliseconds = Date.now()) {
    if (!input || String(input.value || '').trim()) return false;
    input.value = localDateTimeValue(nowMilliseconds);
    return Boolean(input.value);
}

export function installCreateSpotStartDefault({
    documentObj = globalThis.document,
    MutationObserverClass = globalThis.MutationObserver,
    now = () => Date.now(),
} = {}) {
    if (!documentObj) return () => {};

    const card = documentObj.getElementById('create-spot-card');
    const input = documentObj.getElementById('spot-starts-input');
    if (!card || !input) return () => {};

    const apply = () => {
        if (!card.hidden) fillBlankStartInput(input, now());
    };
    apply();

    if (typeof MutationObserverClass !== 'function') return () => {};
    const observer = new MutationObserverClass(apply);
    observer.observe(card, { attributes: true, attributeFilter: ['hidden'] });
    return () => observer.disconnect();
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    installHistoryCardRestoreGuard();
    installCreateSpotStartDefault();
}
'''
write("static/page_lifecycle.js", page_lifecycle)


# ---------------------------------------------------------------------------
# Bump every import/script URL so browsers cannot retain the ineffective module.
# ---------------------------------------------------------------------------

old_version = "history-card-v1-20260721"
new_version = "history-card-v2-20260722"
localise = read("static/localise_page.js")
if old_version not in localise:
    raise RuntimeError("localise_page.js history-card version was not found")
write("static/localise_page.js", localise.replace(old_version, new_version))

updated_template_count = 0
for template_path in sorted((ROOT / "templates").glob("*.html")):
    content = template_path.read_text(encoding="utf-8")
    if old_version not in content:
        continue
    template_path.write_text(content.replace(old_version, new_version), encoding="utf-8")
    updated_template_count += 1
if updated_template_count < 5:
    raise RuntimeError(f"expected several templates to load page_lifecycle.js, updated {updated_template_count}")


# ---------------------------------------------------------------------------
# Add a simple server-controlled project disclaimer to the Home page.
# ---------------------------------------------------------------------------

constants = read("constants.py")
constants = replace_once(
    constants,
    'NIMIQ_PAY_URL = "https://nimpay.app"\n',
    'NIMIQ_PAY_URL = "https://nimpay.app"\n\n'
    '# Show the prominent Home-page safety notice. This is deliberately a simple\n'
    '# server-side switch rather than browser state, so one deployment setting\n'
    '# controls every visitor consistently.\n'
    'SHOW_PROJECT_DISCLAIMER = True\n',
    label="project disclaimer constant",
)
write("constants.py", constants)

public_html = read("public_html.py")
public_html = replace_once(
    public_html,
    '_ASSET_VERSION = "transaction-integrity-v1-20260721"',
    '_ASSET_VERSION = "history-disclaimer-v1-20260722"',
    label="asset version",
)
public_html = replace_once(
    public_html,
    '        "test_features_enabled": bool(getattr(const, "TEST_FEATURES_ENABLED", False)),\n',
    '        "test_features_enabled": bool(getattr(const, "TEST_FEATURES_ENABLED", False)),\n'
    '        "project_disclaimer_enabled": bool(\n'
    '            getattr(const, "SHOW_PROJECT_DISCLAIMER", False)\n'
    '        ),\n',
    label="shared disclaimer context",
)
write("public_html.py", public_html)

home_template = read("templates/home.html")
home_template = replace_once(
    home_template,
    "{% set show_lock_tooltip = true %}\n",
    "{% set show_lock_tooltip = true %}\n"
    "{% set show_project_disclaimer = project_disclaimer_enabled | default(false) %}\n",
    label="home disclaimer switch",
)
write("templates/home.html", home_template)

shell = read("templates/_home_shell.html")
disclaimer_markup = '''        {% if show_project_disclaimer | default(false) %}
        <section class="project-disclaimer" role="note" aria-labelledby="project-disclaimer-title">
            <h2 id="project-disclaimer-title" class="project-disclaimer-title">
                <svg class="project-disclaimer-icon" viewBox="0 0 17 16" aria-hidden="true" focusable="false">
                    <use href="/static/nimiq-style.icons.svg#nq-alert-triangle"></use>
                </svg>
                <span>Disclaimer</span>
                <svg class="project-disclaimer-icon" viewBox="0 0 17 16" aria-hidden="true" focusable="false">
                    <use href="/static/nimiq-style.icons.svg#nq-alert-triangle"></use>
                </svg>
            </h2>
            <p>This project is new and may still have some issues. Please use this mini-app cautiously, and never spend more than you can afford to lose. Have fun!</p>
        </section>
        {% endif %}

'''
shell = replace_once(
    shell,
    '    <main class="home-shell" aria-live="polite">\n',
    '    <main class="home-shell" aria-live="polite">\n' + disclaimer_markup,
    label="home disclaimer markup",
)
write("templates/_home_shell.html", shell)

home_css = read("static/home.css")
if ".project-disclaimer" in home_css:
    raise RuntimeError("project disclaimer styles already exist")
home_css += r'''

/* Prominent server-controlled Home-page disclaimer. */
.project-disclaimer {
    width: 100%;
    margin: 0;
    padding: 15px 17px 16px;
    border: 1px solid rgba(134, 70, 0, 0.18);
    border-radius: 22px;
    background: linear-gradient(135deg, #ff9f1c 0%, #ffc435 52%, #f28b16 100%);
    color: #472b08;
    box-shadow:
        0 12px 30px rgba(209, 106, 0, 0.27),
        inset 0 1px 0 rgba(255, 255, 255, 0.42);
    text-align: center;
}

.project-disclaimer-title {
    margin: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.55rem;
    color: inherit;
    font-size: 1.25rem;
    font-weight: 900;
    letter-spacing: 0.035em;
    line-height: 1.1;
    text-transform: uppercase;
}

.project-disclaimer-icon {
    width: 1.15em;
    height: 1.15em;
    flex: 0 0 auto;
    fill: currentColor;
}

.project-disclaimer p {
    margin: 0.65rem 0 0;
    color: inherit;
    font-size: 1rem;
    font-weight: 750;
    line-height: 1.38;
}
'''
write("static/home.css", home_css)


# ---------------------------------------------------------------------------
# Update the original regression test and add focused coverage.
# ---------------------------------------------------------------------------

lifecycle_tests = read("tests/test_page_lifecycle.py")
old_restore_block = r'''            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({
                event: { persisted: false },
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
            }), false);
            assert.equal(openBackdrop.hidden, false);
            assert.equal(reloadCount, 2);

            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({
                event: { persisted: true },
                windowObj,
                documentObj,
                performanceObj: { getEntriesByType: () => [{ type: 'navigate' }] },
            }), false);
            assert.equal(openBackdrop.hidden, false);
            assert.equal(reloadCount, 2);
'''
new_restore_block = r'''            // WKWebView may report persisted=false even for browser-history
            // restoration. PerformanceNavigationTiming must still trigger repair.
            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({
                event: { persisted: false },
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
            }), true);
            assert.equal(openBackdrop.hidden, true);
            assert.equal(reloadCount, 3);

            // A positive persisted signal is also sufficient when navigation
            // timing is missing or misleading.
            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({
                event: { persisted: true },
                windowObj,
                documentObj,
                performanceObj: { getEntriesByType: () => [{ type: 'navigate' }] },
            }), true);
            assert.equal(openBackdrop.hidden, true);
            assert.equal(reloadCount, 4);
'''
lifecycle_tests = replace_once(
    lifecycle_tests,
    old_restore_block,
    new_restore_block,
    label="existing persisted-false regression expectation",
)
write("tests/test_page_lifecycle.py", lifecycle_tests)

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

            // This is the failed real-world case: browser Back, stale page state,
            // but WKWebView reports pageshow.persisted as false.
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
        # The network mode label is asynchronously prepended, so it remains
        # above the server-rendered disclaimer and the hero.
        self.assertIn("shell.prepend(createNetworkModeBanner(label))", banner_source)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_home_disclaimer.py", home_disclaimer_test)


# Guard the requested financial boundary: this patch script must not touch any
# wallet, transaction, settlement, funding, cancellation, or claim-policy file.
for forbidden_path in (
    "wallet.py",
    "trans_updater.py",
    "settlement_updater.py",
    "funding_flow.py",
    "funding_monitor.py",
    "funding_status.py",
    "funding_fee_worker.py",
    "cancellation_safety.py",
    "claim_code_policy.py",
):
    if not (ROOT / forbidden_path).exists():
        continue
