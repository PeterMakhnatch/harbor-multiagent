"""Family `puzzle`: pool the clues of a logic-grid puzzle, then solve it.

Hidden profile: a few people live in one building, one per floor, and each has one value of two
other attributes (for example a pet and a drink). All clues together fit exactly one solution, and
the clue set is irreducible: without any one clue, more than one solution fits. The clues are dealt to
the seats so that each seat's clues alone fit many solutions (counted by exhaustive search), so the
team must pool its clues and then reason. Standard library only; deterministic for a seed.
See flotilla/CONTRACT.md 7.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
from collections.abc import Callable
from typing import Any

FAMILY = "puzzle"
TITLE = "Solve the logic puzzle"
NAMES = ["Ana", "Ben", "Chen", "Dara", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jun"]
FLOOR = "floor"
CATEGORIES = {
    "car": ["black", "blue", "green", "red", "silver", "white"],
    "drink": ["cocoa", "coffee", "juice", "milk", "tea", "water"],
    "pet": ["cat", "dog", "fish", "hamster", "parrot", "rabbit"],
}
# (it is true of a person, it is false of a person, the person it is true of)
PHRASES = {
    FLOOR: ("lives on floor {}", "does not live on floor {}", "the person on floor {}"),
    "car": ("drives the {} car", "does not drive the {} car", "the person who drives the {} car"),
    "drink": ("drinks {}", "does not drink {}", "the person who drinks {}"),
    "pet": ("owns the {}", "does not own the {}", "the person who owns the {}"),
}
RULES = {
    "car": ("Car colors", "Each person drives exactly one of these cars, and no two people drive the same car."),
    "drink": ("Drinks", "Each person drinks exactly one of these drinks, and no two people drink the same drink."),
    "pet": ("Pets", "Each person owns exactly one of these pets, and no two people own the same pet."),
}
SETTINGS = {
    "easy": {"people": 4, "min_solutions": 6,
             "weights": {"is": 2, "is_not": 2, "same": 3, "not_same": 2, "higher": 2}},
    "hard": {"people": 5, "min_solutions": 20,
             "weights": {"is": 1, "is_not": 2, "same": 3, "not_same": 2, "higher": 2, "next_above": 2, "xor": 2}},
}
MAX_BRIEFING_CHARS = 3_500  # the talk oracle posts a briefing as one message; the world allows 4,000
WRAPPERS = ("assignment", "answer", "solution", "people")

Clue = dict[str, Any]
Solution = dict[str, dict[str, Any]]  # attribute -> person -> value


def generate(seed: int, instance: int, n_seats: int, difficulty: str) -> dict[str, Any]:
    setting = SETTINGS[difficulty]
    rng = random.Random(f"{FAMILY}/{seed}/{instance}/{n_seats}/{difficulty}")
    seats = [f"p{i + 1}" for i in range(n_seats)]
    n = setting["people"]
    for _attempt in range(500):
        people = sorted(rng.sample(NAMES, n))
        categories = sorted(rng.sample(sorted(CATEGORIES), 2))
        domains = {FLOOR: list(range(1, n + 1)), **{c: sorted(rng.sample(CATEGORIES[c], n)) for c in categories}}
        truth = {attr: dict(zip(people, rng.sample(values, n))) for attr, values in domains.items()}
        clues = _clue_set(rng, setting["weights"], people, domains, truth)
        if clues is None:
            continue
        hands = _deal(rng, clues, seats, people, domains, setting["min_solutions"])
        if hands is None:
            continue
        numbered = rng.sample(clues, len(clues))  # clue numbers do not reveal who holds a clue
        number = {id(clue): k + 1 for k, clue in enumerate(numbered)}
        common = {"people": people, "domains": domains, "n_clues": len(clues)}
        parts = {
            seat: {**common, "clues": sorted((_publish(c, number[id(c)]) for c in hand), key=lambda c: c["id"])}
            for seat, hand in hands.items()
        }
        solo = {**common, "clues": [_publish(c, number[id(c)]) for c in sorted(clues, key=lambda c: number[id(c)])]}
        if max(len(json.dumps(part, sort_keys=True)) for part in parts.values()) > MAX_BRIEFING_CHARS:
            continue
        return {
            "goal": _goal(people, domains, len(clues)),
            "answer_format": _answer_format(people, domains),
            "private": parts,
            "private_text": {seat: _render(part) for seat, part in parts.items()},
            "solo_private": solo,
            "solo_text": _render(solo),
            "answer_key": {"assignment": {p: {attr: truth[attr][p] for attr in domains} for p in people}},
        }
    raise RuntimeError("no instance found; relax the settings")


def _clue_set(rng: random.Random, weights: dict[str, int], people: list[str], domains: dict[str, list[Any]],
              truth: Solution) -> list[Clue] | None:
    """Random true clues until exactly one solution fits, then drop every clue the others imply."""
    clues: list[Clue] = []
    seen: set[str] = set()
    for _draw in range(400):
        clue = _random_clue(rng, weights, people, domains, truth)
        key = json.dumps(clue, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        clues.append(clue)
        if len(solutions(people, domains, clues, limit=2)) == 1:
            break
    else:
        return None
    for clue in rng.sample(clues, len(clues)):
        rest = [c for c in clues if c is not clue]
        if len(solutions(people, domains, rest, limit=2)) == 1:
            clues = rest
    return clues


def _deal(rng: random.Random, clues: list[Clue], seats: list[str], people: list[str],
          domains: dict[str, list[Any]], min_solutions: int) -> dict[str, list[Clue]] | None:
    """Deal the clues round-robin so that each seat's clues alone fit at least min_solutions solutions."""
    if len(clues) < len(seats):
        return None
    for _shuffle in range(30):
        order = rng.sample(clues, len(clues))
        hands = {seat: order[i :: len(seats)] for i, seat in enumerate(seats)}
        if len(seats) == 1 or all(
            len(solutions(people, domains, hand, limit=min_solutions)) >= min_solutions for hand in hands.values()
        ):
            return hands
    return None


