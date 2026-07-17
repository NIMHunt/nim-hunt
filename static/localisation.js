// Lightweight localisation helpers for NimHunt.
//
// Nimiq Pay exposes its selected language through window.nimiqPay.language.
// The application currently ships English only, but these helpers allow each
// UI catalogue to add a small number of partial language overrides later.

export const DEFAULT_LANGUAGE = 'en';

function isPlainObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function normaliseLanguageCode(value) {
    if (typeof value !== 'string') return null;
    const clean = value.trim().toLowerCase().replace('_', '-');
    if (!clean) return null;

    const primary = clean.split('-', 1)[0];
    return /^[a-z]{2}$/.test(primary) ? primary : null;
}

export function getPreferredLanguage(nimiqPay = globalThis.window?.nimiqPay) {
    return normaliseLanguageCode(nimiqPay?.language) || DEFAULT_LANGUAGE;
}

export function mergeTranslationObjects(base, override) {
    if (!isPlainObject(base)) {
        return override === undefined ? base : override;
    }

    const merged = { ...base };
    if (!isPlainObject(override)) return merged;

    for (const [key, value] of Object.entries(override)) {
        if (isPlainObject(value) && isPlainObject(base[key])) {
            merged[key] = mergeTranslationObjects(base[key], value);
        } else {
            merged[key] = value;
        }
    }
    return merged;
}

export function resolveTextCatalogue(catalogues, requestedLanguage = getPreferredLanguage()) {
    const requested = normaliseLanguageCode(requestedLanguage) || DEFAULT_LANGUAGE;
    const english = catalogues?.[DEFAULT_LANGUAGE] || {};
    const selected = catalogues?.[requested];
    const language = selected ? requested : DEFAULT_LANGUAGE;

    return {
        requestedLanguage: requested,
        language,
        text: selected
            ? mergeTranslationObjects(english, selected)
            : mergeTranslationObjects(english, undefined),
    };
}

export function textAtPath(text, path) {
    if (!path) return undefined;
    return String(path)
        .split('.')
        .reduce((value, key) => (value === null || value === undefined ? undefined : value[key]), text);
}

function applyAttributeTranslations(root, selector, attributeName, text) {
    for (const element of root.querySelectorAll(selector)) {
        const key = element.dataset[attributeName];
        const value = textAtPath(text, key);
        if (typeof value !== 'string') continue;

        if (attributeName === 'i18n') {
            element.textContent = value;
        } else if (attributeName === 'i18nPlaceholder') {
            element.setAttribute('placeholder', value);
        } else if (attributeName === 'i18nAriaLabel') {
            element.setAttribute('aria-label', value);
        } else if (attributeName === 'i18nTitle') {
            element.setAttribute('title', value);
        } else if (attributeName === 'i18nTooltip') {
            element.dataset.tooltip = value;
        }
    }
}

export function applyTextCatalogue(root, catalogues, requestedLanguage = getPreferredLanguage()) {
    const resolved = resolveTextCatalogue(catalogues, requestedLanguage);
    const documentElement = root?.documentElement || root?.ownerDocument?.documentElement;
    if (documentElement) documentElement.lang = resolved.language;

    if (!root?.querySelectorAll) return resolved;

    applyAttributeTranslations(root, '[data-i18n]', 'i18n', resolved.text);
    applyAttributeTranslations(root, '[data-i18n-placeholder]', 'i18nPlaceholder', resolved.text);
    applyAttributeTranslations(root, '[data-i18n-aria-label]', 'i18nAriaLabel', resolved.text);
    applyAttributeTranslations(root, '[data-i18n-title]', 'i18nTitle', resolved.text);
    applyAttributeTranslations(root, '[data-i18n-tooltip]', 'i18nTooltip', resolved.text);
    return resolved;
}
