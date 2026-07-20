from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:120]!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "constants.py",
    '''NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE = _env_int("NIMHUNT_NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE", 120)
''',
    '''NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE = _env_int("NIMHUNT_NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE", 120)
# The transaction refresher keeps a recent RPC height in memory. Deposit dialogs
# use this validated value instead of making a rate-limited public RPC request on
# every click. A five-minute age still distinguishes TestAlbatross from mainnet
# while tolerating a brief public-node outage.
NIMIQ_CHAIN_HEAD_CACHE_MAX_AGE_SECONDS = _env_int(
    "NIMHUNT_NIMIQ_CHAIN_HEAD_CACHE_MAX_AGE_SECONDS",
    5 * 60,
)
''',
)

replace_once(
    "trans_updater.py",
    '''_TRANS_CHECK_LAST_RESULT: RowDict | None = None
_TRANS_CHECK_LAST_ERROR: str | None = None
''',
    '''_TRANS_CHECK_LAST_RESULT: RowDict | None = None
_TRANS_CHECK_LAST_ERROR: str | None = None
_CHAIN_HEAD_HEIGHT: int | None = None
_CHAIN_HEAD_UPDATED_AT: float | None = None
_CHAIN_HEAD_LAST_ERROR: str | None = None
''',
)

replace_once(
    "trans_updater.py",
    '''async def get_chain_head_height(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    """Return the configured RPC's latest block height."""
    result = await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=str(rpc_url),
        method="getLatestBlock",
        params=[False],
        timeout_seconds=int(timeout_seconds),
    )
    if isinstance(result, dict) and "data" in result:
        result = result.get("data")
    if isinstance(result, (int, str)):
        try:
            height = int(result)
        except ValueError as exc:
            raise RuntimeError("Nimiq RPC returned an invalid block height") from exc
    else:
        height = _extract_block_number(result)
    if height is None or int(height) < 0:
        raise RuntimeError("Nimiq RPC getLatestBlock did not expose a block height")
    return int(height)
''',
    '''async def get_chain_head_height(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    """Return the configured RPC's current block height.

    getBlockNumber is the smallest standard request for this purpose. It avoids
    downloading a full block for every refresh and consumes less of a public
    RPC provider's rate-limit budget.
    """
    result = await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=str(rpc_url),
        method="getBlockNumber",
        params=[],
        timeout_seconds=int(timeout_seconds),
    )
    data, _metadata = _unwrap_rpc_result(result)
    if isinstance(data, bool):
        raise RuntimeError("Nimiq RPC getBlockNumber returned a boolean")
    if isinstance(data, (int, str)):
        try:
            height = int(data)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Nimiq RPC returned an invalid block height") from exc
    else:
        height = _extract_block_number(data)
    if height is None or int(height) < 0:
        raise RuntimeError("Nimiq RPC getBlockNumber did not expose a block height")
    return int(height)


def remember_chain_head_height(height: int) -> int:
    """Store one successfully read chain height for deposit preflight checks."""
    global _CHAIN_HEAD_HEIGHT, _CHAIN_HEAD_UPDATED_AT, _CHAIN_HEAD_LAST_ERROR
    height = int(height)
    if height < 0:
        raise ValueError("chain height must be non-negative")
    _CHAIN_HEAD_HEIGHT = height
    _CHAIN_HEAD_UPDATED_AT = time.monotonic()
    _CHAIN_HEAD_LAST_ERROR = None
    return height


def get_cached_chain_head_height(*, max_age_seconds: int | None = None) -> int | None:
    """Return the recent validated height, or None when missing/stale."""
    if _CHAIN_HEAD_HEIGHT is None or _CHAIN_HEAD_UPDATED_AT is None:
        return None
    max_age = int(
        max_age_seconds
        if max_age_seconds is not None
        else getattr(const, "NIMIQ_CHAIN_HEAD_CACHE_MAX_AGE_SECONDS", 5 * 60)
    )
    if max_age < 0:
        return None
    if time.monotonic() - _CHAIN_HEAD_UPDATED_AT > max_age:
        return None
    return int(_CHAIN_HEAD_HEIGHT)


async def refresh_chain_head_height(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    """Refresh the shared height cache from the configured RPC."""
    global _CHAIN_HEAD_LAST_ERROR
    try:
        height = await get_chain_head_height(
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
    except Exception as exc:
        _CHAIN_HEAD_LAST_ERROR = wallet.redact_secret_values(exc)
        raise
    return remember_chain_head_height(height)


async def get_chain_head_height_for_deposit(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    max_age_seconds: int | None = None,
) -> int:
    """Return a recent validated height without an RPC request on every click."""
    cached = get_cached_chain_head_height(max_age_seconds=max_age_seconds)
    if cached is not None:
        return cached
    return await refresh_chain_head_height(
        rpc_url=rpc_url,
        timeout_seconds=int(timeout_seconds),
    )


def chain_head_cache_status() -> RowDict:
    """Return non-sensitive diagnostics for logs and future status pages."""
    age_seconds = None
    if _CHAIN_HEAD_UPDATED_AT is not None:
        age_seconds = max(0.0, time.monotonic() - _CHAIN_HEAD_UPDATED_AT)
    return {
        "height": _CHAIN_HEAD_HEIGHT,
        "age_seconds": age_seconds,
        "last_error": _CHAIN_HEAD_LAST_ERROR,
    }
''',
)

