export const MY_SPOTS_MAP_COLOURS = Object.freeze({
    activeStandard: '#21bca5',
    activePrizedraw: '#ffc435',
    completed: '#0582ca',
    cancelled: '#d94432',
    muted: '#8c90a8',
});

export function mySpotEndHasElapsed(spot, nowSeconds = Math.floor(Date.now() / 1000)) {
    const endsAt = Number(spot?.ends_at);
    const now = Number(nowSeconds);
    return Number.isFinite(endsAt)
        && endsAt > 0
        && Number.isFinite(now)
        && endsAt <= now;
}

export function spotsVisibleOnMySpotsMap(spots, nowSeconds = Math.floor(Date.now() / 1000)) {
    if (!Array.isArray(spots)) return [];
    return spots.filter((spot) => !mySpotEndHasElapsed(spot, nowSeconds));
}

export function mySpotsMapColourForSpot(spot) {
    const status = String(spot?.status_label || '').trim().toLowerCase();
    if (status === 'completed') return MY_SPOTS_MAP_COLOURS.completed;
    if (status === 'cancelled') return MY_SPOTS_MAP_COLOURS.cancelled;
    if (status !== 'active') return MY_SPOTS_MAP_COLOURS.muted;
    return spot.is_prizedraw
        ? MY_SPOTS_MAP_COLOURS.activePrizedraw
        : MY_SPOTS_MAP_COLOURS.activeStandard;
}
