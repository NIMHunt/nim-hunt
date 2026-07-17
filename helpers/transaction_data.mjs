const MAX_TRANSACTION_DATA_BYTES = 64;

export function encodeTransactionMemo(value) {
  const memo = String(value || '').trim();
  if (!memo) return new Uint8Array();

  const data = new TextEncoder().encode(memo);
  if (data.byteLength > MAX_TRANSACTION_DATA_BYTES) {
    throw new Error(`memo must not exceed ${MAX_TRANSACTION_DATA_BYTES} UTF-8 bytes`);
  }
  return data;
}
