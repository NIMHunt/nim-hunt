// Retry a failed claim-wallet verification only after the user interacts with a
// claim-capable control. This avoids both failure modes at once: a declined or
// transiently failed first signature is not permanent, and background map/status
// refreshes never nag the user with repeated wallet prompts.

export const DEFAULT_CLAIM_AUTH_RETRY_SELECTOR = '.spot-claim-button, .spot-report-button';

export function createClaimAuthInteractionRetry({
    documentRef,
    retry,
    selector = DEFAULT_CLAIM_AUTH_RETRY_SELECTOR,
} = {}) {
    if (!documentRef || typeof documentRef.addEventListener !== 'function') {
        throw new Error('A document-like event target is required.');
    }
    if (typeof retry !== 'function') {
        throw new Error('A retry callback is required.');
    }

    let armedDeviceId = null;
    let listening = false;
    let inFlight = false;

    const removeListener = () => {
        if (!listening) return;
        documentRef.removeEventListener('click', handleClick, true);
        listening = false;
    };

    const arm = (deviceIdHash) => {
        const deviceId = String(deviceIdHash || '').trim().toLowerCase();
        if (!deviceId) return;
        armedDeviceId = deviceId;
        if (listening) return;
        documentRef.addEventListener('click', handleClick, true);
        listening = true;
    };

    const disarm = () => {
        armedDeviceId = null;
        removeListener();
    };

    function handleClick(event) {
        const target = event?.target;
        const matched = typeof target?.closest === 'function'
            ? target.closest(selector)
            : null;
        if (!matched || !armedDeviceId || inFlight) return;

        const deviceId = armedDeviceId;
        armedDeviceId = null;
        removeListener();
        inFlight = true;

        Promise.resolve()
            .then(() => retry(deviceId))
            .catch(() => {
                // A second decline or transient failure is still retryable, but
                // only after another explicit claim/report interaction.
                arm(deviceId);
            })
            .finally(() => {
                inFlight = false;
            });
    }

    return {
        arm,
        disarm,
        isArmed: () => Boolean(armedDeviceId),
        isInFlight: () => inFlight,
    };
}
