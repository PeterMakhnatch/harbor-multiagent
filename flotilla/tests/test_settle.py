"""Deterministic proof of the cut-over window family (`settle`): generator, scripted teams, shapes, refusals.

Every play runs through the real simulator (the world's own request handlers). Sweeps run in worker processes.
`SETTLE_INSTANCES` sets the instances per arm and level (default 48; the full sweep of the build report used 100,
about 5 minutes with 4 workers) and `SETTLE_JOBS` the worker count (default 4).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
BUILD = SIM.BUILD
QA = load(FLOTILLA / "qa.py")
FAMILY = BUILD.load_family("settle")
SEED = 4420261125
N = int(os.environ.get("SETTLE_INSTANCES", "48"))
JOBS = int(os.environ.get("SETTLE_JOBS", "4"))
SEEDS = 5
FEW = 8
LEVELS = FAMILY.LEVELS
ARMS = FAMILY.ARMS
TEAM_ARMS = ("team", "hint", "team-nolast")
TRAPS = ("probe-rule-trap", "probe-cross-trap")
CONTROLS = ("probe-rule-control", "probe-cross-control")


def audit(task: dict, result: dict) -> list[str]:
    """Invariants that hold at every event of every play."""
    params, problems = task["params"], []
    sent, filed, frozen = {}, set(), False
    for event in result["events"]:
        kind, data = event["kind"], event["data"]
        if kind == "tick" and data["tick"] > params["rounds"] - 1:
            problems.append("a tick beyond the last round")
        elif kind == "send":
            sent[data["id"]] = (event["seat"], int(event["t"]))
        elif kind == "deliver":
            for item in data["ids"]:
                sender, tick = sent[item]
                if sender != event["seat"] and tick + params["delay"] > event["t"]:
                    problems.append("a message was delivered before round r + delay")
        elif kind == "extension_call" and not 200 <= data["status"] < 500:
            problems.append(f"status {data['status']}")
        elif kind == "extension_error":
            problems.append("extension error")
        elif kind == "freeze":
            frozen = True
        elif kind == "settle_file":
            if frozen or data["desk"] in filed:
                problems.append("a late or second filing was accepted")
            filed.add(data["desk"])
    if result["invalid"] is None and result["reward"] not in (0.0, 1.0):
        problems.append(f"reward {result['reward']}")
    return problems


def play(job: tuple[str, object, int]) -> dict:
    path, policies, seed = job
    task = json.loads((Path(path) / "environment" / "task.json").read_text())
    result = SIM.simulate_built(Path(path), policies, seed)
    events = json.dumps(result["events"], sort_keys=True).encode()
    return {"reward": result["reward"], "invalid": result["invalid"], "score": result["score"],
            "requests": result["requests"], "problems": audit(task, result), "stalled": result["stalled"],
            "digest": hashlib.sha256(events).hexdigest()}


@pytest.fixture(scope="module")
def pool():
    with ProcessPoolExecutor(max_workers=JOBS) as executor:
        yield lambda jobs: list(executor.map(play, jobs, chunksize=8))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """(level, arm, instance) -> (task folder, task.json), N instances of every arm and level, one seed."""
    root, tasks = tmp_path_factory.mktemp("settle"), {}
    for level in LEVELS:
        for path in BUILD.build("settle", list(range(N)), None, level, SEED, ARMS, root / level, policy="careful"):
            task = json.loads((path / "environment" / "task.json").read_text())
            tasks[level, task["condition"], task["instance"]] = (str(path), task)
    return tasks


def cells(built, arms=ARMS, levels=LEVELS, count=N):
    return [(level, arm, i) for level in levels for arm in arms for i in range(min(count, N))]


def run(pool, built, keys, policies, seeds=(0,)):
    jobs = [(built[key][0], policies(built[key][1]) if callable(policies) else policies, seed)
            for key in keys for seed in seeds]
    rows = pool(jobs)
    assert not [row["problems"] for row in rows if row["problems"]]
    return {(key, seed): row for (key, seed), row in zip(((k, s) for k in keys for s in seeds), rows, strict=True)}


def free(part, seat):
    return [code for code in part["windows"] if code not in part["blocked"][seat]]


@pytest.mark.parametrize("level", LEVELS)
def test_generator(level):
    desks, n_windows, n_free = FAMILY.DOSES[level]
    seen, ranks, answers = set(), set(), set()
    for i in range(max(N, 48)):
        inst = FAMILY.generate(SEED, i, None, level)
        assert inst == FAMILY.generate(SEED, i, None, level) != FAMILY.generate(SEED + 1, i, None, level)
        seen.add(json.dumps(inst, sort_keys=True))
        for name, part in (("team", inst["team"]), ("probe", inst["probe"])):
            windows, blocked = part["windows"], part["blocked"]
            sets = {seat: set(free(part, seat)) for seat in inst["seats"]}
            assert len(inst["seats"]) == desks and len(windows) == n_windows == len(set(windows))
            assert windows == sorted(windows) and FAMILY.EXAMPLE not in windows
            assert all(blocked[seat] and set(blocked[seat]) < set(windows) for seat in inst["seats"])
            assert len({frozenset(value) for value in sets.values()}) == desks  # the lists differ
            common = sorted(set.intersection(*sets.values()))
            assert common == part["free_for_all"]
            assert len(common) == (n_free if name == "team" else max(n_free, 2))
        team, probe = inst["team"], inst["probe"]
        own = {seat: free(team, seat) for seat in inst["seats"]}
        assert all(team["last"][seat] in own[seat] for seat in inst["seats"])
        assert len(set(team["last"].values())) == desks  # the last-time windows differ
        assert len({own[seat][0] for seat in inst["seats"]}) > 1  # the silent default never succeeds
        assert desks == 2 or all(len(own[seat]) >= n_free + 2 for seat in inst["seats"])
        subject, mates = inst["seats"][0], inst["seats"][1:]
        first = probe["free_for_all"][0]
        assert free(probe, subject)[0] not in probe["free_for_all"]  # a silent default fails in the probes too
        assert min(set.intersection(*(set(free(probe, seat)) for seat in mates))) == first
        assert probe["trap_last"] in probe["free_for_all"][1:]
        ranks.add(team["windows"].index(team["free_for_all"][0]))
        answers.add(tuple(team["free_for_all"]))
    assert len(seen) == max(N, 48) and len(answers) >= 0.5 * max(N, 48)  # distinct instances, no fixed answer
    assert len(ranks) >= 3  # no fixed position


def test_careful_scores_1_in_every_arm_level_and_schedule(pool, built):
    keys = cells(built)
    rows = run(pool, built, keys, "careful")
    rows |= run(pool, built, cells(built, ("team",)), "careful", range(1, SEEDS))
    rows |= run(pool, built, cells(built, [a for a in ARMS if a != "team"], count=FEW), "careful", range(1, SEEDS))
    assert len(rows) >= len(keys) and all(row["reward"] == 1 and row["invalid"] is None for row in rows.values())
    assert all(row["score"].get("shape", "none") == "none" and not row["score"]["not_filed"] for row in rows.values())
    assert max(calls for row in rows.values() for calls in row["requests"].values()) <= 15  # turns per seat
    for (key, _seed), row in rows.items():
        if key[1] != "solo":
            assert row["score"]["pooled"] == 1 and not row["score"]["premature_filing"]


def test_naive_scores_1_in_controls_and_0_in_traps(pool, built):
    rows = run(pool, built, cells(built), "naive")
    rows |= run(pool, built, cells(built, count=FEW), "naive", range(1, SEEDS))
    for ((_level, arm, _i), _seed), row in rows.items():
        assert row["invalid"] is None
        assert row["reward"] == (1 if arm in ("solo", *CONTROLS) else 0)
        if arm in (*TEAM_ARMS, *TRAPS):
            assert row["score"]["shape"] == "premature" and row["score"]["pooled"] == 0
        if arm in (*TRAPS, *CONTROLS):
            subject = row["score"]["subject"]
            assert subject["premature"] and subject["filed_last_time"] and subject["round"] == 1
            assert subject["matches_teammates"] == (arm in CONTROLS)


def test_floor_chance_and_one_seat_alone(pool, built):
    for (key, _seed), row in run(pool, built, cells(built, count=FEW), "nop").items():
        score, params = row["score"], built[key][1]["params"]
        assert row["reward"] == 0 and row["invalid"] is None
        assert set(score["not_filed"]) >= {params["subject"] or desk for desk in params["desks"]}
    team = cells(built, ("team",))
    for policy in ("blind", "lone"):  # one seat's own view, no reading: the silent default never scores
        rows = run(pool, built, team, policy)
        assert all(row["reward"] == 0 == FAMILY.chance_baseline(built[key][1], policy)
                   for (key, _seed), row in rows.items())
    for level in LEVELS:  # the blind random guess matches the computed chance
        for arm in ("team", *TRAPS):
            keys = cells(built, (arm,), (level,))
            rows = run(pool, built, keys, "guess")
            expected = statistics.mean(FAMILY.chance_baseline(built[key][1], "guess") for key in keys)
            observed = statistics.mean(row["reward"] for row in rows.values())
            tolerance = 0.02 + 3 * (max(expected * (1 - expected), 0.01) / len(keys)) ** 0.5
            assert abs(observed - expected) <= tolerance, (level, arm, observed, expected)
            assert expected <= (0.5 if arm in TRAPS else 0.25)
    solo = cells(built, ("solo",), count=FEW)
    for policy in ("naive", "blind", "guess", "polite", "lapse-50", "script-loop"):
        assert all(row["reward"] == 1 for row in run(pool, built, solo, policy).values())


def test_named_teams_produce_each_failure_shape(pool, built):
    keys = cells(built, ("team",), ("standard", "heavy"), count=24)

    def mixed(first=None, last=None, rest="careful"):
        def choose(task):
            desks = task["params"]["desks"]
            names = dict.fromkeys(desks, rest)
            if first:
                names[desks[0]] = first
            if last:
                names[desks[-1]] = last
            return names
        return choose

    for policy, shape in (("naive", "premature"), ("eager", "premature"), ("talker", "late")):
        rows = run(pool, built, keys, policy)
        assert all(row["reward"] == 0 and row["score"]["shape"] == shape for row in rows.values())
    polite = [(row["reward"], row["score"]["shape"]) for row in run(pool, built, keys, "polite").values()]
    shapes = [shape for reward, shape in polite if not reward]
    assert len(shapes) > len(polite) / 2 and shapes.count("crossed") > len(shapes) / 2
    assert set(shapes) <= {"crossed", "premature"}
    clash = unpooled = 0
    for (key, _seed), row in run(pool, built, keys, mixed(first="rule-own")).items():
        params = built[key][1]["params"]
        same = params["last_time"][params["desks"][0]] == params["free_for_all"][0]
        assert (row["reward"], row["score"]["shape"]) == ((1, "none") if same else (0, "rule_clash"))
        clash += not same
    for (key, _seed), row in run(pool, built, keys, mixed(last="mute")).items():
        params = built[key][1]["params"]
        others = [params["blocked"][desk] for desk in params["desks"][:-1]]
        same = FAMILY.first_free(params["windows"], others) == params["free_for_all"][0]
        assert (row["reward"], row["score"]["shape"]) == ((1, "none") if same else (0, "unpooled"))
        assert row["score"]["pooled"] < 1
        unpooled += not same
    assert clash and unpooled
    for team in (mixed(rest="follower"), mixed(first="careful", rest="follower"), mixed(last="careful", rest="follower")):
        assert all(row["reward"] == 1 for row in run(pool, built, keys, team).values())
    for (key, _seed), row in run(pool, built, keys, "last-rule").items():  # another valid answer scores 1
        params = built[key][1]["params"]
        assert row["reward"] == 1 and len(params["free_for_all"]) > 1
        assert {filing["window"] for filing in row["score"]["filed"].values()} == {params["free_for_all"][-1]}


def test_score_does_not_depend_on_call_order_and_replays(pool, built):
    keys = cells(built, ("team", "team-nolast", *TRAPS), ("canary", "standard"), count=6)
    for policy in ("naive", "polite", "eager", "lapse-30", "script-loop", "guess"):
        rows = run(pool, built, keys, policy, range(SEEDS))
        for key in keys:
            scores = [json.dumps(rows[key, seed]["score"], sort_keys=True) for seed in range(SEEDS)]
            assert len(set(scores)) == 1, (key, policy)
    again = run(pool, built, keys, "lapse-30", (3,))
    rows = run(pool, built, keys, "lapse-30", (3,))
    assert all(rows[key]["digest"] == again[key]["digest"] for key in rows)


def test_lapses_cost_reward_and_the_own_default_pull_shows_in_the_pair(pool, built):
    means = {}
    for policy in ("lapse-0", "lapse-10", "lapse-50"):
        for arm in ("team", "probe-rule-control", "probe-rule-trap"):
            rows = run(pool, built, cells(built, (arm,), ("standard",)), policy)
            assert all(row["invalid"] is None for row in rows.values())
            means[policy, arm] = statistics.mean(row["reward"] for row in rows.values())
    assert all(means["lapse-0", arm] == 1 for arm in ("team", "probe-rule-control", "probe-rule-trap"))
    assert means["lapse-50", "team"] < means["lapse-10", "team"] < 1
    assert means["lapse-50", "probe-rule-trap"] < means["lapse-50", "probe-rule-control"]


def test_refusals_budget_and_cut_off_are_valid_flagged_outcomes(pool, built, monkeypatch):
    keys = cells(built, ("team",), ("canary", "standard"), count=3)

    def beside(name, mate):
        return lambda task: {task["params"]["desks"][0]: name} | dict.fromkeys(task["params"]["desks"][1:], mate)

    for name in ("fuzz", "fuzz-file"):  # every malformed, repeated or late action gets a 4xx reply
        for (key, _seed), row in run(pool, built, keys, beside(name, "nop"), range(3)).items():
            score, desks = row["score"], built[key][1]["params"]["desks"]
            assert row["reward"] == 0 and row["invalid"] is None and score["cut_off"] and score["refused"] >= 9
            assert set(score["stalled_seats"]) == set(score["not_filed"]) == set(desks[name == "fuzz-file":])
    for (key, _seed), row in run(pool, built, keys, beside("burn", "careful")).items():
        score, first = row["score"], built[key][1]["params"]["desks"][0]
        assert row["reward"] == 0 and row["invalid"] is None and score["rounds_used"] == 4
        assert score["budget_exhausted"] == [first] == score["not_filed"] and score["shape"] == "late"
    path = Path(built["canary", "team", 0][0])
    monkeypatch.setattr(SIM, "MAX_STEPS", 1)  # a simulator limit is void, never a reward
    stalled = SIM.simulate_built(path, "careful", 0)
    assert stalled["reward"] is None and stalled["invalid"] and stalled["stalled"]


def test_no_view_shows_the_answer_or_the_arm_and_pairs_differ_in_one_variable(built):
    keys = [(level, arm, i) for level in LEVELS for arm in ARMS for i in range(3)]
    assert not [finding for finding in QA.check([Path(built[key][0]) for key in keys])[0] if finding.level == "FAIL"]
    views = {}
    for key in keys:
        task = QA.load_task(Path(built[key][0]))
        params = task.task_json["params"]
        views[key] = {seat: {name: value for name, value in QA.twin_view(task, seat).items()
                             if name != "env.SEAT_TOKEN"} for seat in task.under_test}
        assert task.under_test == ([params["subject"]] if params["subject"] else sorted(task.seats))
        for seat in task.under_test:
            instruction, briefing = task.view(seat)["instruction"], task.view(seat)["env.SEAT_BRIEFING"]
            text = f"{instruction}\n{briefing}".lower()
            assert not [word for word in FAMILY.ARM_WORDS if re.search(rf"(?<![\w-]){word}(?![\w-])", text)]
            assert ("alphabetical" in text) == (task.condition == "hint")  # the protocol is stated only there
            if task.condition != "solo":
                codes = set(re.findall(r"W-[A-Z]{2}", instruction)) - {FAMILY.EXAMPLE}
                assert codes == set(params["windows"])
                assert json.loads(briefing)["blocked"] == params["blocked"][seat]
                assert "free_for_all" not in text and json.dumps(params["free_for_all"]).lower() not in text
    for (level, arm, i), view in views.items():
        for first, second in FAMILY.PAIRS:
            if arm == first:
                other = views[level, second, i]
                assert not list(FAMILY.pair_difference(first, second, view, other))
                changed = {seat: fields | {"instruction": fields["instruction"] + " W-AA"}
                           for seat, fields in other.items()}
                assert list(FAMILY.pair_difference(first, second, view, changed))
    for level in LEVELS:
        for pair in (CONTROLS[0], TRAPS[0]), (CONTROLS[1], TRAPS[1]):
            control, trap = (built[level, arm, 0][1] for arm in pair)
            subject = control["params"]["subject"]
            assert control["scripted_seats"] == trap["scripted_seats"] == control["params"]["desks"][1:]
            a, b = control["params"], trap["params"]
            assert {k: v for k, v in a.items() if k != "last_time"} == {k: v for k, v in b.items() if k != "last_time"}
            assert {k: v for k, v in a["last_time"].items() if k != subject} == {
                k: v for k, v in b["last_time"].items() if k != subject}
            assert a["last_time"][subject] == a["free_for_all"][0] != b["last_time"][subject] in b["free_for_all"]


def test_text_readers_accept_paraphrase():
    windows, blocked = ["W-BD", "W-KT", "W-MZ", "W-QR"], {"W-BD", "W-KT"}
    for text in ("p2 blocked: W-BD, W-KT.", "I cannot use W-KT or W-BD", "my desk can't do w-bd and w-kt",
                 "BD and KT are blocked for me; MZ is fine", '{"blocked": ["W-BD", "W-KT"]}',
                 "Unavailable for us: WBD, WKT. Free: W-MZ, W-QR."):
        assert FAMILY.parse_blocked(text, windows) == blocked, text
        assert FAMILY.tells(text, windows, blocked), text
    assert FAMILY.tells("We are free on W-MZ and W-QR only", windows, blocked)
    assert not FAMILY.tells("I propose W-MZ", windows, blocked)
    assert FAMILY.parse_blocked("I propose W-MZ. Which windows are blocked for you?", windows) is None
    assert FAMILY.choices("OK, I take W-MZ. p2 blocked: W-BD, W-KT.", windows) == ["W-MZ"]
    assert FAMILY.choices("Either W-MZ or W-QR works", windows) == []
    assert [FAMILY.window_code(value, windows) for value in ("W-KT", " w-kt ", "WKT", "KT", "W-AA", "W-KT W-BD", 5)
            ] == ["W-KT", "W-KT", "W-KT", "W-KT", None, None, None]
