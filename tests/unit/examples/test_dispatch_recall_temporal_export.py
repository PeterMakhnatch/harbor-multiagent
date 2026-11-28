import contextlib
import hashlib
import io
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import examples.seatlab.dispatch_recall.temporal_export as te
from examples.seatlab.decision_boundary.core import World, analyze
from examples.seatlab.decision_boundary.inputs import SCHEMA, census, read_input
from examples.seatlab.dispatch_recall.certificate import phase_tickets
from examples.seatlab.dispatch_recall.native import ENVIRONMENT, Session

SEED = 7
FILES = ("early-commit.json", "early-close.json", "late-commit.json", "late-close.json")
TIMING = {"early": "immediate", "late": "deferred"}
INFORMED = {
    "early-commit": "1",
    "early-close": "1",
    "late-commit": "1/2",
    "late-close": "1",
}
OBSERVED = {
    "truthful": INFORMED,
    "wrong": INFORMED,
    "silent": dict.fromkeys(INFORMED, "1/2"),
}
CLASSES = {
    "truthful": {
        "early-commit": 2,
        "early-close": 2,
        "late-commit": 1,
        "late-close": 2,
    },
    "wrong": {"early-commit": 2, "early-close": 2, "late-commit": 1, "late-close": 2},
    "silent": dict.fromkeys(INFORMED, 1),
}
AFTER_COMMIT = {
    "truthful": {"early": "equivalent", "late": "refinement"},
    "wrong": {"early": "equivalent", "late": "refinement"},
    "silent": {"early": "equivalent", "late": "equivalent"},
}
Export = tuple[Path, dict[str, Any], str]


@pytest.fixture(scope="module")
def exported() -> Iterator[Export]:
    with tempfile.TemporaryDirectory(
        dir="/tmp", prefix="recall-temporal-"
    ) as workspace:
        out = Path(workspace) / "tables"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            summary = te.export(out, SEED, "truthful")
        yield out.resolve(), summary, printed.getvalue()


def _bundle(out: Path) -> dict[str, list[World]]:
    bundle = {}
    for name in FILES:
        scale, worlds = census(read_input(str(out / name)))
        assert scale == 1
        bundle[name] = worlds
    return bundle


def _line(data: object) -> bytes:
    return (
        json.dumps({"ok": True, "data": data}, separators=(",", ":")).encode() + b"\n"
    )


def _report(sender: str, current: str, bays: list[str]) -> str | None:
    if sender == "truthful":
        return current
    if sender == "wrong":
        return next(bay for bay in bays if bay != current)
    assert sender == "silent"
    return None


def _drive(
    seed: int, transition: str, timing: str, sender: str, index: int
) -> tuple[list[bytes], int]:
    session = Session(seed, transition, timing, tickets=phase_tickets(seed))
    first = session.view("loader")
    lines = [_line(first)]
    lines.append(
        _line(session.apply("loader", {"op": "stage", "bay": first["initial_bay"]}))
    )
    lines.append(_line(session.view("loader")))
    depot = session.view("depot")
    report = _report(sender, depot["current_bay"], first["bays"])
    session.apply("depot", {"op": "report", "bay": report})
    window = session.view("loader")
    lines.append(_line(window))
    commit = {"op": "commit", "bay": first["bays"][index], "ticket": window["ticket"]}
    lines.append(_line(session.apply("loader", commit)))
    reward = session.snapshot()["reward"]
    late = session.view("loader")
    lines.append(_line(late))
    lines.append(
        _line(session.apply("loader", {"op": "close", "ticket": late["ticket"]}))
    )
    assert session.snapshot()["reward"] == reward
    return lines, reward


def _replayed(seed: int, sender: str) -> dict[str, list[World]]:
    bundle: dict[str, list[World]] = {name: [] for name in FILES}
    for phase, timing in TIMING.items():
        for transition in ("stay", "move"):
            runs = [_drive(seed, transition, timing, sender, index) for index in (0, 1)]
            assert runs[0][0] == runs[1][0]
            lines = runs[0][0]
            bays = json.loads(lines[0])["data"]["bays"]
            scores = {bays[index]: runs[index][1] for index in (0, 1)}
            bundle[f"{phase}-commit.json"].append(
                World(transition, 1, b"".join(lines[:4]), scores)
            )
            bundle[f"{phase}-close.json"].append(
                World(transition, 1, b"".join(lines), scores)
            )
    return bundle


