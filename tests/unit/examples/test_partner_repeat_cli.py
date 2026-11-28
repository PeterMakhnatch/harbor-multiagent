import json

import pytest

from examples.seatlab.partner_native import plan
from examples.seatlab.partner_native.__main__ import main


@pytest.fixture(scope="module")
def repeat_studies(tmp_path_factory):
    root = tmp_path_factory.mktemp("partner-repeat-cli")
    args = []
    for prefix in ("", "repeat-"):
        study = root / f"{prefix}study"
        manifest, registration = plan.build(3910, None, "offsetting")
        pin = plan.save(study, manifest, registration)
        args.extend((f"--{prefix}study", str(study), f"--{prefix}pin", pin))
    return args


def test_empty_repeats_do_not_become_zero_rewards(repeat_studies, tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert (
        main(
            [
                "compare-repeats",
                *repeat_studies,
                "--trials",
                str(a),
                "--repeat-trials",
                str(b),
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["registered_trials"] == 48 and result["observed_trials"] == 0
    for row in result["contrasts"]:
        assert row["metrics"]["excess_disagreement"] == {
            "value": None,
            "bounds": ["-1/2", "1"],
        }
    duplicate = tmp_path / "duplicate"
    duplicate.symlink_to(a, target_is_directory=True)
    for invalid in (a, duplicate, tmp_path, tmp_path / "missing"):
        assert (
            main(
                [
                    "compare-repeats",
                    *repeat_studies,
                    "--trials",
                    str(a),
                    "--repeat-trials",
                    str(invalid),
                ]
            )
            == 2
        )
        assert not capsys.readouterr().out


def test_same_study_is_not_evidence_of_a_repeat(repeat_studies, capsys):
    identical = [
        *repeat_studies[:4],
        "--repeat-study",
        repeat_studies[1],
        "--repeat-pin",
        repeat_studies[3],
    ]
    assert main(["rehearse-repeats", *identical]) == 2
    assert not capsys.readouterr().out
