const HEALTH_ENDPOINT = '/healthz';

function isTestNetwork(network) {
    const value = String(network || '').trim().toLowerCase();
    return new Set([
        'testalbatross',
        'testnet',
        '5',
        'devalbatross',
        'devnet',
        '6',
    ]).has(value);
}

function removeAfterPageInitialisation(card) {
    const removeCard = () => card.remove();

    if (document.readyState === 'complete') {
        queueMicrotask(removeCard);
        return;
    }

    window.addEventListener('load', removeCard, { once: true });
}

export async function configureTroubleshootingVisibility() {
    const card = document.querySelector('.debug-card');
    if (!card) return;

    // Fail closed: the card begins hidden and is revealed only after the server
    // confirms that this deployment is using TestAlbatross or DevAlbatross.
    card.hidden = true;

    try {
        const response = await fetch(HEALTH_ENDPOINT, {
            cache: 'no-store',
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) {
            removeAfterPageInitialisation(card);
            return;
        }

        const data = await response.json();
        if (isTestNetwork(data?.network)) {
            card.hidden = false;
            return;
        }

        // Keep Home's existing JavaScript references valid while it starts, then
        // remove the unused card completely from mainnet and unknown deployments.
        removeAfterPageInitialisation(card);
    } catch (err) {
        removeAfterPageInitialisation(card);
        console.warn('Could not determine whether troubleshooting should be shown.', err);
    }
}

configureTroubleshootingVisibility();
