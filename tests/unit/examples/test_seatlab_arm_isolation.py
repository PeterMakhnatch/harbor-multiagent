import json
from pathlib import Path

import pytest

from examples.seatlab.workbench import isolate
from examples.ship_handover.arm_inputs import TASK_DIR, prepare


@pytest.fixture(scope="module")
def controls(tmp_path_factory):
    root = tmp_path_factory.mktemp("ship-isolation") / "locks"
    prepare(str(root))
    return root


def check(controls: Path, capsys, left, right, mode, target, *extra):
    code = isolate.main(
        [
            str(TASK_DIR),
            str(controls / f"{left}.json"),
            str(TASK_DIR),
            str(controls / f"{right}.json"),
            mode,
            target,
            *extra,
            "--json",
        ]
    )
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


@pytest.mark.unit
def test_native_changed_receiver_is_not_a_changed_world(controls, capsys):
    left, right = "recovery-deliver", "sender-only-deliver"
    code, team = check(controls, capsys, left, right, "--participant", "relief")
    assert code == 0 and team["status"] == "isolated"
    assert team["other_changes"] == {} and not team["native_task_digest_changed"]
    code, mislabeled = check(
        controls, capsys, left, right, "--world-setting", "SH_CHANNEL"
    )
    assert code == 1 and not mislabeled["target"]["changed"]
    assert mislabeled["other_changes"] == {"participant": 1}


@pytest.mark.unit
def test_reuse_an_ordered_world_comparison_then_refuse_reversal(controls, capsys):
    left, right = "recovery-deliver", "recovery-withhold"
    measure = ("--world-setting", "SH_CHANNEL")
    code, result = check(controls, capsys, left, right, *measure)
    assert code == 0 and result["target"]["changed"]
    pin = ("--expected-comparison", result["comparison"])
    code, repeated = check(controls, capsys, left, right, *measure, *pin)
    assert code == 0 and repeated["matches_expected"]
    code, reversed_pair = check(controls, capsys, right, left, *measure, *pin)
    assert code == 1 and reversed_pair["reasons"] == [
        "different_from_expected_comparison"
    ]
    assert reversed_pair["inputs"]["left"] == result["inputs"]["right"]


@pytest.mark.unit
def test_world_with_different_receiver_is_not_an_isolated_channel(controls, capsys):
    code, result = check(
        controls,
        capsys,
        "sender-only-deliver",
        "recovery-withhold",
        "--world-setting",
        "SH_CHANNEL",
    )
    assert code == 1 and result["target"]["changed"]
    assert result["other_changes"] == {"participant": 1}
    assert result["reasons"] == ["additional_inputs_differ"]
    assert "deliver" not in json.dumps(result) and "resume" not in json.dumps(result)


@pytest.mark.unit
def test_unknown_target_does_not_turn_two_identical_arms_into_a_change(
    controls, capsys
):
    code, result = check(
        controls,
        capsys,
        "recovery-deliver",
        "recovery-deliver",
        "--role-instruction",
        "missing",
    )
    assert code == 1 and not result["target"]["present_in_both"]
    assert (
        not result["target"]["changed"]
        and result["inputs"]["left"] == result["inputs"]["right"]
    )
