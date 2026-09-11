"""Bounded, privacy-minimised observation of claimant funding origins.

This is deliberately a one-hop corroborating signal, not wallet attribution.
The candidate origin is the sender of the oldest successful, positive-value,
basic-account transfer directly into the signer, but only when the signer's
bounded history can be exhausted. Contract-originated rewards, self transfers,
failed transactions and dust-free-but-zero events are not funding.

Only address hashes and the candidate transfer's timestamp/amount are durable.
The source's first history page is sampled to conservatively suppress sources
that look like services. No source wallet is punished and no graph is crawled.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import constants as const
import db_access
import fresh_claim_guard
import trans_updater

logger = logging.getLogger(__name__)
ORIGIN_PREFIX = "wallet_cluster_guard:origin:"
SOURCE_PREFIX = "wallet_cluster_guard:source:"


def _hash(address: str) -> str:
    return hashlib.sha256(address.encode()).hexdigest()


def _type(raw: dict[str, Any], key: str) -> str | None:
    value = trans_updater._first_chain_scalar_for_keys(raw, {key})
    return trans_updater._normalise_chain_account_type(value)


def _meaningful_inbound(tx: dict[str, Any], address: str) -> dict[str, Any] | None:
    if not fresh_claim_guard._confirmed(tx):
        return None
    sender = trans_updater._extract_chain_from_address(tx)
    recipient = trans_updater._extract_chain_to_address(tx)
    amount = trans_updater._extract_chain_amount(tx)
    timestamp = fresh_claim_guard._transaction_timestamp(tx)
    if (sender is None or recipient != address or sender == address or amount is None
            or amount <= 0 or timestamp is None):
        return None
    # Explicit non-basic endpoints represent rewards/contracts rather than a
    # plain wallet funding relationship. Missing type fields remain compatible
    # with RPC response variants already accepted by trans_updater.
    if _type(tx, "fromType") not in {None, "basic"}:
        return None
    if _type(tx, "toType") not in {None, "basic"}:
        return None
    return {"source": sender, "timestamp": timestamp, "amount": int(amount)}


async def _complete_history(address: str) -> list[dict[str, Any]]:
    cursor = None
    seen: set[str] = set()
    cursors: set[str] = set()
    collected: list[dict[str, Any]] = []
    page_size = max(1, int(const.CLAIM_FUNDING_HISTORY_PAGE_SIZE))
    for _ in range(max(1, int(const.CLAIM_FUNDING_HISTORY_MAX_PAGES))):
        raw = await trans_updater.get_chain_transactions_by_address(
            address, max_transactions=page_size, start_at=cursor,
            timeout_seconds=const.IP_GEOLOCATION_TIMEOUT_SECONDS,
        )
        page = list(trans_updater._iter_candidate_transactions(raw))
        for tx in page:
            tx_hash = trans_updater._extract_chain_hash(tx)
            if tx_hash is None or tx_hash not in seen:
                collected.append(tx)
            if tx_hash:
                seen.add(tx_hash)
        if len(page) < page_size:
            return collected
        cursor = trans_updater._extract_chain_hash(page[-1])
        if not cursor or cursor in cursors:
            raise RuntimeError("funding history pagination did not advance")
        cursors.add(cursor)
    raise RuntimeError("funding history exceeded its bounded page limit")


async def _source_breadth(source: str) -> str:
    """Classify only demonstrated outgoing recipient breadth.

    A full bounded page with few recipients is inconclusive, not a service.
    """
    limit = max(1, int(const.CLAIM_FUNDING_SOURCE_SAMPLE_SIZE))
    raw = await trans_updater.get_chain_transactions_by_address(
        source, max_transactions=limit, start_at=None,
        timeout_seconds=const.IP_GEOLOCATION_TIMEOUT_SECONDS,
    )
    transactions = list(trans_updater._iter_candidate_transactions(raw))
    recipients = {
        recipient for tx in transactions
        if (fresh_claim_guard._confirmed(tx)
            and trans_updater._extract_chain_from_address(tx) == source)
        for recipient in [trans_updater._extract_chain_to_address(tx)]
        if recipient and recipient != source
    }
    if len(recipients) >= int(const.CLAIM_FUNDING_SOURCE_SERVICE_DEGREE):
        return "service"
    if len(transactions) >= limit:
        return "unknown"
    return "ordinary"


def _cluster_state(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict) or source.get("result") != "observed":
        return {"evidence": False, "member_count": 0, "similar_pattern": False}
    members = dict(source.get("members") or {})
    count = int(source.get("member_count") or len(members))
    if source.get("service_like") is True or source.get("membership_uncertain") is True:
        return {"evidence": False, "member_count": count, "similar_pattern": False}
    return {"evidence": source.get("cluster_evidence") is True,
            "member_count": count,
            "similar_pattern": source.get("similar_pattern") is True}


def _pattern(members: dict[str, Any]) -> tuple[bool, bool]:
    values = list(members.values())
    if len(values) < int(const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS):
        return False, False
    timestamps = [int(item["timestamp"]) for item in values]
    amounts = [int(item["amount"]) for item in values]
    tight = max(timestamps) - min(timestamps) <= int(const.CLAIM_FUNDING_CLUSTER_WINDOW_SECONDS)
    # Similar means all positive transfers lie within 10% of the largest. It
    # strengthens/logs evidence but is not an independent enforcement trigger.
    similar = bool(amounts) and min(amounts) * 10 >= max(amounts) * 9
    return bool(tight), bool(tight and similar)


def _add_member(state: dict[str, Any], *, signer_hash: str,
                timestamp: int, amount: int) -> None:
    if state.get("membership_uncertain") is True:
        return
    members = dict(state.get("members") or {})
    if signer_hash not in members:
        limit = max(int(const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS),
                    int(const.CLAIM_FUNDING_CLUSTER_MEMBER_LIMIT))
        if len(members) < limit:
            members[signer_hash] = {"timestamp": int(timestamp), "amount": int(amount)}
        else:
            state["members_saturated"] = True
        state["member_count"] = min(limit + 1,
                                    int(state.get("member_count") or len(members) - 1) + 1)
    else:
        members[signer_hash] = {"timestamp": int(timestamp), "amount": int(amount)}
    state["members"] = members
    evidence, similar = _pattern(members)
    # Additions are monotonic. More claimant wallets can never erase evidence.
    state["cluster_evidence"] = bool(state.get("cluster_evidence") or evidence)
    state["similar_pattern"] = bool(state.get("similar_pattern") or similar)


def _remove_member(state: dict[str, Any], *, signer_hash: str) -> None:
    members = dict(state.get("members") or {})
    if state.get("members_saturated") is True:
        # Exact correction is impossible. Disable punitive evidence rather than
        # retaining a potentially ghost member/cluster.
        state.update({"membership_uncertain": True, "cluster_evidence": False,
                      "similar_pattern": False})
        return
    if signer_hash in members:
        members.pop(signer_hash)
        state["members"] = members
        state["member_count"] = len(members)
        evidence, similar = _pattern(members)
        state.update({"cluster_evidence": evidence, "similar_pattern": similar})


async def observe(db, *, signer_address: str, now: int) -> dict[str, Any]:
    """Return funding evidence; provider failures are cached UNKNOWN briefly."""
    if db.in_transaction:
        raise RuntimeError("funding-origin lookup requires no active transaction")
    address = trans_updater._validate_nimiq_address(
        signer_address, field_name="claim signer address")
    signer_hash = _hash(address)
    origin_key = f"{ORIGIN_PREFIX}{signer_hash}"
    cached = await fresh_claim_guard._get(db, origin_key)
    if isinstance(cached, dict) and int(cached.get("refresh_at") or 0) > now:
        if cached.get("result") == "provider_failure":
            return {"status": "unknown", "evidence": False}
        source_hash = cached.get("source_hash")
        source = await fresh_claim_guard._get(db, f"{SOURCE_PREFIX}{source_hash}")
        return {"status": "observed", **_cluster_state(source)}

    lookup_started_at = int(now)
    try:
        history = await _complete_history(address)
        candidates = [candidate for tx in history
                      if (candidate := _meaningful_inbound(tx, address)) is not None]
        origin = min(candidates, key=lambda item: item["timestamp"]) if candidates else None
        prior_source = (await fresh_claim_guard._get(
            db, f"{SOURCE_PREFIX}{_hash(origin['source'])}")) if origin else None
        if (isinstance(prior_source, dict) and prior_source.get("result") == "observed"
                and int(prior_source.get("source_refresh_at") or 0) > now):
            breadth = str(prior_source.get("breadth") or "unknown")
        else:
            breadth = await _source_breadth(origin["source"]) if origin else None
    except Exception:
        logger.warning("Funding-origin history unavailable for signer_hash=%s", signer_hash[:12])
        async with db_access.transaction(db, immediate=True):
            current = await fresh_claim_guard._get(db, origin_key)
            if (not isinstance(current, dict)
                    or (current.get("result") != "observed"
                        and int(current.get("checked_at") or 0) <= lookup_started_at)):
                await fresh_claim_guard._set(db, origin_key, {
                    "result": "provider_failure", "checked_at": now,
                    "refresh_at": now + const.CLAIM_SIGNER_HISTORY_FAILURE_RETRY_SECONDS,
                })
        return {"status": "unknown", "evidence": False}

    local_record = {
        "result": "observed",
        "source_hash": _hash(origin["source"]) if origin else None,
        "first_funding_at": origin["timestamp"] if origin else None,
        "first_funding_amount": origin["amount"] if origin else None,
        "checked_at": now, "refresh_at": now + const.CLAIM_FUNDING_CACHE_SECONDS,
    }
    async with db_access.transaction(db, immediate=True):
        current = await fresh_claim_guard._get(db, origin_key)
        if (isinstance(current, dict) and current.get("result") == "observed"
                and int(current.get("checked_at") or 0) >= lookup_started_at):
            # The concurrent winner is authoritative in its entirety. Never
            # combine its source with this request's stale amount/timestamp.
            authoritative = current
            wrote_origin = False
        else:
            authoritative = local_record
            wrote_origin = True
            old_source_hash = current.get("source_hash") if isinstance(current, dict) else None
            new_source_hash = authoritative.get("source_hash")
            if old_source_hash and old_source_hash != new_source_hash:
                old_key = f"{SOURCE_PREFIX}{old_source_hash}"
                old_state = await fresh_claim_guard._get(db, old_key)
                if isinstance(old_state, dict):
                    old_state = dict(old_state)
                    _remove_member(old_state, signer_hash=signer_hash)
                    await fresh_claim_guard._set(db, old_key, old_state)
            await fresh_claim_guard._set(db, origin_key, authoritative)
        source_hash = authoritative.get("source_hash")
        if source_hash and wrote_origin:
            key = f"{SOURCE_PREFIX}{source_hash}"
            state = await fresh_claim_guard._get(db, key)
            state = dict(state) if isinstance(state, dict) else {
                "result": "observed", "members": {}}
            _add_member(
                state, signer_hash=signer_hash,
                timestamp=int(authoritative["first_funding_at"]),
                amount=int(authoritative["first_funding_amount"]),
            )
            state.update({"result": "observed",
                          "breadth": breadth,
                          # Demonstrated breadth is monotonic; claimant count is
                          # deliberately not a service classification signal.
                          "service_like": bool(state.get("service_like")
                                               or breadth == "service"),
                          "source_refresh_at": now + const.CLAIM_FUNDING_CACHE_SECONDS,
                          "checked_at": now})
            await fresh_claim_guard._set(db, key, state)
            source = state
        elif source_hash:
            source = await fresh_claim_guard._get(db, f"{SOURCE_PREFIX}{source_hash}")
        else:
            source = None
    return {"status": "observed", **_cluster_state(source)}
