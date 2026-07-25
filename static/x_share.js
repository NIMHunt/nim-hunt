const X_POST_INTENT_URL = 'https://x.com/intent/tweet';
const X_LOGO_PATH = 'M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z';

function cleanAbsoluteUrl(value) {
    const url = new URL(value, window.location.origin);
    url.hash = '';
    return url.toString();
}

export function canonicalPageUrl() {
    const canonicalHref = document.querySelector('link[rel="canonical"]')?.href;
    const pageUrl = new URL(canonicalHref || window.location.href, window.location.origin);
    pageUrl.hash = '';

    if (!canonicalHref) {
        pageUrl.search = '';
    }

    return pageUrl.toString();
}

export function xShareIntentUrl(shareUrl) {
    const intentUrl = new URL(X_POST_INTENT_URL);
    intentUrl.searchParams.set('url', shareUrl);
    return intentUrl.toString();
}

function createXLogo() {
    const namespace = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(namespace, 'svg');
    svg.classList.add('spot-x-share-icon');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');

    const path = document.createElementNS(namespace, 'path');
    path.setAttribute('d', X_LOGO_PATH);
    svg.append(path);
    return svg;
}

function isIndividualClaimPage() {
    return Boolean(document.body.dataset.claimId);
}

function shareUrlForRow(row) {
    if (isIndividualClaimPage()) return canonicalPageUrl();

    const spotHref = row.querySelector('.spot-link-anchor')?.href;
    return cleanAbsoluteUrl(spotHref || canonicalPageUrl());
}

function shareLabel() {
    return isIndividualClaimPage()
        ? 'Share this claim on X'
        : 'Share this Spot on X';
}

function createXShareLink(shareUrl) {
    const link = document.createElement('a');
    link.className = 'spot-copy-button spot-x-share-link';
    link.href = xShareIntentUrl(shareUrl);
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.setAttribute('aria-label', shareLabel());
    link.title = 'Share on X';
    link.append(createXLogo());
    return link;
}

function addXShareLinks(root = document) {
    const rows = root.querySelectorAll?.('.spot-detail-link-row') || [];

    for (const row of rows) {
        if (row.querySelector('.spot-x-share-link')) continue;

        const copyButton = row.querySelector('.spot-copy-button');
        if (!copyButton) continue;

        copyButton.after(createXShareLink(shareUrlForRow(row)));
    }
}

addXShareLinks();

const detailRoot = document.querySelector('main') || document.body;
const observer = new MutationObserver((records) => {
    for (const record of records) {
        for (const node of record.addedNodes) {
            if (!(node instanceof Element)) continue;
            if (node.matches('.spot-detail-link-row')) addXShareLinks(node.parentElement || node);
            else addXShareLinks(node);
        }
    }
});

observer.observe(detailRoot, {
    childList: true,
    subtree: true,
});
