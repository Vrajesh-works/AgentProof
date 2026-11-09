"""A small airline reservations world in the style of tau-bench's airline domain.

Written for AgentProof; tau-bench is the design reference for the domain shape
and for judging success by the final database state.
"""

from __future__ import annotations

from typing import Any

from agentproof.envs.base import InProcessEnvironment, PyTool, ToolError, deep_merge, obj_schema

FREE_BAGS = {"regular": 0, "silver": 1, "gold": 2}
BAG_FEE = 50.0

DEFAULT_STATE: dict[str, Any] = {
    "users": {
        "mia_10": {"name": "Mia Chen", "membership": "gold"},
        "omar_11": {"name": "Omar Haddad", "membership": "regular"},
        "lena_12": {"name": "Lena Park", "membership": "silver"},
    },
    "flights": {
        "AP100": {
            "origin": "BOS",
            "destination": "SFO",
            "date": "2026-12-01",
            "status": "scheduled",
            "seats": 4,
            "price": 320.0,
        },
        "AP102": {
            "origin": "BOS",
            "destination": "SFO",
            "date": "2026-12-02",
            "status": "scheduled",
            "seats": 0,
            "price": 290.0,
        },
        "AP104": {
            "origin": "BOS",
            "destination": "SFO",
            "date": "2026-12-03",
            "status": "scheduled",
            "seats": 7,
            "price": 275.0,
        },
        "AP200": {
            "origin": "JFK",
            "destination": "LAX",
            "date": "2026-12-05",
            "status": "cancelled",
            "seats": 9,
            "price": 300.0,
        },
        "AP300": {
            "origin": "SEA",
            "destination": "ORD",
            "date": "2026-12-08",
            "status": "scheduled",
            "seats": 2,
            "price": 210.0,
        },
    },
    "reservations": {
        "R-5001": {
            "user_id": "mia_10",
            "flight": "AP100",
            "status": "active",
            "insurance": False,
            "bags": 0,
            "bag_fees": 0.0,
        },
        "R-5002": {
            "user_id": "omar_11",
            "flight": "AP300",
            "status": "active",
            "insurance": False,
            "bags": 0,
            "bag_fees": 0.0,
        },
        "R-5003": {
            "user_id": "omar_11",
            "flight": "AP200",
            "status": "active",
            "insurance": False,
            "bags": 1,
            "bag_fees": 50.0,
        },
        "R-5004": {
            "user_id": "lena_12",
            "flight": "AP100",
            "status": "active",
            "insurance": True,
            "bags": 1,
            "bag_fees": 0.0,
        },
    },
}


def _reservation(state: dict[str, Any], reservation_id: str) -> dict[str, Any]:
    res = state["reservations"].get(reservation_id)
    if res is None:
        raise ToolError(f"reservation {reservation_id} not found")
    return res


def get_user(state: dict[str, Any], user_id: str) -> dict[str, Any]:
    user = state["users"].get(user_id)
    if user is None:
        raise ToolError(f"user {user_id} not found")
    reservations = sorted(r for r, v in state["reservations"].items() if v["user_id"] == user_id)
    return {"user_id": user_id, **user, "reservations": reservations}


def get_reservation(state: dict[str, Any], reservation_id: str) -> dict[str, Any]:
    res = _reservation(state, reservation_id)
    return {"reservation_id": reservation_id, **res, "flight_details": state["flights"][res["flight"]]}


def search_flights(state: dict[str, Any], origin: str, destination: str) -> dict[str, Any]:
    found = [
        {"flight": number, **flight}
        for number, flight in sorted(state["flights"].items())
        if flight["origin"] == origin.upper() and flight["destination"] == destination.upper()
    ]
    return {"flights": found}


def cancel_reservation(state: dict[str, Any], reservation_id: str) -> dict[str, Any]:
    res = _reservation(state, reservation_id)
    if res["status"] != "active":
        raise ToolError(f"reservation {reservation_id} is already {res['status']}")
    user = state["users"][res["user_id"]]
    flight = state["flights"][res["flight"]]
    allowed = res["insurance"] or user["membership"] == "gold" or flight["status"] == "cancelled"
    if not allowed:
        raise ToolError(
            "cancellation needs travel insurance, gold membership, or an airline-cancelled flight"
        )
    res["status"] = "cancelled"
    flight["seats"] += 1
    return {
        "reservation_id": reservation_id,
        "status": "cancelled",
        "refund": flight["price"] + res["bag_fees"],
    }


def update_baggage(state: dict[str, Any], reservation_id: str, total_bags: int) -> dict[str, Any]:
    res = _reservation(state, reservation_id)
    if res["status"] != "active":
        raise ToolError(f"reservation {reservation_id} is {res['status']}")
    if total_bags < res["bags"]:
        raise ToolError("bags can be added but not removed")
    free = FREE_BAGS[state["users"][res["user_id"]]["membership"]]
    res["bags"] = total_bags
    res["bag_fees"] = max(0, total_bags - free) * BAG_FEE
    return {"reservation_id": reservation_id, "bags": total_bags, "bag_fees": res["bag_fees"]}


def change_flight(state: dict[str, Any], reservation_id: str, new_flight: str) -> dict[str, Any]:
    res = _reservation(state, reservation_id)
    if res["status"] != "active":
        raise ToolError(f"reservation {reservation_id} is {res['status']}")
    old = state["flights"][res["flight"]]
    new = state["flights"].get(new_flight)
    if new is None:
        raise ToolError(f"flight {new_flight} not found")
    if (new["origin"], new["destination"]) != (old["origin"], old["destination"]):
        raise ToolError("a change must keep the same origin and destination")
    if new["status"] != "scheduled" or new["seats"] < 1:
        raise ToolError(f"flight {new_flight} has no available seats")
    old["seats"] += 1
    new["seats"] -= 1
    res["flight"] = new_flight
    return {
        "reservation_id": reservation_id,
        "flight": new_flight,
        "fare_difference": new["price"] - old["price"],
    }


_STR = {"type": "string", "minLength": 1}

TOOLS = [
    PyTool(
        "get_user",
        "Get a traveller profile, membership tier and reservations.",
        obj_schema({"user_id": _STR}),
        get_user,
    ),
    PyTool(
        "get_reservation",
        "Get a reservation and its flight.",
        obj_schema({"reservation_id": _STR}),
        get_reservation,
    ),
    PyTool(
        "search_flights",
        "List flights between two airports (IATA codes).",
        obj_schema({"origin": _STR, "destination": _STR}),
        search_flights,
    ),
    PyTool(
        "cancel_reservation",
        "Cancel a reservation. Allowed with insurance, gold membership, or when the airline cancelled the flight.",
        obj_schema({"reservation_id": _STR}),
        cancel_reservation,
    ),
    PyTool(
        "update_baggage",
        "Set the total number of checked bags. Fees apply beyond the membership allowance.",
        obj_schema({"reservation_id": _STR, "total_bags": {"type": "integer", "minimum": 0, "maximum": 5}}),
        update_baggage,
    ),
    PyTool(
        "change_flight",
        "Move a reservation to another flight on the same route.",
        obj_schema({"reservation_id": _STR, "new_flight": _STR}),
        change_flight,
    ),
]


def make_airline(initial_state: dict[str, Any] | None = None) -> InProcessEnvironment:
    state = deep_merge(DEFAULT_STATE, initial_state or {})
    env = InProcessEnvironment(state=state, tools={t.name: t for t in TOOLS})
    env.name = "airline"
    return env