def _notice(observation: bytes, index: int) -> str:
    return json.loads(observation.splitlines()[index])["data"]["notice"]


def _winner(world: World) -> str:
    winners = [bay for bay, score in world.scores.items() if score == 1]
    assert len(winners) == 1
    return winners[0]


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_export_publishes_four_private_files_and_compact_summary(exported):
    out, summary, printed = exported
    assert printed == ""
    assert sorted(entry.name for entry in out.parent.iterdir()) == ["tables"]
    assert sorted(entry.name for entry in out.iterdir()) == sorted(FILES)
    assert stat.S_IMODE(os.lstat(out).st_mode) == 0o700
    digests = {}
    for name in FILES:
        info = os.lstat(out / name)
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        digests[name] = _digest((out / name).read_bytes())
    assert list(summary["files"]) == list(FILES)
    assert summary["files"] == digests
    assert summary["schema"] == "seatlab.dispatch-recall.temporal-export/v1"
    assert summary["access"] == "private_benchmark_author"
    assert (summary["seed"], summary["sender"], summary["path"]) == (
        SEED,
        "truthful",
        str(out),
    )
    assert (summary["input_schema"], summary["scale"]) == (SCHEMA, 1)
    assert summary["phases"] == TIMING
    assert (summary["worlds_per_file"], summary["actions_per_world"]) == (2, 2)
    assert summary["observed"] == {
        f"{key}.json": value for key, value in OBSERVED["truthful"].items()
    }
    assert summary["classes"] == {
        f"{key}.json": value for key, value in CLASSES["truthful"].items()
    }
    assert summary["after_commit"] == AFTER_COMMIT["truthful"]
    assert (summary["model_calls"], summary["harbor_trials"]) == (0, 0)
    assert summary["pins"] == {
        "recall_world.py": _digest((ENVIRONMENT / "recall_world.py").read_bytes()),
        "recall_server.py": _digest((ENVIRONMENT / "recall_server.py").read_bytes()),
        "certificate.py": _digest(
            (Path(te.__file__).parent / "certificate.py").read_bytes()
        ),
    }
    assert "hindsight" in summary["scope"] and "No model" in summary["privacy"]
    text = json.dumps(summary)
    assert "observation_b64" not in text and '"scores":' not in text
    for ticket in phase_tickets(SEED):
        assert ticket not in text
    for bay in _bundle(out)[FILES[0]][0].scores:
        assert f'"{bay}"' not in text
    assert len(text) < 4096


def test_files_conserve_the_independently_replayed_engine_wire(exported):
    out, _, _ = exported
    bundle = _bundle(out)
    assert bundle == _replayed(SEED, "truthful")
    for name in FILES:
        root = json.loads((out / name).read_bytes())
        assert set(root) == {"schema", "scale", "worlds"} and root["schema"] == SCHEMA
        assert [row["name"] for row in root["worlds"]] == ["stay", "move"]
        assert all(row["mass"] == 1 for row in root["worlds"])
    for phase in TIMING:
        commit, close = bundle[f"{phase}-commit.json"], bundle[f"{phase}-close.json"]
        for before, after in zip(commit, close, strict=True):
            assert after.observation.startswith(before.observation)
            assert before.observation.count(b"\n") == 4
            assert after.observation.count(b"\n") == 7
            for line in after.observation.splitlines(keepends=True):
                assert json.loads(line)["ok"] is True
                assert te.certificate.encode_reply(json.loads(line)["data"]) == line
            for word in (b"current_bay", b"transition", b"timing", b"stay", b"move"):
                assert word not in after.observation
        assert len(commit[0].observation) == len(commit[1].observation)
        assert len(close[0].observation) == len(close[1].observation)
    stay, move = bundle["early-commit.json"]
    initial = json.loads(stay.observation.splitlines()[0])["data"]["initial_bay"]
    assert _winner(stay) == initial and _winner(move) != initial


