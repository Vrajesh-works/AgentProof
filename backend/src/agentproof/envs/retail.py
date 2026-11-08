"""A small retail customer-service world in the style of tau-bench's retail domain.

The data and tools are written for AgentProof (not copied from tau-bench); the
idea borrowed is the shape: a database of users, orders and products, tools that
enforce business rules, and success measured by the database state at the end.
"""

from __future__ import annotations

from typing import Any

from agentproof.envs.base import InProcessEnvironment, PyTool, ToolError, deep_merge, obj_schema

DEFAULT_STATE: dict[str, Any] = {
    "users": {
        "alice_01": {
            "name": "Alice Moreno",
            "email": "alice@example.com",
            "address": {"street": "12 Elm St", "city": "Boston", "zip": "02115"},
            "payment_methods": {
                "card_4242": {"type": "credit_card", "last4": "4242"},
                "gift_alice": {"type": "gift_card", "balance": 25.0},
            },
        },
        "bruno_02": {
            "name": "Bruno Silva",
            "email": "bruno@example.com",
            "address": {"street": "88 Pine Ave", "city": "Austin", "zip": "73301"},
            "payment_methods": {"paypal_bruno": {"type": "paypal"}},
        },
        "chen_03": {
            "name": "Chen Wei",
            "email": "chen@example.com",
            "address": {"street": "5 Market St", "city": "San Francisco", "zip": "94105"},
            "payment_methods": {
                "card_1881": {"type": "credit_card", "last4": "1881"},
                "gift_chen": {"type": "gift_card", "balance": 0.0},
            },
        },
    },
    "products": {
        "P-LAMP": {
            "name": "Desk Lamp",
            "variants": {
                "I-LAMP-BLK": {"options": {"color": "black"}, "price": 39.0, "available": True},
                "I-LAMP-WHT": {"options": {"color": "white"}, "price": 39.0, "available": False},
            },
        },
        "P-MUG": {
            "name": "Ceramic Mug",
            "variants": {
                "I-MUG-12": {"options": {"size": "12oz"}, "price": 12.5, "available": True},
                "I-MUG-16": {"options": {"size": "16oz"}, "price": 14.0, "available": True},
            },
        },
        "P-KBD": {
            "name": "Mechanical Keyboard",
            "variants": {
                "I-KBD-RED": {"options": {"switch": "red"}, "price": 119.0, "available": True},
            },
        },
    },
    "orders": {
        "W1001": {
            "user_id": "alice_01",
            "status": "pending",
            "items": [{"item_id": "I-LAMP-BLK", "product_id": "P-LAMP", "price": 39.0}],
            "payment_method_id": "card_4242",
        },
        "W1002": {
            "user_id": "alice_01",
            "status": "delivered",
            "items": [
                {"item_id": "I-MUG-12", "product_id": "P-MUG", "price": 12.5},
                {"item_id": "I-MUG-16", "product_id": "P-MUG", "price": 14.0},
            ],
            "payment_method_id": "gift_alice",
        },
        "W2001": {
            "user_id": "bruno_02",
            "status": "processed",
            "items": [{"item_id": "I-KBD-RED", "product_id": "P-KBD", "price": 119.0}],
            "payment_method_id": "paypal_bruno",
        },
        "W3001": {
            "user_id": "chen_03",
            "status": "pending",
            "items": [{"item_id": "I-MUG-16", "product_id": "P-MUG", "price": 14.0}],
            "payment_method_id": "card_1881",
        },
    },
    "escalations": [],
}

CANCEL_REASONS = ["no longer needed", "ordered by mistake"]


def _order(state: dict[str, Any], order_id: str) -> dict[str, Any]:
    order = state["orders"].get(order_id)
    if order is None:
        raise ToolError(f"order {order_id} not found")
    return order


def find_user_by_email(state: dict[str, Any], email: str) -> dict[str, Any]:
    for user_id, user in state["users"].items():
        if user["email"].lower() == email.strip().lower():
            return {"user_id": user_id}
    raise ToolError(f"no user with email {email}")


def get_user(state: dict[str, Any], user_id: str) -> dict[str, Any]:
    user = state["users"].get(user_id)
    if user is None:
        raise ToolError(f"user {user_id} not found")
    orders = sorted(oid for oid, o in state["orders"].items() if o["user_id"] == user_id)
    return {"user_id": user_id, **user, "orders": orders}


def get_order(state: dict[str, Any], order_id: str) -> dict[str, Any]:
    return {"order_id": order_id, **_order(state, order_id)}


def get_product(state: dict[str, Any], product_id: str) -> dict[str, Any]:
    product = state["products"].get(product_id)
    if product is None:
        raise ToolError(f"product {product_id} not found")
    return {"product_id": product_id, **product}


