from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:100]!r}")
    write(path, content.replace(old, new, 1))


def remove(path: str) -> None:
    target = ROOT / path
    if target.exists():
        target.unlink()


# ---------------------------------------------------------------------------
# Browser payment boundary: use the documented Mini App provider contract.
# ---------------------------------------------------------------------------
write(
    "static/nimiq_payment.js",
    r'''const NIMIQ_TRANSACTION_HASH_RE = /^[0-9a-f]{64}$/i;
const DEFAULT_MAX_HEAD_DIFFERENCE = 120;

function providerErrorMessage(value) {
    const error = value?.error;
    if (!error) return '';
    if (typeof error === 'string') return error.trim();
    if (typeof error !== 'object') return String(error);
    return String(error.message || error.reason || error.code || 'Nimiq Pay rejected the request.');
}

function throwProviderError(value) {
    const message = providerErrorMessage(value);
    if (message) throw new Error(message);
    return value;
}

export function normaliseNimiqTransactionHash(value) {
    if (typeof value !== 'string') {
        throw new Error('Nimiq Pay did not return a transaction hash.');
    }
    const hash = value.trim();
    if (!NIMIQ_TRANSACTION_HASH_RE.test(hash)) {
        throw new Error('Nimiq Pay returned an invalid transaction hash. The deposit was not recorded.');
    }
    return hash.toLowerCase();
}

function requireBlockHeight(value, label) {
    const result = throwProviderError(value);
    const height = Number(result);
    if (!Number.isSafeInteger(height) || height < 0) {
        throw new Error(`${label} did not return a valid block height.`);
    }
    return height;
}

function requireAccounts(value) {
    const result = throwProviderError(value);
    if (!Array.isArray(result) || result.length === 0) {
        throw new Error('Nimiq Pay did not share a funding account.');
    }
    const accounts = result.map((account) => typeof account === 'string' ? account.trim() : '');
    if (accounts.some((account) => !account)) {
        throw new Error('Nimiq Pay returned an invalid funding account.');
    }
    return accounts;
}

export async function requestNimiqPayment(provider, intent) {
    if (!provider
        || typeof provider.isConsensusEstablished !== 'function'
        || typeof provider.getBlockNumber !== 'function'
        || typeof provider.listAccounts !== 'function'
        || typeof provider.sendBasicTransactionWithData !== 'function') {
        throw new Error('The Nimiq Pay provider is unavailable or incomplete.');
    }

    const consensus = throwProviderError(await provider.isConsensusEstablished());
    if (consensus !== true) {
        throw new Error('Nimiq Pay has not established blockchain consensus yet. Please try again shortly.');
    }

    const walletHeight = requireBlockHeight(await provider.getBlockNumber(), 'Nimiq Pay');
    const serverHeightValue = intent?.chain_height;
    if (serverHeightValue !== null && serverHeightValue !== undefined) {
        const serverHeight = requireBlockHeight(serverHeightValue, 'NimHunt');
        const maxDifference = Number.isSafeInteger(Number(intent.max_chain_height_difference))
            ? Math.max(1, Number(intent.max_chain_height_difference))
            : DEFAULT_MAX_HEAD_DIFFERENCE;
        if (Math.abs(walletHeight - serverHeight) > maxDifference) {
            throw new Error(
                'Nimiq Pay and NimHunt appear to be connected to different or badly out-of-sync networks. '
                + 'No transaction was requested.'
            );
        }
    }

    const accounts = requireAccounts(await provider.listAccounts());
    const amount = Number(intent?.amount);
    if (!Number.isSafeInteger(amount) || amount <= 0) {
        throw new Error('NimHunt supplied an invalid deposit amount.');
    }

    const result = throwProviderError(await provider.sendBasicTransactionWithData({
        recipient: String(intent?.recipient || '').trim(),
        value: amount,
        data: String(intent?.transaction_description || ''),
        validityStartHeight: walletHeight,
    }));

    // The documented provider contract returns the hash directly as a string.
    // Never recurse through arbitrary result/data fields: signatures, request IDs
    // and device hashes can also be 64-character hexadecimal strings.
    const txHash = normaliseNimiqTransactionHash(result);
    return {
        txHash,
        fromAddress: accounts[0],
        walletHeight,
    };
}
''',
)

write(
    "static/keyed_reconcile.js",
    r'''export function reconcileKeyedItems({
    existingItems,
    desiredRecords,
    existingKey,
    desiredKey,
    existingSignature,
    desiredSignature,
    createItem,
    appendItem,
    removeItem,
}) {
    const grouped = new Map();
    for (const item of existingItems) {
        const key = existingKey(item);
        const candidates = grouped.get(key) || [];
        candidates.push(item);
        grouped.set(key, candidates);
    }

    const finalItems = [];
    for (const record of desiredRecords) {
        const key = desiredKey(record);
        const signature = desiredSignature(record);
        const candidates = grouped.get(key) || [];
        const reusable = candidates.find((item) => existingSignature(item) === signature) || null;

        for (const candidate of candidates) {
            if (candidate !== reusable) removeItem(candidate);
        }

        const item = reusable || createItem(record);
        finalItems.push(item);
        grouped.delete(key);
    }

    for (const candidates of grouped.values()) {
        for (const candidate of candidates) removeItem(candidate);
    }
    for (const item of finalItems) appendItem(item);
    return finalItems;
}
''',
)

replace_once(
    "static/my_spots.js",
    "import { init, requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';\n",
    "import { init, requestDeviceIdentifier } from 'https://esm.sh/@nimiq/mini-app-sdk';\n"
    "import { reconcileKeyedItems } from './keyed_reconcile.js?v=blockchain-flow-v1-20260720';\n"
    "import { requestNimiqPayment } from './nimiq_payment.js?v=blockchain-flow-v1-20260720';\n",
)

replace_once(
    "static/my_spots.js",
    """    title.textContent = `${copy.title} (${spots.length})`;
    const existing = new Map(
        [...list.children].map((item) => [Number(item.dataset.spotId), item]),
    );
    const desired = [];
    for (const spot of spots) {
        const spotId = Number(spot.id);
        const signature = mySpotRenderSignature(spot);
        const current = existing.get(spotId);
        if (current?.dataset.renderSignature === signature) desired.push(current);
        else desired.push(buildMySpotListItem(spot));
        existing.delete(spotId);
    }

    for (const item of desired) list.append(item);
    for (const stale of existing.values()) stale.remove();
""",
    """    title.textContent = `${copy.title} (${spots.length})`;
    reconcileKeyedItems({
        existingItems: [...list.children],
        desiredRecords: spots,
        existingKey: (item) => Number(item.dataset.spotId),
        desiredKey: (spot) => Number(spot.id),
        existingSignature: (item) => item.dataset.renderSignature,
        desiredSignature: mySpotRenderSignature,
        createItem: buildMySpotListItem,
        appendItem: (item) => list.append(item),
        removeItem: (item) => item.remove(),
    });
""",
)

