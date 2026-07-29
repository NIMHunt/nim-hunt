(() => {
    const body = document.body;
    const noticeBackdrop = document.getElementById('notice-backdrop');
    const guideLink = document.getElementById('notice-guide');
    if (!body || !noticeBackdrop || !guideLink || typeof window.fetch !== 'function') return;

    const nativeFetch = window.fetch.bind(window);
    let awaitingFirstVisitNotice = false;
    let showingFirstVisitNotice = false;

    const previewRequested = body.dataset.testFeaturesEnabled === 'true'
        && !body.dataset.homeInformationView
        && new URLSearchParams(window.location.search).get('preview') === 'first-visit';

    function requestPath(input) {
        try {
            const rawUrl = input instanceof Request ? input.url : String(input || '');
            return new URL(rawUrl, window.location.href).pathname;
        } catch (error) {
            return '';
        }
    }

    function canShowFirstVisit(data) {
        return Boolean(data?.user && !data.user.is_banned);
    }

    window.fetch = async (...args) => {
        const response = await nativeFetch(...args);
        if (requestPath(args[0]) !== '/api/home/session') return response;

        // This wrapper exists only to observe the one Home-session response.
        // Restore the native function immediately so every later request follows
        // NimHunt's ordinary fetch path without another interception layer.
        window.fetch = nativeFetch;

        let data = null;
        try {
            data = await response.clone().json();
        } catch (error) {
            return response;
        }

        if (data?.created && canShowFirstVisit(data)) {
            awaitingFirstVisitNotice = true;
        }

        if (!previewRequested || !canShowFirstVisit(data)) return response;

        awaitingFirstVisitNotice = true;
        const previewData = { ...data, created: true };
        const headers = new Headers(response.headers);
        headers.delete('content-encoding');
        headers.delete('content-length');
        headers.set('content-type', 'application/json');

        return new Response(JSON.stringify(previewData), {
            status: response.status,
            statusText: response.statusText,
            headers,
        });
    };

    function syncGuideVisibility() {
        if (noticeBackdrop.hidden) {
            guideLink.hidden = true;
            showingFirstVisitNotice = false;
            return;
        }

        if (awaitingFirstVisitNotice) {
            guideLink.hidden = false;
            awaitingFirstVisitNotice = false;
            showingFirstVisitNotice = true;
            return;
        }

        if (!showingFirstVisitNotice) guideLink.hidden = true;
    }

    const observer = new MutationObserver(syncGuideVisibility);
    observer.observe(noticeBackdrop, {
        attributes: true,
        attributeFilter: ['hidden'],
    });

    guideLink.addEventListener('click', () => {
        noticeBackdrop.hidden = true;
    });

    syncGuideVisibility();
})();
