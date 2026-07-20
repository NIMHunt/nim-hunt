from pathlib import Path

path = Path("trans_updater.py")
text = path.read_text(encoding="utf-8")

old = '''async def get_chain_transactions_by_address(
    address: str,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    max_transactions: int = 500,
    start_at: str = "",
) -> Any:
    """Return recent transactions for one address via Nimiq RPC."""
    return await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=rpc_url,
        method="getTransactionsByAddress",
        params=[address, int(max_transactions), str(start_at or "")],
        timeout_seconds=int(timeout_seconds),
    )
'''

new = '''async def get_chain_transactions_by_address(
    address: str,
    *,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    max_transactions: int = 500,
    start_at: str | None = None,
) -> Any:
    """Return recent transactions for one address via Nimiq RPC.

    ``startAt`` is an optional transaction-hash cursor.  When no cursor is
    requested, omit it entirely rather than sending an empty string: an empty
    string is not a valid Nimiq transaction hash and some RPC servers reject
    the whole request as invalid parameters.
    """
    params: list[Any] = [address, int(max_transactions)]
    clean_start_at = str(start_at or "").strip()
    if clean_start_at:
        if not _NIMIQ_TRANSACTION_HASH_RE.fullmatch(clean_start_at):
            raise ValueError("start_at must be a 64-character hexadecimal Nimiq transaction hash")
        params.append(clean_start_at)

    return await asyncio.to_thread(
        _json_rpc_post_sync,
        rpc_url=rpc_url,
        method="getTransactionsByAddress",
        params=params,
        timeout_seconds=int(timeout_seconds),
    )
'''

count = text.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one address-history function to replace, found {count}")

path.write_text(text.replace(old, new), encoding="utf-8")
