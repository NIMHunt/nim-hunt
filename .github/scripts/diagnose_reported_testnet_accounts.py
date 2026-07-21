from __future__ import annotations

import json
import urllib.request
from typing import Any

RPC_URL = "https://rpc.testnet.nimiqwatch.com/"
ADDRESSES = {
    "creator_reported": "NQ11 9C0Q FLC0 JPK2 TD7F 5XS0 NN1B PFGR A9BE",
    "claimer_reported": "NQ02 K7KP R97B L6M7 KJ1A 5VNN NFR4 85JD F8SJ",
    "fee": "NQ38 2YYK 977P FX4B 2D89 LLL7 YYNQ NPL5 VP1T",
}


def rpc(method: str, params: list[Any]) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        RPC_URL,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode())
    if payload.get("error"):
        raise RuntimeError(f"{method}: {payload['error']!r}")
    result = payload.get("result")
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result


def norm(value: Any) -> str:
    return "".join(str(value or "").upper().split())


def walk(value: Any):
    queue = [value]
    while queue:
        item = queue.pop(0)
        yield item
        if isinstance(item, dict):
            queue.extend(item.values())
        elif isinstance(item, list):
            queue.extend(item)


def scalar(item: Any, keys: set[str]) -> Any:
    if not isinstance(item, dict):
        return None
    lowered = {key.lower() for key in keys}
    for key, value in item.items():
        if key.lower() in lowered and not isinstance(value, (dict, list)):
            return value
    for child in item.values():
        if isinstance(child, (dict, list)):
            found = scalar(child, keys)
            if found is not None:
                return found
    return None


def transactions(result: Any) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in walk(result):
        if not isinstance(item, dict):
            continue
        tx_hash = scalar(item, {"hash", "transactionHash", "txHash"})
        if tx_hash and len(str(tx_hash).replace("0x", "")) >= 64:
            found[str(tx_hash)] = item
    return list(found.values())


def summary(tx: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": scalar(tx, {"hash", "transactionHash", "txHash"}),
        "sender": scalar(tx, {"sender", "from", "senderAddress", "fromAddress"}),
        "senderType": scalar(tx, {"senderType", "fromType"}),
        "recipient": scalar(tx, {"recipient", "to", "recipientAddress", "toAddress"}),
        "recipientType": scalar(tx, {"recipientType", "toType"}),
        "value": scalar(tx, {"value", "amount"}),
        "executionResult": scalar(tx, {"executionResult"}),
        "blockNumber": scalar(tx, {"blockNumber", "blockHeight", "height"}),
        "timestamp": scalar(tx, {"timestamp", "time"}),
    }


def main() -> None:
    report: dict[str, Any] = {"accounts": {}, "histories": {}, "counterparties": {}}
    for name, address in ADDRESSES.items():
        report["accounts"][name] = rpc("getAccountByAddress", [address])
        history = transactions(rpc("getTransactionsByAddress", [address, 100, None]))
        report["histories"][name] = [summary(tx) for tx in history]

    creator = norm(ADDRESSES["creator_reported"])
    known = {norm(v) for v in ADDRESSES.values()}
    counterparty_addresses: list[str] = []
    for tx in report["histories"]["creator_reported"]:
        if norm(tx.get("sender")) == creator and tx.get("recipient"):
            counterparty_addresses.append(str(tx["recipient"]))
        elif norm(tx.get("recipient")) == creator and tx.get("sender"):
            counterparty_addresses.append(str(tx["sender"]))

    unique_counterparties: list[str] = []
    for address in counterparty_addresses:
        if norm(address) and norm(address) not in known and norm(address) not in {norm(v) for v in unique_counterparties}:
            unique_counterparties.append(address)

    for address in unique_counterparties[:12]:
        try:
            account = rpc("getAccountByAddress", [address])
            history = transactions(rpc("getTransactionsByAddress", [address, 100, None]))
            report["counterparties"][address] = {
                "account": account,
                "transactions": [summary(tx) for tx in history],
            }
        except Exception as exc:
            report["counterparties"][address] = {"error": repr(exc)}

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
