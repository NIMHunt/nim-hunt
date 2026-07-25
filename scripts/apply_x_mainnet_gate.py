"""Apply the production-MainAlbatross gate, then remove this helper/workflow."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected patch anchor missing from {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


worker = ROOT / "x_auto_poster.py"
replace_once(
    worker,
    '''@dataclass(frozen=True, order=True)
class ActivationCursor:
    timestamp: int
    spot_id: int = 0


def normalise_account_handle(value: object) -> str:
''',
    '''@dataclass(frozen=True, order=True)
class ActivationCursor:
    timestamp: int
    spot_id: int = 0


def x_posting_block_reason() -> str | None:
    """Explain why automatic X posting is currently unable to run."""
    if not bool(const.X_AUTO_POST_ENABLED):
        return "disabled_by_flag"
    if not bool(getattr(const, "PRODUCTION_MODE", False)):
        return "requires_production_mainalbatross"
    if str(getattr(const, "NIMIQ_NETWORK", "")).strip() != "MainAlbatross":
        return "requires_production_mainalbatross"
    if int(getattr(const, "NIMIQ_NETWORK_ID", 0)) != 24:
        return "requires_production_mainalbatross"
    return None


def x_posting_allowed() -> bool:
    """Return True only for an explicit opt-in on production MainAlbatross."""
    return x_posting_block_reason() is None


def normalise_account_handle(value: object) -> str:
''',
)
replace_once(
    worker,
    '''def validate_configuration() -> None:
    """Fail clearly when the opt-in flag is enabled without safe settings."""
    if not const.X_AUTO_POST_ENABLED:
        return
''',
    '''def validate_configuration() -> None:
    """Validate credentials only when production MainAlbatross posting may run."""
    if not x_posting_allowed():
        return
''',
)
replace_once(
    worker,
    '''    if not const.X_AUTO_POST_ENABLED:
        cursor = await prepare_disabled_mode()
        return {
            "ok": True,
            "enabled": False,
            "cursor": cursor.__dict__,
            "checked_count": 0,
            "posted_count": 0,
        }

    validate_configuration()
''',
    '''    block_reason = x_posting_block_reason()
    if block_reason is not None:
        cursor = await prepare_disabled_mode()
        return {
            "ok": True,
            "requested_enabled": bool(const.X_AUTO_POST_ENABLED),
            "enabled": False,
            "blocked_reason": block_reason,
            "cursor": cursor.__dict__,
            "checked_count": 0,
            "posted_count": 0,
        }

    validate_configuration()
''',
)
replace_once(
    worker,
    '''    if not const.X_AUTO_POST_ENABLED:
        cursor = await prepare_disabled_mode()
        _X_POST_LAST_RESULT = {
            "ok": True,
            "enabled": False,
            "cursor": cursor.__dict__,
            "checked_count": 0,
            "posted_count": 0,
        }
        _X_POST_LAST_ERROR = None
        return

    validate_configuration()
''',
    '''    block_reason = x_posting_block_reason()
    if block_reason is not None:
        cursor = await prepare_disabled_mode()
        _X_POST_LAST_RESULT = {
            "ok": True,
            "requested_enabled": bool(const.X_AUTO_POST_ENABLED),
            "enabled": False,
            "blocked_reason": block_reason,
            "cursor": cursor.__dict__,
            "checked_count": 0,
            "posted_count": 0,
        }
        _X_POST_LAST_ERROR = None
        return

    validate_configuration()
''',
)
replace_once(
    worker,
    '''    return {
        "enabled": bool(const.X_AUTO_POST_ENABLED),
        "account": account,
        "running": _X_POST_TASK is not None and not _X_POST_TASK.done(),
        "last_error": _X_POST_LAST_ERROR,
        "last_result": _X_POST_LAST_RESULT,
        "interval_seconds": int(const.X_POST_INTERVAL_SECONDS),
    }
''',
    '''    block_reason = x_posting_block_reason()
    return {
        "requested_enabled": bool(const.X_AUTO_POST_ENABLED),
        "enabled": block_reason is None,
        "blocked_reason": block_reason,
        "production_mainnet_only": True,
        "deployment_mode": str(getattr(const, "DEPLOYMENT_MODE", "development")),
        "network": str(getattr(const, "NIMIQ_NETWORK", "")),
        "network_id": int(getattr(const, "NIMIQ_NETWORK_ID", 0)),
        "account": account,
        "running": _X_POST_TASK is not None and not _X_POST_TASK.done(),
        "last_error": _X_POST_LAST_ERROR,
        "last_result": _X_POST_LAST_RESULT,
        "interval_seconds": int(const.X_POST_INTERVAL_SECONDS),
    }
''',
)

readme = ROOT / "README.md"
replace_once(
    readme,
    '''is **disabled by default** and makes no X API requests while disabled. When it is
first enabled, the worker starts from that moment rather than posting a backlog
of older active Spots.
''',
    '''is **disabled by default** and makes no X API requests while disabled. It is also
hard-gated to the real blockchain: the worker can run only when NimHunt is in
`production` mode on `MainAlbatross` with network ID `24`. Development,
DevAlbatross and public TestAlbatross deployments remain inert even if the master
flag is accidentally set to `true`. When first enabled on production MainAlbatross,
the worker starts from that moment rather than posting a backlog of older active
Spots.
''',
)
replace_once(
    readme,
    '''| `NIMHUNT_X_MAX_SPOTS_PER_RUN` | `10` | Maximum Posts/retries considered in one worker pass |

The credentials—not the handle setting—determine the account that can post.
''',
    '''| `NIMHUNT_X_MAX_SPOTS_PER_RUN` | `10` | Maximum Posts/retries considered in one worker pass |

There are six required deployment variables for eventual activation: the master
flag, account handle and four OAuth credentials. The interval, timeout, retry and
batch-size variables are optional and may be omitted to use their defaults.

The credentials—not the handle setting—determine the account that can post.
''',
)
replace_once(
    readme,
    '''Only set the flag to `1` after all four private credential variables have been
added to the deployment and the intended account has authorised the App.
''',
    '''Only set the flag to `1` after all four private credential variables have been
added to the deployment, the intended account has authorised the App, and the
service is running in production on MainAlbatross. The worker independently
checks all three conditions before making any X request.
''',
)

for relative in (
    ".github/workflows/apply-x-mainnet-gate.yml",
    "scripts/apply_x_mainnet_gate.py",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
