"""Regression tests for disabled-by-default automatic X posting."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import constants as const
import database as schema
import x_auto_poster

ROOT = Path(__file__).resolve().parents[1]


def run(coroutine):
    return asyncio.run(coroutine)


async def initialise_test_database(monkeypatch, tmp_path: Path) -> Path:
    database_path = tmp_path / "x-auto-poster.db"
    monkeypatch.setattr(schema, "DB_PATH", str(database_path))
    await schema.init_db()
    return database_path


def credentials() -> x_auto_poster.XCredentials:
    return x_auto_poster.XCredentials(
        api_key="consumer-key",
        api_secret="consumer-secret",
        access_token="access-token",
        access_token_secret="access-secret",
    )


def spot_fixture(*, spot_id: int = 42, prizedraw: bool = False) -> dict[str, object]:
    return {
        schema.SPOT_ID: spot_id,
        schema.SPOT_LINK: f"spot-{spot_id}",
        schema.SPOT_TITLE: "Test Spot",
        schema.SPOT_DESC: "A test Spot.",
        schema.SPOT_LAT: 51.5,
        schema.SPOT_LONG: -0.1,
        schema.SPOT_RADIUS: 100,
        schema.SPOT_STARTS_AT: 100,
        schema.SPOT_ENDS_AT: 3600,
        schema.SPOT_STATUS: const.SPOT_STATUS_PUBLISHED,
        schema.SPOT_CANCELLATION_STARTED_AT: None,
        schema.PRIZEDRAW_PRIZE_COUNT: 1 if prizedraw else None,
    }


def test_automatic_posting_is_disabled_by_default() -> None:
    assert const.X_AUTO_POST_ENABLED is False
    assert const.X_ACCOUNT_HANDLE == ""
    assert const.NIMHUNT_X_API_KEY_ENV == "NIMHUNT_X_API_KEY"
    assert const.NIMHUNT_X_ACCESS_TOKEN_SECRET_ENV == (
        "NIMHUNT_X_ACCESS_TOKEN_SECRET"
    )


def test_disabled_configuration_does_not_require_credentials(monkeypatch) -> None:
    monkeypatch.setattr(const, "X_AUTO_POST_ENABLED", False)
    for name in (
        const.NIMHUNT_X_API_KEY_ENV,
        const.NIMHUNT_X_API_SECRET_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_SECRET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    x_auto_poster.validate_configuration()


def test_enabled_configuration_requires_account_and_credentials(monkeypatch) -> None:
    monkeypatch.setattr(const, "X_AUTO_POST_ENABLED", True)
    monkeypatch.setattr(const, "X_ACCOUNT_HANDLE", "NimHunt")
    for name in (
        const.NIMHUNT_X_API_KEY_ENV,
        const.NIMHUNT_X_API_SECRET_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_ENV,
        const.NIMHUNT_X_ACCESS_TOKEN_SECRET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(x_auto_poster.XConfigurationError):
        x_auto_poster.validate_configuration()


def test_account_handle_is_normalised_and_validated() -> None:
    assert x_auto_poster.normalise_account_handle(" @NimHunt ") == "NimHunt"
    with pytest.raises(x_auto_poster.XConfigurationError):
        x_auto_poster.normalise_account_handle("not a handle")


def test_oauth_header_is_deterministic_for_fixed_nonce_and_time() -> None:
    header = x_auto_poster.oauth_authorization_header(
        "POST",
        x_auto_poster.X_CREATE_POST_URL,
        credentials(),
        nonce="fixed-nonce",
        timestamp=1_700_000_000,
    )
    assert header == (
        'OAuth oauth_consumer_key="consumer-key", '
        'oauth_nonce="fixed-nonce", '
        'oauth_signature="j3NoV0%2FjiMd%2B7hgeJNOGyLtj%2Fhc%3D", '
        'oauth_signature_method="HMAC-SHA1", '
        'oauth_timestamp="1700000000", '
        'oauth_token="access-token", '
        'oauth_version="1.0"'
    )


def test_post_text_uses_spot_type_title_and_public_url(monkeypatch) -> None:
    monkeypatch.setenv("NIMHUNT_PUBLIC_BASE_URL", "https://nimhunt.app")
    standard = x_auto_poster.build_spot_post_text(spot_fixture())
    prizedraw = x_auto_poster.build_spot_post_text(
        spot_fixture(prizedraw=True)
    )
    assert standard == (
        "A new NimHunt Spot is now active!\n\n"
        "Test Spot\n\nhttps://nimhunt.app/spot/spot-42"
    )
    assert prizedraw.startswith("A new NimHunt Prizedraw is now active!")
    assert prizedraw.endswith("https://nimhunt.app/spot/spot-42")


def test_account_verification_rejects_credentials_for_another_user(monkeypatch) -> None:
    monkeypatch.setattr(const, "X_ACCOUNT_HANDLE", "NimHunt")

    async def fake_request(*_args, **_kwargs):
        return x_auto_poster.XResponse(
            status=200,
            data={"data": {"username": "AnotherAccount"}},
            headers={},
        )

    monkeypatch.setattr(x_auto_poster, "request_json", fake_request)
    with pytest.raises(x_auto_poster.XConfigurationError):
        run(x_auto_poster.verify_posting_account(credentials()))


def test_mode_transition_skips_disabled_backlog(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        await initialise_test_database(monkeypatch, tmp_path)
        async with schema.get_db() as db:
            changed, cursor = await x_auto_poster._prepare_mode(
                db,
                enabled=False,
                now=100,
            )
            assert changed is True
            assert cursor == x_auto_poster.ActivationCursor(100, 0)
            await db.commit()

        async with schema.get_db() as db:
            changed, cursor = await x_auto_poster._prepare_mode(
                db,
                enabled=True,
                now=200,
            )
            assert changed is True
            assert cursor == x_auto_poster.ActivationCursor(200, 0)
            await db.commit()

        async with schema.get_db() as db:
            changed, cursor = await x_auto_poster._prepare_mode(
                db,
                enabled=True,
                now=300,
            )
            assert changed is False
            assert cursor == x_auto_poster.ActivationCursor(200, 0)

    run(scenario())


def test_activation_cursor_paginates_spots_with_same_timestamp(
    monkeypatch,
    tmp_path,
) -> None:
    async def scenario() -> None:
        await initialise_test_database(monkeypatch, tmp_path)
        async with schema.get_db() as db:
            await db.execute(
                f"""
                INSERT INTO {schema.USER_TABLE_NAME}
                    ({schema.USER_ID}, {schema.USER_DEVICE_ID_HASH},
                     {schema.USER_DISPLAY_NAME}, {schema.USER_STATUS})
                VALUES (1, ?, 'Creator', ?);
                """,
                ("a" * 64, const.USER_STATUS_ACTIVE),
            )
            for number in range(1, 4):
                await db.execute(
                    f"""
                    INSERT INTO {schema.SPOT_TABLE_NAME} (
                        {schema.SPOT_CREATED_BY},
                        {schema.SPOT_LINK},
                        {schema.SPOT_DEPOSIT_ADDRESS},
                        {schema.SPOT_TITLE},
                        {schema.SPOT_LAT},
                        {schema.SPOT_LONG},
                        {schema.SPOT_STARTS_AT},
                        {schema.SPOT_ENDS_AT},
                        {schema.SPOT_STATUS},
                        {schema.SPOT_CREATED_AT},
                        {schema.SPOT_UPDATED_AT},
                        {schema.SPOT_CREATION_FEE_ADDRESS}
                    ) VALUES (1, ?, ?, ?, 51.5, -0.1, 101, 3600, ?, 90, 101, ?);
                    """,
                    (
                        f"same-time-{number}",
                        f"deposit-{number}",
                        f"Same Time {number}",
                        const.SPOT_STATUS_PUBLISHED,
                        "fee-address",
                    ),
                )
            await db.commit()

        async with schema.get_db() as db:
            first = await x_auto_poster._candidate_spots(
                db,
                cursor=x_auto_poster.ActivationCursor(100, 0),
                now=200,
                limit=2,
            )
            assert [row[schema.SPOT_ID] for row in first] == [1, 2]
            assert [row["activation_at"] for row in first] == [101, 101]

            second = await x_auto_poster._candidate_spots(
                db,
                cursor=x_auto_poster.ActivationCursor(101, 2),
                now=200,
                limit=2,
            )
            assert [row[schema.SPOT_ID] for row in second] == [3]

    run(scenario())


def test_success_is_persisted_and_never_posted_twice(
    monkeypatch,
    tmp_path,
) -> None:
    async def scenario() -> None:
        await initialise_test_database(monkeypatch, tmp_path)
        monkeypatch.setattr(const, "X_ACCOUNT_HANDLE", "NimHunt")
        calls = 0

        async def fake_prewarm(_spot):
            return None

        async def fake_request(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return x_auto_poster.XResponse(
                status=201,
                data={"data": {"id": "123456789"}},
                headers={},
            )

        monkeypatch.setattr(x_auto_poster, "prewarm_spot_card", fake_prewarm)
        monkeypatch.setattr(x_auto_poster, "request_json", fake_request)

        first = await x_auto_poster.post_spot_once(
            spot_fixture(),
            credentials(),
            now=200,
        )
        second = await x_auto_poster.post_spot_once(
            spot_fixture(),
            credentials(),
            now=201,
        )
        assert first == {
            "spot_id": 42,
            "posted": True,
            "post_id": "123456789",
        }
        assert second["reason"] == "already_posted"
        assert calls == 1

        async with schema.get_db() as db:
            raw = await x_auto_poster._get_metadata(
                db,
                x_auto_poster._spot_state_key(42),
            )
        state = json.loads(raw)
        assert state["state"] == "posted"
        assert state["post_id"] == "123456789"

    run(scenario())


def test_ambiguous_transport_failure_is_not_retried(
    monkeypatch,
    tmp_path,
) -> None:
    async def scenario() -> None:
        await initialise_test_database(monkeypatch, tmp_path)
        monkeypatch.setattr(const, "X_ACCOUNT_HANDLE", "NimHunt")
        calls = 0

        async def fake_prewarm(_spot):
            return None

        async def fake_request(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise x_auto_poster.XTransportError("timeout")

        monkeypatch.setattr(x_auto_poster, "prewarm_spot_card", fake_prewarm)
        monkeypatch.setattr(x_auto_poster, "request_json", fake_request)

        first = await x_auto_poster.post_spot_once(
            spot_fixture(),
            credentials(),
            now=200,
        )
        second = await x_auto_poster.post_spot_once(
            spot_fixture(),
            credentials(),
            now=300,
        )
        assert first["uncertain"] is True
        assert second["reason"] == "already_uncertain"
        assert calls == 1

    run(scenario())


def test_rate_limit_is_a_safe_retry_but_server_error_is_uncertain() -> None:
    retry_state, retry_result = x_auto_poster._classify_post_response(
        x_auto_poster.XResponse(
            status=429,
            data={},
            headers={"retry-after": "60"},
        ),
        spot_id=7,
        now=100,
    )
    uncertain_state, uncertain_result = x_auto_poster._classify_post_response(
        x_auto_poster.XResponse(status=503, data={}, headers={}),
        spot_id=8,
        now=100,
    )
    assert retry_state["state"] == "retry"
    assert retry_result["retry"] is True
    assert uncertain_state["state"] == "uncertain"
    assert uncertain_result["uncertain"] is True


def test_implementation_uses_existing_metadata_and_lifecycle_hooks() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    database = (ROOT / "database.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    worker = (ROOT / "x_auto_poster.py").read_text(encoding="utf-8")

    assert "SCHEMA_VERSION = 3" in database
    assert "APP_METADATA_TABLE_NAME" in worker
    assert "import x_auto_poster" in main
    assert "await x_auto_poster.start_x_auto_poster(run_immediately=True)" in main
    assert "x_auto_poster.stop_x_auto_poster" in main
    assert '"x_auto_post": x_auto_poster.x_auto_poster_status()' in main
    assert "NIMHUNT_X_AUTO_POST_ENABLED" in readme
    assert "NIMHUNT_X_ACCOUNT_HANDLE" in readme
    assert "NIMHUNT_X_ACCESS_TOKEN_SECRET" in readme
