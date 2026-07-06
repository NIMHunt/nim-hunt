// Centralised NIM formatting helpers for NimHunt frontend code.
// Change NIM_DISPLAY_DECIMAL_PLACES here if the app should show more or fewer
// decimal places for displayed NIM amounts.

export const LUNA_PER_NIM = 100000;
export const NIM_DISPLAY_DECIMAL_PLACES = 1;

function decimalFactor(decimalPlaces = NIM_DISPLAY_DECIMAL_PLACES) {
    const places = Math.max(0, Number.parseInt(String(decimalPlaces), 10) || 0);
    return 10 ** places;
}

export function truncateToDecimalPlaces(value, decimalPlaces = NIM_DISPLAY_DECIMAL_PLACES) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return 0;

    const factor = decimalFactor(decimalPlaces);
    return Math.trunc(number * factor) / factor;
}

export function formatNimAmount(value, { suffix = true, decimalPlaces = NIM_DISPLAY_DECIMAL_PLACES } = {}) {
    const truncated = truncateToDecimalPlaces(value, decimalPlaces);
    const places = Math.max(0, Number.parseInt(String(decimalPlaces), 10) || 0);
    const text = truncated.toLocaleString([], {
        minimumFractionDigits: 0,
        maximumFractionDigits: places,
    });

    return suffix ? `${text} NIM` : text;
}

export function formatNimFromLuna(value, options = {}) {
    return formatNimAmount(Number(value || 0) / LUNA_PER_NIM, options);
}
