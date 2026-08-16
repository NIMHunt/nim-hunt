// Retry a failed claim-wallet verification only after the user interacts with a
// claim-capable control. This avoids both failure modes at once: a declined or
// transiently failed first signature is not permanent, and background map/status
// refreshes never nag the user with repeated wallet prompts.

export const DEFAULT_CLAIM_AUTH_RETRY_SELECTOR = '.spot-claim-button, .spot-report-button';

export function createClaimAuthInteractionRetry({
    documentRef,
    retry,
    selector = DEFAULT_CLAIM_AUTH_RETRY_SELECTOR,
    consumeMatchingInteraction = selector === DEFAULT_CLAIM_AUTH_RETRY_SELECTOR,
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

    const consumeEvent = (event) => {
        if (!consumeMatchingInteraction) return;
        event?.preventDefault?.();
        if (typeof event?.stopImmediatePropagation === 'function') {
            event.stopImmediatePropagation();
        } else {
            event?.stopPropagation?.();
        }
    };

    function handleClick(event) {
        const target = event?.target;
        const matched = typeof target?.closest === 'function'
            ? target.closest(selector)
            : null;
        if (!matched) return;

        // On Find Spots the retry owns Claim/Report clicks until wallet
        // authentication finishes. Do not let the same click also enter the
        // page action, where it could change CLAIM to "Confirming…" or open the
        // report/claim modal while the signing dialog is still unresolved.
        // Keep consuming matching clicks while the retry is in flight as well,
        // so a fast second tap cannot start the underlying action prematurely.
        if (armedDeviceId || inFlight) consumeEvent(event);
        if (!armedDeviceId || inFlight) return;

        const deviceId = armedDeviceId;
        armedDeviceId = null;
        inFlight = true;

        // For the Find Spots controls, keep the capture listener attached while
        // the wallet dialog is open so extra Claim/Report clicks are swallowed.
        // Claim-detail pages use a broad `body` selector and deliberately do not
        // consume normal page interactions, preserving their previous behavior.
        if (!consumeMatchingInteraction) removeListener();

        Promise.resolve()
            .then(() => retry(deviceId))
            .catch(() => {
                // A second decline or transient failure is still retryable, but
                // only after another explicit claim/report interaction.
                arm(deviceId);
            })
            .finally(() => {
                inFlight = false;
                if (!armedDeviceId) removeListener();
            });
    }

    return {
        arm,
        disarm,
        isArmed: () => Boolean(armedDeviceId),
        isInFlight: () => inFlight,
    };
}
