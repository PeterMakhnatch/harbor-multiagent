"""Every family module (CONTRACT.md 7): interface, determinism, oracle properties, robust scoring."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys

import pytest
from conftest import FAMILY_KINDS, FLOTILLA, TEAM_FAMILIES, family, instance, load

INSTANCES = range(30)
SETTINGS = [(difficulty, n_seats) for difficulty in ("easy", "hard") for n_seats in (2, 3)]
WORLD = load(FLOTILLA / "world" / "world.py")
ORACLE = load(FLOTILLA / "world" / "oracle.py")


def texts(inst: dict) -> list[str]:
    return [inst["goal"], inst["answer_format"], inst["solo_text"], *inst["private_text"].values()]


@pytest.mark.parametrize("name", FAMILY_KINDS)
def test_interface(name):
    module = family(name)
    assert module.FAMILY == name.partition(".")[0] and isinstance(module.TITLE, str) and module.TITLE
    for difficulty, n_seats in SETTINGS:
        inst = instance(name, 0, n_seats, difficulty)
        assert set(inst) >= {"goal", "answer_format", "private", "private_text", "solo_private", "solo_text",
                             "answer_key"}
        assert sorted(inst["private"]) == [f"p{i + 1}" for i in range(n_seats)]
        assert sorted(inst["private_text"]) == sorted(inst["private"])
        json.dumps(inst)  # everything is plain JSON (briefings travel as JSON)


@pytest.mark.parametrize("name", FAMILY_KINDS)
def test_generation_is_deterministic(name):
    module = family(name)
    for difficulty, n_seats in SETTINGS:
        assert module.generate(7, 3, n_seats, difficulty) == module.generate(7, 3, n_seats, difficulty)
    assert module.generate(1, 0, 2, "easy") != module.generate(1, 1, 2, "easy")
    assert module.generate(1, 0, 2, "easy") != module.generate(2, 0, 2, "easy")


def _digest_in_subprocess(name: str, hash_seed: str) -> str:
    base, _dot, kind = name.partition(".")
    code = (
        "import hashlib, importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('f', {str(FLOTILLA / 'families' / f'{base}.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"m = m.kind({kind!r}) if {bool(kind)!r} else m\n"
        "out = [m.generate(1, i, n, d) for i in range(3) for n in (2, 3) for d in ('easy', 'hard')]\n"
        "print(hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest())\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                            env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin"})
    return result.stdout.strip()


@pytest.mark.parametrize("name", FAMILY_KINDS)
def test_generation_does_not_depend_on_string_hashing(name):
    """Set iteration order changes with PYTHONHASHSEED; the instances must not."""
    expected = [instance(name, i, n, d) for i in range(3) for n in (2, 3) for d in ("easy", "hard")]
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    assert _digest_in_subprocess(name, "0") == digest
    assert _digest_in_subprocess(name, "4242") == digest


@pytest.mark.parametrize("name", FAMILY_KINDS)
@pytest.mark.parametrize(("difficulty", "n_seats"), SETTINGS)
def test_oracle_with_all_parts_scores_one(name, difficulty, n_seats):
    module = family(name)
    for i in INSTANCES:
        inst = instance(name, i, n_seats, difficulty)
        parts = [inst["private"][seat] for seat in sorted(inst["private"])]
        assert module.score(module.oracle_answer(parts), inst["answer_key"])["score"] == 1.0, i
        assert module.score(module.oracle_answer(parts[::-1]), inst["answer_key"])["score"] == 1.0, i
        assert module.score(module.oracle_answer([inst["solo_private"]]), inst["answer_key"])["score"] == 1.0, i


# A seat alone may guess right by luck; it must stay clearly below 1 on most instances.
MAX_LUCKY_SHARE = {"schedule": 0.35, "ledger": 0.0, "puzzle": 0.05}
MAX_MEAN_ALONE = {"schedule": 0.35, "ledger": 0.0, "puzzle": 0.5}


@pytest.mark.parametrize("name", TEAM_FAMILIES)  # probe gives its subject the whole task: nothing is split
@pytest.mark.parametrize(("difficulty", "n_seats"), SETTINGS)
def test_oracle_with_one_part_scores_clearly_below_one(name, difficulty, n_seats):
    module = family(name)
    scores = []
    for i in INSTANCES:
        inst = instance(name, i, n_seats, difficulty)
        for seat in sorted(inst["private"]):
            scores.append(module.score(module.oracle_answer([inst["private"][seat]]), inst["answer_key"])["score"])
    lucky = sum(1 for s in scores if s == 1.0) / len(scores)
    assert lucky <= MAX_LUCKY_SHARE[name], (lucky, sorted(scores))
    assert math.fsum(scores) / len(scores) <= MAX_MEAN_ALONE[name]


@pytest.mark.parametrize("name", FAMILY_KINDS)
def test_every_briefing_fits_one_oracle_message_at_the_default_limits(name):
    for difficulty, n_seats in SETTINGS:
        for i in range(10):
            for part in instance(name, i, n_seats, difficulty)["private"].values():
                payload = json.dumps(part, sort_keys=True, separators=(",", ":"))
                assert len(ORACLE.briefing_messages(payload, WORLD.DEFAULT_MAX_TEXT, WORLD.DEFAULT_MAX_MESSAGES)) == 1


@pytest.mark.parametrize("name", FAMILY_KINDS)
def test_texts_and_briefings_do_not_contain_the_answer_key(name):
    for difficulty, n_seats in SETTINGS:
        for i in range(10):
            inst = instance(name, i, n_seats, difficulty)
            key = inst["answer_key"]
            spellings = {json.dumps(key), json.dumps(key, sort_keys=True), json.dumps(key, separators=(",", ":"))}
            spellings |= {json.dumps(value) for value in key.values() if isinstance(value, dict)}
            seen = [*texts(inst), *(json.dumps(part) for part in inst["private"].values()),
                    json.dumps(inst["solo_private"])]
            assert not any(spelling in text for spelling in spellings for text in seen)


WEIRD = [None, {}, {"answer": None}, {"balances": None}, {"day": None, "start": None},
         {"x": float("nan"), "y": float("inf"), "z": -float("inf")}, {"a": [1, [2, [3]]], "b": {"c": {"d": {}}}},
         {"balances": {"Elm": "9" * 5000, "Oak": "²", "Fir": True}}, {"day": "²", "start": "²:00"},
         {"start": "9" * 5000, "day": 7}, {"Ana": {"floor": "²", "pet": 3, "drink": None}},
         {"assignment": {"Ana": "cat"}}, {"assignment": [1, 2]}, {"Ana": {"floor": float("nan")}},
         {"Ana": {"floor": 10**400}}, {"balances": {"Elm": 10**400, "Oak": 1e308, "Fir": -0.0}}]


@pytest.mark.parametrize("name", FAMILY_KINDS)
def test_score_never_raises_and_stays_in_range(name):
    module = family(name)
    key = instance(name, 0, 2, "easy")["answer_key"]
    for answer in WEIRD:
        result = module.score(answer, key)
        assert 0.0 <= result["score"] <= 1.0, answer
        assert result["fields_correct"] <= result["fields_total"]
    assert module.score(None, key)["score"] == 0.0
    assert module.score({}, key)["score"] == 0.0


def test_schedule_example_is_never_the_answer_or_the_decoy():
    module = family("schedule")
    for i in range(60):
        inst = instance("schedule", i, 2, "easy")
        assert json.dumps(inst["answer_key"]) not in inst["answer_format"]
    for i in range(4):
        scripted = module.generate_scripted(1, i, "easy")
        for window in (scripted["answer_key"], scripted["decoy"]):
            assert json.dumps(window) not in scripted["answer_format"]


SECRET_SEED = 8_398_302_271_514_603  # of the size that build_tasks.py draws (up to 2**53), fixed for this test


@pytest.mark.parametrize("name", FAMILY_KINDS)
def test_a_secret_seed_gives_its_own_valid_instances(name):
    """build_tasks.py draws a secret seed per build: the same seed gives the same instances, the next seed other
    ones, and the oracle and no-leak properties hold for it as for seed 1."""
    module = family(name)
    for difficulty, n_seats in SETTINGS:
        for i in range(2):
            inst = module.generate(SECRET_SEED, i, n_seats, difficulty)
            assert inst == module.generate(SECRET_SEED, i, n_seats, difficulty)
            assert inst != instance(name, i, n_seats, difficulty)  # seed 1
            if i == 0:
                assert inst != module.generate(SECRET_SEED + 1, i, n_seats, difficulty)
            parts = [inst["private"][seat] for seat in sorted(inst["private"])]
            assert module.score(module.oracle_answer(parts), inst["answer_key"])["score"] == 1.0
            key = inst["answer_key"]
            spellings = {json.dumps(key), json.dumps(key, sort_keys=True), json.dumps(key, separators=(",", ":"))}
            assert not any(spelling in text for spelling in spellings for text in texts(inst))
    if name == "schedule":
        for i in range(3):
            scripted = module.generate_scripted(SECRET_SEED, i, "easy")
            assert scripted == module.generate_scripted(SECRET_SEED, i, "easy")
            for window in (scripted["answer_key"], scripted["decoy"]):
                assert json.dumps(window) not in scripted["answer_format"]
