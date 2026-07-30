(() => {
    const noticeBackdrop = document.getElementById('notice-backdrop');
    const guideLink = document.getElementById('notice-guide');
    if (!noticeBackdrop || !guideLink || typeof window.fetch !== 'function') return;

    const nativeFetch = window.fetch.bind(window);
    let awaitingFirstVisitNotice = false;
    let showingFirstVisitNotice = false;

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
        const isHomeSession = requestPath(args[0]) === '/api/home/session';

        try {
            const response = await nativeFetch(...args);
            if (!isHomeSession) return response;

            let data = null;
            try {
                data = await response.clone().json();
            } catch (error) {
                return response;
            }

            if (data?.created && canShowFirstVisit(data)) {
                awaitingFirstVisitNotice = true;
            }

            return response;
        } finally {
            // Observe only the real Home-session response, then restore the
            // browser's native fetch function, including when the request fails.
            if (isHomeSession) window.fetch = nativeFetch;
        }
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