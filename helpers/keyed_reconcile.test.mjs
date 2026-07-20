import test from 'node:test';
import assert from 'node:assert/strict';
import { reconcileKeyedItems } from '../static/keyed_reconcile.js';

function item(id, signature) {
  return { id, signature, removed: false };
}

test('changed records replace and remove the previous card instead of cloning it', () => {
  const oldDraft = item(7, 'draft');
  const accidentalDuplicate = item(7, 'older-draft');
  const appended = [];
  const created = [];

  reconcileKeyedItems({
    existingItems: [oldDraft, accidentalDuplicate],
    desiredRecords: [{ id: 7, signature: 'depositing' }],
    existingKey: value => value.id,
    desiredKey: value => value.id,
    existingSignature: value => value.signature,
    desiredSignature: value => value.signature,
    createItem: record => {
      const result = item(record.id, record.signature);
      created.push(result);
      return result;
    },
    appendItem: value => appended.push(value),
    removeItem: value => { value.removed = true; },
  });

  assert.equal(created.length, 1);
  assert.equal(appended.length, 1);
  assert.equal(appended[0].signature, 'depositing');
  assert.equal(oldDraft.removed, true);
  assert.equal(accidentalDuplicate.removed, true);
});

test('an unchanged record is reused exactly once and duplicates are removed', () => {
  const reusable = item(7, 'depositing');
  const duplicate = item(7, 'depositing');
  const appended = [];

  reconcileKeyedItems({
    existingItems: [reusable, duplicate],
    desiredRecords: [{ id: 7, signature: 'depositing' }],
    existingKey: value => value.id,
    desiredKey: value => value.id,
    existingSignature: value => value.signature,
    desiredSignature: value => value.signature,
    createItem: () => { throw new Error('should reuse'); },
    appendItem: value => appended.push(value),
    removeItem: value => { value.removed = true; },
  });

  assert.deepEqual(appended, [reusable]);
  assert.equal(reusable.removed, false);
  assert.equal(duplicate.removed, true);
});
