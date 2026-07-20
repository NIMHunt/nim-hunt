from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, mock

import constants as const
import trans_updater

SOURCE = "NQ35 6EUX JD08 6F88 KYA2 EDMC V3BC PXLB ELSB"
RECIPIENT = "NQ54 FTGY F6VJ EJPU NSMN RA5Q 0K21 8EQT Q05P"
SENDER = "NQ94 13CJ G33S CPB5 T4P5 AK5F 38H5 H6MQ KFKN"
HASH = "ab" * 32


class NimiqPayoutAddressResolutionTests(IsolatedAsyncioTestCase):
    async def test_basic_account_is_returned_unchanged(self):
        with mock.patch.object(
            trans_updater,
            "get_chain_account_by_address",
            mock.AsyncMock(return_value={"type": "basic", "address": SOURCE}),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(SOURCE, force_chain_resolution=True)
        self.assertEqual(result, SOURCE)

    async def test_htlc_reward_uses_contract_recipient(self):
        accounts = [
            {
                "type": "htlc",
                "address": SOURCE,
                "recipient": RECIPIENT,
                "sender": SENDER,
                "timeout": 2_000_000_000_000,
            },
            {"type": "basic", "address": RECIPIENT},
        ]
        with mock.patch.object(
            trans_updater,
            "get_chain_account_by_address",
            mock.AsyncMock(side_effect=accounts),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(SOURCE, force_chain_resolution=True)
        self.assertEqual(result, RECIPIENT)

    async def test_htlc_refund_before_timeout_uses_recipient(self):
        source_status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=HASH,
            raw={"fromType": 2, "timestamp": 1_000_000_000_000},
        )
        accounts = [
            {
                "type": "htlc",
                "recipient": RECIPIENT,
                "sender": SENDER,
                "timeout": 1_500_000_000_000,
            },
            {"type": "basic", "address": RECIPIENT},
        ]
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=source_status),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_account_by_address",
                mock.AsyncMock(side_effect=accounts),
            ),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(
                SOURCE,
                source_tx_hash=HASH,
                force_chain_resolution=True,
            )
        self.assertEqual(result, RECIPIENT)

    async def test_htlc_refund_after_timeout_uses_sender(self):
        source_status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=HASH,
            raw={"fromType": "htlc", "timestamp": 2_000_000_000_001},
        )
        accounts = [
            {
                "type": "htlc",
                "recipient": RECIPIENT,
                "sender": SENDER,
                "timeout": 2_000_000_000_000,
            },
            {"type": "basic", "address": SENDER},
        ]
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=source_status),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_account_by_address",
                mock.AsyncMock(side_effect=accounts),
            ),
        ):
            result = await trans_updater.resolve_nimiq_pay_payout_address(
                SOURCE,
                source_tx_hash=HASH,
                force_chain_resolution=True,
            )
        self.assertEqual(result, SENDER)

    async def test_pruned_source_htlc_fails_closed(self):
        source_status = trans_updater.ChainTransactionStatus(
            status="confirmed",
            tx_hash=HASH,
            raw={"fromType": 2, "timestamp": 1_000_000_000_000},
        )
        with (
            mock.patch.object(
                trans_updater,
                "get_chain_transaction_status",
                mock.AsyncMock(return_value=source_status),
            ),
            mock.patch.object(
                trans_updater,
                "get_chain_account_by_address",
                mock.AsyncMock(return_value={"type": "basic", "address": SOURCE}),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "pruned"):
                await trans_updater.resolve_nimiq_pay_payout_address(
                    SOURCE,
                    source_tx_hash=HASH,
                    force_chain_resolution=True,
                )

    async def test_unsupported_contract_type_fails_before_send(self):
        with mock.patch.object(
            trans_updater,
            "get_chain_account_by_address",
            mock.AsyncMock(return_value={"type": "vesting", "address": SOURCE}),
        ):
            with self.assertRaisesRegex(RuntimeError, "not supported"):
                await trans_updater.resolve_nimiq_pay_payout_address(SOURCE, force_chain_resolution=True)
