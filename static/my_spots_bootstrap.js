import './my_spots_world_wrap_install.js?v=my-spots-world-wrap-v1-20260803';

const NIMIQ_PAYMENT_MODULE_URL = './nimiq_payment.js?v=rapid-deposit-v1-20260805';
const MY_SPOTS_MODULE_URL = './my_spots.js?v=rapid-deposit-v1-20260805';

async function refreshCachedPaymentModule(url) {
    try {
        // fetch() resolves relative URLs against the document, unlike import().
        // Resolve from this bootstrap module so /my-spots refreshes the same
        // /static/... resource that the module loader will import below.
        const moduleUrl = new URL(url, import.meta.url);
        const response = await fetch(moduleUrl, { cache: 'reload' });
        if (!response.ok) return;
        // Fully consume the response so the refreshed entry is available to the
        // module loader before My Spots imports the same versioned URL.
        await response.arrayBuffer();
    } catch (_err) {
        // Cache refresh is best effort. The normal module import below still
        // reports a real load failure if the asset is genuinely unavailable.
    }
}

await refreshCachedPaymentModule(NIMIQ_PAYMENT_MODULE_URL);
await refreshCachedPaymentModule(MY_SPOTS_MODULE_URL);
await import(MY_SPOTS_MODULE_URL);
