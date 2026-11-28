import json
from copy import deepcopy
from uuid import UUID

import pytest

from examples.joint_reopen import study
from examples.seatlab.partner_native import plan, repeat_binding, repeat_math
from examples.seatlab.partner_switch.engine import native


def _repin(manifest, registration):
    manifest["registration_sha256"] = plan.digest(plan.record_bytes(registration))


def _pair_of_studies(manifest, registration):
    first = deepcopy((manifest, registration))
    second = deepcopy((manifest, registration))
    for offset, (m, r) in enumerate((first, second)):
        for i, cell in enumerate(r["cells"]):
            cell["ticket"] = f"jr-{offset * 1000 + i:016x}"
        if offset:
            r["cells"].reverse()
        _repin(m, r)
        plan._validate(m, r)
    return (*first, *second)


@pytest.fixture(scope="module")
def controls():
    return _pair_of_studies(*plan.build(1708, None, "different"))


@pytest.fixture(scope="module")
def rosters(tmp_path_factory):
    root = tmp_path_factory.mktemp("partner-repeat-rosters")
    teams = []
    for name in ("third", "first", "second"):
        path = root / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "participants": [
                        {
                            "seat": role,
                            "import_path": study.OWNER_IMPORT_PATH,
                            "model_name": f"local/{name}-{role}",
                            "skip_model_parsing": True,
                        }
                        for role in ("east", "west")
                    ]
                }
            )
        )
        teams.append((name, path))
    return _pair_of_studies(*plan.build(1708, teams, None))


def _blank(registration):
    return {cell["ticket"]: None for cell in registration["cells"]}


def _cell(registration, team, seed):
    return next(
        c for c in registration["cells"] if c["team"] == team and c["seed"] == seed
    )


def _observation(cell, number, reward):
    seed = cell["seed"]
    world = native.Meeting(
        seed, cell["fixture"], cell["channel"], str(UUID(int=number))
    )
    if reward:
        slots = native.permutation(seed)
        for role in ("east", "west"):
            assert world.handle(role, "POST", "/endorse", {"slot": slots[0]})[0] == 200
        for role in ("east", "west"):
            assert world.handle(role, "POST", "/report", {"slots": []})[0] == 200
        for role in ("east", "west"):
            assert world.handle(role, "POST", "/vote", {"slot": slots[1]})[0] == 200
    observation = native._verify.evaluate(world.freeze())
    assert observation["reward"] == reward
    return observation


def _panel(studies, obs0=None, obs1=None, *, source=repeat_binding.NATIVE):
    m0, r0, m1, r1 = studies
    return repeat_binding.panel(
        m0,
        r0,
        _blank(r0) if obs0 is None else obs0,
        m1,
        r1,
        _blank(r1) if obs1 is None else obs1,
        source=source,
    )


@pytest.mark.parametrize("field", ["start", "names", "mode", "task", "participants"])
def test_different_digests_do_not_authorize_changed_study(rosters, field):
    m0, r0, m1, r1 = deepcopy(rosters)
    if field == "start":
        m1["start"] += 1
    elif field == "names":
        m1["names"].reverse()
    elif field == "mode":
        m1["mode"] = "scripted-same"
    elif field == "task":
        r1["task"]["digest"] = "sha256:" + "c" * 64
    else:
        for cell_team, members in r1["teams"].items():
            if cell_team.startswith("p00-"):
                members[0]["model_name"] = "local/changed-east"
    _repin(m1, r1)
    with pytest.raises(ValueError):
        repeat_binding.compatible(m0, r0, m1, r1)


def test_copy_overlap_and_layout_rejected(controls):
    m0, r0, m1, r1 = deepcopy(controls)
    repeat_binding.compatible(m0, r0, m1, r1)
    with pytest.raises(ValueError, match="distinct registration"):
        repeat_binding.compatible(m0, r0, m0, r0)
    r1["cells"][0]["ticket"] = r0["cells"][0]["ticket"]
    _repin(m1, r1)
    with pytest.raises(ValueError, match="overlapping tickets"):
        repeat_binding.compatible(m0, r0, m1, r1)
    for mutation in ("duplicate", "missing", "different"):
        m0, r0, m1, r1 = deepcopy(controls)
        if mutation == "duplicate":
            r1["cells"][1].update(
                {k: v for k, v in r1["cells"][0].items() if k != "ticket"}
            )
        elif mutation == "missing":
            r1["cells"].pop()
        else:
            for cell in r1["cells"]:
                cell["seed"] += 1
        _repin(m1, r1)
        with pytest.raises(ValueError):
            repeat_binding.compatible(m0, r0, m1, r1)


