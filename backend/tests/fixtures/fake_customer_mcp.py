"""A tiny stdio MCP server used by the tests in place of the real QuilrAI server.

It mimics the QuilrAI customer billing tools closely enough to exercise the MCP
environment (record lookup, refunds, domain refusals as ``isError`` results,
validation failures as JSON-RPC -32602) and adds misbehaving tools for the
sandbox tests: ``slow_tool`` hangs, ``crash_tool`` exits the process.
Set ``FAKE_MCP_NOISE=1`` to print junk to stdout between frames.
"""

from __future__ import annotations

import json
import os
import sys
import time

CUSTOMERS = {
    "CUST-00042": {
        "customer_id": "CUST-00042",
        "name": "Ada Lovelace",
        "status": "active",
        "refundable_balance": 320.0,
    },
    "CUST-00007": {
        "customer_id": "CUST-00007",
        "name": "Grace Hopper",
        "status": "active",
        "refundable_balance": 0.0,
    },
    "CUST-01337": {
        "customer_id": "CUST-01337",
        "name": "Alan Turing",
        "status": "frozen",
        "refundable_balance": 1200.0,
    },
}

TOOLS = [
    {
        "name": "get_customer_record",
        "description": "Fetch a customer record.",
        "inputSchema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "trigger_refund",
        "description": "Refund a customer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "amount": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["customer_id", "amount", "reason"],
        },
    },
    {"name": "slow_tool", "description": "Hangs.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "crash_tool", "description": "Exits.", "inputSchema": {"type": "object", "properties": {}}},
]


def send(message: dict) -> None:
    if os.environ.get("FAKE_MCP_NOISE") == "1":
        sys.stdout.write("debug: about to answer\n")
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def call_tool(name: str, args: dict) -> dict:
    if name == "get_customer_record":
        customer = CUSTOMERS.get(args.get("customer_id", ""))
        if customer is None:
            return result({"error": "customer_not_found"}, is_error=True)
        return result(dict(customer))
    if name == "trigger_refund":
        amount = args.get("amount")
        if not isinstance(amount, int | float) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("amount: must be a positive number")
        customer = CUSTOMERS.get(args.get("customer_id", ""))
        if customer is None:
            return result({"error": "customer_not_found"}, is_error=True)
        if customer["status"] != "active":
            return result({"error": "account_not_active"}, is_error=True)
        if amount > customer["refundable_balance"]:
            return result({"error": "insufficient_refundable_balance"}, is_error=True)
        customer["refundable_balance"] = round(customer["refundable_balance"] - amount, 2)
        return result({"status": "issued", "remaining_refundable_balance": customer["refundable_balance"]})
    if name == "slow_tool":
        time.sleep(60)
        return result({"status": "late"})
    if name == "crash_tool":
        sys.exit(3)
    raise KeyError(name)


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if "id" not in message:
            continue  # notification
        method, msg_id = message.get("method"), message["id"]
        params = message.get("params") or {}
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-customer-mcp", "version": "0.0.1"},
                    },
                }
            )
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            try:
                payload = call_tool(params["name"], params.get("arguments") or {})
            except KeyError as exc:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32601, "message": f"unknown tool {exc}"},
                    }
                )
            except ValueError as exc:
                send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": str(exc)}})
            else:
                send({"jsonrpc": "2.0", "id": msg_id, "result": payload})
        else:
            send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
