from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


path = ROOT / "trans_updater.py"
text = path.read_text()
text = replace_once(
    text,
    '''async def resolve_nimiq_pay_payout_address(
    address: str,
    *,
    source_tx_hash: str | None = None,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
) -> str:
''',
    '''async def resolve_nimiq_pay_payout_address(
    address: str,
    *,
    source_tx_hash: str | None = None,
    rpc_url: str = DEFAULT_NIMIQ_RPC_URL,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    force_chain_resolution: bool | None = None,
) -> str:
''',
    label="resolver signature",
)
text = replace_once(
    text,
    '''    clean_address = _validate_nimiq_address(address, field_name="payout address")

    source_status: ChainTransactionStatus | None = None
''',
    '''    raw_address = str(address or "").strip()
    if not raw_address:
        raise ValueError("payout address must be non-empty")

    should_resolve = (
        bool(getattr(const, "PUBLIC_DEPLOYMENT", False))
        if force_chain_resolution is None
        else bool(force_chain_resolution)
    )
    if not should_resolve:
        # Local development intentionally uses placeholder addresses and must not
        # contact a public chain merely to exercise fake wallet sends.
        return raw_address

    clean_address = _validate_nimiq_address(raw_address, field_name="payout address")

    source_status: ChainTransactionStatus | None = None
''',
    label="resolver deployment boundary",
)
path.write_text(text)


path = ROOT / "tests" / "test_nimiq_payout_address_resolution.py"
text = path.read_text()
text = text.replace(
    '''SOURCE = const.DEV_PLATFORM_FEE_ADDRESS
RECIPIENT = const.DEV_SECOND_FUNDING_ADDRESS
SENDER = const.DEV_FUNDING_ADDRESS
''',
    '''SOURCE = "NQ35 6EUX JD08 6F88 KYA2 EDMC V3BC PXLB ELSB"
RECIPIENT = "NQ54 B1TQ 0U90 Q75L 0J5X SR5R JUHY 1K63 50SG"
SENDER = "NQ94 UJTU 52M6 SRLQ 9X0K 5P1K D5X7 VRKG D02U"
''',
)
text = text.replace(
    "await trans_updater.resolve_nimiq_pay_payout_address(SOURCE)",
    "await trans_updater.resolve_nimiq_pay_payout_address(SOURCE, force_chain_resolution=True)",
)
text = text.replace(
    '''                source_tx_hash=HASH,
            )
''',
    '''                source_tx_hash=HASH,
                force_chain_resolution=True,
            )
''',
)
text = text.replace(
    '''                await trans_updater.resolve_nimiq_pay_payout_address(
                    SOURCE,
                    source_tx_hash=HASH,
                )
''',
    '''                await trans_updater.resolve_nimiq_pay_payout_address(
                    SOURCE,
                    source_tx_hash=HASH,
                    force_chain_resolution=True,
                )
''',
)
path.write_text(text)

print("Adjusted HTLC payout repair for public-chain-only resolution")
