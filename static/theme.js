(function () {
    'use strict';

    const STORAGE_KEY = 'nimhunt-theme';
    const LIGHT_THEME = 'light';
    const DARK_THEME = 'dark';
    const TOGGLE_ID = 'theme-toggle';

    function storedTheme() {
        try {
            return window.localStorage.getItem(STORAGE_KEY) === DARK_THEME ? DARK_THEME : LIGHT_THEME;
        } catch (_err) {
            return LIGHT_THEME;
        }
    }

    function storeTheme(theme) {
        try {
            window.localStorage.setItem(STORAGE_KEY, theme);
        } catch (_err) {
            // Some embedded browsers disable storage. The theme still works
            // for the current page in that case.
        }
    }

    function togglePresentation(theme) {
        if (theme === DARK_THEME) {
            return {
                symbol: '☀',
                label: 'Switch to light mode',
            };
        }

        return {
            symbol: '◐',
            label: 'Switch to dark mode',
        };
    }

    function updateToggle(documentObj, theme) {
        const toggle = documentObj.getElementById(TOGGLE_ID);
        if (!toggle) return;

        const presentation = togglePresentation(theme);
        const symbol = toggle.querySelector('.theme-toggle-symbol');
        if (symbol) symbol.textContent = presentation.symbol;
        toggle.setAttribute('aria-label', presentation.label);
        toggle.setAttribute('title', presentation.label);
        toggle.dataset.tooltip = presentation.label;
    }

    function applyTheme(theme, { persist = false, documentObj = document } = {}) {
        const normalizedTheme = theme === DARK_THEME ? DARK_THEME : LIGHT_THEME;
        documentObj.documentElement.dataset.theme = normalizedTheme;
        documentObj.documentElement.style.colorScheme = normalizedTheme;
        updateToggle(documentObj, normalizedTheme);

        if (persist) storeTheme(normalizedTheme);
        return normalizedTheme;
    }

    function switchTheme(theme, { persist = false, documentObj = document } = {}) {
        const normalizedTheme = theme === DARK_THEME ? DARK_THEME : LIGHT_THEME;
        const reduceMotion = documentObj.defaultView?.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

        if (!reduceMotion && typeof documentObj.startViewTransition === 'function') {
            const transition = documentObj.startViewTransition(() => {
                applyTheme(normalizedTheme, { persist, documentObj });
            });

            // A cancelled navigation or browser interruption can reject this
            // promise. The theme itself has already been applied, so there is
            // nothing useful to report to the user in that case.
            transition.finished.catch(() => {});
            return normalizedTheme;
        }

        return applyTheme(normalizedTheme, { persist, documentObj });
    }

    function createToggle(documentObj = document) {
        if (documentObj.getElementById(TOGGLE_ID)) return documentObj.getElementById(TOGGLE_ID);

        const links = documentObj.querySelectorAll('.home-information-links > a');
        if (links.length < 4) return null;

        const toggle = documentObj.createElement('button');
        toggle.id = TOGGLE_ID;
        toggle.className = 'theme-toggle';
        toggle.type = 'button';

        const symbol = documentObj.createElement('span');
        symbol.className = 'theme-toggle-symbol';
        symbol.setAttribute('aria-hidden', 'true');
        toggle.appendChild(symbol);

        // The information footer is About · How To · FAQ · Roadmap. Insert
        // the theme control exactly in the middle, after the second link.
        links[1].after(toggle);

        toggle.addEventListener('click', () => {
            const currentTheme = documentObj.documentElement.dataset.theme === DARK_THEME
                ? DARK_THEME
                : LIGHT_THEME;
            switchTheme(currentTheme === DARK_THEME ? LIGHT_THEME : DARK_THEME, {
                persist: true,
                documentObj,
            });
        });

        updateToggle(documentObj, documentObj.documentElement.dataset.theme || LIGHT_THEME);
        return toggle;
    }

    function installThemeUi(documentObj = document) {
        createToggle(documentObj);
        updateToggle(documentObj, documentObj.documentElement.dataset.theme || LIGHT_THEME);
    }

    applyTheme(storedTheme());

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => installThemeUi(document), { once: true });
    } else {
        installThemeUi(document);
    }

    window.addEventListener('storage', (event) => {
        if (event.key !== STORAGE_KEY) return;
        applyTheme(event.newValue === DARK_THEME ? DARK_THEME : LIGHT_THEME);
    });
})();
