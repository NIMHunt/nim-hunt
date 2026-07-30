from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_roadmap_primary_copy_matches_information_page_weight() -> None:
    renderer = _read("static/static_page.js")
    stylesheet = _read("static/home_information_polish.css")
    shell_template = _read("templates/_home_shell.html")

    assert "item.className = 'nq-text roadmap-item';" in renderer

    roadmap_rule = stylesheet.split(
        ".nq-style .roadmap-items > .roadmap-item {", 2
    )[2].split("}", 1)[0]

    assert "font-weight: 800;" in roadmap_rule
    assert "font-size:" not in roadmap_rule
    assert "line-height:" not in roadmap_rule
    assert "font-family:" not in roadmap_rule
    assert (
        "/static/home_information_polish.css?v=roadmap-typography-v1-20260730"
        in shell_template
    )
