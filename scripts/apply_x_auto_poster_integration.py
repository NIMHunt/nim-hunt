"""Apply automatic-X integration, then remove this one-time helper."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected patch anchor missing from {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ROOT / "constants.py",
    'CLAIM_PAGE_URL_PREFIX = "/claim"\n',
    '''CLAIM_PAGE_URL_PREFIX = "/claim"

# Automatic X announcements are strictly opt-in. The account handle is a safety
# assertion only: the OAuth user Access Token and Secret determine which account
# actually creates Posts, and the worker verifies that identity before posting.
X_AUTO_POST_ENABLED = bool(
    _optional_env_bool("NIMHUNT_X_AUTO_POST_ENABLED") or False
)
X_ACCOUNT_HANDLE = os.getenv("NIMHUNT_X_ACCOUNT_HANDLE", "").strip().lstrip("@")
NIMHUNT_X_API_KEY_ENV = "NIMHUNT_X_API_KEY"
NIMHUNT_X_API_SECRET_ENV = "NIMHUNT_X_API_SECRET"
NIMHUNT_X_ACCESS_TOKEN_ENV = "NIMHUNT_X_ACCESS_TOKEN"
NIMHUNT_X_ACCESS_TOKEN_SECRET_ENV = "NIMHUNT_X_ACCESS_TOKEN_SECRET"
X_POST_INTERVAL_SECONDS = _env_int("NIMHUNT_X_POST_INTERVAL_SECONDS", 30)
X_HTTP_TIMEOUT_SECONDS = _env_int("NIMHUNT_X_HTTP_TIMEOUT_SECONDS", 10)
X_RETRY_AFTER_SECONDS = _env_int("NIMHUNT_X_RETRY_AFTER_SECONDS", 15 * 60)
X_MAX_SPOTS_PER_RUN = _env_int("NIMHUNT_X_MAX_SPOTS_PER_RUN", 10)
''',
)

replace_once(
    ROOT / "main.py",
    "import wallet\n",
    "import wallet\nimport x_auto_poster\n",
)
replace_once(
    ROOT / "main.py",
    '''        await trans_updater.start_transaction_refresher(
            run_immediately=True,
            fail_on_initial_error=strict_startup,
        )
''',
    '''        await trans_updater.start_transaction_refresher(
            run_immediately=True,
            fail_on_initial_error=strict_startup,
        )
        await x_auto_poster.start_x_auto_poster(run_immediately=True)
''',
)
replace_once(
    ROOT / "main.py",
    '''    services = (
        ("transaction refresher", trans_updater.stop_transaction_refresher),
''',
    '''    services = (
        ("automatic X poster", x_auto_poster.stop_x_auto_poster),
        ("transaction refresher", trans_updater.stop_transaction_refresher),
''',
)
replace_once(
    ROOT / "main.py",
    '''            "network": getattr(const, "NIMIQ_NETWORK", ""),
        }
''',
    '''            "network": getattr(const, "NIMIQ_NETWORK", ""),
            "x_auto_post": x_auto_poster.x_auto_poster_status(),
        }
''',
)

replace_once(
    ROOT / "x_auto_poster.py",
    '''              AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
              AND s.{schema.SPOT_STARTS_AT} IS NOT NULL
''',
    '''              AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
              AND s.{schema.SPOT_LAT} IS NOT NULL
              AND s.{schema.SPOT_LONG} IS NOT NULL
              AND s.{schema.SPOT_STARTS_AT} IS NOT NULL
''',
)
replace_once(
    ROOT / "x_auto_poster.py",
    '''          AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
          AND s.{schema.SPOT_STARTS_AT} IS NOT NULL
''',
    '''          AND s.{schema.SPOT_CANCELLATION_STARTED_AT} IS NULL
          AND s.{schema.SPOT_LAT} IS NOT NULL
          AND s.{schema.SPOT_LONG} IS NOT NULL
          AND s.{schema.SPOT_STARTS_AT} IS NOT NULL
''',
)
replace_once(
    ROOT / "x_auto_poster.py",
    '''    if candidates and len(candidates) >= remaining:
        last = candidates[-1]
        next_cursor = ActivationCursor(
            int(last.get("activation_at") or cursor.timestamp),
            int(last[schema.SPOT_ID]),
        )
    else:
        next_cursor = ActivationCursor(int(now), 0)
''',
    '''    if remaining == 0:
        # Retry work consumed this pass. Preserve the activation cursor so new
        # Spots are not skipped merely because older safe retries were due.
        next_cursor = cursor
    elif candidates and len(candidates) >= remaining:
        last = candidates[-1]
        next_cursor = ActivationCursor(
            int(last.get("activation_at") or cursor.timestamp),
            int(last[schema.SPOT_ID]),
        )
    else:
        next_cursor = ActivationCursor(int(now), 0)
''',
)

replace_once(
    ROOT / "README.md",
    "- **On-chain descriptions** — NimHunt-generated transactions include short Spot labels.\n",
    "- **On-chain descriptions** — NimHunt-generated transactions include short Spot labels.\n"
    "- **Optional X announcements** — automatically announce newly-active Spots through a configured account.\n",
)
replace_once(
    ROOT / "README.md",
    "- **Background services** refresh caches, settle completed Prizedraws and reconcile\n  pending blockchain transactions.\n",
    "- **Background services** refresh caches, settle completed Prizedraws, reconcile\n"
    "  pending blockchain transactions and optionally announce newly-active Spots on X.\n",
)
replace_once(
    ROOT / "README.md",
    "## Nimiq networks\n",
    '''## Automatic X posting

NimHunt can announce a published Spot when it first becomes active. This feature
is **disabled by default** and makes no X API requests while disabled. When it is
first enabled, the worker starts from that moment rather than posting a backlog
of older active Spots.

Each generated Post contains a short announcement, the Spot title and its public
`nimhunt.app` link. NimHunt generates and caches the Spot's existing map card
before creating the Post, so X can fetch a warm preview image.

The worker uses OAuth 1.0a user-context credentials. Create an approved X developer
App with posting/Read and Write permission, then configure these server variables:

| Variable | Default | Purpose |
|---|---|---|
| `NIMHUNT_X_AUTO_POST_ENABLED` | `false` | Master switch; accepts the same strict boolean values as other NimHunt flags |
| `NIMHUNT_X_ACCOUNT_HANDLE` | empty | Expected account username, with or without `@` |
| `NIMHUNT_X_API_KEY` | empty | X developer App API/consumer key |
| `NIMHUNT_X_API_SECRET` | empty | X developer App API/consumer secret |
| `NIMHUNT_X_ACCESS_TOKEN` | empty | User Access Token for the posting account |
| `NIMHUNT_X_ACCESS_TOKEN_SECRET` | empty | User Access Token Secret for the posting account |
| `NIMHUNT_X_POST_INTERVAL_SECONDS` | `30` | How often to check for newly-active Spots |
| `NIMHUNT_X_HTTP_TIMEOUT_SECONDS` | `10` | Per-request X API timeout |
| `NIMHUNT_X_RETRY_AFTER_SECONDS` | `900` | Default delay after an authoritative retryable rejection |
| `NIMHUNT_X_MAX_SPOTS_PER_RUN` | `10` | Maximum Posts/retries considered in one worker pass |

The credentials—not the handle setting—determine the account that can post.
Before sending anything, NimHunt calls X's authenticated-user endpoint and refuses
to post unless its returned username matches `NIMHUNT_X_ACCOUNT_HANDLE`.
Credentials stay in environment variables and are never written to SQLite,
health output or logs.

Successful Post IDs and per-Spot delivery states are stored in the existing
`app_metadata` table, so restarts do not duplicate confirmed announcements and no
schema reset is required. Rate limits and explicit authentication rejections can
be retried safely. A timeout, lost connection or X server error is recorded as
**uncertain** and is not retried automatically, because the Post may already have
been created and a blind retry could publish it twice.

Example disabled configuration:

```bash
export NIMHUNT_X_AUTO_POST_ENABLED=0
export NIMHUNT_X_ACCOUNT_HANDLE='NimHunt'
```

Only set the flag to `1` after all four private credential variables have been
added to the deployment and the intended account has authorised the App.

## Nimiq networks
''',
)

for relative in (
    ".github/workflows/apply-x-auto-poster.yml",
    "scripts/apply_x_auto_poster_integration.py",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
