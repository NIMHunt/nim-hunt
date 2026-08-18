import { createCaptchaController } from './simple_captcha.js?v=claim-polish-v2-20260704';
import { makeMySpotsText } from './interface_text.js?v=single-open-details-v1-20260722';

const CREATE_TRIGGER_SELECTOR = 'a[data-nim-hunt-create-spot="1"]';
const DEFAULT_SPOT_TITLE_MIN_LENGTH = 3;
const DEFAULT_SPOT_TITLE_MAX_LENGTH = 27;

export function buildCreateDraftPayload(
    sessionPayload,
    { title, isPrizeDraw = false, captchaPayload = {} } = {},
) {
    return {
        ...(sessionPayload || {}),
        title: String(title || '').trim(),
        is_prizedraw: Boolean(isPrizeDraw),
        ...(captchaPayload || {}),
    };
}

export function createDraftEditUrl(data, createSpotUrl = '/create') {
    if (typeof data?.edit_url === 'string' && data.edit_url.trim()) return data.edit_url;
    const spotId = Number(data?.spot?.id);
    return Number.isFinite(spotId) && spotId > 0
        ? `${String(createSpotUrl || '/create').replace(/\/$/, '')}/${spotId}`
        : String(createSpotUrl || '/create');
}

function createModal(documentObj) {
    let backdrop = documentObj.getElementById('find-create-spot-backdrop');
    if (backdrop) return backdrop;

    const tooltip = documentObj.createElement('div');
    tooltip.id = 'find-create-spot-type-tooltip';
    tooltip.className = 'lock-tooltip create-spot-type-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.hidden = true;

    backdrop = documentObj.createElement('div');
    backdrop.id = 'find-create-spot-backdrop';
    backdrop.className = 'notice-backdrop';
    backdrop.hidden = true;
    backdrop.innerHTML = `
        <section class="notice-card create-spot-modal-card" role="dialog" aria-modal="true" aria-labelledby="find-create-spot-title" aria-describedby="find-create-spot-error">
            <h2 id="find-create-spot-title">Create a Spot</h2>
            <form id="find-create-spot-form" class="create-spot-form" novalidate>
                <label class="sr-only" for="find-create-spot-title-input">Spot Title</label>
                <input
                    id="find-create-spot-title-input"
                    class="create-spot-title-input"
                    type="text"
                    placeholder="Spot Title"
                    autocomplete="off"
                    spellcheck="false"
                >

                <fieldset class="create-spot-type-field" aria-label="Spot type">
                    <label id="find-create-spot-type-standard-option" class="create-spot-type-option">
                        <input id="find-create-spot-type-standard" type="radio" name="find-create-spot-type" value="standard" checked>
                        <span id="find-create-spot-type-standard-label">Standard</span>
                    </label>
                    <label id="find-create-spot-type-prizedraw-option" class="create-spot-type-option">
                        <input id="find-create-spot-type-prizedraw" type="radio" name="find-create-spot-type" value="prizedraw">
                        <span id="find-create-spot-type-prizedraw-label">Prizedraw</span>
                    </label>
                </fieldset>

                <div class="create-spot-captcha-field" aria-label="Create Spot captcha">
                    <label id="find-create-spot-captcha-label" class="report-field-label create-spot-captcha-label" for="find-create-spot-captcha-input">Captcha</label>
                    <div class="report-captcha-row create-spot-captcha-row">
                        <span id="find-create-spot-captcha-question" class="report-captcha-question create-spot-captcha-question"></span>
                        <input
                            id="find-create-spot-captcha-input"
                            class="create-spot-input report-captcha-input create-spot-captcha-input"
                            type="text"
                            inputmode="numeric"
                            autocomplete="off"
                            placeholder="Answer"
                        >
                    </div>
                </div>

                <p id="find-create-spot-error" class="display-name-error" role="alert" hidden></p>

                <div class="create-spot-modal-actions">
                    <button id="find-create-spot-submit" class="nq-button light-blue" type="submit" disabled>Create</button>
                    <button id="find-create-spot-cancel" class="nq-button light-blue create-spot-cancel" type="button">Cancel</button>
                </div>
            </form>
        </section>
    `;

    documentObj.body.append(tooltip, backdrop);
    return backdrop;
}

function responseError(data, fallback) {
    if (typeof data?.message === 'string' && data.message.trim()) return data.message;
    if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail;
    return fallback;
}

