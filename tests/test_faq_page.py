import asyncio
import re
from pathlib import Path

import social_preview

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_faq_is_a_fourth_information_view_with_a_clean_route() -> None:
    home_template = _read("templates/home.html")
    shell_template = _read("templates/_home_shell.html")
    social_source = _read("social_preview.py")

    assert "information_view == 'faq'" in home_template
    assert "{% set page_title = 'FAQ · ' ~ app_name %}" in home_template
    assert '{% include "_faq_content.html" %}' in shell_template
    assert "/static/faq.js?v=faq-accordion-v1-20260730" in shell_template
    assert "/static/faq.css?v=faq-layout-v1-20260730" in shell_template
    assert '"/faq": "faq"' in social_source

    metadata = asyncio.run(social_preview.metadata_for_request("/faq"))
    assert metadata.title == "FAQ · NimHunt"
    assert metadata.canonical_url == "https://nimhunt.app/faq"
    assert social_preview.legacy_information_redirect("/", b"view=faq") == "/faq"
    assert '<meta name="twitter:card" content="summary_large_image">' in (
        social_preview.build_social_tags(metadata)
    )


def test_faq_reuses_spot_and_claim_collapsible_card_structure() -> None:
    partial = _read("templates/_faq_content.html")

    assert partial.count('class="spot-list-item faq-item"') == 8
    assert partial.count('class="spot-list-toggle faq-toggle"') == 8
    assert partial.count('class="spot-list-title faq-question"') == 8
    assert partial.count('class="spot-list-chevron"') == 8
    assert partial.count('class="spot-list-detail faq-answer"') == 8
    assert partial.count('class="nq-text faq-answer-copy"') == 8
    assert partial.count('aria-expanded="false"') == 8

    controls = re.findall(r'aria-controls="([^"]+)"', partial)
    answers = re.findall(r'id="(faq-answer-[^"]+)"', partial)
    assert controls == answers
    assert len(set(answers)) == 8


def test_faq_uses_final_copy_and_lowercase_feature_terms() -> None:
    partial = _read("templates/_faq_content.html")

    for question in (
        "What is NimHunt?",
        "Is NimHunt free to use?",
        "Do I need Nimiq Pay to use NimHunt?",
        "Why does NimHunt need my precise location?",
        "NimHunt says 'Location unavailable'. What should I do?",
        "How do I 'claim' NIM?",
        "What is a prizedraw?",
        "Why might a spot ask for a code?",
    ):
        assert question in partial

    assert "NIM-funded geographic spots" in partial
    assert "users do not pay to make a claim or enter a prizedraw" in partial
    assert "clearer GPS signal" in partial
    assert "Successfully entering a prizedraw" in partial
    assert "one-time-use codes" in partial
    assert "geographic Spots" not in partial
    assert "What is a Prizedraw?" not in partial
    assert "Why might a Spot" not in partial


def test_faq_answers_use_standard_site_text() -> None:
    stylesheet = _read("static/faq.css")
    answer_rule = stylesheet.split(
        ".nq-style .faq-answer > .faq-answer-copy {", 1
    )[1].split("}", 1)[0]

    assert "margin: 0 !important;" in answer_rule
    assert "text-align: left;" in answer_rule
    assert "font-size:" not in answer_rule
    assert "font-weight:" not in answer_rule
    assert "line-height:" not in answer_rule


def test_faq_controller_keeps_only_one_answer_open() -> None:
    controller = _read("static/faq.js")

    assert "function setExpanded(item, nextExpanded)" in controller
    assert "item.classList.toggle('is-expanded', expanded);" in controller
    assert "toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');" in controller
    assert "answer.hidden = !expanded;" in controller
    assert "function collapseOtherItems(activeItem)" in controller
    assert "if (item !== activeItem) setExpanded(item, false);" in controller
    assert "if (expanding) collapseOtherItems(item);" in controller
    assert "innerHTML" not in controller


def test_information_footer_uses_requested_four_link_order() -> None:
    shell_template = _read("templates/_home_shell.html")
    faq_styles = _read("static/faq.css")

    ordered_links = (
        '<a href="/about">About</a>\n'
        '            <a href="/how-to">How To</a>\n'
        '            <a href="/faq">FAQ</a>\n'
        '            <a href="/roadmap">Roadmap</a>'
    )
    assert ordered_links in shell_template
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in faq_styles
    assert ".faq-question" in faq_styles
    assert "white-space: normal;" in faq_styles
    assert ".nq-style .faq-answer > .faq-answer-copy" in faq_styles
