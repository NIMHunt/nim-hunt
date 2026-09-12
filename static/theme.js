(function () {
    'use strict';

    const STORAGE_KEY = 'nimhunt-theme';
    const LIGHT_THEME = 'light';
    const DARK_THEME = 'dark';
    const SYSTEM_THEME_QUERY = '(prefers-color-scheme: dark)';
    const TOGGLE_ID = 'theme-toggle';

    function explicitStoredTheme() {
        try {
            const stored = window.localStorage.getItem(STORAGE_KEY);
            if (stored === DARK_THEME || stored === LIGHT_THEME) return stored;
        } catch (_err) {
            // Some embedded browsers disable storage. Fall through to the
            // system preference so the initial theme still has a useful default.
        }
        return null;
    }

    function systemTheme() {
        try {
            return window.matchMedia?.(SYSTEM_THEME_QUERY).matches ? DARK_THEME : LIGHT_THEME;
        } catch (_err) {
            return LIGHT_THEME;
        }
    }

    function preferredTheme() {
        return explicitStoredTheme() || systemTheme();
    }

    function storeTheme(theme) {
        try {
            window.localStorage.setItem(STORAGE_KEY, theme);
        } catch (_err) {
            // Some embedded browsers disable storage. The theme still works
            // for the current page in that case.
        }
    }

    function toggleLabel(theme) {
        return theme === DARK_THEME
            ? 'Switch to light mode'
            : 'Switch to dark mode';
    }

    function updateToggle(documentObj, theme) {
        const toggle = documentObj.getElementById(TOGGLE_ID);
        if (!toggle) return;

        const label = toggleLabel(theme);
        toggle.setAttribute('aria-label', label);
        toggle.setAttribute('title', label);
        toggle.dataset.tooltip = label;
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

    function buildToggle(documentObj) {
        const toggle = documentObj.createElement('button');
        toggle.id = TOGGLE_ID;
        toggle.className = 'theme-toggle';
        toggle.type = 'button';

        const symbol = documentObj.createElement('span');
        symbol.className = 'theme-toggle-symbol';
        symbol.setAttribute('aria-hidden', 'true');
        toggle.appendChild(symbol);
        return toggle;
    }

    function bindToggle(toggle, documentObj) {
        if (toggle.dataset.themeToggleBound === 'true') return toggle;
        toggle.dataset.themeToggleBound = 'true';

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

    function createToggle(documentObj = document) {
        const existingToggle = documentObj.getElementById(TOGGLE_ID);
        if (existingToggle) return bindToggle(existingToggle, documentObj);

        const adminHeader = documentObj.querySelector('.admin-header');
        if (adminHeader) {
            const toggle = buildToggle(documentObj);
            toggle.classList.add('admin-theme-toggle');

            const signOutForm = adminHeader.querySelector('form[action="/admin/logout"]');
            if (signOutForm) {
                const actions = documentObj.createElement('div');
                actions.className = 'admin-header-actions';
                signOutForm.before(actions);
                actions.append(toggle, signOutForm);
            } else {
                adminHeader.appendChild(toggle);
            }
            return bindToggle(toggle, documentObj);
        }

        const loginCard = documentObj.querySelector('.admin-login-card');
        if (loginCard) {
            const toggle = buildToggle(documentObj);
            toggle.classList.add('admin-theme-toggle');

            const row = documentObj.createElement('div');
            row.className = 'admin-login-theme-row';
            row.appendChild(toggle);
            loginCard.prepend(row);
            return bindToggle(toggle, documentObj);
        }

        return null;
    }

    function installThemeUi(documentObj = document) {
        createToggle(documentObj);
        updateToggle(documentObj, documentObj.documentElement.dataset.theme || LIGHT_THEME);
    }

    function installSystemThemeListener() {
        let mediaQuery;
        try {
            mediaQuery = window.matchMedia?.(SYSTEM_THEME_QUERY);
        } catch (_err) {
            return;
        }
        if (!mediaQuery) return;

        const syncUnpinnedTheme = (event) => {
            // Once the user has used NimHunt's own toggle, their explicit
            // choice wins over later operating-system theme changes.
            if (explicitStoredTheme()) return;
            applyTheme(event.matches ? DARK_THEME : LIGHT_THEME);
        };

        if (typeof mediaQuery.addEventListener === 'function') {
            mediaQuery.addEventListener('change', syncUnpinnedTheme);
        } else if (typeof mediaQuery.addListener === 'function') {
            // Compatibility fallback for older embedded/WebKit browsers.
            mediaQuery.addListener(syncUnpinnedTheme);
        }
    }

    applyTheme(preferredTheme());
    installSystemThemeListener();

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => installThemeUi(document), { once: true });
    } else {
        installThemeUi(document);
    }

    window.addEventListener('storage', (event) => {
        if (event.key !== STORAGE_KEY) return;
        if (event.newValue === DARK_THEME || event.newValue === LIGHT_THEME) {
            applyTheme(event.newValue);
            return;
        }
        applyTheme(systemTheme());
    });
})();
