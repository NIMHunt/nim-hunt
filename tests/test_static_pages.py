import base64
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
    assert "information_view == 'how-to'" in home_template
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
    assert "information_view | default('') in ('about', 'how-to', 'roadmap')" in shell_template
    assert '{% include "_how_to_content.html" %}' in shell_template
    assert "/static/static_page.js?v=about-nimiq-pay-v2-20260725" in shell_template
    assert "/static/static_pages.css?v=home-information-v7-how-to-layout-20260729" in shell_template
    assert "/static/how_to.js?v=how-to-platform-image-v2-20260729" in shell_template


def test_information_links_change_only_the_current_page_link() -> None:
    shell_template = _read("templates/_home_shell.html")

    assert shell_template.index('id="home-metrics"') < shell_template.index(
        'class="home-information-links"'
    )
    assert '<a href="/?view=about">About</a>' in shell_template
    assert '<a href="/?view=how-to">How To</a>' in shell_template
    assert '<a href="/?view=roadmap">Roadmap</a>' in shell_template
    assert '<a href="/">Home</a>' in shell_template
    assert "information_view | default('') == 'about'" in shell_template
    assert "information_view | default('') == 'how-to'" in shell_template
    assert "information_view | default('') == 'roadmap'" in shell_template


def test_information_link_size_and_roadmap_alignment_match_requested_style() -> None:
    stylesheet = _read("static/static_pages.css")

    assert ".home-information-links a" in stylesheet
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in stylesheet
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
    assert "text: 'Nimiq Pay'" in catalogue
    assert "text: 'NimPay'" not in catalogue
    assert "href: 'https://nimpay.app'" in catalogue


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
                    "More Desktop Functionality",
                    "More Marketing",
                    "Dark Mode",
                    "Admin Panel",
                ],
            },
        ]
    }


def test_information_renderer_uses_safe_text_and_bypasses_stale_roadmap_data() -> None:
    renderer = _read("static/static_page.js")

    assert "document.body.dataset.homeInformationView" in renderer
    assert "textContent =" in renderer
    assert "document.createElement('a')" in renderer
    assert "link.href = part.href" in renderer
    assert "innerHTML" not in renderer
    assert "cache: 'no-store'" in renderer


def test_how_to_location_panel_uses_two_columns_and_switches_platforms() -> None:
    partial = _read("templates/_how_to_content.html")
    stylesheet = _read("static/static_pages.css")
    controller = _read("static/how_to.js")

    assert "project-disclaimer-note how-to-location-box nq-button gold action-button primary-action" in partial
    assert "Please Allow Precise Location!" in partial
    assert partial.count('class="project-disclaimer-icon"') == 2
    assert partial.count("#nq-alert-triangle") == 2
    assert 'class="how-to-location-layout"' in partial
    assert 'class="how-to-location-copy-column"' in partial
    assert 'class="how-to-location-example-column"' in partial
    assert 'role="tablist"' in partial
    assert 'data-how-to-platform="ios"' in partial
    assert 'data-how-to-platform="android"' in partial
    assert 'data-how-to-permission="ios"' in partial
    assert 'data-how-to-permission="android"' in partial
    assert "/static/images/how-to/ios-location-permission.svg" in partial
    assert "/static/images/how-to/android-location-permission.svg" in partial

    assert "grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr);" in stylesheet
    assert "pointer-events: auto;" in stylesheet
    assert "text-align: center !important;" in stylesheet
    assert "width: min(100%, 15rem);" in stylesheet
    assert ".how-to-permission-example[hidden]" in stylesheet

    assert "aria-selected" in controller
    assert "panel.hidden =" in controller
    assert "/Android/i.test(navigator.userAgent)" in controller
    assert "ArrowLeft" in controller
    assert "ArrowRight" in controller
    assert "innerHTML" not in controller


def test_how_to_story_centres_copy_and_uses_reliable_visuals() -> None:
    partial = _read("templates/_how_to_content.html")
    stylesheet = _read("static/static_pages.css")

    assert "This is you. Say hello!" in partial
    assert "This is a Spot. This is where you can find some NIM" in partial
    assert "When you move onto a Spot you can click this button to receive some NIM" in partial
    assert "If your claim is valid, the NIM will enter your account immediately." in partial
    assert "🎉" not in partial
    assert 'class="how-to-confetti-art"' in partial
    assert "how-to-confetti-streamer" in partial
    assert "how-to-user-marker" in partial
    assert "how-to-spot-radius" in partial
    assert "spot-claim-button is-standard" in partial

    assert ".how-to-user-marker::after" not in stylesheet
    assert "border: 0.22rem solid #1f2348;" in stylesheet
    assert "background: #1f2348;" in stylesheet
    assert "opacity: 0.92;" in stylesheet
    assert ".how-to-step-reversed .how-to-step-copy" in stylesheet
    assert "text-align: center;" in stylesheet
    assert ".nq-style .spot-claim-button.how-to-claim-button.nq-button-pill" in stylesheet
    assert "background: var(--nh-success) !important;" in stylesheet
    assert "background-image: none !important;" in stylesheet
    assert "color: #ffffff !important;" in stylesheet
    assert ".how-to-confetti-art" in stylesheet
    assert "gap: clamp(5.5rem, 19vw, 8.5rem);" in stylesheet


def test_how_to_find_spots_example_is_one_image_and_fades_out() -> None:
    partial = _read("templates/_how_to_content.html")
    stylesheet = _read("static/static_pages.css")
    controller = _read("static/how_to.js")

    figure = partial.split('<figure class="how-to-find-spots-figure">', 1)[1]
    assert figure.count("<img") == 1
    assert "find-spots-1.svg" not in partial
    assert "find-spots-6.svg" not in partial
    assert "data-how-to-find-spots-image" in partial
    assert 'data-how-to-image-chunk-prefix="/static/images/how-to/find-spots-image-"' in partial
    assert "width=\"440\"" in partial
    assert "height=\"633\"" in partial

    assert "[1, 2, 3, 4]" in controller
    assert "chunks.join('')" in controller
    assert "data:image/webp;base64," in controller
    assert "showFindSpotsImageError" in controller
    assert "innerHTML" not in controller

    assert "-webkit-mask-image: linear-gradient(to bottom, #000 0%, #000 88%, transparent 100%);" in stylesheet
    assert "max-width: 440px;" in stylesheet

    asset_dir = ROOT / "static" / "images" / "how-to"
    encoded = "".join(
        (asset_dir / f"find-spots-image-{number}.b64").read_text(encoding="ascii").strip()
        for number in range(1, 5)
    )
    decoded = base64.b64decode(encoded, validate=True)
    assert decoded[:4] == b"RIFF"
    assert decoded[8:12] == b"WEBP"

    for asset in (
        "ios-location-permission.svg",
        "android-location-permission.svg",
        "find-spots-image-1.b64",
        "find-spots-image-2.b64",
        "find-spots-image-3.b64",
        "find-spots-image-4.b64",
    ):
        assert (asset_dir / asset).exists()


def test_obsolete_standalone_documents_are_removed() -> None:
    assert not (ROOT / "static/about.html").exists()
    assert not (ROOT / "static/roadmap.html").exists()
