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
    assert "/static/static_pages.css?v=home-information-v9-how-to-interactions-20260729" in shell_template
    assert "/static/how_to.js?v=how-to-platform-toggle-v3-20260729" in shell_template


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


def test_how_to_location_panel_uses_one_uniform_size_and_stable_shadow() -> None:
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
    assert "/static/images/how-to/warning-ios.png" in partial
    assert "/static/images/how-to/warning-android.png" in partial

    assert "--how-to-location-font-size: 2rem;" in stylesheet
    assert ".how-to-location-title," in stylesheet
    assert ".how-to-location-copy," in stylesheet
    assert ".how-to-platform-button {" in stylesheet
    assert "font-size: var(--how-to-location-font-size) !important;" in stylesheet
    assert "width: 100%;" in stylesheet
    assert "max-width: none;" in stylesheet
    assert ".nq-style .how-to-location-box:focus-within" in stylesheet
    assert ".nq-style .how-to-location-box:active" in stylesheet
    assert "box-shadow: 0 0 32px rgba(233, 164, 0, 0.34) !important;" in stylesheet
    assert "transform: none !important;" in stylesheet
    assert "cursor: default !important;" in stylesheet
    assert ".how-to-permission-example[hidden]" in stylesheet

    assert "aria-selected" in controller
    assert "panel.hidden =" in controller
    assert "/Android/i.test(navigator.userAgent)" in controller
    assert "ArrowLeft" in controller
    assert "ArrowRight" in controller
    assert "fetch(" not in controller
    assert "innerHTML" not in controller


def test_how_to_story_has_complete_copy_shared_map_tooltips_and_real_claim_button() -> None:
    partial = _read("templates/_how_to_content.html")
    stylesheet = _read("static/static_pages.css")
    home_stylesheet = _read("static/home.css")
    tooltip_stylesheet = _read("static/how_to_map_tooltip.css")

    assert "This is you. Say hello!" in partial
    assert "This is a Spot. This is where you can find some NIM." in partial
    assert "When you move onto a Spot, you can click this button to receive some NIM." in partial
    assert "If your claim is valid, the NIM will enter your account immediately." in partial
    assert "Get started by clicking “Find Spots” below. Happy Hunting!" in partial

    shared_tooltip_classes = (
        'class="how-to-map-tooltip leaflet-tooltip leaflet-tooltip-top '
        'map-spot-title-tooltip"'
    )
    assert partial.count(shared_tooltip_classes) == 2
    assert 'role="tooltip">Hello!</span>' in partial
    assert 'role="tooltip">Example Spot</span>' in partial
    assert 'class="nq-button-pill spot-claim-button is-standard how-to-claim-button" type="button">CLAIM</button>' in partial
    assert "#nq-under-payment" in partial
    assert "how-to-user-marker" in partial
    assert "how-to-spot-radius" in partial

    assert ".nq-style .how-to-story .how-to-step > .how-to-step-copy" in stylesheet
    assert "font-size: 2rem !important;" in stylesheet
    assert "color: var(--nh-text) !important;" in stylesheet
    assert "opacity: 1 !important;" in stylesheet
    assert "text-align: center !important;" in stylesheet
    assert ".how-to-step-reversed .how-to-step-copy" not in stylesheet

    assert ".leaflet-tooltip.map-spot-title-tooltip" in home_stylesheet
    assert "background: rgba(255, 255, 255, 0.96);" in home_stylesheet
    assert "font-size: 1.6rem;" in home_stylesheet
    assert ".how-to-marker-example:hover .how-to-map-tooltip" in tooltip_stylesheet
    assert ".how-to-marker-example:focus .how-to-map-tooltip" in tooltip_stylesheet
    assert "cursor: default !important;" in tooltip_stylesheet
    assert "font-size:" not in tooltip_stylesheet
    assert "background:" not in tooltip_stylesheet

    assert ".nq-style button.spot-claim-button.how-to-claim-button.nq-button-pill:hover" in stylesheet
    assert ".nq-style button.spot-claim-button.how-to-claim-button.nq-button-pill:active" in stylesheet
    assert "background: var(--nh-success) !important;" in stylesheet
    assert "color: #ffffff !important;" in stylesheet
    assert "transform: translateY(-2px) !important;" in stylesheet
    assert "transform: translateY(1px) scale(0.98) !important;" in stylesheet

    assert ".how-to-payment-icon" in stylesheet
    assert "color: #ffc435;" in stylesheet
    assert ".how-to-closing-copy" in stylesheet
    assert "gap: clamp(5.5rem, 19vw, 8.5rem);" in stylesheet


def test_how_to_uses_user_supplied_image_paths_only() -> None:
    partial = _read("templates/_how_to_content.html")
    stylesheet = _read("static/static_pages.css")
    controller = _read("static/how_to.js")

    figure = partial.split('<figure class="how-to-find-spots-figure">', 1)[1]
    assert figure.count("<img") == 1
    assert "/static/images/how-to/example.png" in figure
    assert 'width="703"' in figure
    assert 'height="1015"' in figure
    assert "data-how-to-image-chunk-prefix" not in partial
    assert "data:image/webp;base64," not in controller
    assert "chunks.join" not in controller

    assert "-webkit-mask-image: linear-gradient(to bottom, #000 0%, #000 88%, transparent 100%);" in stylesheet
    assert "max-width: 440px;" in stylesheet

    asset_dir = ROOT / "static" / "images" / "how-to"
    for required_asset in (
        "warning-android.png",
        "warning-ios.png",
        "example.png",
    ):
        asset = asset_dir / required_asset
        assert asset.exists()
        signature = asset.read_bytes()[:8]
        assert signature.startswith(b"\x89PNG") or signature.startswith(b"\xff\xd8\xff")

    for obsolete_asset in (
        "ios-location-permission.svg",
        "android-location-permission.svg",
        "find-spots-1.svg",
        "find-spots-2.svg",
        "find-spots-3.svg",
        "find-spots-4.svg",
        "find-spots-5.svg",
        "find-spots-6.svg",
        "find-spots-image-1.b64",
        "find-spots-image-2.b64",
        "find-spots-image-3.b64",
        "find-spots-image-4.b64",
    ):
        assert not (asset_dir / obsolete_asset).exists()


def test_obsolete_standalone_documents_are_removed() -> None:
    assert not (ROOT / "static/about.html").exists()
    assert not (ROOT / "static/roadmap.html").exists()
