import test from 'node:test';
import assert from 'node:assert/strict';
import { installCreateSpotProgressiveSettings } from '../static/create_spot_progressive.js';

function classList() {
    const names = new Set();
    return {
        add(...values) { for (const value of values) names.add(value); },
        contains(value) { return names.has(value); },
    };
}

function makeLabel(inputId, text) {
    return {
        inputId,
        textContent: text,
        dataset: {},
        classList: classList(),
        children: [],
        replaceChildren(...children) { this.children = children; },
        append(...children) { this.children.push(...children); },
    };
}

function makeRow(inputId, text = inputId) {
    const label = makeLabel(inputId, text);
    const row = {
        id: `row-${inputId}`,
        label,
        helpButton: null,
        querySelector(selector) {
            if (selector === '.create-spot-label-help') return this.helpButton;
            if (selector === `label[for="${inputId}"]`) return label;
            return null;
        },
    };
    const input = {
        closest(selector) {
            return selector === '.create-spot-field-row' ? row : null;
        },
    };
    return { row, input, label };
}

function makeElement(tagName) {
    return {
        tagName: String(tagName).toUpperCase(),
        id: '',
        className: '',
        dataset: {},
        children: [],
        classList: classList(),
        append(...children) { this.children.push(...children); },
        setAttribute() {},
        innerHTML: '',
        textContent: '',
        type: '',
    };
}

function makeFullEditorDocument() {
    const fieldIds = [
        'spot-title-input',
        'spot-description-input',
        'spot-duration-input',
        'spot-max-user-input',
        'spot-max-total-input',
        'spot-prize-count-input',
        'spot-total-value-input',
        'spot-starts-input',
        'spot-ends-input',
        'spot-use-password-input',
    ];
    const fields = Object.fromEntries(fieldIds.map((id) => [id, makeRow(id)]));
    const locationRow = { id: 'row-location' };
    const error = { id: 'create-spot-error' };
    const form = {
        id: 'create-spot-form',
        insertions: [],
        insertBefore(node, before) {
            assert.equal(before, error);
            this.insertions.push(node);
        },
    };

    const documentObj = {
        created: [],
        getElementById(id) {
            if (id === 'create-spot-form') return form;
            if (id === 'create-spot-error') return error;
            if (id === 'create-spot-advanced') return null;
            return fields[id]?.input || null;
        },
        querySelector(selector) {
            return selector === '.create-spot-location-row' ? locationRow : null;
        },
        createElement(tagName) {
            const element = makeElement(tagName);
            this.created.push(element);
            return element;
        },
    };

    return { documentObj, form, fields, locationRow };
}

test('progressive settings ignore the small first-step Create Spot card', () => {
    let createCount = 0;
    const documentObj = {
        getElementById(id) {
            if (id === 'create-spot-form' || id === 'create-spot-error') return {};
            return null;
        },
        createElement() {
            createCount += 1;
            return makeElement('div');
        },
    };

    assert.equal(installCreateSpotProgressiveSettings(documentObj), null);
    assert.equal(createCount, 0);
});

test('full editor keeps Ends After and Description basic, with Stay Duration first in Advanced Settings', () => {
    const { documentObj, form, fields, locationRow } = makeFullEditorDocument();
    const details = installCreateSpotProgressiveSettings(documentObj);

    assert.ok(details);
    assert.deepEqual(
        form.insertions.slice(0, 6).map((row) => row.id),
        [
            'row-spot-title-input',
            locationRow.id,
            'row-spot-total-value-input',
            'row-spot-starts-input',
            'row-spot-ends-input',
            'row-spot-description-input',
        ],
    );
    assert.equal(form.insertions[6], details);

    const advancedBody = details.children[1];
    assert.deepEqual(
        advancedBody.children.map((row) => row.id),
        [
            'row-spot-duration-input',
            'row-spot-max-user-input',
            'row-spot-max-total-input',
            'row-spot-prize-count-input',
            'row-spot-use-password-input',
        ],
    );

    assert.equal(fields['spot-description-input'].label.children.length, 0);
    assert.equal(fields['spot-ends-input'].label.children.length, 0);
    assert.ok(fields['spot-max-total-input'].label.children.length > 0);
    assert.ok(fields['spot-prize-count-input'].label.children.length > 0);
});
