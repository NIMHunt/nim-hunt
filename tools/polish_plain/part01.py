    "    'depositing',\n    'deposited',\n",
)
replace_once(
    "static/spot_ui.js",
    "import { getSpotText } from './interface_text.js?v=qol-v1-20260717';",
    "import { getSpotText } from './interface_text.js?v=polish-live-v1-20260720';",
)
replace_once(
    "static/spot_ui.js",
    dedent("""\
    export function createOwnerClaimCodesControl(textOverrides = {}) {
        const text = claimCodeText(textOverrides);
        const line = document.createElement('li');
        line.className = 'spot-detail-line spot-passwords-line';
        line.hidden = true;

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'spot-passwords-toggle disclosure-toggle';
        toggle.textContent = typeof text.title === 'function' ? text.title(0) : 'Claim Codes (0)';
        toggle.setAttribute('aria-expanded', 'false');

        const panel = document.createElement('div');
        panel.className = 'spot-passwords-panel';
        panel.hidden = true;

        toggle.addEventListener('click', () => {
            const expanded = toggle.getAttribute('aria-expanded') === 'true';
            toggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
            panel.hidden = expanded;
        });

        line.append(toggle, panel);

        function hide() {
            line.hidden = true;
        }

        function setLoading() {
            line.hidden = false;
            toggle.textContent = text.loading || 'Loading claim codes…';
            panel.replaceChildren();
            toggle.setAttribute('aria-expanded', 'false');
            panel.hidden = true;
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

                row.append(left, right);
                rows.append(row);
            }

            toggle.textContent = typeof text.title === 'function' ? text.title(codes.length) : `Claim Codes (${codes.length})`;
            panel.replaceChildren(rows);
            line.hidden = false;
        }

        function setFailed() {
            line.hidden = false;
            toggle.textContent = text.loadFailed || 'Claim codes could not be loaded.';
            panel.replaceChildren();
            toggle.setAttribute('aria-expanded', 'false');
            panel.hidden = true;
        }

        return { line, toggle, panel, render, hide, setLoading, setFailed };
    }
    """),
    dedent("""\
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
    """),
)

# CSS: distinct depositing/completed badges and unambiguous one-line code rows.
replace_once(
    "static/home.css",
    ".spot-badge.is-deposited,\n.spot-badge.is-cancelling {",
