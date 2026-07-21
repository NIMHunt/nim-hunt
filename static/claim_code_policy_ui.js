const OLD_DURATION_CODE_NOTICE =
    'This one-time code is used when verification begins and is not restored if the duration check later fails.';

const SUCCESS_ONLY_CODE_NOTICE =
    'This code is used only if this claim succeeds. Other people may attempt the same code; the first successful claim uses it.';

function updateClaimCodeNotice() {
    const summary = document.getElementById('claim-summary');
    if (!summary) return;

    for (const paragraph of summary.querySelectorAll('p')) {
        if (paragraph.textContent?.trim() === OLD_DURATION_CODE_NOTICE) {
            paragraph.textContent = SUCCESS_ONLY_CODE_NOTICE;
        }
    }
}

const summary = document.getElementById('claim-summary');
if (summary) {
    updateClaimCodeNotice();
    new MutationObserver(updateClaimCodeNotice).observe(summary, {
        childList: true,
        subtree: true,
    });
}
