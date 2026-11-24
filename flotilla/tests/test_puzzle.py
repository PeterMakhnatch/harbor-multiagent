"""Family `puzzle`: unique solutions, a real split of the clues, plain clue language, exact scoring.

The checks use their own clue evaluator and brute force (every assignment of every attribute), not
the module's solver; the module's solver is then checked against the brute force.
"""

from __future__ import annotations

import itertools
import random
import re
from collections import Counter

import pytest
from conftest import family, instance

PUZZLE = family("puzzle")
INSTANCES = range(30)


def person(ref: dict, solution: dict) -> str:
    if "person" in ref:
        return ref["person"]
    return next(p for p, value in solution[ref["attr"]].items() if value == ref["value"])


def holds(clue: dict, solution: dict) -> bool:
    """What each clue's sentence says, written out again from the sentence templates."""
    kind = clue["kind"]
    if kind == "is":
        return solution[clue["attr"]][clue["person"]] == clue["value"]
    if kind == "is_not":
        return solution[clue["attr"]][clue["person"]] != clue["value"]
    if kind in ("same", "not_same"):
        first = person({"attr": clue["a"][0], "value": clue["a"][1]}, solution)
        second = person({"attr": clue["b"][0], "value": clue["b"][1]}, solution)
        return (first == second) == (kind == "same")
    if kind == "xor":
        (a, u), (b, v), who = clue["a"], clue["b"], clue["person"]
        return (solution[a][who] == u) + (solution[b][who] == v) == 1
    x, y = (solution["floor"][person(clue[k], solution)] for k in ("x", "y"))
    return x > y if kind == "higher" else x - y == 1


def assignments(people: list[str], domains: dict) -> list[dict]:
    attrs = sorted(domains)
    return [
        {attr: dict(zip(people, perm)) for attr, perm in zip(attrs, combo)}
        for combo in itertools.product(*(itertools.permutations(domains[attr]) for attr in attrs))
    ]


def violated(clues: list[dict], everything: list[dict]) -> list[frozenset]:
    return [frozenset(c["id"] for c in clues if not holds(c, s)) for s in everything]


def truth(inst: dict) -> dict:
    key = inst["answer_key"]["assignment"]
    return {attr: {p: key[p][attr] for p in key} for attr in next(iter(key.values()))}


@pytest.mark.parametrize("n_seats", [2, 3])
def test_easy_puzzles_by_brute_force(n_seats):
    for i in INSTANCES:
        inst = instance("puzzle", i, n_seats, "easy")
        solo = inst["solo_private"]
        everything = assignments(solo["people"], solo["domains"])
        masks = violated(solo["clues"], everything)
        solutions = [s for s, mask in zip(everything, masks) if not mask]
        assert solutions == [truth(inst)], i  # exactly one solution, and it is the key
        for clue in solo["clues"]:  # irreducible: without any one clue, more than one solution fits
            assert sum(1 for mask in masks if mask <= {clue["id"]}) >= 2, (i, clue)
        for part in inst["private"].values():  # a seat's clues alone fit many solutions
            mine = {clue["id"] for clue in part["clues"]}
            assert sum(1 for mask in masks if not mask & mine) >= PUZZLE.SETTINGS["easy"]["min_solutions"]


@pytest.mark.parametrize("n_seats", [2, 3])
def test_hard_puzzles_with_the_checked_solver(n_seats):
    minimum = PUZZLE.SETTINGS["hard"]["min_solutions"]
    for i in INSTANCES:
        inst = instance("puzzle", i, n_seats, "hard")
        solo = inst["solo_private"]
        people, domains, clues = solo["people"], solo["domains"], solo["clues"]
        assert PUZZLE.solutions(people, domains, clues, limit=2) == [truth(inst)], i
        assert all(holds(clue, truth(inst)) for clue in clues)
        for clue in clues if i < 10 else ():  # irreducible (checked on the first 10 instances, for time)
            rest = [c for c in clues if c is not clue]
            assert len(PUZZLE.solutions(people, domains, rest, limit=2)) == 2, (i, clue)
        for part in inst["private"].values():
            assert len(PUZZLE.solutions(people, domains, part["clues"], limit=minimum)) == minimum


def _canonical(solution: dict) -> list:
    return sorted((attr, sorted(values.items())) for attr, values in solution.items())


