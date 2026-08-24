const INFO_ICON_HTML = '<svg class="nq-icon nq-info-circle-small nh-inline-nimiq-icon create-spot-help-icon" aria-hidden="true" focusable="false"><use href="/static/nimiq-style.icons.svg#nq-info-circle-small" xlink:href="/static/nimiq-style.icons.svg#nq-info-circle-small"></use></svg>';

function rowFor(inputId, documentObj = document) {
    return documentObj.getElementById(inputId)?.closest('.create-spot-field-row') || null;
}

function addHelpButton(row, inputId, tooltip, ariaLabel, documentObj = document) {
    if (!row || row.querySelector('.create-spot-label-help')) return;
    const label = row.querySelector(`label[for="${inputId}"]`);
    if (!label) return;

    const text = label.textContent.trim();
    const i18nKey = label.dataset.i18n || '';
    if (i18nKey) delete label.dataset.i18n;
    label.classList.add('create-spot-label-with-help');
    label.replaceChildren();

    const labelText = documentObj.createElement('span');
    labelText.textContent = text;
    if (i18nKey) labelText.dataset.i18n = i18nKey;

    const button = documentObj.createElement('button');
    button.type = 'button';
    button.className = 'create-spot-label-help';
    button.dataset.tooltip = tooltip;
    button.setAttribute('aria-label', ariaLabel);
    button.innerHTML = INFO_ICON_HTML;

    label.append(labelText, button);
}

export function installCreateSpotProgressiveSettings(documentObj = document) {
    const form = documentObj.getElementById('create-spot-form');
    const error = documentObj.getElementById('create-spot-error');
    const fullEditorTitle = documentObj.getElementById('spot-title-input');
    if (
        !form
        || !error
        || !fullEditorTitle
        || documentObj.getElementById('create-spot-advanced')
    ) {
        return null;
    }

    // Keep the ordinary creation decisions visible. Radius lives inside the
    // location row, so moving that row preserves Location + Radius together.
    const basicRows = [
        rowFor('spot-title-input', documentObj),
        documentObj.querySelector('.create-spot-location-row'),
        rowFor('spot-total-value-input', documentObj),
        rowFor('spot-starts-input', documentObj),
        rowFor('spot-ends-input', documentObj),
        rowFor('spot-description-input', documentObj),
    ].filter(Boolean);

    // Stay Duration is the first optional/advanced decision. The remaining
    // advanced controls are the claim/participant/password rules.
    const advancedRows = [
        rowFor('spot-duration-input', documentObj),
        rowFor('spot-max-user-input', documentObj),
        rowFor('spot-max-total-input', documentObj),
        rowFor('spot-prize-count-input', documentObj),
        rowFor('spot-use-password-input', documentObj),
    ].filter(Boolean);

    addHelpButton(
        rowFor('spot-max-total-input', documentObj),
        'spot-max-total-input',
        'The maximum number of users who can claim or enter this spot.',
        'Explain Total Participants',
        documentObj,
    );
    addHelpButton(
        rowFor('spot-prize-count-input', documentObj),
        'spot-prize-count-input',
        'How many winners will be selected from this Prizedraw.',
        'Explain Prize Count',
        documentObj,
    );

    // The small first-step Create Spot card has none of these full-editor rows.
    // The guard above already prevents us reaching this point there, but keep
    // the details element defensive as well so an empty disclosure can never
    // be inserted by a partially-rendered full form.
    if (advancedRows.length === 0) return null;

    const details = documentObj.createElement('details');
    details.id = 'create-spot-advanced';
    details.className = 'create-spot-advanced';

    const summary = documentObj.createElement('summary');
    summary.className = 'create-spot-advanced-summary';
    summary.textContent = 'Advanced Settings';

    const body = documentObj.createElement('div');
    body.className = 'create-spot-advanced-body';
    for (const row of advancedRows) body.append(row);
    details.append(summary, body);

    for (const row of basicRows) form.insertBefore(row, error);
    form.insertBefore(details, error);
    return details;
}
