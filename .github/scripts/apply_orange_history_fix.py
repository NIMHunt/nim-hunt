from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PAGE_LIFECYCLE = r'''/*
 * Shared browser lifecycle repairs.
 *
 * Safari and embedded WKWebViews may restore a page with its previous DOM and
 * JavaScript memory intact while incorrectly reporting `pageshow.persisted` as
 * false. The card marker lives only in the cached document itself, so it is
 * naturally scoped to that exact history entry and cannot leak into a fresh
 * reload or a later visit to the same URL.
 */

const BACKDROP_SELECTOR = '.notice-backdrop';
const OPEN_BACKDROP_SELECTOR = '.notice-backdrop:not([hidden])';
const CARD_NAVIGATION_MARKER = 'data-nimhunt-card-navigation-pending';
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

function openBackdrops(documentObj) {
    return [...(documentObj?.querySelectorAll?.(OPEN_BACKDROP_SELECTOR) || [])];
}

export function isBackForwardRestore(event, performanceObj = globalThis.performance) {
    return Boolean(event?.persisted) || navigationType(performanceObj) === 'back_forward';
}

export function repairOpenCardsAfterHistoryRestore({
    event,
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
} = {}) {
    if (!windowObj || !documentObj) return false;

    const markedHistoryEntry = hasCardNavigationMarker(documentObj);
    const restoredBackdrops = openBackdrops(documentObj);
    const browserReportsHistoryReturn = isBackForwardRestore(event, performanceObj);

    // A marker retained in the cached DOM belongs to this exact history entry.
    // Trust it even when WKWebView reports both browser-history signals wrongly.
    // Without a marker, require a history-return signal and a visibly stale card.
    if (!markedHistoryEntry && (!browserReportsHistoryReturn || restoredBackdrops.length === 0)) {
        return false;
    }

    clearCardNavigationMarker(documentObj);
    // Hide first so the stale card does not flash while the fresh page loads.
    for (const backdrop of restoredBackdrops) backdrop.hidden = true;
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
        // location in the same task. Delay clearing so navigation preserves the
        // marker inside the old cached history entry. A normal manual close stays
        // on the page long enough for this timer to remove it.
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
    windowObj.addEventListener('pageshow', pageshowHandler);

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

DISCLAIMER_CSS = r'''/*
 * Home disclaimer presentation.
 *
 * The notice uses the same Nimiq action-card structure as the Home navigation.
 * Only its orange colour, non-interactive behaviour, icon row and extra wrapping
 * space differ from the ordinary buttons.
 */
.project-disclaimer-note {
    height: auto !important;
    min-height: 112px;
    cursor: default !important;
    pointer-events: none;
    user-select: text;
    white-space: normal !important;
    overflow: visible !important;
    background: var(--nh-warning) !important;
}

.project-disclaimer-note .action-detail {
    white-space: normal !important;
    overflow-wrap: anywhere;
}

.project-disclaimer-title {
    display: flex !important;
    align-items: center;
    gap: 0.55rem;
}

.project-disclaimer-icon {
    width: 1em;
    height: 1em;
    flex: 0 0 auto;
    fill: currentColor;
}

.project-disclaimer-note::before,
.project-disclaimer-note::after {
    display: none !important;
    content: none !important;
}
'''

HOME_TEST = r'''from __future__ import annotations

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
        self.assertIn("white-space: normal", css_source)
        self.assertIn("overflow-wrap: anywhere", css_source)
        self.assertNotIn("linear-gradient", css_source)
        self.assertIn("shell.prepend(createNetworkModeBanner(label))", banner_source)


if __name__ == "__main__":
    unittest.main()
