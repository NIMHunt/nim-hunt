    ".spot-badge.is-depositing,\n.spot-badge.is-deposited,\n.spot-badge.is-cancelling {",
)
replace_once(
    "static/home.css",
    dedent("""\
    .spot-badge.is-draft,
    .spot-badge.is-ended,
    .spot-badge.is-completed,
    .spot-badge.is-unknown {
        background: var(--nh-muted);
        filter: grayscale(1);
        opacity: 0.62;
    }
    """),
    dedent("""\
    .spot-badge.is-draft,
    .spot-badge.is-ended,
    .spot-badge.is-unknown {
        background: var(--nh-muted);
        filter: grayscale(1);
        opacity: 0.62;
    }

    .spot-badge.is-completed {
        background: #0582ca;
        color: #ffffff;
    }
    """),
)
replace_once(
    "static/home.css",
    dedent("""\
    .spot-password-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 8px 0;
        border-bottom: 1px solid rgba(31, 35, 72, 0.07);
    }
    """),
    dedent("""\
    .spot-password-row {
        min-width: 0;
        display: flex;
        flex-wrap: nowrap;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 8px 0;
        border-bottom: 1px solid rgba(31, 35, 72, 0.07);
    }
    """),
)
replace_once(
    "static/home.css",
    dedent("""\
    .spot-password-left {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        gap: 4px;
    }

    .spot-password-code {
        min-width: 0;
        color: var(--nh-text);
        font-weight: 900;
        overflow-wrap: anywhere;
    }

    .spot-password-status {
        flex: 0 1 auto;
        color: var(--nh-muted);
        font-weight: 850;
        text-align: right;
        overflow-wrap: anywhere;
    }
    """),
    dedent("""\
    .spot-password-left {
        min-width: max-content;
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        gap: 4px;
    }

    .spot-password-code {
        min-width: 0;
        color: var(--nh-text);
        font-weight: 900;
        white-space: nowrap;
    }

    .spot-password-status {
        min-width: 0;
        flex: 1 1 auto;
        overflow: hidden;
        color: var(--nh-muted);
        font-weight: 850;
        text-align: right;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    """),
)


# ---------------------------------------------------------------------------
# Owner serialisation: pending chain deposit -> Depositing; confirmed funding ->
# Deposited. Exhausted Standard Spots are Complete/previous and non-cancellable.
# ---------------------------------------------------------------------------
replace_once(
    "public_html.py",
    '_ASSET_VERSION = "claim-live-status-v1-20260719"',
    '_ASSET_VERSION = "polish-live-status-v1-20260720"',
)
insert_before_once(
    "public_html.py",
    "def _transaction_status_label(status_code: int | None) -> str:\n",
    dedent("""\
    def _owner_spot_effectively_complete(spot: dict[str, Any]) -> bool:
        # A published Standard Spot is complete once every finite claim slot is used.
        if int(spot.get(schema.SPOT_STATUS) or -1) != const.SPOT_STATUS_PUBLISHED:
            return False
        if _spot_is_prizedraw_row(spot):
            return False
        max_total = int(spot.get(schema.SPOT_MAX_TOTAL_CLAIMS) or 0)
        successful = int(spot.get("success_claim_count") or 0)
        return max_total > 0 and successful >= max_total


    """),
)
replace_once(
    "public_html.py",
    dedent("""\
        is_prizedraw = _spot_is_prizedraw_row(spot)
        cancellation = _cancellation_summary(transactions)
        cancellation_started = spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None
        bucket = _owner_spot_bucket(spot, now=now, status_label=status_label)
    """),
    dedent("""\
        is_prizedraw = _spot_is_prizedraw_row(spot)
        effectively_complete = _owner_spot_effectively_complete(spot)
        if effectively_complete:
            status_label = "completed"
        cancellation = _cancellation_summary(transactions)
        cancellation_started = spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None
        bucket = _owner_spot_bucket(spot, now=now, status_label=status_label)
    """),
)
replace_once(
    "public_html.py",
    dedent("""\
                    "deposited"
                    if status_label == "draft"
                    and (
                        int(deposit.get("pending_amount") or 0) > 0
                        or bool(deposit.get("funding_complete"))
                    )
                    else status_label
    """),
    dedent("""\
                    "depositing"
                    if status_label == "draft"
                    and int(deposit.get("pending_amount") or 0) > 0
                    else (
                        "deposited"
                        if status_label == "draft"
                        and bool(deposit.get("funding_complete"))
                        else status_label
                    )
    """),
)
replace_once(
    "public_html.py",
    dedent("""\
                    int(spot[schema.SPOT_STATUS]) == const.SPOT_STATUS_PUBLISHED
                    and not is_prizedraw
    """),
    dedent("""\
                    int(spot[schema.SPOT_STATUS]) == const.SPOT_STATUS_PUBLISHED
                    and not is_prizedraw
                    and not effectively_complete
    """),
)

# Enforce the same completed-Spot cancellation rule inside the chain-facing,
# BEGIN IMMEDIATE protected workflow so it cannot be bypassed or raced.
insert_before_once(
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
