"""Apply Spot requirement-icon and About-link changes, then remove helpers."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET_VERSION = "spot-requirements-v1-20260725"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def write(relative: str, text: str) -> None:
    (ROOT / relative).write_text(text, encoding="utf-8")


def replace_once(relative: str, old: str, new: str) -> None:
    text = read(relative)
    if old not in text:
        raise RuntimeError(f"Expected patch anchor missing from {relative}: {old!r}")
    write(relative, text.replace(old, new, 1))


def replace_section(relative: str, start: str, end: str, replacement: str) -> None:
    text = read(relative)
    start_index = text.find(start)
    end_index = text.find(end, start_index)
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"Expected section anchors missing from {relative}")
    write(relative, text[:start_index] + replacement + text[end_index:])


def replace_module_version(relative: str) -> None:
    text = read(relative)
    updated, count = re.subn(
        r"spot_ui\.js\?v=[^']+",
        f"spot_ui.js?v={ASSET_VERSION}",
        text,
    )
    if count != 1:
        raise RuntimeError(f"Expected one spot_ui module import in {relative}; found {count}")
    write(relative, updated)


# About copy remains in the translation catalogue, but the product name becomes
# a structured, safely-rendered link rather than embedding HTML in a string.
replace_once(
    "static/static_page_text.js",
    """                'NimHunt is a simple geofaucet-style and Prizedraw mini-app for Nimiq and NimPay. Creators can fund geographic Spots, and other users can discover and claim their rewards once they reach the required area.',
""",
    """                {
                    parts: [
                        'NimHunt is a simple geofaucet-style and Prizedraw mini-app for Nimiq and ',
                        {
                            text: 'NimPay',
                            href: 'https://nimpay.app',
                        },
                        '. Creators can fund geographic Spots, and other users can discover and claim their rewards once they reach the required area.',
                    ],
                },
""",
)

replace_once(
    "static/static_page.js",
    "./static_page_text.js?v=home-information-v2-20260725",
    "./static_page_text.js?v=about-nimpay-link-v1-20260725",
)
replace_once(
    "static/static_page.js",
    """function makeParagraph(value) {
    const paragraph = document.createElement('p');
    paragraph.className = 'static-page-copy';
    paragraph.textContent = value;
    return paragraph;
}
""",
    """function appendParagraphPart(paragraph, part) {
    if (typeof part === 'string') {
        paragraph.append(document.createTextNode(part));
        return;
    }
    if (!part || typeof part.text !== 'string') return;

    if (typeof part.href === 'string' && part.href.trim()) {
        const link = document.createElement('a');
        link.href = part.href;
        link.className = 'welcome-link';
        link.textContent = part.text;
        paragraph.append(link);
        return;
    }

    paragraph.append(document.createTextNode(part.text));
}

function makeParagraph(value) {
    const paragraph = document.createElement('p');
    paragraph.className = 'static-page-copy';

    if (typeof value === 'string') {
        paragraph.textContent = value;
    } else if (Array.isArray(value?.parts)) {
        for (const part of value.parts) appendParagraphPart(paragraph, part);
    }

    return paragraph;
}
""",
)

# Translation-ready tooltip copy for the new duration requirement icon.
replace_once(
    "static/interface_text.js",
    "    passwordRequiredTooltip: 'This spot requires a password.',\n",
    "    passwordRequiredTooltip: 'This spot requires a password.',\n"
    "    durationRequiredTooltip: 'This spot requires you to remain within its area for a set duration.',\n",
)

# Generalise the existing password-only title helper into a reusable requirement
# helper while preserving appendSpotTitleWithLock as a backwards-compatible API.
replace_section(
    "static/spot_ui.js",
    "const PASSWORD_TOOLTIP_ID = 'spot-title-lock-tooltip';",
    "export function buildSpotLinkControl(spot) {",
    """const REQUIREMENT_TOOLTIP_ID = 'spot-title-requirement-tooltip';

