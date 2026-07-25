"""Fix lifecycle/health integration, then remove this helper and workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected patch anchor missing from {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ROOT / "main.py",
    '''            "network": getattr(const, "NIMIQ_NETWORK", ""),
            "x_auto_post": x_auto_poster.x_auto_poster_status(),
        }
    )


@app.get("/transaction-healthz", include_in_schema=False)
''',
    '''            "network": getattr(const, "NIMIQ_NETWORK", ""),
        }
    )


@app.get("/x-healthz", include_in_schema=False)
async def x_healthz() -> JSONResponse:
    """Return secret-free automatic X-poster diagnostics."""
    status = x_auto_poster.x_auto_poster_status()
    return JSONResponse(
        {
            "ok": not bool(status.get("last_error")),
            "x_auto_post": status,
        }
    )


@app.get("/transaction-healthz", include_in_schema=False)
''',
)

replace_once(
    ROOT / "tests" / "test_production_safety.py",
    '''            mock.patch.object(
                main.trans_updater,
                "start_transaction_refresher",
                mock.AsyncMock(),
            ) as start_transactions,
        ):
            await main.startup()
''',
    '''            mock.patch.object(
                main.trans_updater,
                "start_transaction_refresher",
                mock.AsyncMock(),
            ) as start_transactions,
            mock.patch.object(
                main.x_auto_poster,
                "start_x_auto_poster",
                mock.AsyncMock(),
            ) as start_x_poster,
        ):
            await main.startup()
''',
)
replace_once(
    ROOT / "tests" / "test_production_safety.py",
    '''        start_transactions.assert_awaited_once_with(
            run_immediately=True,
            fail_on_initial_error=True,
        )

    async def test_shutdown_attempts_every_service_when_one_stop_fails(self):
''',
    '''        start_transactions.assert_awaited_once_with(
            run_immediately=True,
            fail_on_initial_error=True,
        )
        start_x_poster.assert_awaited_once_with(run_immediately=True)

    async def test_shutdown_attempts_every_service_when_one_stop_fails(self):
''',
)
replace_once(
    ROOT / "tests" / "test_production_safety.py",
    '''        with (
            mock.patch.object(
                main.trans_updater,
''',
    '''        with (
            mock.patch.object(
                main.x_auto_poster,
                "stop_x_auto_poster",
                mock.AsyncMock(),
            ) as stop_x_poster,
            mock.patch.object(
                main.trans_updater,
''',
)
replace_once(
    ROOT / "tests" / "test_production_safety.py",
    '''        stop_transactions.assert_awaited_once_with()
        stop_settlement.assert_awaited_once_with()
''',
    '''        stop_x_poster.assert_awaited_once_with()
        stop_transactions.assert_awaited_once_with()
        stop_settlement.assert_awaited_once_with()
''',
)

replace_once(
    ROOT / "tests" / "test_x_auto_poster.py",
    '''    assert '"x_auto_post": x_auto_poster.x_auto_poster_status()' in main
''',
    '''    assert '@app.get("/x-healthz", include_in_schema=False)' in main
    assert '"x_auto_post": status' in main
''',
)

for relative in (
    ".github/workflows/diagnose-x-auto-poster.yml",
    "scripts/fix_x_auto_poster_integration.py",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