@pytest.mark.parametrize("sender", ["truthful", "wrong", "silent"])
@pytest.mark.parametrize("seed", [0, 11, 2**63 - 1])
def test_tables_match_replay_and_the_temporal_design(seed, sender):
    bundle = te.tables(seed, sender)
    assert tuple(bundle) == FILES
    assert bundle == _replayed(seed, sender)
    for name, worlds in bundle.items():
        result = analyze(worlds, 1)
        assert result["observed"] == OBSERVED[sender][name.removesuffix(".json")]
        assert result["observations"] == CLASSES[sender][name.removesuffix(".json")]
        assert result["informed"] == "1"


def test_silence_counterexample_keeps_every_moment_uninformative():
    bundle = te.tables(SEED, "silent")
    for name, (stay, move) in bundle.items():
        assert stay.observation == move.observation, name
        assert _winner(stay) != _winner(move)
        assert analyze([stay, move], 1)["observed"] == "1/2"
    for phase in TIMING:
        stay, _ = bundle[f"{phase}-close.json"]
        assert _notice(stay.observation, 3) == "--"
        assert _notice(stay.observation, 5) == "--"


def test_wrong_codebook_separates_worlds_but_following_the_notice_loses():
    wrong = te.tables(SEED, "wrong")
    truthful = te.tables(SEED, "truthful")
    for world in wrong["early-commit.json"]:
        assert _notice(world.observation, 3) != _winner(world)
        assert world.scores[_notice(world.observation, 3)] == 0
    for world in truthful["early-commit.json"]:
        assert _notice(world.observation, 3) == _winner(world)
    assert analyze(wrong["early-commit.json"], 1)["observed"] == "1"
    for world in wrong["late-commit.json"]:
        assert _notice(world.observation, 3) == "--"
    for world in wrong["late-close.json"]:
        assert world.scores[_notice(world.observation, 5)] == 0
    assert [world.scores for world in wrong["late-close.json"]] == [
        world.scores for world in truthful["late-close.json"]
    ]
    for name in FILES:
        same = [world.observation for world in wrong[name]] == [
            world.observation for world in truthful[name]
        ]
        assert same is (name == "late-commit.json")


def _swap(row: dict[str, Any], old: bytes, new: bytes, keys: tuple[str, ...]) -> None:
    for key in keys:
        assert old in row[key]
        row[key] = row[key].replace(old, new, 1)


def _prefix(transition: str, timing: str, row: dict[str, Any]) -> None:
    if transition == "move":
        cut = len(row["decision"]) - 2
        row["hindsight"] = row["hindsight"][:cut] + b"?" + row["hindsight"][cut + 1 :]


def _width(transition: str, timing: str, row: dict[str, Any]) -> None:
    if transition == "move":
        row["decision"] = b" " + row["decision"]
        row["hindsight"] = b" " + row["hindsight"]


def _physical(transition: str, timing: str, row: dict[str, Any]) -> None:
    if transition == "move":
        row["scores"] = {bay: 1 - score for bay, score in row["scores"].items()}


def _leak(transition: str, timing: str, row: dict[str, Any]) -> None:
    _swap(row, b'"role"', b'"move"', ("decision", "hindsight"))


def _schedule(transition: str, timing: str, row: dict[str, Any]) -> None:
    row["requests"] = [*row["requests"], {"op": "view"}]


def _early_ticket(transition: str, timing: str, row: dict[str, Any]) -> None:
    if transition == "move" and timing == "deferred":
        ticket = phase_tickets(SEED)[0].encode()
        flipped = (b"0" if ticket[:1] != b"0" else b"1") + ticket[1:]
        _swap(row, ticket, flipped, ("decision", "hindsight"))


