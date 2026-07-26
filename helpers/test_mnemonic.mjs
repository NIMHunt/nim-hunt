import { randomBytes } from 'node:crypto';
import * as NimiqModule from '@nimiq/core';

const Nimiq = (NimiqModule.default && NimiqModule.default.MnemonicUtils)
  ? NimiqModule.default
  : NimiqModule;

export function generateTestMnemonic() {
  const generated = Nimiq.MnemonicUtils.entropyToMnemonic(randomBytes(32));
  return Array.isArray(generated) ? generated.join(' ') : String(generated);
}
