const NIMIQ_TRANSACTION_HASH_RE = /^[0-9a-f]{64}$/i;

export const PENDING_DEPOSIT_STORAGE_KEY = 'nimhunt.pendingDepositSubmissions.v3';
export const LEGACY_PENDING_DEPOSIT_STORAGE_KEY = 'nimhunt.pendingDepositSubmission.v2';
export const OBSOLETE_PENDING_DEPOSIT_STORAGE_KEY = 'nimhunt.pendingDepositSubmission.v1';

function parseJson(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
        return JSON.parse(value);
    } catch (_err) {
        return null;
    }
}

export function normalisePendingDepositRecord(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;

    const spotId = Number(value.spotId);
    const amount = Number(value.amount);
    const txHash = String(value.txHash || '').trim().toLowerCase();
    const fromAddress = String(value.fromAddress || '').trim();
    const createdAt = Number(value.createdAt || 0);

    if (!Number.isSafeInteger(spotId) || spotId <= 0) return null;
    if (!Number.isSafeInteger(amount) || amount <= 0) return null;
    if (!NIMIQ_TRANSACTION_HASH_RE.test(txHash)) return null;
    if (!fromAddress) return null;

    return {
        spotId,
        txHash,
        fromAddress,
        amount,
        createdAt: Number.isFinite(createdAt) && createdAt >= 0 ? createdAt : 0,
    };
}

function deduplicateRecords(values) {
    const records = [];
    const seenHashes = new Set();

    for (const value of Array.isArray(values) ? values : []) {
        const record = normalisePendingDepositRecord(value);
        if (!record || seenHashes.has(record.txHash)) continue;
        seenHashes.add(record.txHash);
        records.push(record);
    }

    return records;
}

export function createPendingDepositStore(storage) {
    function getItem(key) {
        try {
            return storage?.getItem?.(key) ?? null;
        } catch (_err) {
            return null;
        }
    }

    function removeItem(key) {
        try {
            storage?.removeItem?.(key);
            return true;
        } catch (_err) {
            return false;
        }
    }

    function persist(records) {
        try {
            if (!storage?.setItem || !storage?.removeItem) return false;
            if (records.length === 0) {
                storage.removeItem(PENDING_DEPOSIT_STORAGE_KEY);
            } else {
                storage.setItem(PENDING_DEPOSIT_STORAGE_KEY, JSON.stringify(records));
            }
            return true;
        } catch (_err) {
            return false;
        }
    }

    function load() {
        // PR #29 briefly used a broad provider-response parser. Never replay
        // hashes stored by that implementation because they may not identify
        // a transaction.
        removeItem(OBSOLETE_PENDING_DEPOSIT_STORAGE_KEY);

        const queueRaw = getItem(PENDING_DEPOSIT_STORAGE_KEY);
        const parsedQueue = parseJson(queueRaw);
        const records = deduplicateRecords(parsedQueue);
        let needsPersist = queueRaw !== null && (
            !Array.isArray(parsedQueue)
            || records.length !== parsedQueue.length
        );

        // Preserve an unresolved v2 submission across deployment of the queue.
        // Only remove the legacy slot after the merged queue is safely written.
        const legacyRaw = getItem(LEGACY_PENDING_DEPOSIT_STORAGE_KEY);
        const legacyRecord = normalisePendingDepositRecord(parseJson(legacyRaw));
        if (legacyRecord && !records.some((record) => record.txHash === legacyRecord.txHash)) {
            records.push(legacyRecord);
            needsPersist = true;
        }

        if ((needsPersist || legacyRaw !== null) && persist(records)) {
            removeItem(LEGACY_PENDING_DEPOSIT_STORAGE_KEY);
        }

        return records.map((record) => ({ ...record }));
    }

    function save(value) {
        const record = normalisePendingDepositRecord(value);
        if (!record) return false;

        const records = load();
        const conflicting = records.find(
            (item) => item.txHash === record.txHash && item.spotId !== record.spotId,
        );
        if (conflicting) return false;

        const next = records.filter((item) => item.txHash !== record.txHash);
        next.push(record);
        return persist(next);
    }

    function remove(value) {
        const record = normalisePendingDepositRecord(value);
        if (!record) return false;

        const records = load();
        const next = records.filter(
            (item) => !(item.txHash === record.txHash && item.spotId === record.spotId),
        );
        if (next.length === records.length) return true;
        return persist(next);
    }

    return { load, save, remove };
}

export async function recoverPendingDepositQueue({ store, submit }) {
    if (!store || typeof store.load !== 'function' || typeof store.remove !== 'function') {
        throw new TypeError('A pending-deposit store is required.');
    }
    if (typeof submit !== 'function') {
        throw new TypeError('A pending-deposit submit function is required.');
    }

    const records = store.load();
    const failures = [];
    let recoveredCount = 0;

    // Recover sequentially. This avoids issuing multiple recording writes at
    // once while still retaining every failed record for a later page load.
    for (const record of records) {
        try {
            await submit(record);
            store.remove(record);
            recoveredCount += 1;
        } catch (error) {
            failures.push({ record, error });
        }
    }

    return {
        attemptedCount: records.length,
        recoveredCount,
        failures,
    };
}
