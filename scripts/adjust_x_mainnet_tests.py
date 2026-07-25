"""Adjust the pre-existing credential test for the new mainnet gate."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tests" / "test_x_auto_poster.py"
text = path.read_text(encoding="utf-8")
old = '''def test_enabled_configuration_requires_account_and_credentials(monkeypatch) -> None:
    monkeypatch.setattr(const, "X_AUTO_POST_ENABLED", True)
    monkeypatch.setattr(const, "X_ACCOUNT_HANDLE", "NimHunt")
'''
new = '''def test_enabled_configuration_requires_account_and_credentials(monkeypatch) -> None:
    monkeypatch.setattr(const, "X_AUTO_POST_ENABLED", True)
    monkeypatch.setattr(const, "PRODUCTION_MODE", True)
    monkeypatch.setattr(const, "DEPLOYMENT_MODE", "production")
    monkeypatch.setattr(const, "NIMIQ_NETWORK", "MainAlbatross")
    monkeypatch.setattr(const, "NIMIQ_NETWORK_ID", 24)
    monkeypatch.setattr(const, "X_ACCOUNT_HANDLE", "NimHunt")
'''
if old not in text:
    raise RuntimeError("Expected credential-test anchor is missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

for relative in (
    ".github/workflows/adjust-x-mainnet-tests.yml",
    "scripts/adjust_x_mainnet_tests.py",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()