replace_once(
    "static/my_spots.js",
    """async function requestDepositPayment(intent) {
    const nimiq = await init();
    let fromAddress = null;

    try {
        const accounts = await nimiq.listAccounts();
        if (Array.isArray(accounts) && accounts.length > 0) {
            fromAddress = accounts[0];
        }
    } catch (err) {
        console.warn('Could not read Nimiq account list before deposit.', err);
    }

    const payment = await nimiq.sendBasicTransactionWithData({
        recipient: intent.recipient,
        value: Number(intent.amount),
        data: intent.transaction_description,
    });

    const txHash = (
        typeof payment === 'string'
            ? payment
            : payment?.txHash || payment?.hash || payment?.transactionHash || payment?.transaction?.hash
    );

    if (!txHash) {
        throw new Error('Nimiq Pay did not return a transaction hash for this deposit.');
    }

    return { txHash, fromAddress };
}

async function confirmDeposit() {
""",
    """const PENDING_DEPOSIT_STORAGE_KEY = 'nimhunt.pendingDepositSubmission.v2';
const OBSOLETE_PENDING_DEPOSIT_STORAGE_KEY = 'nimhunt.pendingDepositSubmission.v1';
const RETRYABLE_DEPOSIT_RECORDING_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);

function savePendingDepositSubmission(record) {
    try {
        sessionStorage.setItem(PENDING_DEPOSIT_STORAGE_KEY, JSON.stringify(record));
    } catch (_err) {
        // Some private WebViews disable storage. The immediate retry still works.
    }
}

function clearPendingDepositSubmission() {
    try {
        sessionStorage.removeItem(PENDING_DEPOSIT_STORAGE_KEY);
    } catch (_err) {
        // Nothing else is required.
    }
}

function readPendingDepositSubmission() {
    try {
        // PR #29 used a broad provider-response parser. Never replay hashes stored
        // by that implementation because they might not identify a transaction.
        sessionStorage.removeItem(OBSOLETE_PENDING_DEPOSIT_STORAGE_KEY);
        const raw = sessionStorage.getItem(PENDING_DEPOSIT_STORAGE_KEY);
        if (!raw) return null;
        const value = JSON.parse(raw);
        if (!value?.spotId || !value?.txHash || !value?.amount) return null;
        return value;
    } catch (_err) {
        clearPendingDepositSubmission();
        return null;
    }
}

function depositRecordingIsRetryable(err) {
    return !Number.isFinite(Number(err?.status))
        || RETRYABLE_DEPOSIT_RECORDING_STATUSES.has(Number(err.status));
}

function depositSubmissionBody(record) {
    return {
        ...authPayload(),
        tx_hash: record.txHash,
        from_address: record.fromAddress,
        amount: record.amount,
    };
}

async function submitDepositRecording(record, { retry = true } = {}) {
    const submit = () => fetchJson(`/api/my-spots/${record.spotId}/deposit-submitted`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(depositSubmissionBody(record)),
    });

    try {
        return await submit();
    } catch (err) {
        if (!retry || !depositRecordingIsRetryable(err)) throw err;
        await new Promise((resolve) => window.setTimeout(resolve, 650));
        return submit();
    }
}

async function recoverPendingDepositSubmission() {
    const record = readPendingDepositSubmission();
    if (!record) return false;
    try {
        await submitDepositRecording(record, { retry: true });
        clearPendingDepositSubmission();
        return true;
    } catch (err) {
        console.warn('NimHunt could not recover the submitted deposit record yet.', err);
        return false;
    }
}

async function requestDepositPayment(intent) {
    const nimiq = await init();
    return requestNimiqPayment(nimiq, intent);
}

async function confirmDeposit() {
""",
)

replace_once(
    "static/my_spots.js",
    """    try {
        const payment = await requestDepositPayment(state.depositIntent);
        await fetchJson(`/api/my-spots/${state.depositSpot.id}/deposit-submitted`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...authPayload(),
                tx_hash: payment.txHash,
                from_address: payment.fromAddress,
                amount: state.depositIntent.amount,
            }),
        });

        state.depositInProgress = false;
        els.depositBackdrop.hidden = true;
        await refreshMySpots();
    } catch (err) {
        console.error(err);
        state.depositInProgress = false;
        els.depositConfirm.disabled = false;
        els.depositCancel.disabled = false;
        els.depositConfirm.textContent = TEXT.deposit.confirm;
        showNotice({
            ...TEXT.deposit.failed,
            body: err?.message || TEXT.deposit.failed.body,
        });
    }
""",
    """    let submittedRecord = null;
    try {
        const payment = await requestDepositPayment(state.depositIntent);
        submittedRecord = {
            spotId: Number(state.depositSpot.id),
            txHash: payment.txHash,
            fromAddress: payment.fromAddress,
            amount: Number(state.depositIntent.amount),
            createdAt: Date.now(),
        };
        savePendingDepositSubmission(submittedRecord);
        await submitDepositRecording(submittedRecord, { retry: true });
        clearPendingDepositSubmission();

        state.depositInProgress = false;
        els.depositBackdrop.hidden = true;
        state.depositSpot = null;
        state.depositIntent = null;
        await refreshMySpots();
    } catch (err) {
        console.error(err);
        state.depositInProgress = false;
        els.depositBackdrop.hidden = true;
        els.depositConfirm.disabled = false;
        els.depositCancel.disabled = false;
        els.depositConfirm.textContent = TEXT.deposit.confirm;
        showNotice({
            ...TEXT.deposit.failed,
            body: submittedRecord
                ? `${err?.message || TEXT.deposit.failed.body} The wallet returned a transaction hash; NimHunt will retry recording that same hash without requesting another payment.`
                : (err?.message || TEXT.deposit.failed.body),
        });
    }
""",
)

replace_once(
    "static/my_spots.js",
    """    renderLoadedMySpots(data);
    scheduleMySpotsRefresh();
""",
    """    renderLoadedMySpots(data);
    if (await recoverPendingDepositSubmission()) {
        renderLoadedMySpots(await loadMySpots());
    }
    scheduleMySpotsRefresh();
""",
)

