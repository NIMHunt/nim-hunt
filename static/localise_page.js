import {
    applyStaticInterfaceText,
    getReportReasonOptions,
} from './interface_text.js?v=qol-v1-20260717';

function ensureResponsiveStylesheets() {
    const stylesheets = [
        {
            selector: 'link[data-nimhunt-responsive="base"]',
            href: '/static/responsive.css?v=mobile-layout-v3-20260718',
            marker: 'base',
        },
        {
            selector: 'link[data-nimhunt-responsive="labels"]',
            href: '/static/responsive_labels.css?v=mobile-layout-v3-20260718',
            marker: 'labels',
        },
    ];

    for (const { selector, href, marker } of stylesheets) {
        if (document.querySelector(selector)) continue;

        const stylesheet = document.createElement('link');
        stylesheet.rel = 'stylesheet';
        stylesheet.href = href;
        stylesheet.dataset.nimhuntResponsive = marker;
        document.head.append(stylesheet);
    }
}

// find_spots.js historically read this catalogue as a global constant. Keep the
// binding available until that page module is next consolidated, so opening the
// report form cannot fail before it renders its reason options.
globalThis.REPORT_REASON_OPTIONS = getReportReasonOptions();

// Every public page already loads this small module. Loading the shared
// responsive layers here keeps the narrow-screen fixes consistent without
// duplicating stylesheet tags across every template.
ensureResponsiveStylesheets();

// Module scripts run after the document has been parsed. This pass translates
// only elements explicitly marked with data-i18n attributes, so public Spot
// titles, descriptions, display names, and other user-generated text are never
// touched by the localisation framework.
applyStaticInterfaceText();
