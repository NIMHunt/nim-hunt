from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_home_metrics_follow_shell_spacing_without_legacy_upward_offset() -> None:
    base_styles = _read("static/home.css")
    polish_styles = _read("static/home_information_polish.css")
    shell = _read("templates/_home_shell.html")

    shell_rule = base_styles.split(".home-shell {", 1)[1].split("}", 1)[0]
    metrics_rule = polish_styles.split(".home-metrics {", 1)[1].split("}", 1)[0]

    assert "gap: 16px;" in shell_rule
    assert "margin-top: 0;" in metrics_rule
    assert shell.index("/static/home.css?") < shell.index("/static/home_information_polish.css?")