replace_once(
    "trans_updater.py",
    '''    checked: list[RowDict] = []
    finalised: list[RowDict] = []
    still_pending: list[RowDict] = []
    unknown: list[RowDict] = []

    async with get_db() as db:
''',
    '''    checked: list[RowDict] = []
    finalised: list[RowDict] = []
    still_pending: list[RowDict] = []
    unknown: list[RowDict] = []
    chain_head_height: int | None = None
    chain_head_error: str | None = None

    try:
        chain_head_height = await refresh_chain_head_height(
            rpc_url=rpc_url,
            timeout_seconds=int(timeout_seconds),
        )
    except Exception as exc:
        chain_head_error = wallet.redact_secret_values(exc)
        logger.warning("Nimiq chain-head refresh failed: %s", chain_head_error)

    async with get_db() as db:
''',
)

replace_once(
    "trans_updater.py",
    '''        "unknown": unknown,
        "creation_fees": creation_fees,
    }
''',
    '''        "unknown": unknown,
        "creation_fees": creation_fees,
        "chain_head_height": chain_head_height,
        "chain_head_error": chain_head_error,
    }
''',
)

replace_once(
    "main.py",
    '''    await verify_public_rpc_network()
    await database.init_db()
''',
    '''    await verify_public_rpc_network()
    if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
        # Seed the height cache during the same strict startup phase that verifies
        # the RPC network. Deposit dialogs can then use this recent value without
        # making a new public-RPC request on every click.
        await trans_updater.refresh_chain_head_height()
    await database.init_db()
''',
)

replace_once(
    "public_html.py",
    '''import asyncio
import json
import re
''',
    '''import asyncio
import json
import logging
import re
''',
)

replace_once(
    "public_html.py",
    '''router = APIRouter()
templates = Jinja2Templates(directory=str(const.TEMPLATES_DIR))
''',
    '''router = APIRouter()
templates = Jinja2Templates(directory=str(const.TEMPLATES_DIR))
logger = logging.getLogger(__name__)
''',
)

