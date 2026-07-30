from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_roadmap_primary_copy_uses_matching_weight_and_real_bullets() -> None:
    renderer = _read("static/static_page.js")
    stylesheet = _read("static/home_information_polish.css")
    shell_template = _read("templates/_home_shell.html")

    assert "item.className = 'nq-text roadmap-item';" in renderer

    list_rule = stylesheet.split(".nq-style .roadmap-items {", 1)[1].split("}", 1)[0]
    item_rule = stylesheet.split(
        ".nq-style .roadmap-items > .roadmap-item {", 2
    )[2].split("}", 1)[0]

    assert "display: block;" in list_rule
    assert "padding-inline-start: 2.4rem !important;" in list_rule
    assert "list-style: disc outside;" in list_rule

    assert "display: list-item;" in item_rule
    assert "margin: 0 0 0.35rem !important;" in item_rule
    assert "font-weight: 800;" in item_rule
    assert "font-size:" not in item_rule
    assert "line-height:" not in item_rule
    assert "font-family:" not in item_rule
    assert (
        "/static/home_information_polish.css?v=roadmap-typography-v2-20260730"
        in shell_template
    )
