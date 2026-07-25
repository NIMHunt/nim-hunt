import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_information_views_are_variants_of_the_real_homepage() -> None:
    home_template = _read("templates/home.html")
    shell_template = _read("templates/_home_shell.html")

    assert "request.query_params.get('view', '')" in home_template
    assert "information_view == 'about'" in home_template
    assert "information_view == 'roadmap'" in home_template
    assert "{% set hero_href = '/' %}" in home_template

    for homepage_feature in (
        'id="notice-backdrop"',
        'class="project-disclaimer-note',
        'class="home-actions"',
        'id="find-spots-button"',
        'id="my-spots-button"',
        'id="my-claims-button"',
        'class="debug-card"',
        'id="home-metrics"',
    ):
        assert homepage_feature in shell_template

    assert 'id="home-information-content"' in shell_template
    assert 'id="home-dynamic-welcome"' in shell_template
    assert "information_view | default('') in ('about', 'roadmap')" in shell_template
    assert "/static/static_page.js?v=home-information-v3-20260725" in shell_template
    assert "/static/static_pages.css?v=home-information-v4-20260725" in shell_template


def test_information_links_change_only_the_current_page_link() -> None:
    shell_template = _read("templates/_home_shell.html")

    assert shell_template.index('id="home-metrics"') < shell_template.index(
        'class="home-information-links"'
    )
    assert '<a href="/?view=about">About</a>' in shell_template
    assert '<a href="/?view=roadmap">Roadmap</a>' in shell_template
    assert '<a href="/">Home</a>' in shell_template
    assert "information_view | default('') == 'about'" in shell_template
    assert "information_view | default('') == 'roadmap'" in shell_template


def test_information_link_size_and_roadmap_alignment_match_requested_style() -> None:
    stylesheet = _read("static/static_pages.css")

    assert ".home-information-links a" in stylesheet
    assert "font-size: 1.2rem;" in stylesheet
    assert ".roadmap-section," in stylesheet
    assert ".roadmap-heading," in stylesheet
    assert ".roadmap-item" in stylesheet
    assert "text-align: left;" in stylesheet
    assert ".static-page-title" in stylesheet
    assert "text-align: center;" in stylesheet


def test_roadmap_items_use_nimiq_generic_text_styling() -> None:
    renderer = _read("static/static_page.js")
    stylesheet = _read("static/static_pages.css")
    roadmap_item_rule = stylesheet.split(".nq-style .roadmap-items > li {", 1)[1].split(
        "}", 1
    )[0]

    assert "item.className = 'nq-text roadmap-item';" in renderer
    assert "static-page-copy roadmap-item" not in renderer
    assert "font-size:" not in roadmap_item_rule
    assert "font-weight:" not in roadmap_item_rule
    assert "line-height:" not in roadmap_item_rule
    assert "color:" not in roadmap_item_rule


def test_about_copy_is_kept_in_translation_ready_catalogue() -> None:
    catalogue = _read("static/static_page_text.js")

    assert "STATIC_PAGE_TEXT_CATALOGUES" in catalogue
    assert "NimHunt is a simple geofaucet-style and Prizedraw mini-app" in catalogue
    assert "a loyal member of the NIMIQ Community" in catalogue
    assert "paragraphs:" in catalogue


def test_roadmap_data_is_simple_customisable_and_current() -> None:
    roadmap = json.loads(_read("static/roadmap.json"))

    assert roadmap == {
        "sections": [
            {
                "heading": "ASAP",
                "items": ["Full release on NIMIQ blockchain"],
            },
            {
                "heading": "August",
                "items": [
                    "Localisation",
                    "Better Desktop Functionality",
                    "Better Marketing",
                    "Admin Panel",
                ],
            },
        ]
    }


def test_information_renderer_uses_safe_text_and_bypasses_stale_roadmap_data() -> None:
    renderer = _read("static/static_page.js")

    assert "document.body.dataset.homeInformationView" in renderer
    assert "textContent =" in renderer
    assert "innerHTML" not in renderer
    assert "cache: 'no-store'" in renderer


def test_obsolete_standalone_documents_are_removed() -> None:
    assert not (ROOT / "static/about.html").exists()
    assert not (ROOT / "static/roadmap.html").exists()