replace_once(
    "public_html.py",
    '''    try:
        chain_height = await trans_updater.get_chain_head_height()
    except Exception:
        if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "nimiq_rpc_unavailable",
                    "message": "NimHunt cannot verify the configured Nimiq network right now. No deposit was requested.",
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        chain_height = None
''',
    '''    try:
        chain_height = await trans_updater.get_chain_head_height_for_deposit()
    except Exception as exc:
        logger.warning(
            "Deposit intent could not obtain a recent Nimiq chain height: spot_id=%s error=%s cache=%s",
            int(spot_id),
            trans_updater.wallet.redact_secret_values(exc),
            trans_updater.chain_head_cache_status(),
        )
        if bool(getattr(const, "PUBLIC_DEPLOYMENT", False)):
            return JSONResponse(
                {
                    **meta,
                    "ok": False,
                    "code": "nimiq_rpc_unavailable",
                    "message": (
                        "NimHunt cannot verify the TestAlbatross chain height right now. "
                        "No deposit was requested; please try again shortly."
                    ),
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        chain_height = None
''',
)

(ROOT / "tests/test_chain_head_preflight.py").write_text(
    '''from __future__ import annotations

import time
import unittest
from unittest import mock

import trans_updater


class ChainHeadPreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.old_height = trans_updater._CHAIN_HEAD_HEIGHT
        self.old_updated = trans_updater._CHAIN_HEAD_UPDATED_AT
        self.old_error = trans_updater._CHAIN_HEAD_LAST_ERROR
        trans_updater._CHAIN_HEAD_HEIGHT = None
        trans_updater._CHAIN_HEAD_UPDATED_AT = None
        trans_updater._CHAIN_HEAD_LAST_ERROR = None

    def tearDown(self) -> None:
        trans_updater._CHAIN_HEAD_HEIGHT = self.old_height
        trans_updater._CHAIN_HEAD_UPDATED_AT = self.old_updated
        trans_updater._CHAIN_HEAD_LAST_ERROR = self.old_error

    async def test_reads_compact_block_number_rpc_shape(self):
        with mock.patch.object(
            trans_updater,
            "_json_rpc_post_sync",
            return_value={"data": 123456, "metadata": None},
        ) as rpc:
            height = await trans_updater.get_chain_head_height(
                rpc_url="https://rpc.test.invalid/",
                timeout_seconds=2,
            )

        self.assertEqual(height, 123456)
        self.assertEqual(rpc.call_args.kwargs["method"], "getBlockNumber")
        self.assertEqual(rpc.call_args.kwargs["params"], [])

    async def test_deposit_uses_recent_cache_without_new_rpc_request(self):
        trans_updater._CHAIN_HEAD_HEIGHT = 456789
        trans_updater._CHAIN_HEAD_UPDATED_AT = time.monotonic()
        with mock.patch.object(
            trans_updater,
            "refresh_chain_head_height",
            mock.AsyncMock(side_effect=AssertionError("unexpected RPC refresh")),
        ) as refresh:
            height = await trans_updater.get_chain_head_height_for_deposit(
                max_age_seconds=300,
            )

        self.assertEqual(height, 456789)
        refresh.assert_not_awaited()

    async def test_stale_cache_is_refreshed(self):
        trans_updater._CHAIN_HEAD_HEIGHT = 100
        trans_updater._CHAIN_HEAD_UPDATED_AT = time.monotonic() - 301
        with mock.patch.object(
            trans_updater,
            "refresh_chain_head_height",
            mock.AsyncMock(return_value=200),
        ) as refresh:
            height = await trans_updater.get_chain_head_height_for_deposit(
                max_age_seconds=300,
            )

        self.assertEqual(height, 200)
        refresh.assert_awaited_once()

    async def test_refresh_records_failures_without_destroying_last_height(self):
        trans_updater._CHAIN_HEAD_HEIGHT = 777
        trans_updater._CHAIN_HEAD_UPDATED_AT = time.monotonic() - 999
        with mock.patch.object(
            trans_updater,
            "get_chain_head_height",
            mock.AsyncMock(side_effect=TimeoutError("temporary RPC timeout")),
        ):
            with self.assertRaises(TimeoutError):
                await trans_updater.refresh_chain_head_height()

        self.assertEqual(trans_updater._CHAIN_HEAD_HEIGHT, 777)
        self.assertIn("temporary RPC timeout", trans_updater._CHAIN_HEAD_LAST_ERROR)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

print("Deposit RPC preflight fix applied.")