function getRequirementTooltip() {
    let tooltip = document.getElementById(REQUIREMENT_TOOLTIP_ID);
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = REQUIREMENT_TOOLTIP_ID;
        tooltip.className = 'lock-tooltip spot-title-requirement-tooltip';
        tooltip.hidden = true;
        document.body.append(tooltip);
    }
    return tooltip;
}

function hideRequirementTooltip() {
    const tooltip = document.getElementById(REQUIREMENT_TOOLTIP_ID);
    if (!tooltip) return;
    tooltip.hidden = true;
    tooltip.textContent = '';
    tooltip.removeAttribute('data-placement');
}

function positionRequirementTooltip(target, tooltip) {
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

function showRequirementTooltip(target) {
    const text = target?.dataset?.tooltip;
    if (!text) return;

    const tooltip = getRequirementTooltip();
    tooltip.textContent = text;
    tooltip.hidden = false;
    tooltip.dataset.placement = 'top';
    requestAnimationFrame(() => positionRequirementTooltip(target, tooltip));
}

function attachRequirementTooltip(wrap) {
    let hideTimer = null;

    const show = () => {
        if (hideTimer) window.clearTimeout(hideTimer);
        showRequirementTooltip(wrap);
    };

    const hide = () => {
        if (hideTimer) window.clearTimeout(hideTimer);
        hideRequirementTooltip();
    };

    const showBriefly = (event) => {
        event.preventDefault();
        event.stopPropagation();
        show();
        if (hideTimer) window.clearTimeout(hideTimer);
        hideTimer = window.setTimeout(hideRequirementTooltip, 1800);
    };

    wrap.addEventListener('mouseenter', show);
    wrap.addEventListener('mouseleave', hide);
    wrap.addEventListener('focusin', show);
    wrap.addEventListener('focusout', hide);
    wrap.addEventListener('click', showBriefly);
    wrap.addEventListener('touchstart', showBriefly, { passive: false });
}

function requirementDefinitions(spot) {
    const requirements = [];
    if (spot?.use_password) {
        requirements.push({
            iconName: 'nq-lock-locked',
            tooltip: SPOT_TEXT.passwordRequiredTooltip || 'This spot requires a password.',
        });
    }
    if (Number(spot?.claim_duration || 0) > 0) {
        requirements.push({
            iconName: 'nq-stopwatch',
            tooltip: SPOT_TEXT.durationRequiredTooltip
                || 'This spot requires you to remain within its area for a set duration.',
        });
    }
    return requirements;
}

function createRequirementIcon(iconName, tooltipText, { interactive = true } = {}) {
    const wrap = document.createElement('span');
    wrap.className = 'spot-title-requirement-icon-wrap';
    wrap.append(createNimiqInlineIcon(iconName));

    if (interactive) {
        wrap.classList.add('has-requirement-tooltip');
        wrap.dataset.tooltip = tooltipText;
        wrap.setAttribute('aria-label', tooltipText);
        attachRequirementTooltip(wrap);
    } else {
        wrap.setAttribute('aria-hidden', 'true');
    }
    return wrap;
}

export function createPasswordRequiredIcon(options = {}) {
    return createRequirementIcon(
        'nq-lock-locked',
        SPOT_TEXT.passwordRequiredTooltip || 'This spot requires a password.',
        options,
    );
}

export function createDurationRequiredIcon(options = {}) {
    return createRequirementIcon(
        'nq-stopwatch',
        SPOT_TEXT.durationRequiredTooltip
            || 'This spot requires you to remain within its area for a set duration.',
        options,
    );
}

export function appendSpotRequirementIcons(container, spot, { interactive = true } = {}) {
    if (!container) return 0;
    const requirements = requirementDefinitions(spot);
    for (const requirement of requirements) {
        container.append(createRequirementIcon(
            requirement.iconName,
            requirement.tooltip,
            { interactive },
        ));
    }
    return requirements.length;
}

// The historical name is retained because all Spot/Claim pages import it. It now
// renders every claim requirement icon rather than only the password lock.
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
    const requirements = requirementDefinitions(spot);

    titleEl.replaceChildren();
    titleEl.removeAttribute('title');
    titleEl.removeAttribute('aria-label');
    titleEl.classList.toggle('is-truncated-title', Boolean(truncate));

    if (truncate) {
        titleEl.title = fullTitle;
        titleEl.setAttribute(
            'aria-label',
            requirements.length > 0
                ? `${fullTitle}. ${requirements.map((item) => item.tooltip).join(' ')}`
                : fullTitle,
        );
    }

    appendSpotRequirementIcons(titleEl, spot);
    titleEl.append(titleText);
}

