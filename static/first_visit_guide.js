(() => {
    const WALLET_NOTICE_SESSION_KEY = 'nimhunt.wallet-unavailable-notice-shown.v1';
    const noticeBackdrop = document.getElementById('notice-backdrop');
    const guideLink = document.getElementById('notice-guide');
    if (!noticeBackdrop || !guideLink || typeof window.fetch !== 'function') return;

    const nativeFetch = window.fetch.bind(window);
    let awaitingFirstVisitNotice = false;
    let showingFirstVisitNotice = false;
    let awaitingWalletUnavailableNotice = false;
    let suppressWalletUnavailableNotice = false;

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

    function claimWalletNoticeForSession() {
        try {
            if (window.sessionStorage.getItem(WALLET_NOTICE_SESSION_KEY) === '1') {
                return false;
            }
            window.sessionStorage.setItem(WALLET_NOTICE_SESSION_KEY, '1');
            return true;
        } catch (error) {
            // Some privacy modes disable browser storage. In that case, keep the
            // original behaviour rather than accidentally hiding useful guidance.
            return true;
        }
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

            if (data?.code === 'wallet_unavailable') {
                awaitingWalletUnavailableNotice = true;
                suppressWalletUnavailableNotice = !claimWalletNoticeForSession();
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

        if (awaitingWalletUnavailableNotice) {
            awaitingWalletUnavailableNotice = false;
            if (suppressWalletUnavailableNotice) {
                suppressWalletUnavailableNotice = false;
                noticeBackdrop.hidden = true;
                return;
            }
            suppressWalletUnavailableNotice = false;
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