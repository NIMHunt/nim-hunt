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
old_prizedraw_guard = _indent_block(dedent("""\
if spot_status == const.SPOT_STATUS_PUBLISHED and await db_access.is_prizedraw(
    db, spot_id=int(spot_id)
):
    raise ValueError("Prizedraw spots cannot be cancelled through this standard cancellation flow")
"""), 4)
new_prizedraw_guard = old_prizedraw_guard + _indent_block(dedent("""\
if spot_status == const.SPOT_STATUS_PUBLISHED and await _published_standard_spot_is_complete(
    db, spot_id=int(spot_id)
):
    raise ValueError("completed spots cannot be cancelled")
"""), 4)
path = ROOT / "trans_updater.py"
text = path.read_text(encoding="utf-8")
if text.count(old_prizedraw_guard) != 2:
    raise RuntimeError(f"Expected two cancellation guards, found {text.count(old_prizedraw_guard)}")
path.write_text(text.replace(old_prizedraw_guard, new_prizedraw_guard), encoding="utf-8")


# ---------------------------------------------------------------------------
# My Spots: keyed DOM reconciliation updates only changed cards. This removes
# the perceived page refresh and keeps expanded cards/codes open.
# ---------------------------------------------------------------------------
replace_once(
