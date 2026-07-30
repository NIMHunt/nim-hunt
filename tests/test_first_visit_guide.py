def _read(relative_path: str) -> str:
    with open(relative_path, encoding="utf-8") as source:
        return source.read()


def test_about_and_roadmap_primary_text_use_nimhunt_text_colour() -> None:
    stylesheet = _read("static/home_information_polish.css")

    assert ".nq-style .static-page-copy," in stylesheet
    assert ".nq-style .roadmap-heading," in stylesheet
    assert ".nq-style .roadmap-items > .roadmap-item" in stylesheet
    assert "color: var(--nh-text);" in stylesheet
    assert "!important" not in stylesheet


def test_first_visit_notice_has_a_quiet_starter_guide_action() -> None:
    shell = _read("templates/_home_shell.html")
    stylesheet = _read("static/home_information_polish.css")
    controller = _read("static/first_visit_guide.js")
    home_controller = _read("static/home.js")
    text_catalogue = _read("static/interface_text.js")

    assert 'id="notice-guide"' in shell
    assert 'class="nq-button notice-guide-button"' in shell
    assert 'href="/?view=how-to"' in shell
    assert ">See Guide</a>" in shell
    assert 'class="notice-actions"' in shell
    assert 'data-test-features-enabled=' in shell
    assert "/static/first_visit_guide.js?v=first-visit-guide-v1-20260729" in shell
    assert "/static/home_information_polish.css?v=first-visit-guide-v2-20260730" in shell

    assert ".notice-actions > .nq-button" in stylesheet
    assert "height: 7.5rem;" in stylesheet
    assert ".nq-style .notice-card .notice-guide-button" in stylesheet
    assert "background: rgba(255, 255, 255, 0.46);" in stylesheet
    assert ".notice-guide-button[hidden]" in stylesheet
    assert "min-height:" not in stylesheet
    assert "font: inherit;" not in stylesheet
    assert "border-radius: 999px;" not in stylesheet

    assert "data?.created" in controller
    assert "requestPath(args[0]) !== '/api/home/session'" in controller
    assert "preview') === 'first-visit'" in controller
    assert "body.dataset.testFeaturesEnabled === 'true'" in controller
    assert "const previewData = { ...data, created: true };" in controller

    assert "if (data.created && state.user && !state.banned)" in home_controller
    assert "scheduleWelcomeConfetti();" in home_controller
    assert "buttonText: \"Let's Go!\"" in text_catalogue
