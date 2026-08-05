from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_VERSION = "spot-duplicate-v1-20260805"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_duplicate_button_reuses_create_modal_and_dedicated_api():
    module = source("static/spot_duplicate.js")
    bootstrap = source("static/my_spots_bootstrap.js")
    template = source("templates/my_spots.html")

    assert "button.textContent = 'Duplicate'" in module
    assert "createOpen.click();" in module
    assert "titleInput.value" in module
    assert "prizedrawInput.checked" in module
    assert "requestTargetsOrdinaryDraftCreation" in module
    assert "`/api/my-spots/${spotId}/duplicate`" in module
    assert f"./spot_duplicate.js?v={CACHE_VERSION}" in bootstrap
    assert f"/static/my_spots_bootstrap.js?v={CACHE_VERSION}-" in template


def test_duplicate_backend_is_isolated_from_transaction_and_claim_writes():
    module = source("spot_duplicate.py")

    assert "duplicate_owned_spot_as_draft" in module
    assert "count_draft_spots_by_user" in module
    assert "SPOT_CREATED_BY" in module
    assert "create_spot(" in module
    assert "create_prizedraw(" in module
    assert "create_spot_deposit_transaction" not in module
    assert "create_claim(" not in module
    assert "create_claim_code(" not in module
    assert "trans_updater" not in module
    assert "settlement_updater" not in module
