    "trans_updater.py",
    "async def submit_spot_cancellation_transactions(\n",
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
)
prizedraw_guard_body = dedent("""\
if spot_status == const.SPOT_STATUS_PUBLISHED and await db_access.is_prizedraw(
    db, spot_id=int(spot_id)
):
    raise ValueError("Prizedraw spots cannot be cancelled through this standard cancellation flow")
""")
complete_guard_body = dedent("""\
if spot_status == const.SPOT_STATUS_PUBLISHED and await _published_standard_spot_is_complete(
    db, spot_id=int(spot_id)
):
    raise ValueError("completed spots cannot be cancelled")
""")
path = ROOT / "trans_updater.py"
text = path.read_text(encoding="utf-8")
for spaces in (4, 8):
    old_guard = _indent_block(prizedraw_guard_body, spaces)
    new_guard = old_guard + _indent_block(complete_guard_body, spaces)
    if text.count(old_guard) != 1:
        raise RuntimeError(
            f"Expected one cancellation guard at {spaces} spaces, found {text.count(old_guard)}"
        )
    text = text.replace(old_guard, new_guard, 1)
path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# My Spots: keyed DOM reconciliation updates only changed cards. This removes
# the perceived page refresh and keeps expanded cards/codes open.
# ---------------------------------------------------------------------------
replace_once(