'''


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one occurrence in {path}, found {count}")
    path.write_text(source.replace(old, new), encoding="utf-8")


def main() -> None:
    (ROOT / "static" / "page_lifecycle.js").write_text(PAGE_LIFECYCLE, encoding="utf-8")
    (ROOT / "static" / "disclaimer_button.css").write_text(DISCLAIMER_CSS, encoding="utf-8")
    (ROOT / "tests" / "test_home_disclaimer.py").write_text(HOME_TEST, encoding="utf-8")

    shell = ROOT / "templates" / "_home_shell.html"
    old_block = '''        <section class="project-disclaimer-note nq-button green action-button primary-action" role="note" aria-labelledby="project-disclaimer-title">
            <h2 id="project-disclaimer-title" class="action-title">Disclaimer</h2>
            <p class="action-detail">This project is new and may still have some issues. Please use this mini-app cautiously, and never spend more than you can afford to lose. Have fun!</p>
        </section>'''
    new_block = '''        <section class="project-disclaimer-note nq-button gold action-button primary-action" role="note" aria-labelledby="project-disclaimer-title">
            <span id="project-disclaimer-title" class="action-title project-disclaimer-title">
                <svg class="project-disclaimer-icon" viewBox="0 0 17 16" aria-hidden="true" focusable="false"><use href="/static/nimiq-style.icons.svg#nq-alert-triangle"></use></svg>
                <span>Disclaimer</span>
                <svg class="project-disclaimer-icon" viewBox="0 0 17 16" aria-hidden="true" focusable="false"><use href="/static/nimiq-style.icons.svg#nq-alert-triangle"></use></svg>
            </span>
            <span class="action-detail">This project is new and may still have some issues. Please use this mini-app cautiously, and never spend more than you can afford to lose. Have fun!</span>
        </section>'''
    replace_once(shell, old_block, new_block)
    replace_once(
        shell,
        "/static/disclaimer_button.css?v=disclaimer-button-v1-20260722",
        "/static/disclaimer_button.css?v=disclaimer-button-v2-20260722",
    )

    old_version = "history-card-v1-20260721"
    new_version = "history-card-v3-20260722"
    version_paths = [ROOT / "static" / "localise_page.js"] + sorted((ROOT / "templates").glob("*.html"))
    changed = 0
    for path in version_paths:
        source = path.read_text(encoding="utf-8")
        if old_version not in source:
            continue
        path.write_text(source.replace(old_version, new_version), encoding="utf-8")
        changed += 1
    if changed < 8:
        raise RuntimeError(f"Expected lifecycle version in at least 8 files, changed {changed}")

    public_html = ROOT / "public_html.py"
    replace_once(
        public_html,
        '_ASSET_VERSION = "presentation-spots-v1-20260722"',
        '_ASSET_VERSION = "orange-disclaimer-history-v1-20260722"',
    )

    lifecycle_test = ROOT / "tests" / "test_page_lifecycle.py"
    source = lifecycle_test.read_text(encoding="utf-8")
    old_expectations = '''            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
            }}), false);
            assert.equal(openBackdrop.hidden, false);
            assert.equal(reloadCount, 2);

            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: true }},
                windowObj,
                documentObj,
                performanceObj: {{ getEntriesByType: () => [{{ type: 'navigate' }}] }},
            }}), false);
            assert.equal(openBackdrop.hidden, false);
            assert.equal(reloadCount, 2);'''
    new_expectations = '''            // Navigation timing can identify a history return even when persisted is false.
            openBackdrop.hidden = false;
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: backForwardPerformance,
            }}), true);
            assert.equal(openBackdrop.hidden, true);
            assert.equal(reloadCount, 3);

            // The cached DOM marker is scoped to this exact history entry and is
            // trusted even when both browser signals claim this is a normal load.
            documentElement.setAttribute(markerName, '1');
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: {{ getEntriesByType: () => [{{ type: 'navigate' }}] }},
            }}), true);
            assert.equal(documentElement.hasAttribute(markerName), false);
            assert.equal(reloadCount, 4);

            // A fresh document has no marker, so the same unreliable signals do
            // nothing and cannot create a reload loop.
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: {{ getEntriesByType: () => [{{ type: 'navigate' }}] }},
            }}), false);
            assert.equal(reloadCount, 4);'''
    if old_expectations not in source:
        raise RuntimeError("Could not locate lifecycle expectation block")
    lifecycle_test.write_text(source.replace(old_expectations, new_expectations), encoding="utf-8")


if __name__ == "__main__":
    main()
