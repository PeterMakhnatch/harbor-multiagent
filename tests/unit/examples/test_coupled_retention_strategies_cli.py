import json
import stat
from pathlib import Path
from typing import Any

from examples.seatlab.coupled_retention import strategies, strategy

FIXTURE = Path(strategies.__file__).with_name("parity-game.json")


def test_synthesized_separate_strategies_can_be_scored_and_changed(
    tmp_path: Path, capsys: Any
) -> None:
    source = ["--game", str(FIXTURE)]
    out = tmp_path / "chosen"
    assert strategies.main(["synthesize", *source, "--out", str(out)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["score"] == {
        "earned": 2,
        "reward": "1/2",
        "total_weight": 4,
        "rows": 4,
    }
    assert receipt["values"] == {"decentralized": "1/2", "pooled": "1", "oracle": "1"}
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    east, west = (out / "east.json", out / "west.json")
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (east, west))
    args = ["score", *source, "--east", str(east), "--west", str(west)]
    assert strategies.main([*args, "--expect-value", "1/2"]) == 0
    assert json.loads(capsys.readouterr().out)["score"] == receipt["score"]
    east.write_bytes(strategy.table("east", {b"\0": "hold", b"\1": "hold"}))
    west.write_bytes(strategy.table("west", {b"\0": "switch", b"\1": "switch"}))
    assert strategies.main([*args, "--expect-value", "1/2"]) == 1
    assert json.loads(capsys.readouterr().out)["score"]["reward"] == "0"
    assert strategies.main(["synthesize", *source, "--out", str(out)]) == 2
    assert capsys.readouterr().out == ""
    assert strategies.main([*args, "--expect-value", "0"]) == 0
    capsys.readouterr()
    west.write_bytes(strategy.table("west", {b"\0": "hold"}))
    assert strategies.main(args) == 2
    assert capsys.readouterr().out == ""


def test_generic_input_cannot_receive_native_labels(
    tmp_path: Path, capsys: Any
) -> None:
    output = tmp_path / "wrong"
    assert (
        strategies.main(
            [
                "synthesize",
                "--game",
                str(FIXTURE),
                "--sender",
                "truthful",
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists() and capsys.readouterr().out == ""
    assert strategies.main(["synthesize", "--start", "2", "--out", str(output)]) == 2
    assert not output.exists() and capsys.readouterr().out == ""
