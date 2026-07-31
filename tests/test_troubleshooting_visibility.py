from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_troubleshooting_card_starts_hidden_and_loads_network_gate() -> None:
    shell = _read("templates/_home_shell.html")

    assert (
        'class="debug-card" aria-label="Troubleshooting information" '
        'data-i18n-aria-label="home.troubleshootingAria" hidden'
    ) in shell
    assert (
        '/static/troubleshooting_visibility.js?v=mainnet-hidden-v1-20260731'
        in shell
    )


def test_troubleshooting_is_revealed_only_for_test_or_dev_networks() -> None:
    controller = _read("static/troubleshooting_visibility.js")

    assert "const HEALTH_ENDPOINT = '/healthz';" in controller
    assert "'testalbatross'" in controller
    assert "'testnet'" in controller
    assert "'5'" in controller
    assert "'devalbatross'" in controller
    assert "'devnet'" in controller
    assert "'6'" in controller
    assert "card.hidden = true;" in controller
    assert "card.hidden = false;" in controller
    assert "removeAfterPageInitialisation(card);" in controller
    assert "cache: 'no-store'" in controller

    for forbidden_term in (
        "transaction",
        "settlement",
        "payout",
        "wallet.py",
        "database",
    ):
        assert forbidden_term not in controller.lower()
