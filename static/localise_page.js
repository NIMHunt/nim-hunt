import {
    applyStaticInterfaceText,
    getReportReasonOptions,
} from './interface_text.js?v=qol-v1-20260717';
import { installCreateSpotDeleteNavigationGuard } from './create_spot_delete_guard.js?v=ios-delete-guard-v1-20260718';
import { installNetworkModeBanner } from './network_mode_banner.js?v=network-mode-banner-v1-20260718';
import { installOwnerUiPolish } from './owner_ui_polish.js?v=cancellation-safety-v1-20260720';

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
        {
            selector: 'link[data-nimhunt-responsive="home"]',
            href: '/static/responsive_home.css?v=mobile-hero-v1-20260718',
            marker: 'home',
        },
        {
            selector: 'link[data-nimhunt-responsive="network-mode"]',
            href: '/static/network_mode_banner.css?v=network-mode-banner-v1-20260718',
            marker: 'network-mode',
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

function upgradeBackLinkArrows() {
    for (const link of document.querySelectorAll('.back-link')) {
        for (const node of link.childNodes) {
            if (node.nodeType !== Node.TEXT_NODE || !node.textContent?.includes('‹')) continue;
            node.textContent = node.textContent.replaceAll('‹', '←');
        }
    }
}

// find_spots.js historically read this catalogue as a global constant. Keep the
// binding available until that page module is next consolidated, so opening the
// report form cannot fail before it renders its reason options.
globalThis.REPORT_REASON_OPTIONS = getReportReasonOptions();

// Install the cross-page owner enhancements before individual page modules run.
// This allows My Spots responses and funded-draft deletion requests to be
// normalised without duplicating cancellation logic across several large files.
installOwnerUiPolish();

// The Create Spot page loads this shared module before create_spot.js. Installing
// the guard here means its fetch observer is in place before a draft can be
// deleted, without affecting any non-Create page.
installCreateSpotDeleteNavigationGuard();

// Every public page already loads this small module. Loading the shared
// responsive layers here keeps the narrow-screen fixes consistent without
// duplicating stylesheet tags across every template.
ensureResponsiveStylesheets();

// Keep every existing and future backlink on the proper left-arrow character,
// including older templates that still contain the former single chevron.
upgradeBackLinkArrows();

// Read the server's verified network selection from its secret-free health
// endpoint. TestAlbatross and DevAlbatross receive a prominent banner; mainnet
// deliberately receives no banner at all.
installNetworkModeBanner();

// Module scripts run after the document has been parsed. This pass translates
// only elements explicitly marked with data-i18n attributes, so public Spot
// titles, descriptions, display names, and other user-generated text are never
// touched by the localisation framework.
applyStaticInterfaceText();