def test_the_solver_matches_brute_force_for_every_clue_kind():
    rng = random.Random("puzzle-solver-check")
    weights = {kind: 1 for kind in ("is", "is_not", "same", "not_same", "higher", "next_above", "xor")}
    people = ["Ana", "Ben", "Chen", "Dara"]
    for trial in range(40):
        categories = sorted(rng.sample(sorted(PUZZLE.CATEGORIES), 2))
        domains = {"floor": [1, 2, 3, 4], **{c: sorted(rng.sample(PUZZLE.CATEGORIES[c], 4)) for c in categories}}
        everything = assignments(people, domains)
        secret = rng.choice(everything)
        clues = [{"id": k, **PUZZLE._random_clue(rng, weights, people, domains, secret)}
                 for k in range(rng.randint(1, 9))]
        expected = [s for s in everything if all(holds(c, s) for c in clues)]
        found = PUZZLE.solutions(people, domains, clues, limit=10**6)
        assert sorted(map(_canonical, found)) == sorted(map(_canonical, expected)), trial
        assert all(holds(c, secret) for c in clues)  # generated clues are true of the solution


def test_clue_numbers_partition_the_clues():
    for n_seats in (2, 3):
        for difficulty in ("easy", "hard"):
            for i in range(10):
                inst = instance("puzzle", i, n_seats, difficulty)
                k = inst["solo_private"]["n_clues"]
                held = Counter(c["id"] for part in inst["private"].values() for c in part["clues"])
                assert held == Counter(range(1, k + 1))
                assert all(part["clues"] for part in inst["private"].values())


NAME = r"[A-Z][a-z]+"
WHO = r"(?:[A-Z][a-z]+|the person (?:on floor \d|who owns the \w+|who drinks \w+|who drives the \w+ car))"
SAYS = r"(?:lives on floor \d|owns the \w+|drinks \w+|drives the \w+ car)"
SAYS_NOT = r"(?:does not live on floor \d|does not own the \w+|does not drink \w+|does not drive the \w+ car)"
TEMPLATES = {
    "is": rf"{NAME} {SAYS}\.",
    "is_not": rf"{NAME} {SAYS_NOT}\.",
    "same": rf"The person (?:on floor \d|who \w+ (?:the )?\w+(?: car)?) {SAYS}\.",
    "not_same": rf"The person (?:on floor \d|who \w+ (?:the )?\w+(?: car)?) {SAYS_NOT}\.",
    "higher": rf"{WHO} lives on a higher floor than {WHO}\.",
    "next_above": rf"{WHO} lives exactly one floor above {WHO}\.",
    "xor": rf"{NAME} {SAYS} or {SAYS}, but not both\.",
}


def test_clue_sentences_follow_plain_templates():
    seen = Counter()
    for difficulty in ("easy", "hard"):
        for i in INSTANCES:
            inst = instance("puzzle", i, 2, difficulty)
            solo = inst["solo_private"]
            words = set(solo["people"]) | {str(v) for values in solo["domains"].values() for v in values}
            for clue in solo["clues"]:
                text = re.sub(r"^The", "the", clue["text"]) if clue["kind"] in ("higher", "next_above") else clue["text"]
                assert re.fullmatch(TEMPLATES[clue["kind"]], text, flags=re.IGNORECASE if text != clue["text"] else 0), clue
                assert clue["text"] == PUZZLE.clue_text(clue)
                for name in re.findall(r"\b[A-Z][a-z]+\b", clue["text"]):
                    assert name in words or name == "The", clue["text"]
                seen[clue["kind"]] += 1
    assert set(seen) == {"is", "is_not", "same", "not_same", "higher", "next_above", "xor"}


@pytest.mark.parametrize(("clue", "text"), [
    ({"kind": "is", "person": "Ana", "attr": "floor", "value": 3}, "Ana lives on floor 3."),
    ({"kind": "is_not", "person": "Ben", "attr": "pet", "value": "cat"}, "Ben does not own the cat."),
    ({"kind": "same", "a": ["floor", 2], "b": ["drink", "tea"]}, "The person on floor 2 drinks tea."),
    ({"kind": "not_same", "a": ["car", "red"], "b": ["pet", "dog"]},
     "The person who drives the red car does not own the dog."),
    ({"kind": "higher", "x": {"attr": "drink", "value": "milk"}, "y": {"person": "Chen"}},
     "The person who drinks milk lives on a higher floor than Chen."),
    ({"kind": "next_above", "x": {"person": "Dara"}, "y": {"attr": "pet", "value": "fish"}},
     "Dara lives exactly one floor above the person who owns the fish."),
    ({"kind": "xor", "person": "Eli", "a": ["floor", 1], "b": ["car", "blue"]},
     "Eli lives on floor 1 or drives the blue car, but not both."),
])
def test_clue_text(clue, text):
    assert PUZZLE.clue_text(clue) == text


