const TRAILING_DETAIL = /\s*\([^)]*\)\s*$/;
const CLAIM_BADGE_LIST_IDS = ['claim-detail-list', 'my-claims-list'];

export function compactClaimBadgeStatus(value) {
    return String(value ?? '').replace(TRAILING_DETAIL, '').trim();
}

function compactVisibleClaimBadges(claimList) {
    for (const badge of claimList.querySelectorAll('.spot-badge')) {
        const compactText = compactClaimBadgeStatus(badge.textContent);
        if (compactText && compactText !== badge.textContent) badge.textContent = compactText;
    }
}

if (typeof document !== 'undefined') {
    for (const listId of CLAIM_BADGE_LIST_IDS) {
        const claimList = document.getElementById(listId);
        if (!claimList) continue;
        compactVisibleClaimBadges(claimList);
        new MutationObserver(() => compactVisibleClaimBadges(claimList)).observe(claimList, {
            childList: true,
            subtree: true,
            characterData: true,
        });
    }
}
