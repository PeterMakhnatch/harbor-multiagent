import base64
import json
from itertools import product
from pathlib import Path

from examples.seatlab.decision_boundary.__main__ import main


def _census(path: Path, slots: str) -> None:
    worlds = []
    for first, second in product("01", repeat=2):
        payload = (first if "f" in slots else "?") + (second if "s" in slots else "?")
        worlds.append(
            {
                "name": first + second,
                "mass": 1,
                "observation_b64": base64.b64encode(payload.encode()).decode(),
                "scores": {
                    "straight": int(first == second),
                    "crossed": int(first != second),
                },
            }
        )
    path.write_text(
        json.dumps(
            {"schema": "seatlab.decision-worlds/v1", "scale": 1, "worlds": worlds}
        )
    )


def test_file_contract_and_failed_hypothesis_are_distinct(tmp_path: Path, capsys):
    files = []
    for index, slots in enumerate(("", "f", "s", "fs")):
        path = tmp_path / str(index)
        _census(path, slots)
        files.append(str(path))
    args = ["joint", "--files", *files, "--expect-each-needed"]
    assert main([*args, "yes"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["qualification"]["conditional_gains"] == {
        "first_given_second": "1/2",
        "second_given_first": "1/2",
    }
    assert report["expectation_met"] is True
    assert main([*args, "no"]) == 1
    assert json.loads(capsys.readouterr().out)["expectation_met"] is False
    path = Path(files[1])
    data = json.loads(path.read_text())
    data["scale"] = True
    path.write_text(json.dumps(data))
    assert main([*args, "yes"]) == 2
    invalid = capsys.readouterr()
    assert not invalid.out and "scale" in invalid.err
    assert main(["joint", "--files", "-", "-", files[2], files[3]]) == 2
    assert "standard input" in capsys.readouterr().err