def test_no_line_states_a_whole_row_of_the_answer():
    for n_seats in (2, 3):
        for difficulty in ("easy", "hard"):
            for i in range(10):
                inst = instance("puzzle", i, n_seats, difficulty)
                key = inst["answer_key"]["assignment"]
                lines = [line for text in (inst["solo_text"], *inst["private_text"].values()) for line in
                         text.splitlines()]
                for who, row in key.items():
                    needles = [rf"\b{who}\b", rf"floor {row['floor']}\b",
                               *(rf"\b{value}\b" for attr, value in row.items() if attr != "floor")]
                    assert not any(all(re.search(n, line) for n in needles) for line in lines), who


def test_one_seat_oracle_answers_fit_that_seats_clues():
    for i in range(10):
        inst = instance("puzzle", i, 2, "hard")
        for part in inst["private"].values():
            answer = PUZZLE.oracle_answer([part])
            solution = {attr: {p: answer[p][attr] for p in answer} for attr in part["domains"]}
            assert all(holds(clue, solution) for clue in part["clues"])


KEY = {"assignment": {"Ana": {"floor": 1, "pet": "cat", "car": "red"},
                      "Ben": {"floor": 2, "pet": "dog", "car": "blue"}}}


@pytest.mark.parametrize(("answer", "correct"), [
    ({"Ana": {"floor": 1, "pet": "cat", "car": "red"}, "Ben": {"floor": 2, "pet": "dog", "car": "blue"}}, 6),
    ({"assignment": {"Ana": {"floor": 1, "pet": "cat", "car": "red"}}}, 3),  # wrapper; Ben missing
    ({"solution": {" ana ": {"Floor": "1", " PET ": "Cat", "car": "RED"}}}, 3),  # case and whitespace
    ({"Ana": {"floor": "floor 1", "pet": "the cat", "car": "the red car"}}, 3),
    ({"Ana": {"floor": 1.0, "pets": "a cat", "cars": "red"}, "Ben": {"floor": "2nd"}}, 4),  # plural keys
    ({"Ana": {"floor": True, "pet": "cats", "car": "red or blue"}}, 0),  # True is not floor 1
    ({"Ana": {"floor": 2, "pet": "dog", "car": "blue"}, "Ben": {"floor": 1, "pet": "cat", "car": "red"}}, 0),
    ({"Ana": {"floor": 1.5, "pet": ["cat"], "car": {"color": "red"}}}, 0),
    ({"Ana": {"floor": 1, "pet": "cat"}, "ana": {"floor": 1, "pet": "dog"}}, 1),  # conflicting duplicates
    ({"Ana": {"floor": "1", "pet": "cat"}, "ANA": {"floor": 1, "pet": "Cat"}}, 2),  # agreeing duplicates
    ({"Ana": "floor 1, cat, red"}, 0),
    ({"Zed": {"floor": 1}}, 0),
    ([], 0),
])
def test_score_is_forgiving_about_format_but_never_about_values(answer, correct):
    result = PUZZLE.score(answer, KEY)
    assert (result["fields_correct"], result["fields_total"]) == (correct, 6)
    assert result["score"] == correct / 6


def test_solution_counts_per_seat_are_reported(capsys):
    """Prints how many solutions one seat's clues allow (capped at 1,000; run with -s to see it)."""
    for difficulty in ("easy", "hard"):
        for n_seats in (2, 3):
            counts = []
            for i in range(10):
                inst = instance("puzzle", i, n_seats, difficulty)
                for part in inst["private"].values():
                    counts.append(len(PUZZLE.solutions(part["people"], part["domains"], part["clues"], 1000)))
            print(difficulty, n_seats, "solutions per seat (first 10 instances, capped at 1000):", sorted(counts))
            assert min(counts) >= PUZZLE.SETTINGS[difficulty]["min_solutions"]
