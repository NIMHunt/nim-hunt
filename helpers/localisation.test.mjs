import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_LANGUAGE,
  getPreferredLanguage,
  mergeTranslationObjects,
  normaliseLanguageCode,
  resolveTextCatalogue,
  textAtPath,
} from '../static/localisation.js';

test('Nimiq Pay language is normalised and English is the fallback', () => {
  assert.equal(normaliseLanguageCode('DE'), 'de');
  assert.equal(normaliseLanguageCode('pt-BR'), 'pt');
  assert.equal(normaliseLanguageCode('not-a-language'), null);
  assert.equal(getPreferredLanguage({ language: 'ES' }), 'es');
  assert.equal(getPreferredLanguage({}), DEFAULT_LANGUAGE);
  assert.equal(getPreferredLanguage(null), DEFAULT_LANGUAGE);
});

test('partial translations inherit missing English text', () => {
  const catalogues = {
    en: {
      notice: { title: 'Notice', ok: 'OK' },
      status: { active: 'Active' },
    },
    de: {
      notice: { title: 'Hinweis' },
    },
  };

  const resolved = resolveTextCatalogue(catalogues, 'de');
  assert.equal(resolved.language, 'de');
  assert.equal(resolved.text.notice.title, 'Hinweis');
  assert.equal(resolved.text.notice.ok, 'OK');
  assert.equal(resolved.text.status.active, 'Active');
  assert.equal(textAtPath(resolved.text, 'notice.title'), 'Hinweis');
});

test('unknown languages use a fresh English catalogue', () => {
  const english = { nested: { label: 'English' } };
  const resolved = resolveTextCatalogue({ en: english }, 'fr');
  assert.equal(resolved.language, 'en');
  assert.deepEqual(resolved.text, english);
  assert.notEqual(resolved.text, english);

  const merged = mergeTranslationObjects(english, { nested: { extra: 'Extra' } });
  assert.deepEqual(merged, { nested: { label: 'English', extra: 'Extra' } });

  const englishOptions = [{ value: 10, label: 'Spam' }];
  const options = resolveTextCatalogue({ en: englishOptions }, 'fr');
  assert.deepEqual(options.text, englishOptions);
});
