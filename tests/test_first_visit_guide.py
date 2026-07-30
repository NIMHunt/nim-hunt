def _read(relative_path: str) -> str:
    with open(relative_path, encoding="utf-8") as source:
        return source.read()


def test_about_and_roadmap_primary_text_use_nimhunt_text_colour() -> None:
    stylesheet = _read("static/home_information_polish.css")

    assert ".nq-style .static-page-copy," in stylesheet
    assert ".nq-style .roadmap-heading," in stylesheet
    assert ".nq-style .roadmap-items > .roadmap-item" in stylesheet

    primary_text_rule = stylesheet.split(
        ".nq-style .static-page-copy,", 1
    )[1].split("}", 1)[0]
    assert "color: var(--nh-text);" in primary_text_rule
    assert "!important" not in primary_text_rule


def test_first_visit_notice_has_a_quiet_starter_guide_action() -> None:
    shell = _read("templates/_home_shell.html")
    stylesheet = _read("static/home_information_polish.css")
    controller = _read("static/first_visit_guide.js")
    home_controller = _read("static/home.js")
    text_catalogue = _read("static/interface_text.js")

    assert 'id="notice-guide"' in shell
    assert 'class="nq-button notice-guide-button"' in shell
    assert 'href="/how-to"' in shell
    assert ">See Guide</a>" in shell
    assert 'class="notice-actions"' in shell
    assert "data-test-features-enabled" not in shell
    assert "/static/first_visit_guide.js?v=first-visit-guide-v2-20260730" in shell
    assert "/static/home_information_polish.css?v=roadmap-typography-v2-20260730" in shell

    assert ".notice-actions > .nq-button" in stylesheet
    assert "height: 7.5rem;" in stylesheet
    assert ".nq-style .notice-card .notice-guide-button" in stylesheet
    assert "background: rgba(255, 255, 255, 0.46);" in stylesheet
    assert "background: rgba(255, 255, 255, 0.58);" in stylesheet
    assert "color: var(--nh-muted);" in stylesheet
    assert ".notice-guide-button[hidden]" in stylesheet
    assert "min-height:" not in stylesheet
    assert "font: inherit;" not in stylesheet
    assert "border-radius: 999px;" not in stylesheet

    overlay_rule = stylesheet.split(
        ".nq-style .notice-card .notice-guide-button::before {", 1
    )[1].split("}", 1)[0]
    assert "background: transparent;" in overlay_rule
    assert "background-image: none;" in overlay_rule

    hover_rules = stylesheet.split(".nq-style .notice-card .notice-guide-button:hover,", 1)[1]
    hover_rules = hover_rules.split(".nq-style .notice-card .notice-guide-button:active", 1)[0]
    assert "color: var(--nh-muted);" in hover_rules
    assert "color: var(--nh-text);" not in hover_rules

    assert "data?.created" in controller
    assert "const isHomeSession = requestPath(args[0]) === '/api/home/session';" in controller
    assert "if (isHomeSession) window.fetch = nativeFetch;" in controller
    assert "preview" not in controller
    assert "testFeaturesEnabled" not in controller
    assert "created: true" not in controller
    assert "new Response(" not in controller

    assert "if (data.created && state.user && !state.banned)" in home_controller
    assert "scheduleWelcomeConfetti();" in home_controller
    assert "buttonText: \"Let's Go!\"" in text_catalogue
