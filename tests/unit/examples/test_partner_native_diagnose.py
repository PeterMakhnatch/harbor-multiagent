import json
from copy import deepcopy
from uuid import uuid4

import pytest

from examples.seatlab.partner_native import diagnose, plan
from examples.seatlab.partner_native.__main__ import main
from examples.seatlab.partner_switch.engine import native


@pytest.fixture(scope="module")
def study():
    return plan.build(1708, None, "different")


def test_scripted_native_observation_is_not_a_missing_native_trial(
    study, tmp_path, capsys
):
    manifest, registration = study
    unknown = {cell["ticket"]: None for cell in registration["cells"]}
    cell = registration["cells"][0]
    world = native.Meeting(cell["seed"], "both", "deliver", str(uuid4()))
    state = native._verify.evaluate(world.freeze())
    cases = {**unknown, cell["ticket"]: state}
    explained = diagnose.panel(manifest, registration, cases, source=diagnose.SCRIPTED)
    assert (
        explained["source"] == "scripted_native_worlds"
        and explained["observed_worlds"] == 1
    )
    checked = [pair for row in explained["matrix"] for pair in row]
    assert sum(pair["outcomes"]["no_vote_pair"] for pair in checked) == 1
    assert sum(pair["unknown"] for pair in checked) == 23
    assert sum(pair["receipt_before_vote"]["neither"] for pair in checked) == 1
    assert state["run"] not in json.dumps(explained) and cell[
        "ticket"
    ] not in json.dumps(explained)
    study_dir = tmp_path / "study"
    pin = plan.save(study_dir, manifest, registration)
    trials = tmp_path / "trials"
    trials.mkdir()
    assert (
        main(
            [
                "explain",
                "--study",
                str(study_dir),
                "--pin",
                pin,
                "--trials",
                str(trials),
            ]
        )
        == 1
    )
    public = json.loads(capsys.readouterr().out)
    assert (
        public["source"] == "collected_native_trials" and public["observed_worlds"] == 0
    )
    for row in public["matrix"]:
        for pair in row:
            assert pair["unknown"] == 6 and sum(pair["outcomes"].values()) == 0
    for pair in public["contrasts"]:
        assert pair["complete_pairs"] == 0 and pair["unknown_pairs"] == 6
        assert pair["original_minus_replacement_bounds"] == ["-1", "1"]


def test_panel_refuses_partial_mapping_wrong_world_and_reused_receipt(study):
    manifest, registration = study
    cells = registration["cells"]
    blank = {cell["ticket"]: None for cell in cells}
    first = cells[0]
    same_seed = next(
        cell
        for cell in cells
        if cell["ticket"] != first["ticket"] and cell["seed"] == first["seed"]
    )
    other_seed = next(cell for cell in cells if cell["seed"] != first["seed"])
    world = native.Meeting(first["seed"], "both", "deliver", str(uuid4()))
    observation = native._verify.evaluate(world.freeze())
    missing = dict(blank)
    del missing[first["ticket"]]
    for bad in (
        missing,
        {**blank, other_seed["ticket"]: observation},
        {**blank, first["ticket"]: observation, same_seed["ticket"]: observation},
    ):
        with pytest.raises(ValueError):
            diagnose.panel(manifest, registration, bad, source=diagnose.SCRIPTED)
    with pytest.raises(ValueError):
        diagnose.panel(manifest, registration, blank, source="model_estimate")
    external = deepcopy(manifest)
    external["mode"] = "native-rosters"
    with pytest.raises(ValueError):
        diagnose.panel(external, registration, blank, source=diagnose.SCRIPTED)