def test_missing_extra_misbound_and_repeated_observations(controls):
    _, r0, _, r1 = controls
    first = r0["cells"][0]
    seed = first["seed"]
    other = next(c for c in r0["cells"] if c["seed"] != seed)
    same_seed = next(c for c in r0["cells"] if c["seed"] == seed and c != first)
    repeated = _cell(r1, first["team"], seed)
    observation = _observation(first, 1, 0)
    blank = _blank(r0)
    missing = dict(blank)
    del missing[first["ticket"]]
    for bad in (
        missing,
        {**blank, "unexpected": None},
        {**blank, other["ticket"]: observation},
        {**blank, first["ticket"]: observation, same_seed["ticket"]: observation},
    ):
        with pytest.raises(ValueError):
            _panel(controls, bad)
    with pytest.raises(ValueError, match="physical world"):
        _panel(
            controls,
            {**blank, first["ticket"]: observation},
            {**_blank(r1), repeated["ticket"]: observation},
        )
    other_repeat = _cell(r1, other["team"], other["seed"])
    with pytest.raises(ValueError, match="physical world"):
        _panel(
            controls,
            {**blank, first["ticket"]: observation},
            {
                **_blank(r1),
                other_repeat["ticket"]: _observation(other_repeat, 1, 0),
            },
        )


def test_fixed_role_three_teams_order_and_partial_bounds(rosters):
    m0, r0, m1, r1 = rosters
    names = m0["names"]
    seeds = sorted(c["seed"] for c in r0["cells"] if c["team"] == "p00-00")
    obs0, obs1 = _blank(r0), _blank(r1)
    for number, (registration, observations, team, reward) in enumerate(
        (
            (r0, obs0, "p00-00", 1),
            (r1, obs1, "p00-00", 0),
            (r0, obs0, "p00-01", 0),
            (r1, obs1, "p00-01", 1),
            (r1, obs1, "p01-00", 0),
        ),
        start=1,
    ):
        cell = _cell(registration, team, seeds[0])
        observations[cell["ticket"]] = _observation(cell, number, reward)
    result = _panel(rosters, obs0, obs1)
    assert result["schema"] == "seatlab.partner-native-repeat/v1"
    assert result["registration_sha256"] == [
        m0["registration_sha256"],
        m1["registration_sha256"],
    ]
    assert result["names"] == names
    assert result["registered_trials"] == 108
    assert result["observed_trials"] == 5
    assert result["complete"] is False
    contrasts = result["contrasts"]
    assert [(c["anchor"], c["new_partner"], c["role"]) for c in contrasts] == [
        (anchor, partner, role)
        for anchor in names
        for partner in names
        if anchor != partner
        for role in ("east", "west")
    ]
    unknown = [(None, None, None, None)] * 5
    metrics = contrasts[0]["metrics"]
    assert metrics == repeat_math.summarize([(1, 0, 0, 1), *unknown])
    assert metrics["complete_cases"] == 1
    assert metrics["excess_disagreement"] == {
        "value": None,
        "bounds": ["-1/2", "3/4"],
    }
    assert metrics["cross_disagreement"]["bounds"] == ["1/12", "11/12"]
    assert contrasts[1]["metrics"] == repeat_math.summarize([(1, 0, None, 0), *unknown])
    assert contrasts[2]["metrics"] == repeat_math.summarize(
        [(1, 0, None, None), *unknown]
    )
    assert contrasts[-1]["metrics"] == repeat_math.summarize(
        [(None, None, None, None)] * 6
    )
    encoded = json.dumps(result)
    for registration, observations in ((r0, obs0), (r1, obs1)):
        for cell in registration["cells"]:
            assert cell["ticket"] not in encoded
        for observation in observations.values():
            if observation is not None:
                assert observation["run"] not in encoded
    assert "local/" not in encoded


def test_complete_scripted_worlds_and_source_boundary(controls, rosters):
    _, r0, _, r1 = controls
    first = {
        cell["ticket"]: _observation(cell, i, 0)
        for i, cell in enumerate(r0["cells"], start=1)
    }
    second = {
        cell["ticket"]: _observation(cell, i, 0)
        for i, cell in enumerate(r1["cells"], start=1000)
    }
    result = _panel(controls, first, second, source=repeat_binding.SCRIPTED)
    assert result["source"] == "scripted_native_worlds"
    assert result["registered_trials"] == result["observed_trials"] == 48
    assert result["complete"] is True
    for contrast in result["contrasts"]:
        assert contrast["metrics"] == repeat_math.summarize([(0, 0, 0, 0)] * 6)
    with pytest.raises(ValueError, match="evidence source"):
        _panel(rosters, source=repeat_binding.SCRIPTED)
    with pytest.raises(ValueError, match="evidence source"):
        _panel(controls, source="model_estimate")
