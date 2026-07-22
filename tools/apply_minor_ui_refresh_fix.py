from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one match, found {count}: {old[:80]!r}"
        )
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


lifecycle = Path("static/page_lifecycle.js")
text = lifecycle.read_text(encoding="utf-8")
old = """export function repairOpenCardsAfterHistoryRestore({
    event,
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
} = {}) {"""
new = """export function repairOpenCardsAfterHistoryRestore({
    event,
    windowObj = globalThis.window,
    documentObj = globalThis.document,
    performanceObj = globalThis.performance,
    historyEntryWasHidden = false,
} = {}) {"""
if text.count(old) != 1:
    raise SystemExit("page_lifecycle.js: repair signature changed unexpectedly")
text = text.replace(old, new, 1)

old = """    // A marker retained in the cached DOM belongs to this exact history entry.
    // Trust it even when WKWebView reports both browser-history signals wrongly.
    // Without a marker, require a history-return signal and a visibly stale card.
    if (!markedHistoryEntry && (!browserReportsHistoryReturn || restoredBackdrops.length === 0)) {
        return false;
    }"""
new = """    // A marker retained in the cached DOM is trustworthy only after this same
    // JavaScript page instance has actually been hidden. This distinguishes a
    // genuine history restoration from the initial pageshow event, where an
    // immediately denied location request may already have opened a notice.
    const trustedMarkedHistoryEntry = markedHistoryEntry
        && (historyEntryWasHidden || browserReportsHistoryReturn);
    if (!trustedMarkedHistoryEntry
        && (!browserReportsHistoryReturn || restoredBackdrops.length === 0)) {
        return false;
    }"""
if text.count(old) != 1:
    raise SystemExit("page_lifecycle.js: marker condition changed unexpectedly")
text = text.replace(old, new, 1)

old = """    const pageshowHandler = (event) => repairOpenCardsAfterHistoryRestore({
        event,
        windowObj,
        documentObj,
        performanceObj,
    });
    windowObj.addEventListener('pageshow', pageshowHandler);"""
new = """    let historyEntryWasHidden = false;
    const pagehideHandler = () => {
        historyEntryWasHidden = true;
    };
    const pageshowHandler = (event) => {
        const wasHidden = historyEntryWasHidden;
        historyEntryWasHidden = false;
        return repairOpenCardsAfterHistoryRestore({
            event,
            windowObj,
            documentObj,
            performanceObj,
            historyEntryWasHidden: wasHidden,
        });
    };
    windowObj.addEventListener('pagehide', pagehideHandler);
    windowObj.addEventListener('pageshow', pageshowHandler);"""
if text.count(old) != 1:
    raise SystemExit("page_lifecycle.js: handler block changed unexpectedly")
text = text.replace(old, new, 1)

old = """        observer?.disconnect();
        windowObj.removeEventListener('pageshow', pageshowHandler);"""
new = """        observer?.disconnect();
        windowObj.removeEventListener('pagehide', pagehideHandler);
        windowObj.removeEventListener('pageshow', pageshowHandler);"""
if text.count(old) != 1:
    raise SystemExit("page_lifecycle.js: cleanup block changed unexpectedly")
lifecycle.write_text(text.replace(old, new, 1), encoding="utf-8")

replace_once(
    "static/spot_ui.js",
    "        titleEl.append(document.createTextNode(' '), lockIcon);",
    "        titleEl.append(lockIcon);",
)

css = Path("static/home.css")
text = css.read_text(encoding="utf-8")
old = """.spot-list-title.is-truncated-title .spot-title-text {
    display: block;
    flex: 1 1 auto;"""
new = """.spot-list-title.is-truncated-title .spot-title-text {
    display: block;
    flex: 0 1 auto;"""
if text.count(old) != 1:
    raise SystemExit("home.css: truncated title flex block changed unexpectedly")
text = text.replace(old, new, 1)
old = """.spot-list-title .spot-title-lock-icon-wrap,
#claim-page-title .spot-title-lock-icon-wrap {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1em;
    height: 1em;
    margin-left: 0.25em;"""
new = """.spot-list-title .spot-title-lock-icon-wrap,
#claim-page-title .spot-title-lock-icon-wrap {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1em;
    height: 1em;
    margin-left: 0;"""
if text.count(old) != 1:
    raise SystemExit("home.css: lock icon margin block changed unexpectedly")
css.write_text(text.replace(old, new, 1), encoding="utf-8")

replace_once(
    "static/disclaimer_button.css",
    "    min-height: 112px;\n",
    "",
)

for file in Path("static").glob("*.js"):
    value = file.read_text(encoding="utf-8")
    value = value.replace(
        "long-titles-v1-20260722", "long-titles-v2-20260722"
    )
    value = value.replace(
        "history-card-v3-20260722", "history-card-v4-20260722"
    )
    file.write_text(value, encoding="utf-8")

for file in Path("templates").glob("*.html"):
    value = file.read_text(encoding="utf-8")
    value = value.replace(
        "history-card-v3-20260722", "history-card-v4-20260722"
    )
    value = value.replace(
        "disclaimer-button-v2-20260722", "disclaimer-button-v3-20260722"
    )
    file.write_text(value, encoding="utf-8")

replace_once(
    "public_html.py",
    '_ASSET_VERSION = "presentation-spots-v1-20260722"',
    '_ASSET_VERSION = "desktop-location-lock-v1-20260722"',
)

test = Path("tests/test_presentation_spots.py")
text = test.read_text(encoding="utf-8")
old = """        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn("white-space: nowrap", css)"""
new = """        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn("white-space: nowrap", css)
        self.assertIn("titleEl.append(lockIcon);", source)
        self.assertNotIn("document.createTextNode(' '), lockIcon", source)
        self.assertIn("flex: 0 1 auto;", css)
        self.assertIn("margin-left: 0;", css)"""
