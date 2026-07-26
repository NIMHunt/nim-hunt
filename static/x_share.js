import { getCommonText } from './interface_text.js?v=action-tooltips-v1-20260725';

const X_POST_INTENT_URL = 'https://x.com/intent/tweet';
const X_LOGO_PATH = 'M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z';
const ACTION_TOOLTIP_ID = 'spot-inline-action-tooltip';
const UI_COPY = getCommonText().actions;
let activeTooltipTarget = null;

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

function publishedSpotLinkForRow(row) {
    const spotHref = row.querySelector('.spot-link-anchor')?.href;
    if (!spotHref) return null;

    const url = new URL(spotHref, window.location.origin);
    return url.pathname.startsWith('/spot/') ? url : null;
}

function rowIsShareable(row) {
    return isIndividualClaimPage() || Boolean(publishedSpotLinkForRow(row));
}

function shareUrlForRow(row) {
    if (isIndividualClaimPage()) return canonicalPageUrl();
    return cleanAbsoluteUrl(publishedSpotLinkForRow(row));
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
    link.dataset.tooltip = UI_COPY.shareOnX;
    link.append(createXLogo());
    return link;
}

function addXShareLinks(root = document) {
    const rows = root.querySelectorAll?.('.spot-detail-link-row') || [];

    for (const row of rows) {
        if (row.querySelector('.spot-x-share-link')) continue;
        if (!rowIsShareable(row)) continue;

        const copyButton = row.querySelector('.spot-copy-button');
        if (!copyButton) continue;

        copyButton.after(createXShareLink(shareUrlForRow(row)));
    }
}

function getActionTooltip() {
    let tooltip = document.getElementById(ACTION_TOOLTIP_ID);
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = ACTION_TOOLTIP_ID;
        tooltip.className = 'lock-tooltip spot-inline-action-tooltip';
        tooltip.setAttribute('role', 'tooltip');
        tooltip.hidden = true;
        document.body.append(tooltip);
    }
    return tooltip;
}

function tooltipText(target) {
    if (target.classList.contains('spot-x-share-link')) return UI_COPY.shareOnX;
    return target.classList.contains('is-copied') ? UI_COPY.copied : UI_COPY.copy;
}

function positionActionTooltip(target, tooltip) {
    if (!target || !tooltip || tooltip.hidden) return;

    const gap = 12;
    const edgePadding = 12;
    const targetRect = target.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();

    let placement = 'top';
    let top = targetRect.top - tooltipRect.height - gap;
    if (top < edgePadding) {
        placement = 'bottom';
        top = targetRect.bottom + gap;
    }

    let left = targetRect.left + (targetRect.width / 2) - (tooltipRect.width / 2);
    left = Math.max(edgePadding, Math.min(left, window.innerWidth - tooltipRect.width - edgePadding));

    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
    tooltip.dataset.placement = placement;
}

function refreshActionTooltip(target = activeTooltipTarget) {
    if (!target || target !== activeTooltipTarget) return;

    const tooltip = getActionTooltip();
    const text = tooltipText(target);
    target.dataset.tooltip = text;
    tooltip.textContent = text;
    tooltip.hidden = false;
    requestAnimationFrame(() => positionActionTooltip(target, tooltip));
}

function showActionTooltip(target) {
    activeTooltipTarget = target;
    refreshActionTooltip(target);
}

function hideActionTooltip(target) {
    if (activeTooltipTarget !== target) return;

    activeTooltipTarget = null;
    const tooltip = document.getElementById(ACTION_TOOLTIP_ID);
    if (!tooltip) return;
    tooltip.hidden = true;
    tooltip.textContent = '';
    tooltip.removeAttribute('data-placement');
}

function attachActionTooltip(target) {
    if (!target || target.dataset.actionTooltipAttached === 'true') return;

    target.dataset.actionTooltipAttached = 'true';
    target.removeAttribute('title');
    target.dataset.tooltip = tooltipText(target);
    target.addEventListener('mouseenter', () => showActionTooltip(target));
    target.addEventListener('mouseleave', () => hideActionTooltip(target));
    target.addEventListener('focusin', () => showActionTooltip(target));
    target.addEventListener('focusout', () => hideActionTooltip(target));
}

function addActionTooltips(root = document) {
    const targets = [];
    if (root.matches?.('.spot-copy-button')) targets.push(root);
    targets.push(...(root.querySelectorAll?.('.spot-copy-button') || []));

    for (const target of targets) attachActionTooltip(target);
}

addXShareLinks();
addActionTooltips();

const detailRoot = document.querySelector('main') || document.body;
const observer = new MutationObserver((records) => {
    for (const record of records) {
        if (record.type === 'attributes') {
            if (record.target === activeTooltipTarget) refreshActionTooltip(record.target);
            continue;
        }

        for (const node of record.addedNodes) {
            if (!(node instanceof Element)) continue;
            if (node.matches('.spot-detail-link-row')) addXShareLinks(node.parentElement || node);
            else addXShareLinks(node);
            addActionTooltips(node);
        }
    }
});

observer.observe(detailRoot, {
    attributes: true,
    attributeFilter: ['class'],
    childList: true,
    subtree: true,
});

window.addEventListener('resize', () => {
    if (activeTooltipTarget) refreshActionTooltip(activeTooltipTarget);
});
window.addEventListener('scroll', () => {
    if (activeTooltipTarget) refreshActionTooltip(activeTooltipTarget);
}, true);