def _refund(state: dict[str, Any], user_id: str, method_id: str, amount: float) -> None:
    method = state["users"][user_id]["payment_methods"][method_id]
    if method["type"] == "gift_card":
        method["balance"] = round(method["balance"] + amount, 2)


def cancel_pending_order(state: dict[str, Any], order_id: str, reason: str) -> dict[str, Any]:
    order = _order(state, order_id)
    if order["status"] != "pending":
        raise ToolError(f"order {order_id} is {order['status']}; only pending orders can be cancelled")
    if reason not in CANCEL_REASONS:
        raise ToolError(f"reason must be one of {CANCEL_REASONS}")
    total = round(sum(item["price"] for item in order["items"]), 2)
    order["status"] = "cancelled"
    order["cancel_reason"] = reason
    order["refund_amount"] = total
    _refund(state, order["user_id"], order["payment_method_id"], total)
    return {"order_id": order_id, "status": "cancelled", "refund_amount": total}


def return_delivered_order_items(
    state: dict[str, Any], order_id: str, item_ids: list[str], payment_method_id: str
) -> dict[str, Any]:
    order = _order(state, order_id)
    if order["status"] != "delivered":
        raise ToolError(f"order {order_id} is {order['status']}; only delivered orders can be returned")
    owned = [item["item_id"] for item in order["items"]]
    missing = [item_id for item_id in item_ids if item_id not in owned]
    if missing or not item_ids:
        raise ToolError(f"items {missing or item_ids} are not in order {order_id}")
    user_methods = state["users"][order["user_id"]]["payment_methods"]
    if payment_method_id not in user_methods:
        raise ToolError(f"payment method {payment_method_id} does not belong to the order's user")
    if (
        payment_method_id != order["payment_method_id"]
        and user_methods[payment_method_id]["type"] != "gift_card"
    ):
        raise ToolError("refunds go to the original payment method or a gift card")
    total = round(sum(item["price"] for item in order["items"] if item["item_id"] in item_ids), 2)
    order["status"] = "return_requested"
    order["return_items"] = sorted(item_ids)
    order["refund_method"] = payment_method_id
    order["refund_amount"] = total
    _refund(state, order["user_id"], payment_method_id, total)
    return {"order_id": order_id, "status": "return_requested", "refund_amount": total}


def modify_pending_order_address(
    state: dict[str, Any],
    order_id: str,
    street: str,
    city: str,
    zip: str,  # noqa: A002 - domain field name
) -> dict[str, Any]:
    order = _order(state, order_id)
    if order["status"] != "pending":
        raise ToolError(f"order {order_id} is {order['status']}; only pending orders can be modified")
    order["shipping_address"] = {"street": street, "city": city, "zip": zip}
    return {"order_id": order_id, "shipping_address": order["shipping_address"]}


def transfer_to_human(state: dict[str, Any], summary: str) -> dict[str, Any]:
    state["escalations"].append(summary)
    return {"status": "transferred", "ticket": f"ESC-{len(state['escalations']):03d}"}


_STR = {"type": "string", "minLength": 1}

TOOLS = [
    PyTool(
        "find_user_by_email",
        "Look up a user id by email address.",
        obj_schema({"email": _STR}),
        find_user_by_email,
    ),
    PyTool(
        "get_user",
        "Get a user's profile, payment methods and order ids.",
        obj_schema({"user_id": _STR}),
        get_user,
    ),
    PyTool(
        "get_order",
        "Get an order's status, items and payment method.",
        obj_schema({"order_id": _STR}),
        get_order,
    ),
    PyTool("get_product", "Get a product and its variants.", obj_schema({"product_id": _STR}), get_product),
    PyTool(
        "cancel_pending_order",
        "Cancel a pending order and refund it to the original payment method.",
        obj_schema({"order_id": _STR, "reason": {"type": "string", "enum": CANCEL_REASONS}}),
        cancel_pending_order,
    ),
    PyTool(
        "return_delivered_order_items",
        "Request a return for items of a delivered order; refund goes to the original method or a gift card.",
        obj_schema(
            {
                "order_id": _STR,
                "item_ids": {"type": "array", "items": _STR, "minItems": 1},
                "payment_method_id": _STR,
            }
        ),
        return_delivered_order_items,
    ),
    PyTool(
        "modify_pending_order_address",
        "Change the shipping address of a pending order.",
        obj_schema({"order_id": _STR, "street": _STR, "city": _STR, "zip": _STR}),
        modify_pending_order_address,
    ),
    PyTool(
        "transfer_to_human",
        "Escalate to a human agent when the request cannot be handled with the tools.",
        obj_schema({"summary": _STR}),
        transfer_to_human,
    ),
]


def make_retail(initial_state: dict[str, Any] | None = None) -> InProcessEnvironment:
    state = deep_merge(DEFAULT_STATE, initial_state or {})
    env = InProcessEnvironment(state=state, tools={t.name: t for t in TOOLS})
    env.name = "retail"
    return env
