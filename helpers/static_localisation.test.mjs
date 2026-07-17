import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

import { applyStaticInterfaceText } from '../static/interface_text.js';

const ATTRIBUTE_MAP = new Map([
  ['data-i18n', { selector: '[data-i18n]', datasetKey: 'i18n' }],
  ['data-i18n-placeholder', { selector: '[data-i18n-placeholder]', datasetKey: 'i18nPlaceholder' }],
  ['data-i18n-aria-label', { selector: '[data-i18n-aria-label]', datasetKey: 'i18nAriaLabel' }],
  ['data-i18n-title', { selector: '[data-i18n-title]', datasetKey: 'i18nTitle' }],
  ['data-i18n-tooltip', { selector: '[data-i18n-tooltip]', datasetKey: 'i18nTooltip' }],
]);

function markedElements() {
  const elementsBySelector = new Map([...ATTRIBUTE_MAP.values()].map(({ selector }) => [selector, []]));
  const templateDir = resolve(import.meta.dirname, '../templates');

  for (const filename of readdirSync(templateDir).filter((name) => name.endsWith('.html'))) {
    const source = readFileSync(resolve(templateDir, filename), 'utf8');
    for (const [attribute, { selector, datasetKey }] of ATTRIBUTE_MAP) {
      const pattern = new RegExp(`${attribute}="([^"]+)"`, 'g');
      for (const match of source.matchAll(pattern)) {
        let translated = false;
        const element = {
          dataset: { [datasetKey]: match[1] },
          set textContent(_value) { translated = true; },
          setAttribute() { translated = true; },
          get translated() { return translated || this.dataset.tooltip !== undefined; },
          description: `${filename}: ${attribute}="${match[1]}"`,
        };
        elementsBySelector.get(selector).push(element);
      }
    }
  }
  return elementsBySelector;
}

test('every marked template string exists in the English static catalogue', () => {
  const elementsBySelector = markedElements();
  const root = {
    documentElement: { lang: 'en' },
    querySelectorAll(selector) { return elementsBySelector.get(selector) || []; },
  };

  applyStaticInterfaceText(root, { language: 'en' });
  const untranslated = [...elementsBySelector.values()]
    .flat()
    .filter((element) => !element.translated)
    .map((element) => element.description);

  assert.deepEqual(untranslated, []);
  assert.equal(root.documentElement.lang, 'en');
});