# Remove the global provider/fetch monkeypatch added by PR #29.
replace_once(
    "static/localise_page.js",
    "import { installDepositFlowRepair } from './deposit_flow_repair.js?v=deposit-recording-v1-20260720';\n",
    "",
)
replace_once(
    "static/localise_page.js",
    """// Wrap only the HTTP recording half of the deposit flow. The blockchain payment
// remains under Nimiq Pay's control; retries reuse its returned transaction hash
// and can never reopen the wallet transaction prompt.
installDepositFlowRepair();

""",
    "",
)
remove("static/deposit_flow_repair.js")

# Cache-bust the repaired browser modules in Nimiq Pay's WebView.
replace_once(
    "public_html.py",
    '_ASSET_VERSION = "polish-live-status-v1-20260720"',
    '_ASSET_VERSION = "blockchain-flow-v1-20260720"',
)

# ---------------------------------------------------------------------------
# Core transaction logic: production and tests now use the same deposit code.
# ---------------------------------------------------------------------------
replace_once("trans_updater.py", "import os\n", "import os\nimport re\n")
replace_once(
    "trans_updater.py",
    'LOCAL_TRANSACTION_INTENT_PREFIX = "NIMHUNT_INTENT:"\n',
    'LOCAL_TRANSACTION_INTENT_PREFIX = "NIMHUNT_INTENT:"\n'
    '_NIMIQ_TRANSACTION_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")\n'
    'DEFAULT_USER_DEPOSIT_STALE_AFTER_SECONDS = int(getattr(const, "USER_DEPOSIT_STALE_AFTER_SECONDS", 30 * 60))\n',
)
replace_once(
    "trans_updater.py",
    """    tx_hash = str(result.tx_hash or "").strip()
    if not tx_hash or _is_local_intent_hash(tx_hash):
        raise RuntimeError("Nimiq helper returned an invalid transaction hash")
""",
    """    tx_hash = str(result.tx_hash or "").strip().lower()
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(tx_hash):
        raise RuntimeError("Nimiq helper returned an invalid transaction hash")
""",
)

replace_once(
    "trans_updater.py",
    """async def verify_configured_rpc_network(
    *,
    expected_network_id: int | None = None,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    \"\"\"Fail startup when the configured RPC serves a different Nimiq network.\"\"\"
    expected = int(
        expected_network_id
        if expected_network_id is not None
        else getattr(const, \"NIMIQ_NETWORK_ID\", 0)
    )
    actual = await get_configured_rpc_network_id(
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
    )
    if actual != expected:
        raise RuntimeError(
            f\"Configured Nimiq RPC serves network ID {actual}, expected {expected}\"
        )
    return actual


""",
    """async def verify_configured_rpc_network(
    *,
    expected_network_id: int | None = None,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    \"\"\"Fail startup when the configured RPC serves a different Nimiq network.\"\"\"
    expected = int(
        expected_network_id
        if expected_network_id is not None
        else getattr(const, \"NIMIQ_NETWORK_ID\", 0)
    )
    actual = await get_configured_rpc_network_id(
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
    )
    if actual != expected:
        raise RuntimeError(
            f\"Configured Nimiq RPC serves network ID {actual}, expected {expected}\"
        )
    return actual


async def get_chain_head_height(
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> int:
    \"\"\"Return the configured RPC's latest block height.\"\"\"
    result = await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=str(rpc_url),
        method=\"getLatestBlock\",
        params=[False],
        timeout_seconds=int(timeout_seconds),
    )
    if isinstance(result, dict) and \"data\" in result:
        result = result.get(\"data\")
    if isinstance(result, (int, str)):
        try:
            height = int(result)
        except ValueError as exc:
            raise RuntimeError(\"Nimiq RPC returned an invalid block height\") from exc
    else:
        height = _extract_block_number(result)
    if height is None or int(height) < 0:
        raise RuntimeError(\"Nimiq RPC getLatestBlock did not expose a block height\")
    return int(height)


""",
)

replace_once(
    "trans_updater.py",
    """async def check_pending_transaction(
    trans: RowDict,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    fail_after_seconds: int = DEFAULT_FAIL_AFTER_SECONDS,
) -> ChainTransactionStatus:
""",
    """async def check_pending_transaction(
    trans: RowDict,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    fail_after_seconds: int = DEFAULT_FAIL_AFTER_SECONDS,
    user_deposit_stale_after_seconds: int = DEFAULT_USER_DEPOSIT_STALE_AFTER_SECONDS,
) -> ChainTransactionStatus:
""",
)

replace_once(
    "trans_updater.py",
    """    if chain_status.status != "pending":
        return chain_status

    if _transaction_age_seconds(trans) >= int(fail_after_seconds):
""",
    """    if chain_status.status != "pending":
        return chain_status

    age_seconds = _transaction_age_seconds(trans)
    if (
        int(trans.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and age_seconds >= int(user_deposit_stale_after_seconds)
    ):
        address = _verification_address_for_record(trans)
        if address is None:
            return ChainTransactionStatus(
                status="unknown",
                tx_hash=tx_hash,
                reason="stale deposit cannot be checked because its recipient address is invalid",
            )
        try:
            history = await get_chain_transactions_by_address(
                address,
                rpc_url=rpc_url,
                timeout_seconds=int(timeout_seconds),
                max_transactions=int(getattr(const, "NIMIQ_ADDRESS_TX_LOOKUP_LIMIT", 500)),
            )
        except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
            return ChainTransactionStatus(
                status="unknown",
                tx_hash=tx_hash,
                reason=f"stale deposit address-history check failed: {exc!r}",
            )
        matched = _find_transaction_by_hash(history, tx_hash)
        if matched is not None:
            return ChainTransactionStatus(
                status="confirmed",
                tx_hash=tx_hash,
                block_number=_extract_block_number(matched),
                raw=matched,
                reason="found through deposit-address history",
            )
        return ChainTransactionStatus(
            status="failed",
            tx_hash=tx_hash,
            reason=(
                f"deposit hash was not found by hash or recipient history after {age_seconds} seconds; "
                "the Nimiq transaction validity window has elapsed"
            ),
        )

    if age_seconds >= int(fail_after_seconds):
""",
)