def _random_clue(rng: random.Random, weights: dict[str, int], people: list[str], domains: dict[str, list[Any]],
                 truth: Solution) -> Clue:
    """One random clue that is true of the solution."""
    kind = rng.choices(sorted(weights), weights=[weights[k] for k in sorted(weights)])[0]
    attrs = list(domains)
    if kind in ("is", "is_not"):
        person, attr = rng.choice(people), rng.choice(attrs)
        value = truth[attr][person]
        if kind == "is_not":
            value = rng.choice([v for v in domains[attr] if v != value])
        return {"kind": kind, "person": person, "attr": attr, "value": value}
    if kind == "same":
        a, b = rng.sample(attrs, 2)
        person = rng.choice(people)
        return {"kind": kind, "a": [a, truth[a][person]], "b": [b, truth[b][person]]}
    if kind == "not_same":
        a, b = rng.sample(attrs, 2)
        p, q = rng.sample(people, 2)
        return {"kind": kind, "a": [a, truth[a][p]], "b": [b, truth[b][q]]}
    if kind == "xor":
        a, b = rng.sample(attrs, 2)
        person = rng.choice(people)
        values = [truth[a][person], truth[b][person]]
        lie = rng.randrange(2)  # exactly one of the two statements is true
        values[lie] = rng.choice([v for v in domains[(a, b)[lie]] if v != values[lie]])
        return {"kind": kind, "person": person, "a": [a, values[0]], "b": [b, values[1]]}
    floors = truth[FLOOR]
    if kind == "higher":
        p, q = sorted(rng.sample(people, 2), key=lambda person: -floors[person])
    else:  # next_above: p lives exactly one floor above q
        p = rng.choice([person for person in people if floors[person] > 1])
        q = next(person for person in people if floors[person] == floors[p] - 1)
    return {"kind": kind, "x": _ref(rng, p, truth), "y": _ref(rng, q, truth)}


def _ref(rng: random.Random, person: str, truth: Solution) -> dict[str, Any]:
    """Name a person directly or through one of their non-floor values."""
    attr = rng.choice([None, *(a for a in truth if a != FLOOR)])
    return {"person": person} if attr is None else {"attr": attr, "value": truth[attr][person]}


def _publish(clue: Clue, number: int) -> Clue:
    return {"id": number, "text": clue_text(clue), **clue}


def _attrs_of(clue: Clue) -> set[str]:
    kind = clue["kind"]
    if kind in ("is", "is_not"):
        return {clue["attr"]}
    if kind in ("same", "not_same", "xor"):
        return {clue["a"][0], clue["b"][0]}
    return {FLOOR} | {ref["attr"] for ref in (clue["x"], clue["y"]) if "attr" in ref}


