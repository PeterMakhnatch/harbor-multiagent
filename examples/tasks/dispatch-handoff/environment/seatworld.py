from hashlib import sha256
from itertools import permutations
from typing import NamedTuple, TypedDict, cast

BAYS: tuple[str, ...] = ("A", "B", "C", "D")

_SCENARIO_SCHEMA = "seatlab.scenario/v1"
_WORLD_SCHEMA = "seatlab.world/v1"
_ARRIVALS = {
    "silent": (),
    "stale": (1,),
    "fresh": (2,),
    "reordered": (2, 1),
    "pooled": (),
}
_ORDERS = tuple(permutations(range(len(BAYS))))

type _Pair = tuple[str, str]
type _Reports = tuple[_Pair, _Pair]


class NotReady(Exception):
    """The dispatch seat must wait for inventory to submit."""


class _Settings(TypedDict):
    schema: str
    seed: int
    episodes: int
    condition: str


class _Case(NamedTuple):
    ranks: tuple[int, ...]
    before: _Pair
    after: _Pair

    def utilities(self) -> dict[str, int]:
        return dict(zip(BAYS, self.ranks, strict=True))


def _order(seed: int, episode: int, domain: str) -> tuple[int, ...]:
    digest = sha256(f"{_WORLD_SCHEMA}/{domain}/{seed:x}/{episode}".encode()).digest()
    return _ORDERS[int.from_bytes(digest, "big") % len(_ORDERS)]


def _pair(first: str, second: str) -> _Pair:
    return (first, second) if first < second else (second, first)


def _case(seed: int, episode: int) -> _Case:
    retained, dropped, added, _ = _order(seed, episode, "availability")
    before = _pair(BAYS[retained], BAYS[dropped])
    after = _pair(BAYS[retained], BAYS[added]) if episode % 2 else before
    ranks = tuple(rank + 1 for rank in _order(seed, episode, "utilities"))
    return _Case(ranks, before, after)


def _object(value: object, *fields: str) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != set(fields):
        raise ValueError(f"Expected an object with fields: {', '.join(fields)}")
    return cast(dict[str, object], value)


def _bay(value: object) -> str:
    if not isinstance(value, str) or value not in BAYS:
        raise ValueError("Expected a bay A, B, C, or D")
    return value


def _reports(value: object) -> _Reports:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Expected reports for revisions 1 and 2")
    reports: dict[int, _Pair] = {}
    for item in value:
        report = _object(item, "revision", "available")
        revision = report["revision"]
        if type(revision) is not int or revision not in (1, 2) or revision in reports:
            raise ValueError("Expected one report for each revision")
        available = report["available"]
        if not isinstance(available, list) or len(available) != 2:
            raise ValueError("Expected two available bays")
        first, second = _bay(available[0]), _bay(available[1])
        if first == second:
            raise ValueError("Expected distinct available bays")
        reports[revision] = _pair(first, second)
    return reports[1], reports[2]


def _bulletins(
    reports: _Reports, revisions: tuple[int, ...] = (1, 2)
) -> list[dict[str, object]]:
    return [
        {"revision": revision, "available": list(reports[revision - 1])}
        for revision in revisions
    ]


class World:
    """A deterministic batch with independent inventory and dispatch commitments."""

    def __init__(self, settings: object) -> None:
        config = _object(settings, "schema", "seed", "episodes", "condition")
        seed, episodes, condition = (
            config["seed"],
            config["episodes"],
            config["condition"],
        )
        if config["schema"] != _SCENARIO_SCHEMA:
            raise ValueError("Unknown scenario schema")
        if type(seed) is not int:
            raise ValueError("Expected an integer seed")
        if type(episodes) is not int or not 1 <= episodes <= 256:
            raise ValueError("Expected 1 to 256 episodes")
        if not isinstance(condition, str) or condition not in _ARRIVALS:
            raise ValueError("Unknown condition")
        self._settings = _Settings(
            schema=_SCENARIO_SCHEMA, seed=seed, episodes=episodes, condition=condition
        )
        self._cases = tuple(_case(seed, episode) for episode in range(episodes))
        self._inventory: tuple[_Reports, ...] | None = None
        self._dispatch: tuple[str, ...] | None = None

    @property
    def _pooled(self) -> bool:
        return self._settings["condition"] == "pooled"

    def _require_inventory(self) -> None:
        if not self._pooled and self._inventory is None:
            raise NotReady("Inventory has not submitted")

    def _visible(self, episode: int) -> list[dict[str, object]]:
        if self._inventory is None:
            return []
        return _bulletins(
            self._inventory[episode], _ARRIVALS[self._settings["condition"]]
        )

    def view(self, role: str) -> dict[str, object]:
        cases: list[dict[str, object]]
        if role == "inventory":
            cases = [
                {"id": episode, "reports": _bulletins((case.before, case.after))}
                for episode, case in enumerate(self._cases)
            ]
        elif role == "dispatch":
            self._require_inventory()
            cases = [
                {
                    "id": episode,
                    "utilities": case.utilities(),
                    "messages": self._visible(episode),
                    "pooled_available": list(case.after) if self._pooled else None,
                }
                for episode, case in enumerate(self._cases)
            ]
        else:
            raise ValueError("Unknown role")
        return {"role": role, "condition": self._settings["condition"], "cases": cases}

    def _ordered_rows(self, payload: object, field: str) -> list[object]:
        items = _object(payload, "rows")["rows"]
        episodes = len(self._cases)
        if not isinstance(items, list) or len(items) != episodes:
            raise ValueError("Expected a row for every episode")
        rows: dict[int, object] = {}
        for item in items:
            row = _object(item, "id", field)
            episode = row["id"]
            if (
                type(episode) is not int
                or not 0 <= episode < episodes
                or episode in rows
            ):
                raise ValueError("Expected distinct episode IDs in range")
            rows[episode] = row[field]
        return [rows[episode] for episode in range(episodes)]

    def submit(self, role: str, payload: object) -> dict[str, bool]:
        if role == "inventory":
            if self._pooled:
                raise ValueError("Pooled trials do not accept inventory submissions")
            reports = tuple(
                _reports(row) for row in self._ordered_rows(payload, "reports")
            )
            if self._inventory is None:
                self._inventory = reports
            elif self._inventory != reports:
                raise ValueError("Inventory has already submitted a different batch")
        elif role == "dispatch":
            self._require_inventory()
            choices = tuple(_bay(row) for row in self._ordered_rows(payload, "bay"))
            if self._dispatch is None:
                self._dispatch = choices
            elif self._dispatch != choices:
                raise ValueError("Dispatch has already submitted a different batch")
        else:
            raise ValueError("Unknown role")
        return {"accepted": True}

    def evidence(self) -> dict[str, object]:
        return {
            "schema": _WORLD_SCHEMA,
            "settings": dict(self._settings),
            "cases": [
                {
                    "id": episode,
                    "utilities": case.utilities(),
                    "before": list(case.before),
                    "after": list(case.after),
                    "reports": _bulletins(self._inventory[episode])
                    if self._inventory is not None
                    else None,
                    "visible": self._visible(episode),
                    "chosen": self._dispatch[episode]
                    if self._dispatch is not None
                    else None,
                }
                for episode, case in enumerate(self._cases)
            ],
            "complete": self._dispatch is not None
            and (self._pooled or self._inventory is not None),
        }