old_record = '''async def record_spot_deposit_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    tx_hash: str,
    to_address: str | None = None,
) -> RowDict:
    """Record a user-initiated SPOT deposit returned by Nimiq Pay.

    The user signs/sends this transaction in the Pay webview. NimHunt only
    records the returned hash and later confirms it through check_pending_*().
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("only draft spots can receive creator deposits")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        raise ValueError("this draft is being cancelled and cannot receive another deposit")

    clean_to_address = wallet.normalise_nimiq_address(
        str(to_address or spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="deposit to_address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )

    try:
        clean_from_address = wallet.normalise_nimiq_address(
            str(from_address or ""),
            field_name="deposit from_address",
            allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
        )
    except ValueError:
        if not getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False):
            raise
        clean_from_address = str(from_address or "Nimiq Pay").strip() or "Nimiq Pay"

    funding_address = await db_access.get_confirmed_spot_funding_address(
        db,
        spot_id=int(spot_id),
    )
    if funding_address is not None:
        established_sender = _normalise_address_for_compare(funding_address)
        submitted_sender = _normalise_address_for_compare(clean_from_address)
        if established_sender is None or submitted_sender != established_sender:
            raise ValueError(
                "Additional deposits for this Spot must come from its original funding wallet."
            )

    trans_id = await db_access.create_spot_deposit_transaction(
        db,
        user_id=int(user_id),
        spot_id=int(spot_id),
        amount=amount,
        from_address=clean_from_address,
        to_address=clean_to_address,
        tx_hash=str(tx_hash).strip(),
    )

    return {"ok": True, "trans_id": int(trans_id), "spot_id": int(spot_id), "amount": amount}
'''
new_record = '''async def _transaction_by_hash(db, *, tx_hash: str) -> RowDict | None:
    cur = await db.execute(
        f"SELECT * FROM {schema.TRANS_TABLE_NAME} WHERE {schema.TRANS_TX_HASH} = ? LIMIT 1;",
        (str(tx_hash).strip().lower(),),
    )
    row = await cur.fetchone()
    return dict(row) if row is not None else None


def _same_recorded_deposit(existing: RowDict, *, user_id: int, spot_id: int) -> bool:
    return (
        int(existing.get(schema.TRANS_TYPE) or -1) == const.TRANS_TYPE_FILL_SPOT
        and int(existing.get(schema.TRANS_USER_ID) or -1) == int(user_id)
        and int(existing.get(schema.TRANS_SPOT_ID) or -1) == int(spot_id)
    )


async def record_spot_deposit_transaction(
    db,
    *,
    user_id: int,
    spot_id: int,
    amount: int,
    from_address: str,
    tx_hash: str,
    to_address: str | None = None,
) -> RowDict:
    """Record one strictly shaped, idempotent Nimiq Pay deposit response."""
    clean_hash = str(tx_hash or "").strip().lower()
    if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_hash):
        raise ValueError("tx_hash must be a 64-character hexadecimal Nimiq transaction hash")

    existing = await _transaction_by_hash(db, tx_hash=clean_hash)
    if existing is not None:
        if not _same_recorded_deposit(existing, user_id=user_id, spot_id=spot_id):
            raise ValueError("this transaction hash is already attached to a different record")
        return {
            "ok": True,
            "already_recorded": True,
            "trans_id": int(existing[schema.TRANS_ID]),
            "spot_id": int(spot_id),
            "amount": int(existing.get(schema.TRANS_AMOUNT) or 0),
        }

    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")

    spot = await db_access.get_spot(db, spot_id=int(spot_id))
    if spot is None:
        raise ValueError(f"spot id={spot_id} does not exist")
    if int(spot[schema.SPOT_STATUS]) != const.SPOT_STATUS_DRAFT:
        raise ValueError("only draft spots can receive creator deposits")
    if spot.get(schema.SPOT_CANCELLATION_STARTED_AT) is not None:
        raise ValueError("this draft is being cancelled and cannot receive another deposit")

    clean_to_address = wallet.normalise_nimiq_address(
        str(to_address or spot.get(schema.SPOT_DEPOSIT_ADDRESS) or ""),
        field_name="deposit to_address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )
    clean_from_address = wallet.normalise_nimiq_address(
        str(from_address or ""),
        field_name="deposit from_address",
        allow_dev_placeholder=bool(getattr(const, "ALLOW_DEV_WALLET_PLACEHOLDERS", False)),
    )

    totals = await db_access.get_spot_deposit_totals(db, spot_id=int(spot_id))
    if int(totals.get("pending_amount") or 0) > 0:
        raise ValueError("this draft already has a pending deposit")
    required = int(db_access.spot_required_deposit_amount(spot))
    amount_due = max(0, required - int(totals.get("confirmed_amount") or 0))
    if amount_due <= 0:
        raise ValueError("this draft is already fully funded")
    amount = min(amount, amount_due)

    funding_address = await db_access.get_confirmed_spot_funding_address(
        db,
        spot_id=int(spot_id),
    )
    if funding_address is not None:
        established_sender = _normalise_address_for_compare(funding_address)
        submitted_sender = _normalise_address_for_compare(clean_from_address)
        if established_sender is None or submitted_sender != established_sender:
            raise ValueError(
                "Additional deposits for this Spot must come from its original funding wallet."
            )

    try:
        trans_id = await db_access.create_spot_deposit_transaction(
            db,
            user_id=int(user_id),
            spot_id=int(spot_id),
            amount=amount,
            from_address=clean_from_address,
            to_address=clean_to_address,
            tx_hash=clean_hash,
        )
    except sqlite3.IntegrityError:
        existing = await _transaction_by_hash(db, tx_hash=clean_hash)
        if existing is None or not _same_recorded_deposit(existing, user_id=user_id, spot_id=spot_id):
            raise
        return {
            "ok": True,
            "already_recorded": True,
            "trans_id": int(existing[schema.TRANS_ID]),
            "spot_id": int(spot_id),
            "amount": int(existing.get(schema.TRANS_AMOUNT) or 0),
        }

    return {
        "ok": True,
        "already_recorded": False,
        "trans_id": int(trans_id),
        "spot_id": int(spot_id),
        "amount": amount,
    }
'''
replace_once("trans_updater.py", old_record, new_record)

# The PR #29 runtime monkeypatch is obsolete now that the core recorder is safe.
replace_once(
    "funding_flow.py",
    "from deposit_submission_safety import install as install_deposit_submission_safety\n",
    "",
)
replace_once(
    "funding_flow.py",
    "    install_deposit_submission_safety()\n",
    "",
)
remove("deposit_submission_safety.py")
remove("tests/test_deposit_submission_safety.py")