Tamper = Callable[[str, str, dict[str, Any]], None]


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (_prefix, "do not extend the commit bytes"),
        (_width, "fixed-width wire lengths"),
        (_physical, "physical stay and move bays"),
        (_leak, "leak the arm"),
        (_schedule, "fixed protocol"),
        (_early_ticket, "late commit optimum"),
    ],
)
def test_tampered_rows_are_refused_before_any_output(
    monkeypatch, tamper: Tamper, message
):
    real = te._transition_row

    def tampered(
        seed: int, transition: str, timing: str, sender: str, tickets: tuple[str, str]
    ) -> dict[str, Any]:
        row = real(seed, transition, timing, sender, tickets)
        tamper(transition, timing, row)
        return row

    monkeypatch.setattr(te, "_transition_row", tampered)
    with tempfile.TemporaryDirectory(
        dir="/tmp", prefix="recall-temporal-bad-"
    ) as workspace:
        with pytest.raises(ValueError, match=message):
            te.export(Path(workspace) / "tables", SEED, "truthful")
        assert os.listdir(workspace) == []


@pytest.mark.parametrize(
    ("seed", "sender"),
    [(-1, "truthful"), (2**63, "truthful"), (True, "truthful"), (SEED, "loud")],
)
def test_invalid_seed_or_sender_is_refused(seed, sender):
    with pytest.raises(ValueError):
        te.tables(seed, sender)


def test_export_refuses_existing_checkout_and_git_outputs(exported):
    out, _, _ = exported
    before = {entry.name: entry.read_bytes() for entry in out.iterdir()}
    with pytest.raises(ValueError, match="already exists"):
        te.export(out, SEED, "truthful")
    assert {entry.name: entry.read_bytes() for entry in out.iterdir()} == before
    inside = Path(te.__file__).resolve().parent / "refused-tables"
    with pytest.raises(ValueError, match="outside checkout"):
        te.export(inside, SEED, "truthful")
    assert not os.path.lexists(inside)
    with tempfile.TemporaryDirectory(
        dir="/tmp", prefix="recall-temporal-git-"
    ) as workspace:
        os.mkdir(Path(workspace) / ".git")
        with pytest.raises(ValueError, match="outside git"):
            te.export(Path(workspace) / "tables", SEED, "truthful")
        assert os.listdir(workspace) == [".git"]


def test_failed_write_and_raced_output_leave_no_partial_directory(monkeypatch):
    real = te.write_exclusive
    calls = []

    def failing(path: Path, data: dict[str, Any], mode: int) -> None:
        calls.append(path.name)
        if len(calls) == 3:
            raise OSError("injected write failure")
        real(path, data, mode)

    with tempfile.TemporaryDirectory(
        dir="/tmp", prefix="recall-temporal-fault-"
    ) as workspace:
        root = Path(workspace)
        monkeypatch.setattr(te, "write_exclusive", failing)
        with pytest.raises(OSError, match="injected"):
            te.export(root / "tables", SEED, "truthful")
        assert calls == list(FILES[:3])
        assert os.listdir(root) == []
        monkeypatch.setattr(te, "write_exclusive", real)
        tables = te.tables

        def racing(seed: int, sender: str) -> dict[str, list[World]]:
            os.mkdir(root / "tables", 0o700)
            (root / "tables" / "mine").write_bytes(b"kept")
            return tables(seed, sender)

        monkeypatch.setattr(te, "tables", racing)
        with pytest.raises(FileExistsError):
            te.export(root / "tables", SEED, "truthful")
        assert os.listdir(root) == ["tables"]
        assert os.listdir(root / "tables") == ["mine"]


def test_cli_reproduces_the_bundle_and_refuses_reuse(exported, capsys):
    _, summary, _ = exported
    with tempfile.TemporaryDirectory(
        dir="/tmp", prefix="recall-temporal-cli-"
    ) as workspace:
        out = Path(workspace) / "again"
        code = te.main(["--seed", str(SEED), "--sender", "truthful", "--out", str(out)])
        printed = json.loads(capsys.readouterr().out)
        assert code == 0
        assert list(printed["files"]) == list(FILES)
        assert {key: value for key, value in printed.items() if key != "path"} == {
            key: value for key, value in summary.items() if key != "path"
        }
        assert (
            te.main(["--seed", str(SEED), "--sender", "truthful", "--out", str(out)])
            == 2
        )
        captured = capsys.readouterr()
        assert captured.out == "" and "already exists" in captured.err
        with pytest.raises(SystemExit):
            te.main(["--seed", str(SEED), "--sender", "loud", "--out", str(out) + "-x"])
        assert not os.path.lexists(str(out) + "-x")
