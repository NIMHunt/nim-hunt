import { getSpotText } from './interface_text.js?v=polish-live-v1-20260720';
import { formatNimFromLuna } from './nim_format.js';

const SPOT_TEXT = getSpotText();

const STATUS_CLASS_NAMES = new Set([
    'draft',
    'depositing',
    'deposited',
    'active',
    'upcoming',
    'ended',
    'completed',
    'cancelled',
    'cancelling',
    'banned',
    'unknown',
]);

const badgeColourCache = new Map();

export function metresToText(value) {
    if (value === null || value === undefined) return 'distance unknown';
    if (value < 1000) return `${Math.round(value)} m away`;
    return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} km away`;
}

export function unixToText(value) {
    if (!value) return null;

    const date = new Date(Number(value) * 1000);
    const now = new Date();
    const dateDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const nowDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const daysFromToday = Math.round((dateDay - nowDay) / 86400000);
    const timeText = date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    });

    if (daysFromToday === 0) return `today, ${timeText}`;
    if (daysFromToday === 1) return `tomorrow, ${timeText}`;

    const dateText = date.toLocaleDateString([], {
        day: 'numeric',
        month: 'short',
    });
    return `${dateText}, ${timeText}`;
}

export function durationText(seconds) {
    const value = Number(seconds || 0);
    if (value <= 0) return null;
    if (value < 60) return `${value} sec`;
    if (value < 3600) return `${Math.round(value / 60)} min`;
    if (value < 86400) return `${(value / 3600).toFixed(value % 3600 === 0 ? 0 : 1)} hr`;
    return `${(value / 86400).toFixed(value % 86400 === 0 ? 0 : 1)} days`;
}

export function nimFromLunaText(value) {
    return formatNimFromLuna(value);
}

export function highestTimeUnitText(seconds, suffix = '') {
    const value = Math.max(0, Math.floor(Number(seconds || 0)));
    if (value <= 60) return `Less than 1 Minute${suffix ? ` ${suffix}` : ''}`;

    const units = [
        ['Week', 7 * 24 * 60 * 60],
        ['Day', 24 * 60 * 60],
        ['Hour', 60 * 60],
        ['Minute', 60],
    ];

    for (const [name, size] of units) {
        if (value >= size) {
            const count = Math.floor(value / size);
            return `${count} ${name}${count === 1 ? '' : 's'}${suffix ? ` ${suffix}` : ''}`;
        }
    }

    return `Less than 1 Minute${suffix ? ` ${suffix}` : ''}`;
}

export function spotScheduleTooltip(spot) {
    const starts = unixToText(spot?.starts_at) || 'now';
    const ends = unixToText(spot?.ends_at) || 'no end time';
    return `Active ${starts} until ${ends}`;
}

export function spotScheduleSummary(spot, { now = Math.floor(Date.now() / 1000) } = {}) {
    const status = String(spot?.status_label || '').toLowerCase();
    const bucket = String(spot?.bucket || '').toLowerCase();
    const startsAt = Number(spot?.starts_at || 0);
    const endsAt = Number(spot?.ends_at || 0);

    if ((status === 'active' || (!status && endsAt > now && (!startsAt || startsAt <= now))) && endsAt > 0) {
        return highestTimeUnitText(Math.max(0, endsAt - now), 'Remaining');
    }

    if ((status === 'upcoming' || (startsAt > now && bucket !== 'previous')) && startsAt > 0) {
        return highestTimeUnitText(Math.max(0, startsAt - now), 'Until Start');
    }

    if (bucket === 'previous' || status === 'ended' || status === 'completed' || (endsAt > 0 && endsAt <= now)) {
        return `Ended ${unixToText(endsAt) || 'recently'}`;
    }

    return spotScheduleTooltip(spot);
}

export function createScheduleTextSpan(spot) {
    const span = document.createElement('span');
    span.className = 'spot-time-summary';
    span.textContent = spotScheduleSummary(spot);
    span.title = spotScheduleTooltip(spot);
    span.setAttribute('aria-label', `${span.textContent}. ${span.title}`);
    return span;
}


export function spotPlaceText(spot) {
    return spot.city || spot.country || SPOT_TEXT.unknownArea;
}

export function spotTypeText(spot) {
    return spot.is_prizedraw ? SPOT_TEXT.type.prizeDraw : SPOT_TEXT.type.spot;
}

export function spotStatusText(spot) {
    const label = spot.badge_status_label || spot.status_label;
    return SPOT_TEXT.status[label] || SPOT_TEXT.status.unknown;
}

export function spotStatusClass(spot) {
    const label = String(spot.badge_status_label || spot.status_label || 'unknown').toLowerCase();
    return `is-${STATUS_CLASS_NAMES.has(label) ? label : 'unknown'}`;
}

export function createSpotBadge(spot) {
    const badge = document.createElement('span');
    badge.className = `spot-badge ${spotStatusClass(spot)}`;
    badge.textContent = spotStatusText(spot);
    return badge;
}

export function badgeColourForSpot(spot) {
    const className = spotStatusClass(spot);
    if (badgeColourCache.has(className)) return badgeColourCache.get(className);

    const probe = document.createElement('span');
    probe.className = `spot-badge ${className}`;
    probe.textContent = '•';
    probe.setAttribute('aria-hidden', 'true');
    probe.style.position = 'fixed';
    probe.style.left = '-9999px';
    probe.style.top = '-9999px';
    probe.style.pointerEvents = 'none';
    document.body.append(probe);

    const style = window.getComputedStyle(probe);
    const colour = style.backgroundColor || style.color || '#8c90a8';
    probe.remove();

    badgeColourCache.set(className, colour);
    return colour;
}

export function publicSpotUrl(spot) {
    return new URL(spot.href, window.location.origin).toString();
}

export async function copyText(text) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.append(textarea);
    textarea.select();
    document.execCommand('copy');
    textarea.remove();
}

export function appendParts(el, parts) {
    for (const part of parts) {
        if (part === null || part === undefined || part === '') continue;
        if (part instanceof Node) {
            el.append(part);
        } else {
            el.append(document.createTextNode(String(part)));
        }
    }
}

export function appendDetailDescription(container, text) {
    const description = document.createElement('p');
    description.className = 'spot-detail-description';
    description.textContent = text || SPOT_TEXT.noDescription;
    container.append(description);
}

export function appendBulletLine(list, ...parts) {
    const hasContent = parts.some((part) => {
        if (part instanceof Node) return true;
        return part !== null && part !== undefined && String(part) !== '';
    });
    if (!hasContent) return;

    const line = document.createElement('li');
    line.className = 'spot-detail-line';
    appendParts(line, parts);
    list.append(line);
}

const NIMIQ_ICON_SVG_NS = 'http://www.w3.org/2000/svg';
const NIMIQ_ICON_SPRITE_PATH = '/static/nimiq-style.icons.svg';

export function createNimiqInlineIcon(iconName) {
    const safeIconName = String(iconName || '').trim();
    const svg = document.createElementNS(NIMIQ_ICON_SVG_NS, 'svg');
    svg.classList.add('nq-icon', safeIconName, 'nh-inline-nimiq-icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');

    const use = document.createElementNS(NIMIQ_ICON_SVG_NS, 'use');
    const href = `${NIMIQ_ICON_SPRITE_PATH}#${safeIconName}`;
    use.setAttribute('href', href);
    use.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', href);
    svg.append(use);

    return svg;
}

