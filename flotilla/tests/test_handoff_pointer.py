import json

import pytest
from test_handoff import FAMILY, SEED, SIM


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_pointer_policy(dose, tmp_path):
    paths = SIM.BUILD.build("handoff", [0, 1, 2], FAMILY.DOSES[dose], dose, SEED,
                            ["team", "team-fullrecord"], tmp_path)
    for path in paths:
        task = json.loads((path / "environment/task.json").read_text())
        for schedule in (0, 1):
            run = SIM.simulate_built(path, "pointer", schedule)
            assert run["invalid"] is None and run["score"]["wrong_settlements"] == 0
            assert run["score"]["note_unparsed"] == 0
            assert run["score"]["first_loss_shift"] == 1
            assert run["score"]["lots_pointer"] == task["params"]["shifts"] - 1
            assert run["score"]["pairs_kept"] == task["params"]["shifts"] - 1
            assert run["reward"] == (0.5 if task["condition"] == "team" else 1.0)
