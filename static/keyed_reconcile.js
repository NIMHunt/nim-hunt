export function reconcileKeyedItems({
    existingItems,
    desiredRecords,
    existingKey,
    desiredKey,
    existingSignature,
    desiredSignature,
    createItem,
    appendItem,
    removeItem,
}) {
    const grouped = new Map();
    for (const item of existingItems) {
        const key = existingKey(item);
        const candidates = grouped.get(key) || [];
        candidates.push(item);
        grouped.set(key, candidates);
    }

    const finalItems = [];
    for (const record of desiredRecords) {
        const key = desiredKey(record);
        const signature = desiredSignature(record);
        const candidates = grouped.get(key) || [];
        const reusable = candidates.find((item) => existingSignature(item) === signature) || null;

        for (const candidate of candidates) {
            if (candidate !== reusable) removeItem(candidate);
        }

        const item = reusable || createItem(record);
        finalItems.push(item);
        grouped.delete(key);
    }

    for (const candidates of grouped.values()) {
        for (const candidate of candidates) removeItem(candidate);
    }
    for (const item of finalItems) appendItem(item);
    return finalItems;
}
