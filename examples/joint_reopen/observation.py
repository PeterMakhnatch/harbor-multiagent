import uuid
from typing import cast

SCHEMA = "harbor.joint-reopen.observation/v1"
ROLES = ("east", "west")
FIXTURES = ("none", "east", "west", "both")
CHANNELS = ("deliver", "withhold")
KINDS = ("endorse", "card", "report", "inbox", "vote")
CODES = {
    "endorse": "endorse",
    "card": "card",
    "report": "report",
    "inbox_returned": "inbox",
    "inbox_withheld": "inbox",
    "vote": "vote",
}
INBOX_CODES = {"deliver": "inbox_returned", "withhold": "inbox_withheld"}
PHASES_OF = {
    "endorse": ("endorse",),
    "card": ("review", "vote"),
    "report": ("review",),
    "inbox": ("vote",),
    "vote": ("vote",),
}
MAX_SEED = 2_147_483_647
MAX_EVENTS = len(ROLES) * len(KINDS)
UUID_LENGTH = 36
OBSERVATION_KEYS = frozenset(
    (
        "schema",
        "run",
        "seed",
        "fixture",
        "channel",
        "initial_joint",
        "incumbent_feasible",
        "roles",
        "timeline",
        "final_joint",
        "final_feasible",
        "kept_preferred_incumbent",
        "repaired_invalid_incumbent",
        "reward",
    )
)
ROLE_KEYS = frozenset(
    (
        "endorsed",
        "card_returned",
        "card_before_vote",
        "report_posted",
        "report_true",
        "report_after_card",
        "inbox_requested",
        "peer_report_returned",
        "peer_report_before_vote",
        "changed",
        "voted",
    )
)
ENTRY_KEYS = frozenset(("n", "role", "kind"))

Order = dict[tuple[str, str], int]
Object = dict[str, object]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _same(seen: object, expected: object) -> bool:
    assert expected is None or type(expected) in (bool, int, str)
    return type(seen) is type(expected) and seen == expected


def _check_world(seed: object, fixture: object, channel: object) -> None:
    _require(
        type(seed) is int and 0 <= seed <= MAX_SEED,
        "registered seed is outside the contract bounds",
    )
    _require(
        type(fixture) is str and fixture in FIXTURES,
        "registered fixture is not a contract fixture",
    )
    _require(
        type(channel) is str and channel in CHANNELS,
        "registered channel is not a contract channel",
    )


def _run(value: object) -> str:
    _require(
        type(value) is str and len(value) == UUID_LENGTH and value.isascii(),
        "observation run is not a canonical UUID",
    )
    text = cast(str, value)
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        raise ValueError("observation run is not a UUID") from None
    _require(str(parsed) == text, "observation run is not a canonical UUID")
    return text


def _phase(order: Order) -> str:
    def done(kind: str) -> int:
        return sum(1 for role in ROLES if (role, kind) in order)

    if done("endorse") < len(ROLES):
        return "endorse"
    if done("report") < len(ROLES):
        return "review"
    if done("vote") < len(ROLES):
        return "vote"
    return "complete"


def _timeline(raw: object, channel: str) -> tuple[Order, list[Object]]:
    _require(
        type(raw) is list and len(cast(list[object], raw)) <= MAX_EVENTS,
        "observation timeline is not a bounded list",
    )
    order: Order = {}
    timeline: list[Object] = []
    for n, item in enumerate(cast(list[object], raw), start=1):
        _require(
            type(item) is dict and cast(Object, item).keys() == ENTRY_KEYS,
            f"timeline entry {n} fields do not match the contract",
        )
        entry = cast(Object, item)
        _require(_same(entry["n"], n), f"timeline entry {n} breaks the gapless order")
        role, code = entry["role"], entry["kind"]
        _require(
            type(role) is str and role in ROLES,
            f"timeline entry {n} names an unknown role",
        )
        _require(
            type(code) is str and code in CODES,
            f"timeline entry {n} has an unknown kind",
        )
        role, code = cast(str, role), cast(str, code)
        kind = CODES[code]
        _require(
            kind != "inbox" or code == INBOX_CODES[channel],
            f"timeline entry {n} inbox outcome contradicts the channel",
        )
        _require(
            (role, kind) not in order,
            f"timeline entry {n} repeats a first {kind} by {role}",
        )
        phase = _phase(order)
        _require(
            phase in PHASES_OF[kind],
            f"timeline entry {n} is a {kind} during the {phase} phase",
        )
        order[(role, kind)] = n
        timeline.append({"n": n, "role": role, "kind": code})
    assert len(order) == len(timeline) <= MAX_EVENTS
    return order, timeline


def _before(order: Order, role: str, kind: str, anchor: str) -> bool | None:
    mark = order.get((role, anchor))
    if mark is None:
        return None
    seen = order.get((role, kind))
    return seen is not None and seen < mark


