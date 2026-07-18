import { applyStaticInterfaceText } from './interface_text.js?v=qol-v1-20260717';

function ensureResponsiveStylesheet() {
    if (document.querySelector('link[data-nimhunt-responsive]')) return;

    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = '/static/responsive.css?v=mobile-layout-v1-20260718';
    stylesheet.dataset.nimhuntResponsive = 'true';
    document.head.append(stylesheet);
}

// Every public page already loads this small module. Loading the shared
// responsive layer here keeps the narrow-screen fixes consistent without
// duplicating another stylesheet tag across every template.
ensureResponsiveStylesheet();

// Module scripts run after the document has been parsed. This pass translates
// only elements explicitly marked with data-i18n attributes, so public Spot
// titles, descriptions, display names, and other user-generated text are never
// touched by the localisation framework.
applyStaticInterfaceText();
