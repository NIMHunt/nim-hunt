import './home.js?v=home-onboarding-v1-20260718';
import { makeHomeText } from './interface_text.js?v=qol-v1-20260717';

const STYLE_ID = 'home-display-name-edit-styles';

function ensureDisplayNameEditStyles() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
        .welcome-edit-control {
            margin: 0;
            padding: 0.08em 0.14em;
            display: inline-flex;
            align-items: center;
            gap: 0.28em;
            border: 0;
            border-radius: 0.45em;
            background: transparent;
            color: inherit;
            cursor: pointer;
            font: inherit;
            font-weight: 900;
            line-height: 1;
            vertical-align: baseline;
            transition: transform 140ms ease, text-shadow 140ms ease;
        }

        .welcome-edit-control:hover,
        .welcome-edit-control:focus-visible {
            transform: translateY(-2px);
            text-shadow: 0 6px 14px rgba(31, 35, 72, 0.22);
        }

        .welcome-edit-control:focus-visible {
            outline: 3px solid rgba(33, 188, 165, 0.28);
            outline-offset: 3px;
        }

        .welcome-edit-control svg {
            width: 1em;
            height: 1em;
            flex: 0 0 auto;
        }

        @media (prefers-reduced-motion: reduce) {
            .welcome-edit-control {
                transition: none;
            }

            .welcome-edit-control:hover,
            .welcome-edit-control:focus-visible {
                transform: none;
            }
        }
    `;
    document.head.append(style);
}

function homeCopy() {
    return makeHomeText({
        appName: document.body.dataset.appName || document.title || 'NimHunt',
        displayNameMin: Number.parseInt(document.body.dataset.displayNameMin || '3', 10),
        displayNameMax: Number.parseInt(document.body.dataset.displayNameMax || '18', 10),
    });
}

function placeCaretAtEnd(input) {
    if (!input?.isConnected) return;

    const end = input.value.length;
    try {
        input.setSelectionRange(end, end);
    } catch (err) {
        // Some embedded browsers do not expose selection APIs consistently.
    }
}

function focusDisplayNameInputFromTap() {
    const input = document.getElementById('display-name-input');
    if (!input) return;

    // Keep focus inside the original tap/click event. Mobile browsers are much
    // more likely to open the software keyboard when focus is not deferred.
    try {
        input.focus({ preventScroll: true });
    } catch (err) {
        input.focus();
    }

    placeCaretAtEnd(input);

    // home.js performs a delayed select() for desktop convenience. Restore the
    // mobile-friendly caret position after that callback has run.
    window.requestAnimationFrame(() => placeCaretAtEnd(input));
    window.setTimeout(() => placeCaretAtEnd(input), 0);
}

function cancelDisplayNameEditIfActive() {
    const input = document.getElementById('display-name-input');
    const cancelButton = document.getElementById('display-name-cancel');
    if (!input || !cancelButton || cancelButton.disabled) return;

    // Reuse home.js's existing Cancel path so state, validation messages, and
    // the normal welcome control are restored in one place.
    cancelButton.click();
}

function isInsideDisplayNameEditor(target) {
    if (!(target instanceof Node)) return false;

    const input = document.getElementById('display-name-input');
    const editorActions = document.getElementById('display-name-editor');
    return Boolean(input?.contains(target) || editorActions?.contains(target));
}

function enhanceDisplayNameEditControl() {
    const welcomeLine = document.getElementById('welcome-line');
    const editButton = welcomeLine?.querySelector(':scope > .welcome-edit-button');
    if (!welcomeLine || !editButton || editButton.classList.contains('welcome-edit-control')) return;

    const welcomeTextNode = [...welcomeLine.childNodes]
        .find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim());
    if (!welcomeTextNode) return;

    const fullWelcomeText = welcomeTextNode.textContent || '';
    const welcomePrefix = homeCopy().status.userWelcome('');
    const hasKnownPrefix = fullWelcomeText.startsWith(welcomePrefix);
    const displayName = hasKnownPrefix
        ? fullWelcomeText.slice(welcomePrefix.length)
        : fullWelcomeText;
    if (!displayName.trim()) return;

    const icon = editButton.querySelector('svg');
    const name = document.createElement('span');
    name.className = 'welcome-edit-name';
    name.textContent = displayName;

    editButton.classList.add('welcome-edit-control');
    editButton.replaceChildren(name);
    if (icon) editButton.append(icon);

    // home.js attached its edit handler when this button was created. This
    // listener therefore runs afterwards, once the text input exists.
    editButton.addEventListener('click', focusDisplayNameInputFromTap);

    welcomeLine.replaceChildren(
        document.createTextNode(hasKnownPrefix ? welcomePrefix : ''),
        editButton,
    );
}

ensureDisplayNameEditStyles();

const welcomeLine = document.getElementById('welcome-line');
if (welcomeLine) {
    const observer = new MutationObserver(enhanceDisplayNameEditControl);
    observer.observe(welcomeLine, { childList: true });
    enhanceDisplayNameEditControl();
}

// A tap/click outside the input or its Save/Cancel controls abandons the draft
// name. Capture the pointer before links navigate so the Home page cannot be
// stored in the browser's back/forward cache with a live, partly edited input.
document.addEventListener('pointerdown', (event) => {
    if (!document.getElementById('display-name-input')) return;
    if (isInsideDisplayNameEditor(event.target)) return;
    cancelDisplayNameEditIfActive();
}, true);

// Apply the same rule to keyboard navigation on desktop and tablet devices.
document.addEventListener('focusin', (event) => {
    if (!document.getElementById('display-name-input')) return;
    if (isInsideDisplayNameEditor(event.target)) return;
    cancelDisplayNameEditIfActive();
});

// pagehide runs for ordinary navigation and for pages entering the back/forward
// cache. pageshow is a defensive fallback for browsers that restore old DOM.
window.addEventListener('pagehide', cancelDisplayNameEditIfActive);
window.addEventListener('pageshow', cancelDisplayNameEditIfActive);