# Chain-aware deposit intent and idempotent recording retries.
replace_once(
    "public_html.py",
    """        if int(deposit.get("pending_amount") or 0) > 0:
            return JSONResponse({**meta, "ok": False, "code": "deposit_pending", "message": "This draft already has a pending deposit. Wait for it to confirm or fail before making another deposit."}, status_code=status.HTTP_409_CONFLICT)

        amount_due = int(deposit.get("amount_due") or 0)
        if amount_due <= 0:
            return JSONResponse({**meta, "ok": False, "code": "deposit_covered", "message": "This draft already has submitted deposits covering its Spot value and creation fee."}, status_code=status.HTTP_409_CONFLICT)

    return JSONResponse(
""",
    """        if int(deposit.get("pending_amount") or 0) > 0:
            return JSONResponse({**meta, "ok": False, "code": "deposit_pending", "message": "This draft already has a pending deposit. Wait for it to confirm or fail before making another deposit."}, status_code=status.HTTP_409_CONFLICT)

        amount_due = int(deposit.get("amount_due") or 0)
        if amount_due <= 0:
            return JSONResponse({**meta, "ok": False, "code": "deposit_covered", "message": "This draft already has submitted deposits covering its Spot value and creation fee."}, status_code=status.HTTP_409_CONFLICT)

    try:
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

    return JSONResponse(
""",
)
replace_once(
    "public_html.py",
    """            "recipient": spot.get(schema.SPOT_DEPOSIT_ADDRESS),
            "transaction_description": build_transaction_description(
""",
    """            "recipient": spot.get(schema.SPOT_DEPOSIT_ADDRESS),
            "network": getattr(const, "NIMIQ_NETWORK", None),
            "network_id": int(getattr(const, "NIMIQ_NETWORK_ID", 0)),
            "chain_height": chain_height,
            "max_chain_height_difference": int(getattr(const, "NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE", 120)),
            "transaction_description": build_transaction_description(
""",
)

replace_once(
    "public_html.py",
    """            if int(deposit.get("pending_amount") or 0) > 0:
                return JSONResponse({**meta, "ok": False, "code": "deposit_pending", "message": "This draft already has a pending deposit. Wait for it to confirm or fail before making another deposit."}, status_code=status.HTTP_409_CONFLICT)

            amount_due = int(deposit.get("amount_due") or 0)
            if amount_due <= 0:
                return JSONResponse({**meta, "ok": False, "code": "deposit_covered", "message": "This draft already has submitted deposits covering its Spot value and creation fee."}, status_code=status.HTTP_409_CONFLICT)

            # The normal Nimiq Pay flow submits the full requested amount, but
            # recording a smaller positive amount allows deliberate/manual
            # part-funding without weakening safety. Chain verification later
            # replaces this expectation with the actual confirmed amount. Never
            # record more than the current server-calculated amount due.
            submitted_amount = min(
                amount_due,
                max(1, int(payload.amount if payload.amount is not None else amount_due)),
            )
""",
    """            amount_due = int(deposit.get("amount_due") or 0)
            requested_amount = max(
                1,
                int(payload.amount if payload.amount is not None else amount_due or 1),
            )
            # record_spot_deposit_transaction() checks the hash first, so a lost
            # HTTP response can be retried idempotently even while the original
            # row is already pending. New deposits are still clamped to amount due.
            submitted_amount = min(amount_due, requested_amount) if amount_due > 0 else requested_amount
""",
)

# Configuration for chain preflight and stale user-deposit resolution.
replace_once(
    "constants.py",
    """# Transaction loop. This checks pending Nimiq transaction hashes and moves them
# from pending to confirmed/failed when the chain/RPC result is known.
TRANSACTION_CHECK_INTERVAL_SECONDS = 60
""",
    """# Transaction loop. This checks pending Nimiq transaction hashes and moves them
# from pending to confirmed/failed when the chain/RPC result is known.
TRANSACTION_CHECK_INTERVAL_SECONDS = 60
# Nimiq transactions expire after a 120-block validity window. We use a much
# longer wall-clock grace period before treating a user-submitted hash as absent,
# and require a successful recipient-history check before releasing the draft.
USER_DEPOSIT_STALE_AFTER_SECONDS = _env_int("NIMHUNT_USER_DEPOSIT_STALE_AFTER_SECONDS", 30 * 60)
# A large block-height gap is a practical indication that Nimiq Pay and the
# server RPC are connected to different networks or one side is badly stale.
NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE = _env_int("NIMHUNT_NIMIQ_PROVIDER_MAX_HEAD_DIFFERENCE", 120)
""",
)

# ---------------------------------------------------------------------------
# Outgoing helper: an RPC call must return a real accepted hash.
# ---------------------------------------------------------------------------
replace_once(
    "helpers/nimiq_helper.mjs",
    "    typeof tx.hash === 'function' ? tx.hash() : undefined,\n",
    "    typeof tx?.hash === 'function' ? tx.hash() : undefined,\n",
)
replace_once(
    "helpers/nimiq_helper.mjs",
    """function transactionHash(tx, txDetails = null) {
  const candidates = [
    txDetails?.transactionHash,
    txDetails?.hash,
    txDetails?.txHash,
    typeof tx?.hash === 'function' ? tx.hash() : undefined,
  ];
  for (const value of candidates) {
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return '';
}
""",
    """function transactionHash(tx, txDetails = null) {
  const candidates = [
    txDetails?.transactionHash,
    txDetails?.hash,
    txDetails?.txHash,
    typeof tx?.hash === 'function' ? tx.hash() : undefined,
  ];
  for (const value of candidates) {
    if (value === undefined || value === null) continue;
    const candidate = typeof value?.toHex === 'function' ? value.toHex() : String(value).trim();
    if (candidate) return candidate;
  }
  return '';
}

function requireTransactionHash(value, source) {
  const hash = String(value || '').trim().replace(/^0x/i, '').toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(hash)) {
    throw new Error(`${source} did not return a 64-character hexadecimal transaction hash.`);
  }
  return hash;
}
""",
)

