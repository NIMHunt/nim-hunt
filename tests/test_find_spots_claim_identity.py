from pathlib import Path


def test_claim_submission_does_not_choose_a_browser_payout_account() -> None:
    source = Path("static/find_spots.js").read_text(encoding="utf-8")

    assert "requestClaimPayoutAddress" not in source
    assert "nimiq.listAccounts()" not in source
    claim_submission = source.split("async function postClaimForSpot", 1)[1].split(
        "function redirectToClaim", 1
    )[0]
    assert "payout_address" not in claim_submission
