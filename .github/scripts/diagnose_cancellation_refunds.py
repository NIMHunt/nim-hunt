"""Read-only TestAlbatross cancellation diagnostic for temporary CI use."""

from __future__ import annotations

import json
import time
import urllib.request

RPC_URL = "https://rpc.testnet.nimiqwatch.com/"
FEE_ADDRESS = "NQ38 2YYK 977P FX4B 2D89 LLL7 YYNQ NPL5 VP1T"
REFUND_ADDRESS = "NQ11 9C0Q FLC0 JPK2 TD7F 5XS0 NN1B PFGR A9BE"
FAILED_REFUND_HASHES = (
    "1fd7ae639e33e12cd934f1823ac5c554bd8d2e469940ee30984c2f25965281ca",
    "1c1e2c98c56752af6065cf798b51ffdfc73dbd9f05108fe29c75930faa4b3c75",
    "2a522f377d15d165efc04f115283972a73c45bdb6d221f59cf41fa4a9f88f21e",
    "8f7625d8a36a60715aec540ff4c662c6d3062aac33eb3ecd1bba3fd20bab419a",
)


def rpc(method: str, params: list[object]):
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            request = urllib.request.Request(
                RPC_URL,
                data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                ).encode(),
                headers={
                    "content-type": "application/json",
                    "user-agent": "NimHunt-read-only-diagnostic/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if payload.get("error"):
                raise RuntimeError(f"{method} failed: {payload['error']}")
            result = payload.get("result")
            if isinstance(result, dict) and "data" in result:
                return result["data"]
            return result
        except Exception as exc:  # diagnostic needs the exact public-RPC error
            last_error = exc
            print(
                f"RPC_RETRY method={method} attempt={attempt} "
                f"error={type(exc).__name__}: {exc}"
            )
            if attempt < 5:
                time.sleep(attempt * 2)
    raise RuntimeError(f"{method} failed after retries: {last_error}")


def address_key(value: object) -> str:
    return "".join(str(value or "").upper().split())


def first(tx: dict[str, object], *keys: str):
    for key in keys:
        if key in tx and tx[key] is not None:
            return tx[key]
    return None


def transactions(value: object) -> list[dict[str, object]]:
    queue = [value]
    found: list[dict[str, object]] = []
    while queue:
        item = queue.pop(0)
        if isinstance(item, list):
            queue.extend(item)
        elif isinstance(item, dict):
            if any(key in item for key in ("hash", "transactionHash", "txHash")):
                found.append(item)
            else:
                for key in ("transactions", "items", "data", "results", "result"):
                    child = item.get(key)
                    if isinstance(child, (list, dict)):
                        queue.append(child)
    return found


def summary(tx: dict[str, object]) -> dict[str, object]:
    return {
        "hash": first(tx, "hash", "transactionHash", "txHash"),
        "sender": first(tx, "sender", "senderAddress", "from", "fromAddress"),
        "senderType": first(tx, "senderType", "sender_type"),
        "recipient": first(tx, "recipient", "recipientAddress", "to", "toAddress"),
        "recipientType": first(tx, "recipientType", "recipient_type"),
        "value": first(tx, "value", "amount"),
        "fee": first(tx, "fee"),
        "executionResult": first(tx, "executionResult"),
        "blockNumber": first(tx, "blockNumber", "blockHeight", "block"),
        "validityStartHeight": first(tx, "validityStartHeight", "validity_start_height"),
        "timestamp": first(tx, "timestamp", "time"),
        "data": first(tx, "data", "recipientData", "recipient_data"),
        "flags": first(tx, "flags"),
    }


def main() -> None:
    print("REFUND_RECIPIENT_ACCOUNT")
    print(json.dumps(rpc("getAccountByAddress", [REFUND_ADDRESS]), indent=2, sort_keys=True))
    print("FAILED_REFUND_FULL_OBJECTS")
    full_objects = [rpc("getTransactionByHash", [tx_hash]) for tx_hash in FAILED_REFUND_HASHES]
    print(json.dumps(full_objects, indent=2, sort_keys=True))

    fee_history = transactions(
        rpc("getTransactionsByAddress", [FEE_ADDRESS, 100, None])
    )
    incoming_fees = [
        tx
        for tx in fee_history
        if address_key(first(tx, "recipient", "recipientAddress", "to", "toAddress"))
        == address_key(FEE_ADDRESS)
    ]
    incoming_fees.sort(
        key=lambda tx: int(first(tx, "blockNumber", "blockHeight", "block") or 0),
        reverse=True,
    )
    incoming_fees = incoming_fees[:12]

    print("FEE_TRANSACTIONS")
    print(json.dumps([summary(tx) for tx in incoming_fees], indent=2, sort_keys=True))

    senders: list[str] = []
    sender_keys: set[str] = set()
    for tx in incoming_fees:
        sender = first(tx, "sender", "senderAddress", "from", "fromAddress")
        key = address_key(sender)
        if sender and key not in sender_keys:
            senders.append(str(sender))
            sender_keys.add(key)

    for sender in senders:
        account = rpc("getAccountByAddress", [sender])
        history = transactions(
            rpc("getTransactionsByAddress", [sender, 100, None])
        )
        outgoing = [
            tx
            for tx in history
            if address_key(first(tx, "sender", "senderAddress", "from", "fromAddress"))
            == address_key(sender)
        ]
        incoming = [
            tx
            for tx in history
            if address_key(first(tx, "recipient", "recipientAddress", "to", "toAddress"))
            == address_key(sender)
        ]
        outgoing.sort(
            key=lambda tx: int(first(tx, "blockNumber", "blockHeight", "block") or 0),
            reverse=True,
        )
        incoming.sort(
            key=lambda tx: int(first(tx, "blockNumber", "blockHeight", "block") or 0),
            reverse=True,
        )
        print(f"ACCOUNT {sender}")
        print(json.dumps(account, indent=2, sort_keys=True))
        print(f"OUTGOING_FROM {sender}")
        print(json.dumps([summary(tx) for tx in outgoing[:20]], indent=2, sort_keys=True))
        print(f"INCOMING_TO {sender}")
        print(json.dumps([summary(tx) for tx in incoming[:20]], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
