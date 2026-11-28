import hashlib
import json
from pathlib import Path
from typing import Any

from examples.seatlab.convention_crossplay import ballots
from examples.seatlab.coupled_retention import strategy

GAME = Path(strategy.__file__).with_name("parity-game.json")


def _pin(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _jsonl(rows: list[dict[str, str]]) -> bytes:
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


def _deck(root: Path, role: str, capsys: Any) -> tuple[list[str], list[dict[str, str]]]:
    assert (
        ballots.main(
            ["prepare", "--game", str(GAME), "--role", role, "--out", str(root)]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert "prompt" not in receipt
    assert receipt["deck_sha256"] == _pin((root / "deck.json").read_bytes())
    rows = [
        json.loads(row) for row in (root / "requests.jsonl").read_bytes().splitlines()
    ]
    assert {row["prompt"].encode() for row in rows} == {b"\0", b"\1"}
    return [
        "freeze",
        "--role",
        role,
        "--deck",
        str(root),
        "--deck-sha256",
        receipt["deck_sha256"],
    ], rows


def _answers(rows: list[dict[str, str]], action: str) -> list[dict[str, str]]:
    return [
        {
            "id": row["id"],
            "prompt_sha256": row["prompt_sha256"],
            "reply": json.dumps({"action": action}),
        }
        for row in rows
    ]


def test_role_captures_count_equal_teams_without_changing_any_vote(
    tmp_path: Path, capsys: Any
) -> None:
    args = ["compare", "--game", str(GAME), "--require-equal-home"]
    ids: set[str] = set()
    for name, action in (("alpha", "hold"), ("beta", "switch"), ("gamma", "hold")):
        args += ["--team", name]
        for role in ("east", "west"):
            deck = tmp_path / (name + role)
            cmd, requests = _deck(deck, role, capsys)
            assert ids.isdisjoint(row["id"] for row in requests)
            ids.update(row["id"] for row in requests)
            answers = _answers(requests, action)
            files = [tmp_path / (name + role + str(i)) for i in (0, 1)]
            for path, answer in zip(files, reversed(answers), strict=True):
                path.write_bytes(_jsonl([answer]))
            out = tmp_path / (name + role + "_frozen")
            assert (
                ballots.main(
                    [
                        *cmd,
                        "--source",
                        name,
                        "--replies",
                        *(str(x) for x in files),
                        "--out",
                        str(out),
                    ]
                )
                == 0
            )
            receipt = json.loads(capsys.readouterr().out)
            assert receipt == json.loads((out / "evidence.json").read_bytes())
            assert receipt["reply_shard_sha256"] == sorted(
                _pin(x.read_bytes()) for x in files
            )
            assert receipt["table_sha256"] == _pin((out / "strategy.json").read_bytes())
            args.append(str(out))
    assert ballots.main([*args, "--expect-gap", "positive"]) == 0
    report = json.loads(capsys.readouterr().out)
    matrix = report["closed_role_replay"]
    assert matrix["matrix"] == [
        ["1/2", "0", "1/2"],
        ["0", "1/2", "0"],
        ["1/2", "0", "1/2"],
    ]
    assert matrix["unit"] == "recorded_team" and matrix["distinct_behaviors"] == 2
    assert (matrix["home_mean"], matrix["cross_mean"], matrix["home_advantage"]) == (
        "1/2",
        "1/6",
        "1/3",
    )
    assert (
        ballots.main(
            [
                *args,
                "--cohort-sha256",
                report["cohort"]["cohort_sha256"],
                "--expect-gap",
                "zero",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["closed_role_replay"] == matrix
    assert ballots.main([*args, "--cohort-sha256", "sha256:" + "0" * 64]) == 2
    assert capsys.readouterr().out == ""


def test_failed_capture_publishes_nothing_and_never_overwrites(
    tmp_path: Path, capsys: Any
) -> None:
    cmd, rows = _deck(tmp_path / "deck", "east", capsys)
    out = tmp_path / "out"
    shard = tmp_path / "replies.jsonl"
    args = [*cmd, "--source", "alpha", "--out", str(out), "--replies", str(shard)]
    good = _answers(rows, "hold")
    shard.write_bytes(
        _jsonl(
            [
                good[0],
                {**good[0], "prompt_sha256": "0" * 64},
                {**good[0], "id": "unknown"},
            ]
        )
    )
    assert ballots.main(args) == 1
    r = json.loads(capsys.readouterr().out)
    assert r["counts"] == dict(
        expected=2,
        represented=1,
        valid=0,
        missing=1,
        duplicate_ids=1,
        stale_ids=1,
        invalid_ids=0,
        unknown_rows=1,
    )
    assert r["table_sha256"] is None and not out.exists()
    shard.write_bytes(b"")
    assert (
        ballots.main(args) == 1
        and json.loads(capsys.readouterr().out)["counts"]["missing"] == 2
    )
    shard.write_bytes(_jsonl(good))
    assert (
        ballots.main([*args, "--role", "west"]) == 2
        and capsys.readouterr().out == ""
        and not out.exists()
    )
    assert ballots.main(args) == 0
    capsys.readouterr()
    saved = (out / "evidence.json").read_bytes()
    assert ballots.main(args) == 2 and not capsys.readouterr().out
    assert (out / "evidence.json").read_bytes() == saved
