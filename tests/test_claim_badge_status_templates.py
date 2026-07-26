from pathlib import Path


CLAIM_TEMPLATE = Path("templates/claim.html")
MY_CLAIMS_TEMPLATE = Path("templates/my_claims.html")


def test_claim_badge_helper_loads_on_detail_and_list_pages():
    claim_template = CLAIM_TEMPLATE.read_text(encoding="utf-8")
    my_claims_template = MY_CLAIMS_TEMPLATE.read_text(encoding="utf-8")

    assert "/static/claim_badge_status.js" in claim_template
    assert "/static/claim_badge_status.js?v=claim-badge-v2-20260726" in my_claims_template
