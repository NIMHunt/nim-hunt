# Keep the new async regression compatible with the repository's test dependencies.
replace_once(
    "tests/test_ui_polish_regressions.py",
    "from unittest.mock import AsyncMock\n",
    "import asyncio\nfrom unittest.mock import AsyncMock\n",
)
replace_once(
    "tests/test_ui_polish_regressions.py",
    dedent("""\
    @pytest.mark.asyncio
    async def test_chain_cancellation_guard_rejects_complete_standard_spot(monkeypatch) -> None:
        summary = owner_spot(success_claim_count=2, max_total_claims=2)
        monkeypatch.setattr(
            trans_updater.db_access,
            "get_spot_owner_summary",
            AsyncMock(return_value=summary),
        )

        assert await trans_updater._published_standard_spot_is_complete(object(), spot_id=7)
    """),
    dedent("""\
    def test_chain_cancellation_guard_rejects_complete_standard_spot(monkeypatch) -> None:
        summary = owner_spot(success_claim_count=2, max_total_claims=2)
        monkeypatch.setattr(
            trans_updater.db_access,
            "get_spot_owner_summary",
            AsyncMock(return_value=summary),
        )

        assert asyncio.run(
            trans_updater._published_standard_spot_is_complete(
                object(),
                spot_id=7,
                spot=summary,
            )
        )
    """),
)