""",
)

# Every page importing the shared title helper must request its new cache key.
for relative in (
    "static/find_spots.js",
    "static/my_spots.js",
    "static/my_claims.js",
    "static/spot_detail.js",
    "static/claim_detail.js",
):
    replace_module_version(relative)

# Find Spots map title tooltips show both requirements when applicable.
replace_once(
    "static/find_spots.js",
    "    createNimiqInlineIcon,\n",
    "    appendSpotRequirementIcons,\n",
)
replace_once(
    "static/find_spots.js",
    """    if (spot.use_password) {
        const lock = document.createElement('span');
        lock.className = 'map-spot-title-tooltip-lock';
        lock.setAttribute('aria-hidden', 'true');
        lock.append(createNimiqInlineIcon('nq-lock-locked'));
        content.append(lock);
    }
""",
    """    const requirements = document.createElement('span');
    requirements.className = 'map-spot-title-tooltip-requirements';
    appendSpotRequirementIcons(requirements, spot, { interactive: false });
    if (requirements.childElementCount > 0) content.append(requirements);
""",
)

# Shared-map popups on My Spots and My Claims receive the same requirement icons.
replace_once(
    "static/my_spots.js",
    "    appendDetailDescription,\n",
    "    appendDetailDescription,\n    appendSpotRequirementIcons,\n",
)
replace_once(
    "static/my_spots.js",
    """function spotPopupContent(spot) {
    const title = document.createElement('span');
    title.className = 'nh-spot-popup-title';
    title.textContent = spot.title || SPOT_TEXT.fallbackTitle;
    return title;
}
""",
    """function spotPopupContent(spot) {
    const title = document.createElement('span');
    title.className = 'nh-spot-popup-title';
    appendSpotRequirementIcons(title, spot, { interactive: false });

    const text = document.createElement('span');
    text.textContent = spot.title || SPOT_TEXT.fallbackTitle;
    title.append(text);
    return title;
}
""",
)

replace_once(
    "static/my_claims.js",
    "    appendDetailDescription,\n",
    "    appendDetailDescription,\n    appendSpotRequirementIcons,\n",
)
replace_once(
    "static/my_claims.js",
    """function claimPopupContent(item) {
    const wrap = document.createElement('span');
    wrap.className = 'nh-spot-popup-title';
    wrap.textContent = `${item.title || TEXT.fallbackTitle} - ${claimStatusText(item.claim || {})}`;
    return wrap;
}
""",
    """function claimPopupContent(item) {
    const wrap = document.createElement('span');
    wrap.className = 'nh-spot-popup-title';
    appendSpotRequirementIcons(wrap, item, { interactive: false });
    wrap.append(document.createTextNode(
        `${item.title || TEXT.fallbackTitle} - ${claimStatusText(item.claim || {})}`,
    ));
    return wrap;
}
""",
)

# The standalone Spot page now uses the same title helper and map-popup icons.
replace_once(
    "static/spot_detail.js",
    "    appendDetailDescription,\n",
    "    appendDetailDescription,\n    appendSpotRequirementIcons,\n    appendSpotTitleWithLock,\n",
)
replace_once(
    "static/spot_detail.js",
    """function makeSpotMapPopup(spot) {
    const title = document.createElement('span');
    title.className = 'nh-spot-popup-title';
    title.textContent = spot.title || 'NimHunt Spot';

    const wrap = document.createElement('div');
""",
    """function makeSpotMapPopup(spot) {
    const title = document.createElement('span');
    title.className = 'nh-spot-popup-title';
    appendSpotRequirementIcons(title, spot, { interactive: false });

    const text = document.createElement('span');
    text.textContent = spot.title || 'NimHunt Spot';
    title.append(text);

    const wrap = document.createElement('div');
""",
)
replace_once(
    "static/spot_detail.js",
    """    const title = document.createElement('span');
    title.className = 'spot-list-title';
    title.textContent = spot.title || 'NimHunt Spot';
""",
    """    const title = document.createElement('span');
    title.className = 'spot-list-title';
    appendSpotTitleWithLock(title, spot);
""",
)

# Generalise requirement-icon CSS and keep pairs compact in titles and map popups.
stylesheet = read("static/home.css")
stylesheet = stylesheet.replace("spot-title-lock-icon-wrap", "spot-title-requirement-icon-wrap")
stylesheet = stylesheet.replace("has-lock-tooltip", "has-requirement-tooltip")
stylesheet = stylesheet.replace("spot-title-lock-tooltip", "spot-title-requirement-tooltip")
old_map_css = """.map-spot-title-tooltip-lock {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1em;
    height: 1em;
    margin-right: 0.25em;
    color: currentColor;
}

.map-spot-title-tooltip-lock .nq-icon {
    display: block;
    width: 1em;
    height: 1em;
}
"""
new_map_css = """.map-spot-title-tooltip-requirements {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.14em;
    margin-right: 0.25em;
    color: currentColor;
}

.map-spot-title-tooltip-requirements .spot-title-requirement-icon-wrap {
    width: 1em;
    height: 1em;
    margin: 0;
}

.map-spot-title-tooltip-requirements .nq-icon {
    display: block;
    width: 1em;
    height: 1em;
}
"""
if old_map_css not in stylesheet:
    raise RuntimeError("Expected map requirement CSS block is missing")
stylesheet = stylesheet.replace(old_map_css, new_map_css, 1)
stylesheet = stylesheet.replace(
    """.spot-list-title .spot-title-requirement-icon-wrap,
#claim-page-title .spot-title-requirement-icon-wrap {
""",
    """.spot-list-title .spot-title-requirement-icon-wrap,
#claim-page-title .spot-title-requirement-icon-wrap,
.nh-spot-popup-title .spot-title-requirement-icon-wrap {
""",
    1,
)
write("static/home.css", stylesheet)

# Force browsers to fetch the revised CSS/page modules and the About renderer.
replace_once(
    "public_html.py",
    '_ASSET_VERSION = "marker-white-outline-v1-20260723"',
    f'_ASSET_VERSION = "{ASSET_VERSION}"',
)
replace_once(
    "templates/_home_shell.html",
    '/static/static_page.js?v=home-information-v3-20260725',
    '/static/static_page.js?v=about-nimpay-link-v1-20260725',
)

# Existing static-page tests now protect the structured external link and safe DOM renderer.
static_tests = read("tests/test_static_pages.py")
static_tests = static_tests.replace(
    '/static/static_page.js?v=home-information-v3-20260725',
    '/static/static_page.js?v=about-nimpay-link-v1-20260725',
)
static_tests = static_tests.replace(
    """    assert "paragraphs:" in catalogue
""",
    """    assert "paragraphs:" in catalogue
    assert "text: 'NimPay'" in catalogue
    assert "href: 'https://nimpay.app'" in catalogue
""",
    1,
)
static_tests = static_tests.replace(
    """    assert "textContent =" in renderer
    assert "innerHTML" not in renderer
""",
    """    assert "textContent =" in renderer
    assert "document.createElement('a')" in renderer
    assert "link.href = part.href" in renderer
    assert "innerHTML" not in renderer
""",
    1,
)
write("tests/test_static_pages.py", static_tests)

# Remove the one-time patch machinery from the final branch.
for relative in (
    ".github/workflows/apply-spot-requirement-icons.yml",
    "scripts/apply_spot_requirement_icons.py",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