replace_once(
    "helpers/nimiq_helper.mjs",
    """  let height;
  let sendResult;

  if (rpcUrl) {
    const latestBlock = await rpcCall(rpcUrl, 'getLatestBlock', [false]);
    height = blockHeight(latestBlock);
  } else {
    const client = await createClient({ ...payload, network });
    try {
      height = await client.getHeadHeight();
    } finally {
      await closeClient(client);
    }
  }

  const data = encodeTransactionMemo(payload.memo);
  const tx = data.byteLength > 0
    ? Nimiq.TransactionBuilder.newBasicWithData(
        keyPair.toAddress(), recipient, data, amount, fee, height, networkId,
      )
    : Nimiq.TransactionBuilder.newBasic(
        keyPair.toAddress(), recipient, amount, fee, height, networkId,
      );
  tx.sign(keyPair);
  tx.verify(networkId);

  if (rpcUrl) {
    const rawTx = Buffer.from(tx.serialize()).toString('hex');
    sendResult = await rpcCall(rpcUrl, 'sendRawTransaction', [rawTx]);
  } else {
    const client = await createClient({ ...payload, network });
    try {
      sendResult = await client.sendTransaction(tx);
    } finally {
      await closeClient(client);
    }
  }

  const hash = transactionHash(tx, typeof sendResult === 'object' ? sendResult : { hash: sendResult });
  if (!hash) throw new Error('Transaction was sent but no transaction hash could be determined.');
""",
    """  let height;

  if (rpcUrl) {
    const latestBlock = await rpcCall(rpcUrl, 'getLatestBlock', [false]);
    height = blockHeight(latestBlock);
  } else {
    const client = await createClient({ ...payload, network });
    try {
      height = await client.getHeadHeight();
    } finally {
      await closeClient(client);
    }
  }

  const data = encodeTransactionMemo(payload.memo);
  const tx = data.byteLength > 0
    ? Nimiq.TransactionBuilder.newBasicWithData(
        keyPair.toAddress(), recipient, data, amount, fee, height, networkId,
      )
    : Nimiq.TransactionBuilder.newBasic(
        keyPair.toAddress(), recipient, amount, fee, height, networkId,
      );
  tx.sign(keyPair);
  const verified = tx.verify(networkId);
  if (verified === false) throw new Error('The locally signed Nimiq transaction failed verification.');

  let hash;
  if (rpcUrl) {
    const rawTx = Buffer.from(tx.serialize()).toString('hex');
    const sendResult = await rpcCall(rpcUrl, 'sendRawTransaction', [rawTx]);
    // Do not fall back to tx.hash() here. A local hash proves construction, not
    // that the configured RPC accepted and broadcast the transaction.
    hash = requireTransactionHash(
      transactionHash(null, typeof sendResult === 'object' ? sendResult : { hash: sendResult }),
      'Nimiq RPC sendRawTransaction',
    );
  } else {
    const client = await createClient({ ...payload, network });
    try {
      await client.sendTransaction(tx);
    } finally {
      await closeClient(client);
    }
    hash = requireTransactionHash(transactionHash(tx), 'Locally signed transaction');
  }
""",
)

# ---------------------------------------------------------------------------
# Tests: provider contract, UI reconciliation, RPC rejection, real DB flows,
# and production runtime composition.
# ---------------------------------------------------------------------------
write(
    "helpers/nimiq_payment.test.mjs",
    r'''import test from 'node:test';
import assert from 'node:assert/strict';
import { requestNimiqPayment } from '../static/nimiq_payment.js';

const HASH = 'ab'.repeat(32);
const INTENT = {
  recipient: 'NQ TEST RECIPIENT',
  amount: 100000,
  transaction_description: 'Funding Test',
  chain_height: 5000,
  max_chain_height_difference: 120,
};

function provider(overrides = {}) {
  const calls = [];
  return {
    calls,
    async isConsensusEstablished() { calls.push('consensus'); return true; },
    async getBlockNumber() { calls.push('height'); return 5002; },
    async listAccounts() { calls.push('accounts'); return ['NQ FUNDING']; },
    async sendBasicTransactionWithData(request) { calls.push(['send', request]); return HASH; },
    ...overrides,
  };
}

test('deposit uses the documented direct hash response and current validity height', async () => {
  const nimiq = provider();
  const payment = await requestNimiqPayment(nimiq, INTENT);
  assert.equal(payment.txHash, HASH);
  assert.equal(payment.fromAddress, 'NQ FUNDING');
  assert.equal(nimiq.calls[3][1].validityStartHeight, 5002);
  assert.equal(nimiq.calls.filter(call => Array.isArray(call) && call[0] === 'send').length, 1);
});

test('wrapped result data is never mistaken for a transaction hash', async () => {
  const nimiq = provider({
    async sendBasicTransactionWithData() { return { data: HASH }; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /did not return a transaction hash/);
});

test('provider error objects are surfaced and never recorded', async () => {
  const nimiq = provider({
    async sendBasicTransactionWithData() { return { error: { message: 'Broadcast rejected' } }; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /Broadcast rejected/);
});

test('network/head mismatch stops before account sharing and payment', async () => {
  const nimiq = provider({
    async getBlockNumber() { this.calls.push('height'); return 900000; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /different or badly out-of-sync networks/);
  assert.deepEqual(nimiq.calls, ['consensus', 'height']);
});

test('payment is blocked while wallet consensus is unavailable', async () => {
  const nimiq = provider({
    async isConsensusEstablished() { this.calls.push('consensus'); return false; },
  });
  await assert.rejects(() => requestNimiqPayment(nimiq, INTENT), /consensus/);
  assert.deepEqual(nimiq.calls, ['consensus']);
});
''',
)

write(
    "helpers/keyed_reconcile.test.mjs",
    r'''import test from 'node:test';
import assert from 'node:assert/strict';
import { reconcileKeyedItems } from '../static/keyed_reconcile.js';

function item(id, signature) {
  return { id, signature, removed: false };
}

test('changed records replace and remove the previous card instead of cloning it', () => {
  const oldDraft = item(7, 'draft');
  const accidentalDuplicate = item(7, 'older-draft');
  const appended = [];
  const created = [];

  reconcileKeyedItems({
    existingItems: [oldDraft, accidentalDuplicate],
    desiredRecords: [{ id: 7, signature: 'depositing' }],
    existingKey: value => value.id,
    desiredKey: value => value.id,
    existingSignature: value => value.signature,
    desiredSignature: value => value.signature,
    createItem: record => {
      const result = item(record.id, record.signature);
      created.push(result);
      return result;
    },
    appendItem: value => appended.push(value),
    removeItem: value => { value.removed = true; },
  });

  assert.equal(created.length, 1);
  assert.equal(appended.length, 1);
  assert.equal(appended[0].signature, 'depositing');
  assert.equal(oldDraft.removed, true);
  assert.equal(accidentalDuplicate.removed, true);
});

test('an unchanged record is reused exactly once and duplicates are removed', () => {
  const reusable = item(7, 'depositing');
  const duplicate = item(7, 'depositing');
  const appended = [];

  reconcileKeyedItems({
    existingItems: [reusable, duplicate],
    desiredRecords: [{ id: 7, signature: 'depositing' }],
    existingKey: value => value.id,
    desiredKey: value => value.id,
    existingSignature: value => value.signature,
    desiredSignature: value => value.signature,
    createItem: () => { throw new Error('should reuse'); },
    appendItem: value => appended.push(value),
    removeItem: value => { value.removed = true; },
  });

  assert.deepEqual(appended, [reusable]);
  assert.equal(reusable.removed, false);
  assert.equal(duplicate.removed, true);
});
''',
)

