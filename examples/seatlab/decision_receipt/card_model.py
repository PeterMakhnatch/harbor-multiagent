from collections.abc import Callable, Sequence
from typing import Any, cast

from . import decision_verify as decision

inflight = decision.inflight
stock = decision.stock

WORLDS = {
    stock.CUT_SCHEMA: "stock",
    inflight.CUT_SCHEMA: "inflight",
    decision.CUT_SCHEMA: "decision-receipt",
}
EVALUATORS: dict[str, Callable[[object], dict[str, Any]]] = {
    "stock": stock.evaluate,
    "inflight": inflight.evaluate,
    "decision-receipt": decision.evaluate,
}
AXES: dict[str, tuple[str, Sequence[str]]] = {
    "stock": ("prior", stock.PRIORS),
    "inflight": ("fate", inflight.FATES),
    "decision-receipt": ("fate", inflight.FATES),
}
SEATS = {"handoff": "outgoing", "inbox": "relief", "resolve": "relief"}
CARD_KEYS = (
    "world",
    "channel",
    "axis",
    "state",
    "events",
    "citation",
    "before",
    "final",
    "twin",
)
UNRECORDED = "unrecorded"
CITATION_STATUSES = (*decision.STATUSES, UNRECORDED)
REFERENCES = (None, *decision.CITABLE)
OUTCOMES: tuple[str, ...] = tuple(stock.OUTCOMES)

assert len(WORLDS) == len(set(WORLDS.values())) == 3
assert WORLDS.keys() == {stock.CUT_SCHEMA, inflight.CUT_SCHEMA, decision.CUT_SCHEMA}
assert set(EVALUATORS) == set(AXES) == set(WORLDS.values())
assert set(SEATS) == set(stock.CHOICES)
assert all(len(states) == 2 for _, states in AXES.values())
assert OUTCOMES == tuple(inflight.OUTCOMES) == ("omission", "exactly_one", "duplicate")
assert UNRECORDED not in decision.STATUSES

Event = tuple[int, str]


def _route(cut: object) -> str:
    if type(cut) is not dict:
        raise ValueError("cut is not a JSON object")
    schema = cast(dict[object, object], cut).get("schema")
    if type(schema) is not str or schema not in WORLDS:
        raise ValueError("cut schema is not a supported shipping world")
    return WORLDS[schema]


def _observe(world: str, cut: object) -> dict[str, Any]:
    assert world in EVALUATORS
    try:
        observation = EVALUATORS[world](cut)
    except decision.CutError:
        raise ValueError("cut does not replay under its trusted verifier") from None
    assert type(observation) is dict and "reward" in observation
    return observation


def _outcome(parcels: list[str]) -> str:
    assert len(set(parcels)) == len(parcels) <= len(stock.IDENTITIES)
    return OUTCOMES[min(len(parcels), 2)]


def _grade(parcels: list[str]) -> int:
    return int(_outcome(parcels) == "exactly_one")


def _other(states: Sequence[str], state: str) -> str:
    assert state in states and len(states) == 2
    return states[1 - states.index(state)]


def _resolve(observation: dict[str, Any]) -> Event | None:
    found = [
        (event["seq"], event["choice"])
        for event in observation["events"]
        if event["kind"] == "resolve"
    ]
    assert len(found) <= 1
    return found[0] if found else None


def _events(observation: dict[str, Any]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for item in observation["events"]:
        kind = item["kind"]
        choice = observation["relief_action"] if kind == "resolve" else item["choice"]
        assert kind in SEATS and type(choice) is str and choice != "absent"
        events.append(
            {"seq": item["seq"], "seat": SEATS[kind], "kind": kind, "choice": choice}
        )
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert len(events) <= len(SEATS)
    return events


def _ledgers(
    world: str, state: str, observation: dict[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    resolve = _resolve(observation)
    other = _other(AXES[world][1], state)
    if world == "stock":
        before, final = stock.replay_ledger(state, resolve)
        twin = stock.replay_ledger(other, resolve)[1]
        assert final == observation["effects"]
        return before, final, twin
    replaced = observation["replace"]
    before, final = inflight.replay_settlement(state, resolve, replaced)
    twin = inflight.replay_settlement(other, resolve, replaced)[1]
    assert before == observation["settled"] and final == observation["drained"]
    assert _grade(before) == observation["grade_before_drain"]
    return before, final, twin


def _citation(world: str, observation: dict[str, Any]) -> dict[str, object]:
    if world != "decision-receipt":
        assert decision.CITATION not in observation
        return {"status": UNRECORDED, "reference": None}
    status = observation["citation_status"]
    reference = observation[decision.CITATION]
    assert status in CITATION_STATUSES and reference in REFERENCES
    assert (reference is None) == (status != "cited_at_decision")
    return {"status": status, "reference": reference}


def card(cut: object) -> dict[str, Any]:
    world = _route(cut)
    observation = _observe(world, cut)
    axis, states = AXES[world]
    state = observation[axis]
    assert state in states and observation["channel"] in stock.CHANNELS
    before, final, twin = _ledgers(world, state, observation)
    assert observation["outcome"] == _outcome(final)
    assert observation["reward"] == _grade(final)
    result: dict[str, Any] = {
        "world": world,
        "channel": observation["channel"],
        "axis": axis,
        "state": state,
        "events": _events(observation),
        "citation": _citation(world, observation),
        "before": {"parcels": list(before), "grade": _grade(before)},
        "final": {
            "parcels": list(final),
            "outcome": _outcome(final),
            "grade": _grade(final),
        },
        "twin": {
            "state": _other(states, state),
            "parcels": list(twin),
            "outcome": _outcome(twin),
            "grade": _grade(twin),
        },
    }
    assert tuple(result) == CARD_KEYS
    return result