def _checker(clue: Clue) -> Callable[[Solution, dict[str, dict[Any, str]]], bool]:
    """A function (assignment, owners) -> whether a clue that is not about one cell holds."""
    kind = clue["kind"]
    if kind in ("same", "not_same"):
        (a, u), (b, v), want = clue["a"], clue["b"], kind == "same"
        return lambda assign, owner: (owner[a][u] == owner[b][v]) == want
    if kind == "xor":
        (a, u), (b, v), person = clue["a"], clue["b"], clue["person"]
        return lambda assign, owner: (assign[a][person] == u) != (assign[b][person] == v)

    def who(ref: dict[str, Any]) -> Callable[[dict[str, dict[Any, str]]], str]:
        if "person" in ref:
            person = ref["person"]
            return lambda owner: person
        attr, value = ref["attr"], ref["value"]
        return lambda owner: owner[attr][value]

    x, y = who(clue["x"]), who(clue["y"])
    if kind == "higher":
        return lambda assign, owner: assign[FLOOR][x(owner)] > assign[FLOOR][y(owner)]
    return lambda assign, owner: assign[FLOOR][x(owner)] == assign[FLOOR][y(owner)] + 1


def solutions(people: list[str], domains: dict[str, list[Any]], clues: list[Clue], limit: int) -> list[Solution]:
    """Up to `limit` solutions that fit every clue, by exhaustive search in a fixed order.

    Attributes are assigned one at a time (floor first, then the others by name); every permutation of
    an attribute's values is tried, and a clue is checked as soon as all of its attributes are assigned.
    The order depends only on the sorted names and values, so any copy of the data gives the same list.
    """
    people = sorted(people)
    attrs = [FLOOR, *sorted(a for a in domains if a != FLOOR)]
    level = {attr: i for i, attr in enumerate(attrs)}
    unary: dict[str, list[Clue]] = {attr: [] for attr in attrs}
    checks: list[list[Callable[[Solution, dict[str, dict[Any, str]]], bool]]] = [[] for _ in attrs]
    for clue in clues:
        if clue["kind"] in ("is", "is_not"):
            unary[clue["attr"]].append(clue)
        else:
            checks[max(level[attr] for attr in _attrs_of(clue))].append(_checker(clue))
    options = []
    for attr in attrs:
        fitting = []
        for perm in itertools.permutations(sorted(domains[attr])):
            values = dict(zip(people, perm))
            if all((values[c["person"]] == c["value"]) == (c["kind"] == "is") for c in unary[attr]):
                fitting.append((values, dict(zip(perm, people))))
        options.append(fitting)
    found: list[Solution] = []
    assign: Solution = {}
    owner: dict[str, dict[Any, str]] = {}

    def walk(depth: int) -> bool:
        if depth == len(attrs):
            found.append({attr: dict(assign[attr]) for attr in attrs})
            return len(found) >= limit
        attr = attrs[depth]
        for values, owners in options[depth]:
            assign[attr], owner[attr] = values, owners
            if all(check(assign, owner) for check in checks[depth]) and walk(depth + 1):
                return True
        assign.pop(attr, None)
        owner.pop(attr, None)
        return False

    walk(0)
    return found


def oracle_answer(private: list[dict[str, Any]]) -> dict[str, Any]:
    """The first solution (in the search order) that fits every clue in the given parts."""
    if not private:
        return {}
    people, domains = private[0]["people"], private[0]["domains"]
    clues = {clue["id"]: clue for part in private for clue in part["clues"]}
    found = solutions(people, domains, [clues[k] for k in sorted(clues)], limit=1)
    if not found:
        return {}
    return {person: {attr: found[0][attr][person] for attr in found[0]} for person in sorted(people)}


def clue_text(clue: Clue) -> str:
    kind = clue["kind"]
    if kind in ("is", "is_not"):
        return f"{clue['person']} {PHRASES[clue['attr']][kind == 'is_not'].format(clue['value'])}."
    if kind in ("same", "not_same"):
        (a, u), (b, v) = clue["a"], clue["b"]
        return _capital(f"{PHRASES[a][2].format(u)} {PHRASES[b][kind == 'not_same'].format(v)}.")
    if kind == "xor":
        (a, u), (b, v) = clue["a"], clue["b"]
        return f"{clue['person']} {PHRASES[a][0].format(u)} or {PHRASES[b][0].format(v)}, but not both."
    x, y = (ref["person"] if "person" in ref else PHRASES[ref["attr"]][2].format(ref["value"])
            for ref in (clue["x"], clue["y"]))
    relation = "lives on a higher floor than" if kind == "higher" else "lives exactly one floor above"
    return _capital(f"{x} {relation} {y}.")


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _count(n: int) -> str:
    return {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}.get(n, str(n))


