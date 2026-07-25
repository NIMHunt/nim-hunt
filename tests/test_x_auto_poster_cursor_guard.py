"""Guard the retry-only pass from advancing past unprocessed activations."""

from pathlib import Path


def test_retry_only_pass_preserves_activation_cursor() -> None:
    source = (Path(__file__).resolve().parents[1] / "x_auto_poster.py").read_text(
        encoding="utf-8"
    )
    assert "if remaining == 0:" in source
    assert "next_cursor = cursor" in source
    assert "Spots are not skipped" in source