write(
    "helpers/helper_rpc_failure.test.mjs",
    r'''import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import http from 'node:http';
import { resolve } from 'node:path';

const helper = resolve(import.meta.dirname, 'nimiq_helper.mjs');
const MNEMONIC = 'legal winner thank year wave sausage worth useful legal winner thank yellow';
const BASE_ENV = {
  ...process.env,
  NIMHUNT_DEPLOYMENT_MODE: 'public-testnet',
  NIMHUNT_PRODUCTION: '',
  NIMHUNT_NIMIQ_MNEMONIC: MNEMONIC,
  NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC: '0',
};

function derive(index) {
  const result = spawnSync(process.execPath, [helper], {
    input: JSON.stringify({
      action: 'derive_spot_deposit_address', network: 'TestAlbatross', network_id: 5,
      key_index: index, key_path: `m/44'/242'/${index}'/0'`, key_version: 1,
    }),
    encoding: 'utf8', env: BASE_ENV,
  });
  assert.equal(result.status, 0, result.stdout || result.stderr);
  return JSON.parse(result.stdout).address;
}

function runHelper(payload) {
  return new Promise(resolveResult => {
    const child = spawn(process.execPath, [helper], { env: BASE_ENV });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('close', status => resolveResult({ status, stdout, stderr }));
    child.stdin.end(JSON.stringify(payload));
  });
}

async function runWithRpc(sendResult) {
  const sender = derive(0);
  const recipient = derive(1);
  const server = http.createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    const rpc = JSON.parse(body);
    response.setHeader('content-type', 'application/json');
    if (rpc.method === 'getLatestBlock') {
      response.end(JSON.stringify({ jsonrpc: '2.0', id: rpc.id, result: { data: { number: 123456 } } }));
      return;
    }
    response.end(JSON.stringify({ jsonrpc: '2.0', id: rpc.id, result: { data: sendResult } }));
  });
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  try {
    const address = server.address();
    return await runHelper({
      action: 'send_luna_from_spot_deposit', network: 'TestAlbatross', network_id: 5,
      rpc_url: `http://127.0.0.1:${address.port}`, from_address: sender,
      to_address: recipient, amount: 100000, fee: 0, memo: 'RPC rejection test',
      deposit_key_index: 0, deposit_key_path: "m/44'/242'/0'/0'", deposit_key_version: 1,
    });
  } finally {
    await new Promise(resolveClose => server.close(resolveClose));
  }
}

test('RPC success without a transaction hash is not reported as a broadcast', async () => {
  const result = await runWithRpc(null);
  assert.notEqual(result.status, 0);
  assert.match(result.stdout || result.stderr, /64-character hexadecimal transaction hash/);
});

test('RPC arbitrary text is not reported as a broadcast hash', async () => {
  const result = await runWithRpc('accepted');
  assert.notEqual(result.status, 0);
  assert.match(result.stdout || result.stderr, /64-character hexadecimal transaction hash/);
});
''',
)

replace_once(
    "helpers/package.json",
    '"test": "node --test transaction_data.test.mjs localisation.test.mjs prizedraw_rules.test.mjs static_localisation.test.mjs helper_safety.test.mjs helper_rpc_send.test.mjs"',
    '"test": "node --test transaction_data.test.mjs localisation.test.mjs prizedraw_rules.test.mjs static_localisation.test.mjs helper_safety.test.mjs helper_rpc_send.test.mjs helper_rpc_failure.test.mjs nimiq_payment.test.mjs keyed_reconcile.test.mjs"',
)