export function installFindSpotsCreateModal(runtime, { documentObj = document } = {}) {
    if (!runtime || !documentObj.getElementById('spot-map') || runtime.__nimHuntFindCreateModal) {
        return runtime?.__nimHuntFindCreateModal || null;
    }

    const backdrop = createModal(documentObj);
    const tooltip = documentObj.getElementById('find-create-spot-type-tooltip');
    const form = documentObj.getElementById('find-create-spot-form');
    const titleEl = documentObj.getElementById('find-create-spot-title');
    const titleInput = documentObj.getElementById('find-create-spot-title-input');
    const standardInput = documentObj.getElementById('find-create-spot-type-standard');
    const standardOption = documentObj.getElementById('find-create-spot-type-standard-option');
    const standardLabel = documentObj.getElementById('find-create-spot-type-standard-label');
    const prizedrawInput = documentObj.getElementById('find-create-spot-type-prizedraw');
    const prizedrawOption = documentObj.getElementById('find-create-spot-type-prizedraw-option');
    const prizedrawLabel = documentObj.getElementById('find-create-spot-type-prizedraw-label');
    const captchaLabel = documentObj.getElementById('find-create-spot-captcha-label');
    const captchaQuestion = documentObj.getElementById('find-create-spot-captcha-question');
    const captchaInput = documentObj.getElementById('find-create-spot-captcha-input');
    const submit = documentObj.getElementById('find-create-spot-submit');
    const cancel = documentObj.getElementById('find-create-spot-cancel');
    const error = documentObj.getElementById('find-create-spot-error');

    const appName = documentObj.body?.dataset?.appName || 'NimHunt';
    const nimiqPayUrl = documentObj.body?.dataset?.nimiqPayUrl || 'https://nimpay.app';
    const createSpotUrl = documentObj.body?.dataset?.createSpotUrl || '/create';
    const minTitle = Number.parseInt(
        documentObj.body?.dataset?.spotTitleMin || String(DEFAULT_SPOT_TITLE_MIN_LENGTH),
        10,
    );
    const maxTitle = Number.parseInt(
        documentObj.body?.dataset?.spotTitleMax || String(DEFAULT_SPOT_TITLE_MAX_LENGTH),
        10,
    );
    const text = makeMySpotsText({ appName, nimiqPayUrl });
    const state = { creating: false, captcha: null };

    function setError(message) {
        if (!error) return;
        error.textContent = message || '';
        error.hidden = !message;
    }

    function titleCheck() {
        const title = String(titleInput?.value || '').trim();
        const ok = title.length >= minTitle && title.length <= maxTitle;
        return {
            title,
            ok,
            message: ok ? '' : text.createSpot.invalidTitle({ min: minTitle, max: maxTitle }),
        };
    }

    function syncForm({ showTitleError = false } = {}) {
        const check = titleCheck();
        const captchaOk = Boolean(state.captcha?.passed());
        titleInput?.classList.toggle('is-invalid', !check.ok);
        if (submit && !state.creating) {
            submit.disabled = !(check.ok && captchaOk);
            submit.classList.toggle('is-disabled-by-validation', submit.disabled);
        }
        setError(showTitleError && !check.ok ? check.message : null);
        return { ...check, captchaOk };
    }

    state.captcha = createCaptchaController({
        questionEl: captchaQuestion,
        inputEl: captchaInput,
        questionText: text.createSpot.captchaQuestion,
        onChange: () => syncForm(),
    });

    function reset() {
        state.creating = false;
        if (titleInput) {
            titleInput.value = '';
            titleInput.classList.remove('is-invalid');
        }
        if (standardInput) standardInput.checked = true;
        state.captcha?.reset();
        if (submit) {
            submit.textContent = text.createSpot.submit;
            submit.disabled = true;
            submit.classList.add('is-disabled-by-validation');
        }
        setError(null);
        syncForm();
    }

    function show() {
        reset();
        backdrop.hidden = false;
        runtime.window.setTimeout(() => titleInput?.focus(), 0);
    }

    function hide() {
        if (state.creating) return;
        if (tooltip) tooltip.hidden = true;
        backdrop.hidden = true;
        reset();
    }

    function hideTooltip() {
        if (!tooltip) return;
        tooltip.hidden = true;
        tooltip.textContent = '';
        tooltip.removeAttribute('data-placement');
    }

    function showTooltip(target) {
        if (!tooltip || !target?.dataset?.tooltip) return;
        tooltip.textContent = target.dataset.tooltip;
        tooltip.hidden = false;
        tooltip.dataset.placement = 'top';
        runtime.window.requestAnimationFrame(() => {
            const gap = 12;
            const edge = 12;
            const targetRect = target.getBoundingClientRect();
            const tipRect = tooltip.getBoundingClientRect();
            let placement = 'top';
            let top = targetRect.top - tipRect.height - gap;
            if (top < edge) {
                placement = 'bottom';
                top = targetRect.bottom + gap;
            }
            let left = targetRect.left + targetRect.width / 2 - tipRect.width / 2;
            left = Math.max(edge, Math.min(left, runtime.window.innerWidth - tipRect.width - edge));
            tooltip.style.left = `${Math.round(left)}px`;
            tooltip.style.top = `${Math.round(top)}px`;
            tooltip.dataset.placement = placement;
        });
    }

    function bindTooltip(target) {
        if (!target) return;
        target.addEventListener('mouseenter', () => showTooltip(target));
        target.addEventListener('focusin', () => showTooltip(target));
        target.addEventListener('mouseleave', hideTooltip);
        target.addEventListener('focusout', hideTooltip);
    }

    async function submitForm(event) {
        event.preventDefault();
        if (state.creating) return;

        const check = syncForm({ showTitleError: true });
        if (!check.ok) {
            titleInput?.focus();
            return;
        }
        if (!check.captchaOk) {
            setError(text.createSpot.captchaIncomplete);
            captchaInput?.focus();
            return;
        }

        const sessionPayload = runtime.lastSessionPayload;
        if (!sessionPayload?.device_id_hash) {
            setError(text.createSpot.walletUnavailable.body);
            return;
        }

        state.creating = true;
        submit.disabled = true;
        submit.classList.remove('is-disabled-by-validation');
        submit.textContent = text.createSpot.submitting;

        try {
            const response = await runtime.window.fetch('/api/create-spot/draft', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify(buildCreateDraftPayload(sessionPayload, {
                    title: check.title,
                    isPrizeDraw: Boolean(prizedrawInput?.checked),
                    captchaPayload: state.captcha?.payload() || {},
                })),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.ok === false) {
                throw new Error(responseError(data, text.createSpot.createFailed.body));
            }
            runtime.window.location.href = createDraftEditUrl(data, createSpotUrl);
        } catch (err) {
            state.creating = false;
            submit.textContent = text.createSpot.submit;
            syncForm();
            setError(err?.message || text.createSpot.createFailed.body);
        }
    }

    if (titleEl) titleEl.textContent = text.createSpot.title;
    if (titleInput) {
        titleInput.placeholder = text.createSpot.titlePlaceholder;
        titleInput.minLength = minTitle;
        titleInput.maxLength = maxTitle;
    }
    if (standardLabel) standardLabel.textContent = text.createSpot.standard;
    if (prizedrawLabel) prizedrawLabel.textContent = text.createSpot.prizeDraw;
    if (standardOption) standardOption.dataset.tooltip = text.createSpot.standardTooltip;
    if (prizedrawOption) prizedrawOption.dataset.tooltip = text.createSpot.prizeDrawTooltip;
    if (captchaLabel) captchaLabel.textContent = text.createSpot.captchaLabel;
    if (captchaInput) captchaInput.placeholder = text.createSpot.captchaPlaceholder;
    if (submit) submit.textContent = text.createSpot.submit;
    if (cancel) cancel.textContent = text.createSpot.cancel;

    documentObj.addEventListener('click', (event) => {
        const trigger = event.target?.closest?.(CREATE_TRIGGER_SELECTOR);
        if (!trigger) return;
        // Keep /create as a reliable fallback if identity has not been captured.
        if (!runtime.lastSessionPayload?.device_id_hash) return;
        event.preventDefault();
        show();
    });
    titleInput?.addEventListener('input', () => syncForm());
    form?.addEventListener('submit', submitForm);
    cancel?.addEventListener('click', hide);
    backdrop.addEventListener('click', (event) => {
        if (event.target === backdrop) hide();
    });
    documentObj.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !backdrop.hidden) hide();
    });
    runtime.window.addEventListener('scroll', hideTooltip, { passive: true });
    runtime.window.addEventListener('resize', hideTooltip);
    bindTooltip(standardOption);
    bindTooltip(prizedrawOption);

    reset();
    runtime.__nimHuntFindCreateModal = { show, hide };
    return runtime.__nimHuntFindCreateModal;
}
