from copy import deepcopy
from threading import RLock
from typing import Any

from .cases import AUTHORED_MODES, capacity, valid_mode, validate_case
from .notes import deliver_notes, normalize_notes

LOADER = "loader"
REGISTRAR = "registrar"
DEPOT = "depot"
ROLES = (LOADER, REGISTRAR, DEPOT)
PHASES = ("request", "registrar", "depot", "dispatch", "done")
TURNS = (LOADER, REGISTRAR, DEPOT, LOADER, None)


def _select_ids(values: object, allowed: set[str], cap: int) -> list[str]:
    if type(values) is not list or len(values) > cap:
        raise ValueError("invalid selection")
    selected: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value not in allowed or value in selected:
            raise ValueError("invalid selection")
        selected.add(value)
    return sorted(selected)


def _require_role(role: str) -> None:
    if type(role) is not str or role not in ROLES:
        raise ValueError("invalid role")


class Session:
    def __init__(self, case: dict[str, Any], mode: str):
        if type(mode) is not str:
            raise ValueError("invalid mode")
        if type(case) is not dict:
            raise ValueError("invalid case")
        try:
            owned_case = deepcopy(case)
            validate_case(owned_case)
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            OverflowError,
            RecursionError,
        ):
            raise ValueError("invalid case") from None
        self._case = owned_case
        self._mode = mode
        if not valid_mode(owned_case, mode):
            raise ValueError("invalid mode for profile")
        self._nominate = (
            len(owned_case["briefs"]) if mode == "open" else owned_case["nominate"]
        )
        self._events: list[dict[str, Any]] = []
        self._lock = RLock()

    def _nominated(self) -> list[dict[str, Any]]:
        if not self._events:
            return []
        ids = set(self._events[0]["action"]["slips"])
        return sorted(
            (brief for brief in self._case["briefs"] if brief["id"] in ids),
            key=lambda brief: brief["id"],
        )

    def _file(self, role: str) -> list[dict[str, Any]]:
        index = 1 if role == REGISTRAR else 2
        if len(self._events) <= index:
            return []
        ids = set(self._events[index]["action"]["ids"])
        return sorted(
            (card for card in self._case[role] if card["id"] in ids),
            key=lambda card: card["id"],
        )

    def _agenda(self) -> list[dict[str, Any]]:
        if (
            self._mode not in ("linked", "open", "masked", "bundled", *AUTHORED_MODES)
            or len(self._events) < 2
        ):
            return []
        weights: dict[tuple[str, int], int] = {}
        cards = self._file(REGISTRAR)
        for brief in self._nominated():
            epoch = brief["epoch"]
            for exhibit in brief["items"]:
                for card in cards:
                    if card["key"] == exhibit and card["start"] <= epoch <= card["end"]:
                        intent = (card["value"], epoch)
                        weights[intent] = weights.get(intent, 0) + brief["weight"]
        return [
            {"key": key, "epoch": epoch, "weight": weight}
            for (key, epoch), weight in sorted(weights.items())
        ]

    def _bundles(self) -> list[dict[str, Any]]:
        if len(self._events) < 2:
            return []
        if self._mode in AUTHORED_MODES:
            return deliver_notes(
                self._events[1]["action"]["notes"], disclose=self._mode == "authored"
            )
        if self._mode not in ("masked", "bundled"):
            return []
        cards = self._file(REGISTRAR)
        bundles = []
        for brief in self._nominated():
            epoch = brief["epoch"]
            keys = sorted(
                c["value"]
                for c in cards
                if c["key"] in brief["items"] and c["start"] <= epoch <= c["end"]
            )
            if len(keys) == len(brief["items"]):
                weight = brief["weight"] if self._mode == "bundled" else 0
                bundles.append({"epoch": epoch, "keys": keys, "weight": weight})
        return sorted(bundles, key=lambda bundle: (bundle["epoch"], bundle["keys"]))

    def _order(self) -> dict[str, Any]:
        return next(
            brief
            for brief in self._case["briefs"]
            if brief["id"] == self._case["realized"]
        )

    def _view(self, role: str) -> dict[str, Any]:
        step = len(self._events)
        assert 0 <= step < len(PHASES)
        briefs = []
        if role == LOADER:
            briefs = self._case["briefs"]
        elif role == REGISTRAR and self._mode != "silent":
            briefs = self._nominated()
        delivered = role == LOADER and step >= 3 and self._mode != "silent"
        return deepcopy(
            {
                "role": role,
                "phase": PHASES[step],
                "turn": TURNS[step],
                "quota": capacity(self._case, role, self._mode),
                **(
                    {"note_capacity": 4}
                    if role == REGISTRAR and self._mode in AUTHORED_MODES
                    else {}
                ),
                "nominate": self._nominate,
                "exhibits": self._case["exhibits"],
                "crates": self._case["crates"],
                "bays": self._case["bays"],
                "briefs": briefs,
                "cards": self._case[role] if role != LOADER else [],
                "agenda": self._agenda() if role == DEPOT else [],
                **(
                    {"bundles": self._bundles() if role == DEPOT else []}
                    if self._case["profile"] == "coupling"
                    else {}
                ),
                **(
                    {"bundle_source": "registrar"}
                    if role == DEPOT and self._mode in AUTHORED_MODES
                    else {}
                ),
                "registrar_file": self._file(REGISTRAR) if delivered else [],
                "depot_file": self._file(DEPOT) if delivered else [],
                "order": self._order() if role == LOADER and step >= 3 else None,
            }
        )

    def view(self, role: str) -> dict[str, Any]:
        _require_role(role)
        with self._lock:
            return self._view(role)

    def _normalize(
        self, role: str, action: dict[str, Any], step: int
    ) -> dict[str, Any]:
        authored = step == 1 and self._mode in AUTHORED_MODES
        if type(action) is not dict or type(action.get("op")) is not str:
            raise ValueError("invalid action")
        if authored:
            if action.keys() != {"op", "ids", "notes"} or action["op"] != "file":
                raise ValueError("invalid action")
            allowed = {card["id"] for card in self._case[role]}
            return {
                "op": "file",
                "ids": _select_ids(
                    action["ids"], allowed, capacity(self._case, role, self._mode)
                ),
                "notes": normalize_notes(action["notes"], self._case["crates"]),
            }
        if len(action) != 2:
            raise ValueError("invalid action")
        if step == 0 and action["op"] == "request" and "slips" in action:
            allowed = {brief["id"] for brief in self._case["briefs"]}
            return {
                "op": "request",
                "slips": _select_ids(action["slips"], allowed, self._nominate),
            }
        if step in (1, 2) and action["op"] == "file" and "ids" in action:
            allowed = {card["id"] for card in self._case[role]}
            return {
                "op": "file",
                "ids": _select_ids(
                    action["ids"], allowed, capacity(self._case, role, self._mode)
                ),
            }
        if step == 3 and action["op"] == "commit" and "routes" in action:
            routes = action["routes"]
            if type(routes) is not dict or len(routes) != 2:
                raise ValueError("invalid routes")
            items = self._order()["items"]
            bays = self._case["bays"]
            for exhibit, bay in routes.items():
                if type(exhibit) is not str or exhibit not in items:
                    raise ValueError("invalid routes")
                if type(bay) is not str or bay not in bays:
                    raise ValueError("invalid routes")
            return {"op": "commit", "routes": dict(sorted(routes.items()))}
        raise ValueError("invalid action")

    def apply(self, role: str, action: dict[str, Any]) -> dict[str, Any]:
        _require_role(role)
        with self._lock:
            step = len(self._events)
            if role != TURNS[step]:
                raise ValueError("out of turn")
            normalized = self._normalize(role, action, step)
            self._events.append({"seq": step + 1, "role": role, "action": normalized})
            return self._view(role)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(
                {
                    "version": 1,
                    "case": self._case,
                    "mode": self._mode,
                    "events": self._events,
                }
            )
