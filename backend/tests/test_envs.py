from __future__ import annotations

import asyncio

from agentproof.envs.airline import make_airline
from agentproof.envs.base import deep_merge
from agentproof.envs.retail import make_retail


def call(env, tool, **args):
    return asyncio.run(env.call(tool, args))


def test_retail_cancel_pending_refunds_and_updates_state():
    env = make_retail()
    result = call(env, "cancel_pending_order", order_id="W1001", reason="ordered by mistake")
    assert result.ok, result.error
    assert env.state["orders"]["W1001"]["status"] == "cancelled"
    assert env.state["orders"]["W1001"]["refund_amount"] == 39.0


def test_retail_business_rule_is_a_domain_error():
    env = make_retail()
    result = call(env, "cancel_pending_order", order_id="W2001", reason="ordered by mistake")
    assert not result.ok
    assert result.error_kind == "domain"
    assert env.state["orders"]["W2001"]["status"] == "processed"


def test_retail_schema_validation_happens_before_the_tool_runs():
    env = make_retail()
    result = call(env, "cancel_pending_order", order_id="W1001", reason="because")
    assert result.error_kind == "validation"
    assert env.state["orders"]["W1001"]["status"] == "pending"
    assert call(env, "nope").error_kind == "protocol"


def test_retail_return_to_gift_card_tops_up_balance():
    env = make_retail()
    result = call(
        env,
        "return_delivered_order_items",
        order_id="W1002",
        item_ids=["I-MUG-16"],
        payment_method_id="gift_alice",
    )
    assert result.ok, result.error
    assert env.state["users"]["alice_01"]["payment_methods"]["gift_alice"]["balance"] == 39.0
    # A card belonging to someone else is refused.
    env = make_retail()
    result = call(
        env,
        "return_delivered_order_items",
        order_id="W1002",
        item_ids=["I-MUG-16"],
        payment_method_id="card_1881",
    )
    assert result.error_kind == "domain"


def test_initial_state_patch_is_deep_merged_and_isolated():
    env = make_retail({"orders": {"W2001": {"status": "pending"}}})
    assert env.state["orders"]["W2001"]["status"] == "pending"
    assert env.state["orders"]["W2001"]["items"]  # untouched siblings survive
    fresh = make_retail()
    assert fresh.state["orders"]["W2001"]["status"] == "processed"


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1}}
    out = deep_merge(base, {"a": {"c": 2}})
    assert out == {"a": {"b": 1, "c": 2}}
    assert base == {"a": {"b": 1}}


def test_airline_policy_and_baggage_fees():
    env = make_airline()
    assert call(env, "cancel_reservation", reservation_id="R-5002").error_kind == "domain"
    assert call(env, "cancel_reservation", reservation_id="R-5001").ok
    assert env.state["flights"]["AP100"]["seats"] == 5
    result = call(env, "update_baggage", reservation_id="R-5004", total_bags=3)
    assert result.output["bag_fees"] == 100.0


def test_airline_change_flight_rules():
    env = make_airline()
    assert (
        call(env, "change_flight", reservation_id="R-5001", new_flight="AP102").error_kind == "domain"
    )  # full
    assert (
        call(env, "change_flight", reservation_id="R-5001", new_flight="AP300").error_kind == "domain"
    )  # route
    assert call(env, "change_flight", reservation_id="R-5001", new_flight="AP104").ok
    assert env.state["flights"]["AP104"]["seats"] == 6


def test_snapshot_is_a_copy():
    env = make_retail()
    snap = asyncio.run(env.snapshot())
    snap["orders"]["W1001"]["status"] = "mutated"
    assert env.state["orders"]["W1001"]["status"] == "pending"
