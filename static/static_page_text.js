import {
    getPreferredLanguage,
    resolveTextCatalogue,
} from './localisation.js';

// Translation-ready copy for NimHunt's small informational pages.
// Add language catalogues alongside `en` when localisation work begins.
export const STATIC_PAGE_TEXT_CATALOGUES = {
    en: {
        common: {
            returnHome: 'Return to Home',
            loading: 'Loading…',
            loadFailed: 'This page could not be loaded.',
        },
        about: {
            pageTitle: 'About · NimHunt',
            title: 'About',
            paragraphs: [
                {
                    parts: [
                        'NimHunt is a simple geofaucet-style and Prizedraw mini-app for Nimiq and ',
                        {
                            text: 'NimPay',
                            href: 'https://nimpay.app',
                        },
                        '. Creators can fund geographic Spots, and other users can discover and claim their rewards once they reach the required area.',
                    ],
                },
                'It was made to offer the Nimiq community a simple and playful way to share NIM, encourage exploration, and experiment with location-based rewards.',
                'NimHunt is an independent community project made by a loyal member of the NIMIQ Community.',
            ],
        },
        roadmap: {
            pageTitle: 'Roadmap · NimHunt',
            title: 'Roadmap',
            empty: 'More roadmap details will be added soon.',
            loadFailed: 'The roadmap could not be loaded.',
        },
    },
};

export function getStaticPageText({ language = getPreferredLanguage() } = {}) {
    return resolveTextCatalogue(STATIC_PAGE_TEXT_CATALOGUES, language).text;
}