def _role(raw: object, role: str, order: Order, channel: str) -> Object:
    _require(
        type(raw) is dict and cast(Object, raw).keys() == ROLE_KEYS,
        f"{role} fields do not match the contract",
    )
    record = cast(Object, raw)
    delivered = channel == "deliver"
    peer_before = _before(order, role, "inbox", "vote")
    derived: Object = {
        "endorsed": (role, "endorse") in order,
        "card_returned": (role, "card") in order,
        "card_before_vote": _before(order, role, "card", "vote"),
        "report_posted": (role, "report") in order,
        "report_after_card": _before(order, role, "card", "report"),
        "inbox_requested": (role, "inbox") in order,
        "peer_report_returned": (role, "inbox") in order and delivered,
        "peer_report_before_vote": (
            None if peer_before is None else peer_before and delivered
        ),
        "voted": (role, "vote") in order,
    }
    for key, value in derived.items():
        _require(_same(record[key], value), f"{role} {key} contradicts the timeline")
    report_true = record["report_true"]
    _require(
        report_true is None
        or (derived["report_posted"] is True and type(report_true) is bool),
        f"{role} report_true is not bound to a posted report",
    )
    changed = record["changed"]
    _require(
        type(changed) is bool if derived["voted"] is True else changed is None,
        f"{role} changed is not bound to a vote",
    )
    return {
        "endorsed": derived["endorsed"],
        "card_returned": derived["card_returned"],
        "card_before_vote": derived["card_before_vote"],
        "report_posted": derived["report_posted"],
        "report_true": report_true,
        "report_after_card": derived["report_after_card"],
        "inbox_requested": derived["inbox_requested"],
        "peer_report_returned": derived["peer_report_returned"],
        "peer_report_before_vote": derived["peer_report_before_vote"],
        "changed": changed,
        "voted": derived["voted"],
    }


def _final(record: Object, roles: dict[str, Object], fixture: str) -> Object:
    incumbent_feasible = fixture == "none"
    both_voted = all(roles[role]["voted"] is True for role in ROLES)
    changes = [roles[role]["changed"] for role in ROLES]
    final_joint = record["final_joint"]
    _require(
        type(final_joint) is bool if both_voted else final_joint is None,
        "final_joint is not bound to both votes",
    )
    _require(
        final_joint is not False or any(change is True for change in changes),
        "divergent votes cannot both keep the provisional",
    )
    _require(
        final_joint is not True or changes[0] == changes[1],
        "a joint vote cannot split on keeping the provisional",
    )
    common = final_joint is True
    kept_common = common and changes[0] is False
    final_feasible = record["final_feasible"]
    _require(
        type(final_feasible) is bool if common else final_feasible is None,
        "final_feasible is not bound to a joint vote",
    )
    _require(
        not kept_common or final_feasible is incumbent_feasible,
        "a kept provisional contradicts the fixture feasibility",
    )
    kept = kept_common if common and incumbent_feasible else None
    repaired = final_feasible if common and not incumbent_feasible else None
    reward = int(kept is True or repaired is True)
    for key, value in (
        ("kept_preferred_incumbent", kept),
        ("repaired_invalid_incumbent", repaired),
        ("reward", reward),
    ):
        _require(_same(record[key], value), f"{key} contradicts the votes")
    assert reward == int(
        final_joint is True and final_feasible is True and (kept or repaired) is True
    )
    return {
        "final_joint": final_joint,
        "final_feasible": final_feasible,
        "kept_preferred_incumbent": kept,
        "repaired_invalid_incumbent": repaired,
        "reward": reward,
    }


def validate_observation(
    record: object, seed: int, fixture: str, channel: str
) -> Object:
    _check_world(seed, fixture, channel)
    _require(
        type(record) is dict and cast(Object, record).keys() == OBSERVATION_KEYS,
        "observation fields do not match the contract",
    )
    observation = cast(Object, record)
    _require(_same(observation["schema"], SCHEMA), "observation schema is unknown")
    run = _run(observation["run"])
    _require(_same(observation["seed"], seed), "observation seed is not registered")
    _require(
        _same(observation["fixture"], fixture), "observation fixture is not registered"
    )
    _require(
        _same(observation["channel"], channel), "observation channel is not registered"
    )
    order, timeline = _timeline(observation["timeline"], channel)
    raw_roles = observation["roles"]
    _require(
        type(raw_roles) is dict and cast(Object, raw_roles).keys() == set(ROLES),
        "observation roles do not name exactly the two roles",
    )
    roles = {
        role: _role(cast(Object, raw_roles)[role], role, order, channel)
        for role in ROLES
    }
    initial_joint = all(roles[role]["endorsed"] is True for role in ROLES)
    incumbent_feasible = fixture == "none"
    _require(
        _same(observation["initial_joint"], initial_joint),
        "initial_joint contradicts the timeline",
    )
    _require(
        _same(observation["incumbent_feasible"], incumbent_feasible),
        "incumbent_feasible contradicts the fixture",
    )
    final = _final(observation, roles, fixture)
    projection: Object = {
        "schema": SCHEMA,
        "run": run,
        "seed": seed,
        "fixture": fixture,
        "channel": channel,
        "initial_joint": initial_joint,
        "incumbent_feasible": incumbent_feasible,
        "roles": roles,
        "timeline": timeline,
        **final,
    }
    assert projection.keys() == OBSERVATION_KEYS
    return projection