export function setCopyButtonIcon(button, iconName) {
    if (!button) return;
    button.replaceChildren(createNimiqInlineIcon(iconName));
}

const PASSWORD_TOOLTIP_ID = 'spot-title-lock-tooltip';

function getPasswordTooltip() {
    let tooltip = document.getElementById(PASSWORD_TOOLTIP_ID);
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = PASSWORD_TOOLTIP_ID;
        tooltip.className = 'lock-tooltip spot-title-lock-tooltip';
        tooltip.hidden = true;
        document.body.append(tooltip);
    }
    return tooltip;
}

function hidePasswordTooltip() {
    const tooltip = document.getElementById(PASSWORD_TOOLTIP_ID);
    if (!tooltip) return;
    tooltip.hidden = true;
    tooltip.textContent = '';
    tooltip.removeAttribute('data-placement');
}

function positionPasswordTooltip(target, tooltip) {
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

function showPasswordTooltip(target) {
    const text = target?.dataset?.tooltip || SPOT_TEXT.passwordRequiredTooltip || 'This spot requires a password.';
    if (!text) return;

    const tooltip = getPasswordTooltip();
    tooltip.textContent = text;
    tooltip.hidden = false;
    tooltip.dataset.placement = 'top';
    requestAnimationFrame(() => positionPasswordTooltip(target, tooltip));
}

function attachPasswordTooltip(wrap) {
    let hideTimer = null;

    const show = () => {
        if (hideTimer) window.clearTimeout(hideTimer);
        showPasswordTooltip(wrap);
    };

    const hide = () => {
        if (hideTimer) window.clearTimeout(hideTimer);
        hidePasswordTooltip();
    };

    const showBriefly = (event) => {
        event.preventDefault();
        event.stopPropagation();
        show();
        if (hideTimer) window.clearTimeout(hideTimer);
        hideTimer = window.setTimeout(hidePasswordTooltip, 1800);
    };

    wrap.addEventListener('mouseenter', show);
    wrap.addEventListener('mouseleave', hide);
    wrap.addEventListener('focusin', show);
    wrap.addEventListener('focusout', hide);
    wrap.addEventListener('click', showBriefly);
    wrap.addEventListener('touchstart', showBriefly, { passive: false });
}

export function createPasswordRequiredIcon() {
    const wrap = document.createElement('span');
    wrap.className = 'spot-title-lock-icon-wrap has-lock-tooltip';
    wrap.dataset.tooltip = SPOT_TEXT.passwordRequiredTooltip || 'This spot requires a password.';
    wrap.setAttribute('aria-label', SPOT_TEXT.passwordRequiredTooltip || 'This spot requires a password.');
    wrap.append(createNimiqInlineIcon('nq-lock-locked'));
    attachPasswordTooltip(wrap);
    return wrap;
}

export function appendSpotTitleWithLock(
    titleEl,
    spot,
    { truncate = false } = {},
) {
    if (!titleEl) return;

    const fullTitle = String(spot.title || SPOT_TEXT.fallbackTitle);
    const titleText = document.createElement('span');
    titleText.className = 'spot-title-text';
    titleText.textContent = fullTitle;

    titleEl.replaceChildren();
    titleEl.removeAttribute('title');
    titleEl.removeAttribute('aria-label');
    titleEl.classList.toggle('is-truncated-title', Boolean(truncate));

    if (truncate) {
        titleEl.title = fullTitle;
        titleEl.setAttribute(
            'aria-label',
            `${fullTitle}${spot.use_password ? '. Requires a password.' : ''}`,
        );
    }

    titleEl.append(titleText);
    if (spot.use_password) {
        const lockIcon = createPasswordRequiredIcon();
        titleEl.append(lockIcon);
    }
}

export function buildSpotLinkControl(spot) {
    const wrap = document.createElement('span');
    wrap.className = 'spot-detail-link-row';

    const link = document.createElement('a');
    link.href = spot.href;
    link.className = 'spot-link-anchor';
    link.textContent = spot.link || spot.href;

    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'spot-copy-button';
    copyButton.setAttribute('aria-label', SPOT_TEXT.copySpotLink);

    setCopyButtonIcon(copyButton, 'nq-copy');

    copyButton.addEventListener('click', async () => {
        try {
            await copyText(publicSpotUrl(spot));
            copyButton.classList.add('is-copied');
            setCopyButtonIcon(copyButton, 'nq-checkmark-small');
            window.setTimeout(() => {
                copyButton.classList.remove('is-copied');
                setCopyButtonIcon(copyButton, 'nq-copy');
            }, 900);
        } catch (err) {
            console.error(err);
        }
    });

    wrap.append(link, copyButton);
    return wrap;
}


function claimCodeText(overrides = {}) {
    return {
        ...(SPOT_TEXT.ownerClaimCodes || {}),
        ...overrides,
    };
}

function setClaimCodeCopyButtonIcon(button, iconName) {
    setCopyButtonIcon(button, iconName);
}

export function buildClaimCodeCopyButton(code, textOverrides = {}) {
    const text = claimCodeText(textOverrides);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'spot-copy-button spot-password-copy-button';
    button.setAttribute('aria-label', text.copy || 'Copy claim code');
    setClaimCodeCopyButtonIcon(button, 'nq-copy');

    button.addEventListener('click', async () => {
        try {
            await copyText(code);
            button.classList.add('is-copied');
            button.setAttribute('aria-label', text.copied || 'Copied');
            setClaimCodeCopyButtonIcon(button, 'nq-checkmark-small');
            window.setTimeout(() => {
                button.classList.remove('is-copied');
                button.setAttribute('aria-label', text.copy || 'Copy claim code');
                setClaimCodeCopyButtonIcon(button, 'nq-copy');
            }, 900);
        } catch (err) {
            console.error(err);
        }
    });

    return button;
}

export function createOwnerClaimCodesControl(
    textOverrides = {},
    { expanded = false, onToggle = null } = {},
) {
    const text = claimCodeText(textOverrides);
    const line = document.createElement('li');
    line.className = 'spot-detail-line spot-passwords-line';
    line.hidden = true;

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'spot-passwords-toggle disclosure-toggle';
    toggle.textContent = typeof text.title === 'function' ? text.title(0) : 'Claim Codes (0)';

    const panel = document.createElement('div');
    panel.className = 'spot-passwords-panel';

    let isExpanded = Boolean(expanded);
    function syncExpanded() {
        toggle.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
        panel.hidden = !isExpanded;
    }

    function setExpanded(nextExpanded, { notify = true } = {}) {
        isExpanded = Boolean(nextExpanded);
        syncExpanded();
        if (notify && typeof onToggle === 'function') onToggle(isExpanded);
    }

    toggle.addEventListener('click', () => setExpanded(!isExpanded));
    line.append(toggle, panel);
    syncExpanded();

    function hide() {
        line.hidden = true;
    }

    function setLoading() {
        line.hidden = false;
        toggle.textContent = text.loading || 'Loading claim codes…';
        panel.replaceChildren();
        syncExpanded();
    }

    function render(codes) {
        if (!Array.isArray(codes) || codes.length <= 0) {
            hide();
            return;
        }

        const rows = document.createElement('div');
        rows.className = 'spot-passwords-list';

        for (const item of codes) {
            const row = document.createElement('div');
            row.className = 'spot-password-row';
            row.classList.toggle('is-used', Boolean(item.used));

            const left = document.createElement('span');
            left.className = 'spot-password-left';

            const code = document.createElement('span');
            code.className = 'spot-password-code';
            code.textContent = item.code || '';
            left.append(code);

            if (!item.used && item.code) {
                left.append(buildClaimCodeCopyButton(item.code, text));
            }

            const right = document.createElement('span');
            right.className = 'spot-password-status';
            right.textContent = item.used
                ? (item.recipient_display_name || `User ${item.recipient_id || ''}`.trim())
                : (text.unused || 'Unused');
            right.title = right.textContent;

            row.append(left, right);
            rows.append(row);
        }

        toggle.textContent = typeof text.title === 'function' ? text.title(codes.length) : `Claim Codes (${codes.length})`;
        panel.replaceChildren(rows);
        line.hidden = false;
        syncExpanded();
    }

    function setFailed() {
        line.hidden = false;
        toggle.textContent = text.loadFailed || 'Claim codes could not be loaded.';
        panel.replaceChildren();
        syncExpanded();
    }

    return {
        line,
        toggle,
        panel,
        render,
        hide,
        setLoading,
        setFailed,
        setExpanded,
        isExpanded: () => isExpanded,
    };
}

export function createSpotListItem({ spot, detailBuilder, expanded = false, onToggle = null, metaBuilder = null }) {
    const spotId = Number(spot.id);
    const item = document.createElement('li');
    item.className = 'spot-list-item';

    const summary = document.createElement('button');
    summary.type = 'button';
    summary.className = 'spot-list-toggle';
    summary.setAttribute('aria-expanded', 'false');

    const topRow = document.createElement('span');
    topRow.className = 'spot-list-row spot-list-top-row';

    const title = document.createElement('span');
    title.className = 'spot-list-title';
    appendSpotTitleWithLock(title, spot, { truncate: true });

    const chevron = document.createElement('span');
    chevron.className = 'spot-list-chevron';
    chevron.textContent = '⌄';
    chevron.setAttribute('aria-hidden', 'true');

    const actions = document.createElement('span');
    actions.className = 'spot-list-actions';
    actions.append(createSpotBadge(spot), chevron);

    topRow.append(title, actions);

    const bottomRow = document.createElement('span');
    bottomRow.className = 'spot-list-row spot-list-bottom-row';

    const meta = document.createElement('span');
    meta.className = 'spot-list-meta';

    if (typeof metaBuilder === 'function') {
        const metaContent = metaBuilder(spot);
        if (metaContent instanceof Node) {
            meta.append(metaContent);
        } else if (metaContent !== null && metaContent !== undefined) {
            meta.textContent = String(metaContent);
        }
    } else {
        meta.textContent = spot.meta_text || spotPlaceText(spot);
    }

    bottomRow.append(meta);
    summary.append(topRow, bottomRow);

    const detail = detailBuilder(spot);
    detail.hidden = true;

    function setExpanded(nextExpanded) {
        item.classList.toggle('is-expanded', nextExpanded);
        summary.setAttribute('aria-expanded', nextExpanded ? 'true' : 'false');
        detail.hidden = !nextExpanded;
        if (typeof onToggle === 'function') onToggle(spotId, nextExpanded);
    }

    summary.addEventListener('click', () => {
        setExpanded(summary.getAttribute('aria-expanded') !== 'true');
    });

    item.append(summary, detail);
    setExpanded(Boolean(expanded));
    return item;
}
