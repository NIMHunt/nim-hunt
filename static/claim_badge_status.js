const TRAILING_DETAIL = /\s*\([^)]*\)\s*$/;

export function compactClaimBadgeStatus(value) {
    return String(value ?? '').replace(TRAILING_DETAIL, '').trim();
}

function compactVisibleClaimBadges() {
    const claimList = document.getElementById('claim-detail-list');
    if (!claimList) return;
    for (const badge of claimList.querySelectorAll('.spot-badge')) {
        const compactText = compactClaimBadgeStatus(badge.textContent);
        if (compactText && compactText !== badge.textContent) badge.textContent = compactText;
    }
}

if (typeof document !== 'undefined') {
    const claimList = document.getElementById('claim-detail-list');
    if (claimList) {
        compactVisibleClaimBadges();
        new MutationObserver(compactVisibleClaimBadges).observe(claimList, {
            childList: true,
            subtree: true,
            characterData: true,
        });
    }
}