if text.count(old) != 1:
    raise SystemExit("test_presentation_spots.py: expected assertion block missing")
test.write_text(text.replace(old, new, 1), encoding="utf-8")

test = Path("tests/test_home_disclaimer.py")
text = test.read_text(encoding="utf-8")
old = '        self.assertIn("height: auto", css_source)\n'
new = (
    '        self.assertIn("height: auto", css_source)\n'
    '        self.assertNotIn("min-height: 112px", css_source)\n'
)
if text.count(old) != 1:
    raise SystemExit("test_home_disclaimer.py: height assertion missing")
test.write_text(text.replace(old, new, 1), encoding="utf-8")

test = Path("tests/test_page_lifecycle.py")
text = test.read_text(encoding="utf-8")
old = """            // The cached DOM marker is scoped to this exact history entry and is
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
            assert.equal(reloadCount, 4);"""
new = """            // A marker that appears before the initial pageshow—for example when
            // location permission is denied immediately—must not be mistaken for
            // a restored history entry.
            documentElement.setAttribute(markerName, '1');
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: {{ getEntriesByType: () => [{{ type: 'navigate' }}] }},
                historyEntryWasHidden: false,
            }}), false);
            assert.equal(documentElement.hasAttribute(markerName), true);
            assert.equal(reloadCount, 3);

            // Once the same page instance has received pagehide, its cached DOM
            // marker is trustworthy even when both browser signals are wrong.
            assert.equal(lifecycle.repairOpenCardsAfterHistoryRestore({{
                event: {{ persisted: false }},
                windowObj,
                documentObj,
                performanceObj: {{ getEntriesByType: () => [{{ type: 'navigate' }}] }},
                historyEntryWasHidden: true,
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
                historyEntryWasHidden: false,
            }}), false);
            assert.equal(reloadCount, 4);"""
if text.count(old) != 1:
    raise SystemExit("test_page_lifecycle.py: direct marker block changed unexpectedly")
text = text.replace(old, new, 1)

old = """            let trackedReloadCount = 0;
            let pageshowHandler = null;
            const trackedWindow = {{
                location: {{ reload() {{ trackedReloadCount += 1; }} }},
                addEventListener(name, handler) {{
                    if (name === 'pageshow') pageshowHandler = handler;
                }},
                removeEventListener(name, handler) {{
                    if (name === 'pageshow' && pageshowHandler === handler) pageshowHandler = null;
                }},"""
new = """            let trackedReloadCount = 0;
            let pagehideHandler = null;
            let pageshowHandler = null;
            const trackedWindow = {{
                location: {{ reload() {{ trackedReloadCount += 1; }} }},
                addEventListener(name, handler) {{
                    if (name === 'pagehide') pagehideHandler = handler;
                    if (name === 'pageshow') pageshowHandler = handler;
                }},
                removeEventListener(name, handler) {{
                    if (name === 'pagehide' && pagehideHandler === handler) pagehideHandler = null;
                    if (name === 'pageshow' && pageshowHandler === handler) pageshowHandler = null;
                }},"""
if text.count(old) != 1:
    raise SystemExit("test_page_lifecycle.py: tracked window block changed unexpectedly")
text = text.replace(old, new, 1)

old = """            assert.equal(typeof pageshowHandler, 'function');
            for (const callback of [...timers.values()]) callback();"""
new = """            assert.equal(typeof pagehideHandler, 'function');
            assert.equal(typeof pageshowHandler, 'function');
            for (const callback of [...timers.values()]) callback();"""
if text.count(old) != 1:
    raise SystemExit("test_page_lifecycle.py: handler assertion block changed unexpectedly")
text = text.replace(old, new, 1)

old = """            trackedBackdrop.hidden = false;
            lifecycleObserverCallback();
            assert.equal(trackedDocumentElement.hasAttribute(markerName), true);

            // Hide and navigate in the same task: the delayed manual-close clear
            // has not fired, so a restored page is still recognised as stale.
            trackedBackdrop.hidden = true;
            lifecycleObserverCallback();
            assert.equal(trackedDocumentElement.hasAttribute(markerName), true);
            pageshowHandler({{ persisted: true }});
            assert.equal(trackedReloadCount, 1);"""
new = """            trackedBackdrop.hidden = false;
            lifecycleObserverCallback();
            assert.equal(trackedDocumentElement.hasAttribute(markerName), true);

            // An immediately denied location request can open a notice before the
            // initial pageshow. Without a preceding pagehide, it must not reload.
            pageshowHandler({{ persisted: false }});
            assert.equal(trackedReloadCount, 0);
            assert.equal(trackedDocumentElement.hasAttribute(markerName), true);

            // Hide and navigate in the same task: the delayed manual-close clear
            // has not fired, so a restored page is still recognised as stale.
            trackedBackdrop.hidden = true;
            lifecycleObserverCallback();
            assert.equal(trackedDocumentElement.hasAttribute(markerName), true);
            pagehideHandler();
            pageshowHandler({{ persisted: false }});
            assert.equal(trackedReloadCount, 1);"""
if text.count(old) != 1:
    raise SystemExit("test_page_lifecycle.py: tracked restore block changed unexpectedly")
text = text.replace(old, new, 1)

old = """            assert.equal(lifecycleObserverDisconnected, true);
            assert.equal(pageshowHandler, null);"""
new = """            assert.equal(lifecycleObserverDisconnected, true);
            assert.equal(pagehideHandler, null);
            assert.equal(pageshowHandler, null);"""
if text.count(old) != 1:
    raise SystemExit("test_page_lifecycle.py: cleanup assertions changed unexpectedly")
test.write_text(text.replace(old, new, 1), encoding="utf-8")
