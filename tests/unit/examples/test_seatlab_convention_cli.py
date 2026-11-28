import json
from pathlib import Path
from typing import Any

from examples.seatlab.convention_crossplay.__main__ import main
from examples.seatlab.coupled_retention import strategy

GAME = Path(strategy.__file__).with_name("parity-game.json")


def _constant_pair(folder: Path, name: str, action: str) -> list[str]:
    files = []
    for role in ("east", "west"):
        dest = folder / f"{name}-{role}.json"
        dest.write_bytes(strategy.table(role, {b"\0": action, b"\1": action}))
        files.append(str(dest))
    return ["--team", name, *files]


def test_private_roster_freezes_both_teams_and_scores_every_permutation(
    tmp_path: Path, capsys: Any
) -> None:
    a = _constant_pair(tmp_path, "first", "hold")
    b = _constant_pair(tmp_path, "second", "switch")
    private = tmp_path / "roster"
    assert main(["enroll", *a, *b, "--out", str(private)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["teams"] == 2
    args = [
        "score",
        "--game",
        str(GAME),
        "--roster",
        str(private / "roster.json"),
        "--roster-sha256",
        receipt["roster_sha256"],
        "--require-equal-home",
    ]
    assert main([*args, "--expect-gap", "positive"]) == 0
    result = json.loads(capsys.readouterr().out)["closed_role_replay"]
    assert result["matrix"] == [["1/2", "0"], ["0", "1/2"]]
    assert main([*args, "--expect-gap", "zero"]) == 1
    capsys.readouterr()
    Path(a[2]).write_bytes(b"unrelated later changes")
    assert main(args) == 0
    capsys.readouterr()
    roster = private / "roster.json"
    frozen = roster.read_bytes()
    roster.write_bytes(frozen + b" ")
    assert main(args) == 2 and capsys.readouterr().out == ""
    roster.write_bytes(frozen)
    p = private / "t01-west.json"
    p.write_bytes(p.read_bytes() + b" ")
    assert main(args) == 2 and capsys.readouterr().out == ""


def test_submit_cannot_count_copies_as_new_teams(tmp_path: Path, capsys: Any) -> None:
    a = _constant_pair(tmp_path, "alpha", "hold")
    b = _constant_pair(tmp_path, "beta", "hold")
    out = tmp_path / "copy"
    assert main(["enroll", *a, *b, "--out", str(out)]) == 2
    assert not out.exists() and capsys.readouterr().out == ""