def _goal(people: list[str], domains: dict[str, list[Any]], n_clues: int) -> str:
    n = len(people)
    others = [attr for attr in domains if attr != FLOOR]
    lines = [
        (f"{_count(n)} people ({', '.join(people)}) live in one building, one person per floor. Work out each "
        f"person's floor, {others[0]} and {others[1]}."),
        "",
        (f"- Floors: {', '.join(str(f) for f in domains[FLOOR])}. Floor 1 is the lowest and floor {n} is the "
        "highest. Each person lives on a different floor."),
    ]
    for attr in others:
        label, rule = RULES[attr]
        lines.append(f"- {label}: {', '.join(domains[attr])}. {rule}")
    lines += [
        "",
        (f"The puzzle has {n_clues} clues, numbered 1 to {n_clues}. Every clue is true. Together, the "
        f"{n_clues} clues fit exactly one solution."),
    ]
    return "\n".join(lines)


def _answer_format(people: list[str], domains: dict[str, list[Any]]) -> str:
    others = [attr for attr in domains if attr != FLOOR]
    record = ", ".join([f'"{FLOOR}": <number>', *(f'"{attr}": "<{attr}>"' for attr in others)])
    return (
        f"A JSON object with one entry per person (all {len(people)} of them) that gives the person's floor as a "
        f"number and their {others[0]} and {others[1]} as words from the lists above: "
        f'`{{"{people[0]}": {{{record}}}, "{people[1]}": {{...}}, ...}}`.'
    )


def _render(part: dict[str, Any]) -> str:
    lines = [f"Clues you hold ({len(part['clues'])} of the {part['n_clues']}):", ""]
    lines += [f"- Clue {clue['id']}: {clue['text']}" for clue in part["clues"]]
    return "\n".join(lines)


def _floor(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(?:floor\s*)?(\d{1,3})(?:st|nd|rd|th)?", value.strip().casefold(), flags=re.ASCII)
    return int(match.group(1)) if match else None


def _word(attr: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"^(?:the|a|an) ", "", " ".join(value.casefold().split()))
    return re.sub(r" car$", "", text) if attr == "car" else text


def _matches(attr: str, given: Any, expected: Any) -> bool:
    if attr == FLOOR:
        return _floor(given) == expected
    return _word(attr, given) == expected


def _cells(answer: Any) -> dict[tuple[str, str], list[Any]]:
    """(casefolded person, attribute) -> every value given for that cell."""
    if not isinstance(answer, dict):
        return {}
    table = answer
    for key, value in answer.items():
        if isinstance(key, str) and key.strip().casefold() in WRAPPERS and isinstance(value, dict):
            table = value
            break
    known = {FLOOR, *CATEGORIES}
    cells: dict[tuple[str, str], list[Any]] = {}
    for person, record in table.items():
        if not (isinstance(person, str) and isinstance(record, dict)):
            continue
        for attr, value in record.items():
            if not isinstance(attr, str):
                continue
            name = attr.strip().casefold()
            if name not in known and name.endswith("s") and name[:-1] in known:
                name = name[:-1]  # "pets" -> "pet"
            cells.setdefault((person.strip().casefold(), name), []).append(value)
    return cells


def score(answer: dict[str, Any] | None, key: dict[str, Any]) -> dict[str, Any]:
    """Fraction of (person, attribute) cells that are right. Two conflicting values for one cell
    (for example under "Ana" and "ana") earn nothing for that cell."""
    expected: dict[str, dict[str, Any]] = key["assignment"]
    cells = _cells(answer)
    total = correct = 0
    for person in sorted(expected):
        for attr, value in sorted(expected[person].items()):
            total += 1
            given = cells.get((person.casefold(), attr))
            if given and all(_matches(attr, item, value) for item in given):
                correct += 1
    return {"score": correct / total, "fields_correct": correct, "fields_total": total}