write(
    "tests/test_blockchain_flow_integration.py",
    r'''from __future__ import annotations

import tempfile
from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import database as schema
import db_access
import trans_updater


HASH_1 = "11" * 32
HASH_2 = "22" * 32
HASH_3 = "33" * 32
FUNDING_ADDRESS = const.DEV_PLATFORM_FEE_ADDRESS


class BlockchainFlowIntegrationTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=True)
        self._old_path = schema.DB_PATH
        schema.DB_PATH = self._tmp.name
        await schema.init_db()

    async def asyncTearDown(self):
        schema.DB_PATH = self._old_path
        self._tmp.close()

    async def create_owner_spot(self):
        async with schema.get_db() as db:
            owner_id = await db_access.create_user(db, device_id_hash="owner-blockchain")
            spot_id = await db_access.create_spot(db, created_by=owner_id, title="Chain Spot")
            await db.commit()
            spot = await db_access.get_spot(db, spot_id=spot_id)
        return owner_id, spot_id, spot

    async def record_confirmed_deposit(self, *, owner_id, spot_id, spot, tx_hash=HASH_1):
        required = db_access.spot_required_deposit_amount(spot)
        async with schema.get_db() as db:
            record = await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=required,
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                tx_hash=tx_hash,
            )
            await db.commit()
            row = await db_access.get_transaction(db, trans_id=record["trans_id"])
            verified = trans_updater.VerifiedChainDetails(
                ok=True,
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                amount=required,
            )
            await trans_updater.mark_trans_as_confirmed(
                db,
                row,
                block_number=123,
                verified_details=verified,
            )
        return record, required

    async def test_deposit_recording_is_idempotent_and_chain_confirmation_updates_funding(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        first, required = await self.record_confirmed_deposit(
            owner_id=owner_id, spot_id=spot_id, spot=spot
        )
        async with schema.get_db() as db:
            repeated = await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=required,
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                tx_hash=HASH_1.upper(),
            )
            total = await db_access.get_confirmed_spot_deposit_total(db, spot_id=spot_id)
            transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=20)

        self.assertTrue(repeated["already_recorded"])
        self.assertEqual(repeated["trans_id"], first["trans_id"])
        self.assertEqual(total, required)
        fills = [row for row in transactions if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_FILL_SPOT]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0][schema.TRANS_FROM_ADDRESS], FUNDING_ADDRESS)

    async def test_same_hash_cannot_be_attached_to_another_spot(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        await self.record_confirmed_deposit(owner_id=owner_id, spot_id=spot_id, spot=spot)
        async with schema.get_db() as db:
            other_id = await db_access.create_spot(db, created_by=owner_id, title="Other Chain Spot")
            other = await db_access.get_spot(db, spot_id=other_id)
            with self.assertRaisesRegex(ValueError, "different record"):
                await trans_updater.record_spot_deposit_transaction(
                    db,
                    user_id=owner_id,
                    spot_id=other_id,
                    amount=db_access.spot_required_deposit_amount(other),
                    from_address=FUNDING_ADDRESS,
                    to_address=other[schema.SPOT_DEPOSIT_ADDRESS],
                    tx_hash=HASH_1,
                )

    async def test_stale_unseen_user_deposit_is_released_only_after_address_history_proves_absence(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        async with schema.get_db() as db:
            record = await trans_updater.record_spot_deposit_transaction(
                db,
                user_id=owner_id,
                spot_id=spot_id,
                amount=db_access.spot_required_deposit_amount(spot),
                from_address=FUNDING_ADDRESS,
                to_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                tx_hash=HASH_1,
            )
            await db.execute(
                f"UPDATE {schema.TRANS_TABLE_NAME} SET {schema.TRANS_CREATED_AT} = unixepoch() - 3600 WHERE {schema.TRANS_ID} = ?;",
                (record["trans_id"],),
            )
            await db.commit()
            row = await db_access.get_transaction(db, trans_id=record["trans_id"])

        pending = trans_updater.ChainTransactionStatus(
            status="pending", tx_hash=HASH_1, reason="hash not found yet"
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=pending),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_transactions_by_address",
                mock.AsyncMock(return_value=[]),
            ),
        ):
            result = await trans_updater.check_pending_transaction(
                row,
                user_deposit_stale_after_seconds=1,
            )
        self.assertEqual(result.status, "failed")
        self.assertIn("validity window", result.reason)

    async def test_claim_payout_is_broadcast_once_and_reuses_the_database_guard(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        async with schema.get_db() as db:
            claimant_id = await db_access.create_user(db, device_id_hash="claimant-blockchain")
            claim_id = await db_access.create_claim(
                db,
                spot_id=spot_id,
                user_id=claimant_id,
                lat=1.0,
                long=2.0,
                accuracy=1.0,
                payout_address=FUNDING_ADDRESS,
            )
            await db.commit()

            submitted = trans_updater.SubmittedChainTransaction(
                tx_hash=HASH_2,
                from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                to_address=FUNDING_ADDRESS,
                amount=100_000,
            )
            with (
                mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True),
                mock.patch.object(
                    trans_updater,
                    "submit_chain_send_from_spot_deposit",
                    mock.AsyncMock(return_value=submitted),
                ) as send,
            ):
                first = await trans_updater.submit_claim_reward_transaction(
                    db, claim_id=claim_id, amount=100_000
                )
                second = await trans_updater.submit_claim_reward_transaction(
                    db, claim_id=claim_id, amount=100_000
                )

            transactions = await db_access.get_transactions_by_claim(db, claim_id=claim_id)

        self.assertFalse(first["already_exists"])
        self.assertTrue(second["already_exists"])
        send.assert_awaited_once()
        payouts = [row for row in transactions if int(row[schema.TRANS_TYPE]) == const.TRANS_TYPE_CLAIM]
        self.assertEqual(len(payouts), 1)
        self.assertEqual(payouts[0][schema.TRANS_TX_HASH], HASH_2)

    async def test_repeated_cancellation_does_not_broadcast_refund_or_fee_twice(self):
        owner_id, spot_id, spot = await self.create_owner_spot()
        await self.record_confirmed_deposit(owner_id=owner_id, spot_id=spot_id, spot=spot)
        counter = 0

        async def fake_send(*, spot, to_address, amount, memo=None):
            nonlocal counter
            counter += 1
            return trans_updater.SubmittedChainTransaction(
                tx_hash=(HASH_2 if counter == 1 else HASH_3),
                from_address=spot[schema.SPOT_DEPOSIT_ADDRESS],
                to_address=to_address,
                amount=amount,
            )

        async with schema.get_db() as db:
            with (
                mock.patch.object(const, "ALLOW_DEV_WALLET_SENDS", True),
                mock.patch.object(
                    trans_updater,
                    "submit_chain_send_from_spot_deposit",
                    side_effect=fake_send,
                ) as send,
            ):
                first = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=const.SPOT_CANCELLATION_FEE,
                    fee_address=FUNDING_ADDRESS,
                )
                second = await trans_updater.submit_spot_cancellation_transactions(
                    db,
                    spot_id=spot_id,
                    cancellation_fee=const.SPOT_CANCELLATION_FEE,
                    fee_address=FUNDING_ADDRESS,
                )
            transactions = await db_access.get_transactions_by_spot(db, spot_id=spot_id, limit=50)

        self.assertTrue(first["cancellation_pending"])
        self.assertTrue(second["cancellation_pending"])
        self.assertLessEqual(send.await_count, 2)
        outgoing = [
            row for row in transactions
            if int(row[schema.TRANS_TYPE]) in {const.TRANS_TYPE_CANCEL_SPOT, const.TRANS_TYPE_PLAT_FEE}
        ]
        self.assertLessEqual(len(outgoing), 2)
''',
)

write(
    "tests/test_runtime_financial_composition.py",
    r'''from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def test_production_runtime_installs_financial_guards_but_keeps_core_deposit_recorder():
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        env = {
            **os.environ,
            "NIMHUNT_DB_PATH": db_file.name,
            "NIMHUNT_DEPLOYMENT_MODE": "development",
            "NIMHUNT_PRODUCTION": "",
            "NIMHUNT_NIMIQ_NETWORK": "TestAlbatross",
            "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC": "1",
        }
        code = """
import main
import public_html
import trans_updater
assert trans_updater.record_spot_deposit_transaction.__module__ == 'trans_updater'
assert trans_updater.submit_spot_cancellation_transactions.__module__ == 'cancellation_safety'
assert trans_updater.submit_spot_creation_fee_transaction.__module__ == 'funding_fee_worker'
assert public_html._deposit_summary.__module__ == 'funding_status'
print('runtime-financial-composition-ok')
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "runtime-financial-composition-ok" in result.stdout
''',
)

print("Blockchain repair source changes applied.")
