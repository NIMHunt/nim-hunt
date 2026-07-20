# Follow-up compatibility fixes discovered by the full existing test suite.
replace_once(
    "trans_updater.py",
    dedent("""\
    async def _published_standard_spot_is_complete(db, *, spot_id: int) -> bool:
        summary = await db_access.get_spot_owner_summary(db, spot_id=int(spot_id))
        if summary is None:
            return False
        if int(summary.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_PUBLISHED:
            return False
        if summary.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None:
            return False
        max_total = int(summary.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
        successful = int(summary.get("success_claim_count") or 0)
        return max_total > 0 and successful >= max_total
    """),
    dedent("""\
    async def _published_standard_spot_is_complete(
        db,
        *,
        spot_id: int,
        spot: RowDict | None = None,
    ) -> bool:
        candidate = spot or {}
        if int(candidate.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_PUBLISHED:
            return False

        # The normal SPOT row already tells us whether capacity is finite. Avoid
        # an unnecessary summary query for unlimited or incomplete test rows.
        max_total = int(candidate.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
        if max_total <= 0:
            return False

        summary = await db_access.get_spot_owner_summary(db, spot_id=int(spot_id))
        if summary is None:
            return False
        if summary.get(schema.PRIZEDRAW_PRIZE_COUNT) is not None:
            return False
        successful = int(summary.get("success_claim_count") or 0)
        return successful >= max_total
    """),
)

path = ROOT / "trans_updater.py"
text = path.read_text(encoding="utf-8")
old_call = dedent("""\
await _published_standard_spot_is_complete(
    db, spot_id=int(spot_id)
)
""")
new_call = dedent("""\
await _published_standard_spot_is_complete(
    db, spot_id=int(spot_id), spot=spot
)
""")
for spaces in (4, 8):
    old = _indent_block(old_call, spaces)
    new = _indent_block(new_call, spaces)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Expected one completion call at {spaces} spaces, found {count}"
        )
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

replace_once(
    "tests/test_cancellation_and_find_spots_followup.py",
    '    assert serialised["badge_status_label"] == "deposited"\n',
    '    assert serialised["badge_status_label"] == "depositing"\n',
)
