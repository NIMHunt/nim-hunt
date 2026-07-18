const HEALTH_ENDPOINT = '/healthz';

function networkModeLabel(network) {
    const value = String(network || '').trim().toLowerCase();

    if (value === 'testalbatross' || value === 'testnet' || value === '5') {
        return 'Testnet mode';
    }
    if (value === 'devalbatross' || value === 'devnet' || value === '6') {
        return 'Devnet mode';
    }

    // MainAlbatross deliberately has no banner. Unknown networks are also kept
    // hidden rather than risking an inaccurate label; startup validation rejects
    // unsupported network names before the public app becomes available.
    return '';
}

function createNetworkModeBanner(label) {
    const banner = document.createElement('div');
    banner.id = 'network-mode-banner';
    banner.className = 'home-metrics network-mode-banner';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-label', `Nimiq ${label}`);

    const badge = document.createElement('span');
    badge.className = 'nq-label network-mode-label';
    badge.textContent = label;

    banner.append(badge);
    return banner;
}

export async function installNetworkModeBanner() {
    const shell = document.querySelector('main.home-shell');
    if (!shell || document.getElementById('network-mode-banner')) return;

    try {
        const response = await fetch(HEALTH_ENDPOINT, {
            cache: 'no-store',
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) return;

        const data = await response.json();
        const label = networkModeLabel(data?.network);
        if (!label || document.getElementById('network-mode-banner')) return;

        shell.prepend(createNetworkModeBanner(label));
    } catch (err) {
        // The banner is informational. A failed health request must not prevent
        // the page itself from loading or operating normally.
        console.warn('Could not determine the active Nimiq network.', err);
    }
}
