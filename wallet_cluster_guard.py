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


async def _source_is_service_like(source: str) -> bool:
    limit = max(1, int(const.CLAIM_FUNDING_SOURCE_SAMPLE_SIZE))
    raw = await trans_updater.get_chain_transactions_by_address(
        source, max_transactions=limit, start_at=None,
        timeout_seconds=const.IP_GEOLOCATION_TIMEOUT_SECONDS,
    )
    transactions = list(trans_updater._iter_candidate_transactions(raw))
    recipients = {
        recipient for tx in transactions
        if fresh_claim_guard._confirmed(tx)
        for recipient in [trans_updater._extract_chain_to_address(tx)]
        if recipient and recipient != source
    }
    # A full page means degree may be arbitrarily higher than the sample. Treat
    # it as service-like even if repeated recipients obscure sampled degree.
    return (len(transactions) >= limit
            or len(recipients) >= int(const.CLAIM_FUNDING_SOURCE_SERVICE_DEGREE))


def _cluster_state(source: Any, *, signer_hash: str) -> dict[str, Any]:
    if not isinstance(source, dict) or source.get("result") != "observed":
        return {"evidence": False, "member_count": 0, "similar_pattern": False}
    members = dict(source.get("members") or {})
    if signer_hash not in members or source.get("service_like") is not False:
        return {"evidence": False, "member_count": len(members), "similar_pattern": False}
    values = list(members.values())
    timestamps = [int(item["timestamp"]) for item in values]
    amounts = [int(item["amount"]) for item in values]
    tight = max(timestamps) - min(timestamps) <= int(const.CLAIM_FUNDING_CLUSTER_WINDOW_SECONDS)
    # Similar means all positive transfers lie within 10% of the largest. It
    # strengthens/logs evidence but is not an independent enforcement trigger.
    similar = bool(amounts) and min(amounts) * 10 >= max(amounts) * 9
    enough = len(members) >= int(const.CLAIM_FUNDING_CLUSTER_MIN_CLAIMANTS)
    return {"evidence": bool(enough and tight), "member_count": len(members),
            "similar_pattern": bool(enough and tight and similar)}


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
        return {"status": "observed", **_cluster_state(source, signer_hash=signer_hash)}

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
            service_like = bool(prior_source.get("service_like"))
        else:
            service_like = await _source_is_service_like(origin["source"]) if origin else None
    except Exception:
        logger.warning("Funding-origin history unavailable for signer_hash=%s", signer_hash[:12])
        async with db_access.transaction(db, immediate=True):
            current = await fresh_claim_guard._get(db, origin_key)
            if not isinstance(current, dict) or int(current.get("checked_at") or 0) <= lookup_started_at:
                await fresh_claim_guard._set(db, origin_key, {
                    "result": "provider_failure", "checked_at": now,
                    "refresh_at": now + const.CLAIM_SIGNER_HISTORY_FAILURE_RETRY_SECONDS,
                })
        return {"status": "unknown", "evidence": False}

    source_hash = _hash(origin["source"]) if origin else None
    async with db_access.transaction(db, immediate=True):
        current = await fresh_claim_guard._get(db, origin_key)
        if (isinstance(current, dict) and current.get("result") == "observed"
                and int(current.get("checked_at") or 0) >= lookup_started_at):
            source_hash = current.get("source_hash")
        else:
            await fresh_claim_guard._set(db, origin_key, {
                "result": "observed", "source_hash": source_hash,
                "first_funding_at": origin["timestamp"] if origin else None,
                "first_funding_amount": origin["amount"] if origin else None,
                "checked_at": now, "refresh_at": now + const.CLAIM_FUNDING_CACHE_SECONDS,
            })
        if source_hash:
            key = f"{SOURCE_PREFIX}{source_hash}"
            state = await fresh_claim_guard._get(db, key)
            state = dict(state) if isinstance(state, dict) else {
                "result": "observed", "members": {}}
            members = dict(state.get("members") or {})
            members[signer_hash] = {"timestamp": int(origin["timestamp"]),
                                    "amount": int(origin["amount"])}
            service_like = bool(
                state.get("service_like") or service_like
                or len(members) >= const.CLAIM_FUNDING_SOURCE_SERVICE_DEGREE
            )
            if service_like:
                # Once suppression is certain, individual claimant membership
                # no longer serves the rule and need not remain correlated.
                members = {}
            state.update({"result": "observed", "members": members,
                          "service_like": service_like,
                          "source_refresh_at": now + const.CLAIM_FUNDING_CACHE_SECONDS,
                          "checked_at": now})
            await fresh_claim_guard._set(db, key, state)
            source = state
        else:
            source = None
    return {"status": "observed", **_cluster_state(source, signer_hash=signer_hash)}
