import base64
import json
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.coupled_retention.__main__ import main
from examples.seatlab.coupled_retention.author import SCHEMA, census, vote


def _game() -> dict[str, Any]:
    rows = []
    for east in range(2):
        for west in range(2):
            target = east ^ west
            rows.append(
                {
                    "weight": 1,
                    "east_b64": base64.b64encode(bytes([east])).decode(),
                    "west_b64": base64.b64encode(bytes([west])).decode(),
                    "scores": [
                        [int(i == j == target) for j in range(2)] for i in range(2)
                    ],
                }
            )
    return {
        "schema": SCHEMA,
        "east_actions": ["short", "long"],
        "west_actions": ["low", "high"],
        "rows": rows,
    }


def test_author_can_detect_pooling_that_no_separate_role_can_use(
    tmp_path: Path, capsys: Any
) -> None:
    path = tmp_path / "switches.json"
    path.write_text(json.dumps(_game()))
    assert main(["inspect", str(path), "--expect-pooling-gap", "yes"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["values"]["decentralized"] == "1/2"
    assert report["values"]["pooled"] == report["values"]["oracle"] == "1"
    assert main(["inspect", str(path), "--expect-pooling-gap", "no"]) == 1


def test_no_private_vote_fields_or_partial_game(tmp_path: Path) -> None:
    path = tmp_path / "answer"
    for body in (
        b'{"slot":"A","slot":"B"}',
        b'{"slot":"C","other":"C"}',
        b'{"slot":"D"}',
        b'{"slot":"A"}\n{"slot":"B"}',
        b"null",
    ):
        path.write_bytes(body)
        with pytest.raises(ValueError):
            vote(str(path), ("A", "B", "C"))
    game = _game()
    game["rows"][0]["scores"] = [[1, 0]]
    path.write_text(json.dumps(game))
    with pytest.raises(ValueError):
        census(str(path))
    game = _game()
    game["rows"][0]["east_b64"] = "AB=="
    path.write_text(json.dumps(game))
    with pytest.raises(ValueError):
        census(str(path))
