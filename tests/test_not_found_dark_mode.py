import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_branded_404_copy_is_present_and_explicitly_visible_in_dark_mode():
    template = source("templates/not_found.html")
    shell = source("templates/_home_shell.html")
    stylesheet = source("static/ux_accessibility.css")

    assert "{% set error_code = '404' %}" in template
    assert "{% set error_message = 'This page could not be found.' %}" in template
    assert 'class="home-error-code"' in shell
    assert 'class="home-error-message"' in shell

    code_selector = 'html[data-theme="dark"] body.nq-style .home-error-code'
    message_selector = 'html[data-theme="dark"] body.nq-style .home-error-message'
    assert code_selector in stylesheet
    assert message_selector in stylesheet

    code_rule = stylesheet.split(f"{code_selector} {{", 1)[1].split("}", 1)[0]
    message_rule = stylesheet.split(f"{message_selector} {{", 1)[1].split("}", 1)[0]
    assert "color: var(--nh-text, #fafafa) !important;" in code_rule
    assert "color: var(--nh-soft-text, #d5d6e0) !important;" in message_rule
