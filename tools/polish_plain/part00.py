from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def _indent_block(value: str, spaces: int) -> str:
    prefix = " " * int(spaces)
    return "".join(
        prefix + line if line.strip("\r\n") else line
        for line in value.splitlines(keepends=True)
    )


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)

    # Most anchors are top-level. A few are nested inside a function or mapping;
    # accept one shared leading indentation level while keeping relative nesting.
    if count == 0 and "\n" in old:
        matches: list[tuple[str, str]] = []
        for spaces in range(1, 33):
            indented_old = _indent_block(old, spaces)
            if text.count(indented_old) == 1:
                matches.append((indented_old, _indent_block(new, spaces)))
        if len(matches) == 1:
            old, new = matches[0]
            count = 1

    if count != 1:
        raise RuntimeError(f"Expected one match in {relative}, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before_once(relative: str, marker: str, addition: str) -> None:
    replace_once(relative, marker, addition + marker)


# ---------------------------------------------------------------------------
# Home/Nimiq Pay startup: do not let a stalled remote SDK import or host bridge
# leave the initial "Preparing home screen" copy on screen indefinitely.
# ---------------------------------------------------------------------------
replace_once(
    "static/browser_utils.js",
    "const DEVICE_IDENTIFIER_PATTERN = /^[0-9a-fA-F]{64}$/;\n",
    dedent("""\
    const DEVICE_IDENTIFIER_PATTERN = /^[0-9a-fA-F]{64}$/;
    const MINI_APP_SDK_URL = 'https://esm.sh/@nimiq/mini-app-sdk';
    let miniAppSdkPromise = null;

    function delay(milliseconds) {
        return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    }

    function withTimeout(promise, timeoutMs, message) {
        const timeout = Math.max(1, Number(timeoutMs || 1));
        return new Promise((resolve, reject) => {
            const timer = window.setTimeout(() => reject(new Error(message)), timeout);
            Promise.resolve(promise).then(
                (value) => {
                    window.clearTimeout(timer);
                    resolve(value);
                },
                (error) => {
                    window.clearTimeout(timer);
                    reject(error);
                },
            );
        });
    }

    export async function loadNimiqMiniAppSdk({ timeoutMs = 7000, retries = 1, retryDelayMs = 300 } = {}) {
        let lastError = null;
        for (let attempt = 0; attempt <= Math.max(0, Number(retries || 0)); attempt += 1) {
            try {
                if (!miniAppSdkPromise) miniAppSdkPromise = import(MINI_APP_SDK_URL);
                return await withTimeout(
                    miniAppSdkPromise,
                    timeoutMs,
                    'Nimiq Pay did not finish preparing the MiniApp connection.',
                );
            } catch (error) {
                lastError = error;
                if (attempt < retries) await delay(retryDelayMs);
            }
        }
        throw lastError || new Error('Nimiq Pay could not be loaded.');
    }
    """),
)
replace_once(
    "static/browser_utils.js",
    dedent("""\
    export async function requestDeviceIdentifierHash(requestDeviceIdentifier, reason) {
        const identifier = await requestDeviceIdentifier({ reason });
        if (typeof identifier !== 'string' || !DEVICE_IDENTIFIER_PATTERN.test(identifier)) {
            throw new Error('Nimiq Pay returned an invalid device identifier.');
        }
        return identifier.toLowerCase();
    }
    """),
    dedent("""\
    export async function requestDeviceIdentifierHash(
        requestDeviceIdentifier,
        reason,
        { timeoutMs = 7000, retries = 0, retryDelayMs = 300 } = {},
    ) {
        let lastError = null;
        for (let attempt = 0; attempt <= Math.max(0, Number(retries || 0)); attempt += 1) {
            try {
                const identifier = await withTimeout(
                    Promise.resolve().then(() => requestDeviceIdentifier({ reason })),
                    timeoutMs,
                    'Nimiq Pay did not return a device identifier in time.',
                );
                if (typeof identifier !== 'string' || !DEVICE_IDENTIFIER_PATTERN.test(identifier)) {
                    throw new Error('Nimiq Pay returned an invalid device identifier.');
                }
                return identifier.toLowerCase();
            } catch (error) {
                lastError = error;
                if (attempt < retries) await delay(retryDelayMs);
            }
        }
        throw lastError || new Error('Nimiq Pay could not identify this device.');
    }
    """),
)
replace_once(
    "static/home.js",
    "import { requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';\n",
    "",
)
replace_once(
    "static/home.js",
    "    createNoticePresenter,\n    getLanguage,\n    requestDeviceIdentifierHash,\n",
    "    createNoticePresenter,\n    getLanguage,\n    loadNimiqMiniAppSdk,\n    requestDeviceIdentifierHash,\n",
)
replace_once(
    "static/home.js",
    dedent("""\
    async function requestWalletDeviceId() {
        try {
            state.deviceIdHash = await requestDeviceIdentifierHash(
                requestDeviceIdentifier,
                UI_COPY.nimiqPay.deviceIdReason,
            );
    """),
    dedent("""\
    async function requestWalletDeviceId() {
        try {
            const { requestDeviceIdentifier } = await loadNimiqMiniAppSdk({
                timeoutMs: 6000,
                retries: 1,
                retryDelayMs: 350,
            });
            state.deviceIdHash = await requestDeviceIdentifierHash(
                requestDeviceIdentifier,
                UI_COPY.nimiqPay.deviceIdReason,
                { timeoutMs: 5000, retries: 1, retryDelayMs: 350 },
            );
    """),
)
replace_once(
    "static/home_entry.js",
    "import './home.js?v=home-onboarding-v1-20260718';\nimport { makeHomeText } from './interface_text.js?v=qol-v1-20260717';\n",
    "import './home.js?v=home-ready-v1-20260720';\nimport { makeHomeText } from './interface_text.js?v=polish-live-v1-20260720';\n",
)
replace_once(
    "static/home.js",
    "} from './browser_utils.js?v=qol-v1-20260717';",
    "} from './browser_utils.js?v=home-ready-v1-20260720';",
)
replace_once(
    "static/home.js",
    "import { makeHomeText } from './interface_text.js?v=qol-v1-20260717';",
    "import { makeHomeText } from './interface_text.js?v=polish-live-v1-20260720';",
)


# ---------------------------------------------------------------------------
# Deposit/complete badge wording and shared claim-code disclosure state.
# ---------------------------------------------------------------------------
replace_once(
    "static/interface_text.js",
    "        deposited: 'Deposited',\n",
    "        depositing: 'Depositing',\n        deposited: 'Deposited',\n",
)
replace_once(
    "static/interface_text.js",
    "        completed: 'Completed',\n",
    "        completed: 'Complete',\n",
)
replace_once(
    "static/spot_ui.js",
    "    'deposited',\n",
