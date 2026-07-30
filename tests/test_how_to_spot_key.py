def _read(relative_path: str) -> str:
    with open(relative_path, encoding="utf-8") as source:
        return source.read()


def test_how_to_heading_is_starter_guide_without_renaming_navigation_link() -> None:
    shell = _read("templates/_home_shell.html")

    assert "{% elif information_view == 'how-to' %}Starter Guide{% else %}" in shell
    assert '<a href="/how-to">How To</a>' in shell
    assert (
        '/static/how_to_key.css?v=spot-key-v2-map-tooltip-20260729' in shell
    )


def test_spot_key_sits_above_example_and_uses_existing_nimiq_icons() -> None:
    partial = _read("templates/_how_to_content.html")
    stylesheet = _read("static/how_to_key.css")

    key_position = partial.index('class="how-to-spot-key"')
    example_position = partial.index('<figure class="how-to-find-spots-figure">')

    assert key_position < example_position
    assert '>Spot Key</h3>' in partial
    assert "Spot Key:" not in partial
    assert "#nq-stopwatch" in partial
    assert "#nq-lock-locked" in partial
    assert (
        "These Spots require you to remain within their area for a certain amount "
        "of time before you can claim."
    ) in partial
    assert "These Spots require a private code for you to make a claim." in partial

    assert '@import url("/static/how_to_map_tooltip.css?v=map-tooltip-v1-20260729");' in stylesheet
    assert ".how-to-spot-key-title" in stylesheet
    assert "text-align: center;" in stylesheet
    assert ".how-to-spot-key-copy" in stylesheet
    assert "text-align: left;" in stylesheet
    assert "grid-template-columns: 4.8rem minmax(0, 1fr);" in stylesheet
    assert "align-items: center;" in stylesheet
    assert "width: 4.8rem;" in stylesheet
    assert "height: 4.8rem;" in stylesheet
    assert "color: var(--nh-text);" in stylesheet
    assert "fill: currentColor;" in stylesheet
