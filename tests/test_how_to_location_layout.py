from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_location_permission_heading_spans_both_columns() -> None:
    stylesheet = _read("static/how_to_location_layout.css")

    assert '"title title"' in stylesheet
    assert '"copy example"' in stylesheet
    assert ".how-to-location-copy-column" in stylesheet
    assert "display: contents;" in stylesheet
    assert ".how-to-location-title" in stylesheet
    assert "grid-area: title;" in stylesheet


def test_location_permission_panel_stacks_at_560_pixels() -> None:
    stylesheet = _read("static/how_to_location_layout.css")

    assert "@media (max-width: 560px)" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr);" in stylesheet
    assert '"title"' in stylesheet
    assert '"copy"' in stylesheet
    assert '"example"' in stylesheet


def test_stacked_location_copy_uses_available_card_width() -> None:
    stylesheet = _read("static/how_to_location_layout.css")
    mobile_rules = stylesheet.split("@media (max-width: 560px)", 1)[1]
    copy_rule = mobile_rules.split(".how-to-location-copy {", 1)[1].split("}", 1)[0]

    assert "width: 100%;" in copy_rule
    assert "max-width: none;" in copy_rule
    assert "justify-self: stretch;" in copy_rule
    assert "24rem" not in copy_rule


def test_location_layout_override_loads_only_for_how_to_page() -> None:
    shell = _read("templates/_home_shell.html")
    conditional = "{% if information_view | default('') == 'how-to' %}"
    stylesheet_link = (
        '<link rel="stylesheet" '
        'href="/static/how_to_location_layout.css?'
        'v=full-width-title-stack-560-v1-20260731">'
    )

    assert conditional in shell
    assert stylesheet_link in shell
    assert shell.index(conditional) < shell.index(stylesheet_link)
    assert shell.index(stylesheet_link) < shell.index("{% endif %}", shell.index(stylesheet_link))
