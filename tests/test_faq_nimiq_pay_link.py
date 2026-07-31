from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_faq_nimiq_pay_answer_links_to_nimpay() -> None:
    partial = (ROOT / "templates" / "_faq_content.html").read_text(encoding="utf-8")
    answer = partial.split('id="faq-answer-nimiq-pay"', 1)[1].split("</div>", 1)[0]

    assert (
        '<a class="welcome-link" href="https://nimpay.app" '
        'target="_blank" rel="noopener noreferrer">Nimiq Pay</a>'
    ) in answer
    assert answer.count('href="https://nimpay.app"') == 1
