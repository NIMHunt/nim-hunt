from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_homepage_exposes_information_links_below_metrics() -> None:
    home_template = _read("templates/home.html")
    shell_template = _read("templates/_home_shell.html")

    assert "{% set show_information_links = true %}" in home_template
    assert shell_template.index('id="home-metrics"') < shell_template.index(
        'class="home-information-links"'
    )
    assert 'href="/static/about.html"' in shell_template
    assert 'href="/static/roadmap.html"' in shell_template
    assert "/static/static_pages.css?v=static-pages-v1-20260724" in shell_template


def test_about_copy_is_kept_in_translation_ready_catalogue() -> None:
    catalogue = _read("static/static_page_text.js")

    assert "STATIC_PAGE_TEXT_CATALOGUES" in catalogue
    assert "NimHunt is a simple geofaucet-style and Prizedraw mini-app" in catalogue
    assert "a loyal member of the NIMIQ Community" in catalogue
    assert "paragraphs:" in catalogue


def test_roadmap_data_is_simple_and_customisable() -> None:
    roadmap = _read("static/roadmap.json")

    assert '"sections": [' in roadmap
    assert '"heading": "Q3 2026"' in roadmap
    assert '"heading": "Beyond"' in roadmap
    assert '"Localisation Features"' in roadmap
    assert '"Desktop View"' in roadmap


def test_static_pages_reuse_homepage_visual_structure() -> None:
    for page_name in ("about", "roadmap"):
        page = _read(f"static/{page_name}.html")

        assert 'class="home-shell"' in page
        assert 'class="hero-card home-hero-link"' in page
        assert 'class="welcome-card static-page-card"' in page
        assert 'class="nq-label static-page-title"' in page
        assert '/static/home.css?v=marker-white-outline-v1-20260723' in page
        assert '/static/static_page.js?v=static-pages-v1-20260724' in page


def test_roadmap_renderer_uses_safe_text_and_bypasses_stale_data() -> None:
    renderer = _read("static/static_page.js")

    assert "textContent =" in renderer
    assert "innerHTML" not in renderer
    assert "cache: 'no-store'" in renderer
