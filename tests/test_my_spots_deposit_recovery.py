from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_VERSION = "rapid-deposit-v1-20260805"
BOOTSTRAP_CACHE_VERSION = "nimiq-2-compat-v1-20260812"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_spots_lets_the_wallet_choose_transaction_validity():
    payment = source("static/nimiq_payment.js")

    assert "await provider.getBlockNumber()" in payment
    assert "Math.abs(walletHeight - serverHeight)" in payment
    assert "validityStartHeight" not in payment
    assert "sendBasicTransactionWithData" in payment


def test_my_spots_uses_a_multi_record_pending_deposit_queue():
    page = source("static/my_spots.js")
    store = source("static/pending_deposit_store.js")

    assert f"./pending_deposit_store.js?v={CACHE_VERSION}" in page
    assert "recoverPendingDepositQueue" in page
    assert "recoverPendingDepositSubmissions" in page
    assert "clearPendingDepositSubmission(submittedRecord);" in page
    assert "nimhunt.pendingDepositSubmissions.v3" in store
    assert "nimhunt.pendingDepositSubmission.v2" in store
    assert "for (const record of records)" in store
    assert "await submit(record);" in store


def test_returning_webviews_receive_the_nimiq_2_payment_fix():
    page = source("static/my_spots.js")
    bootstrap = source("static/my_spots_bootstrap.js")
    template = source("templates/my_spots.html")

    assert f"./nimiq_payment.js?v={CACHE_VERSION}" in page
    assert f"const NIMIQ_PAYMENT_MODULE_URL = './nimiq_payment.js?v={CACHE_VERSION}'" in bootstrap
    assert f"const MY_SPOTS_MODULE_URL = './my_spots.js?v={CACHE_VERSION}'" in bootstrap
    assert "fetch(url, { cache: 'reload' })" in bootstrap
    assert "await response.arrayBuffer();" in bootstrap
    assert "await import(MY_SPOTS_MODULE_URL);" in bootstrap
    assert f"/static/my_spots_bootstrap.js?v={BOOTSTRAP_CACHE_VERSION}-" in template
    assert "./nimiq_payment.js?v=blockchain-flow-v1-20260720" not in page
