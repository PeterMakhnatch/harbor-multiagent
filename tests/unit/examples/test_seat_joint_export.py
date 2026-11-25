import contextlib
import hashlib
import io
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import examples.seatlab.joint_handoffs.export as exporting
from examples.seatlab.decision_boundary.__main__ import main as boundary
from examples.seatlab.decision_boundary.core import World
from examples.seatlab.decision_boundary.inputs import SCHEMA, census, read_input
from examples.seatlab.decision_boundary.joint import ARMS, qualify

START = 7
FILES = tuple(f"{arm}.json" for arm in ARMS)
WORLD_KEYS = (
    b"registry-",
    b"bay-original",
    b"bay-swapped",
    b"swapped",
    b"EXHIBIT_",
    b"registry_map",
    b"bay_map",
)
Export = tuple[Path, dict[str, Any], str]


@pytest.fixture(scope="module")
def exported() -> Iterator[Export]:
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="jexport-") as workspace:
        out = Path(workspace) / "tables"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            summary = exporting.export(out, START)
        yield out.resolve(), summary, printed.getvalue()


def _arms(out: Path) -> dict[str, list[World]]:
    arms = {}
    for arm, name in zip(ARMS, FILES, strict=True):
        scale, worlds = census(read_input(str(out / name)))
        assert scale == 1
        arms[arm] = worlds
    return arms


def _payload(request: bytes) -> dict[str, Any]:
    envelope = json.loads(request)
    assert set(envelope) == {"schema", "prompt"}
    assert envelope["schema"] == "seatlab.seat-turn/v1"
    _, marker, body = envelope["prompt"].partition("Input JSON: ")
    assert marker
    payload = json.loads(body)
    assert set(payload) == {"instruction", "view", "previous_action"}
    return payload


def _active(cards: list[dict[str, Any]], key: str, epoch: int) -> str:
    values = [
        card["value"]
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    assert len(values) == 1
    return values[0]


def _routes(view: dict[str, Any]) -> dict[str, str]:
    order = view["order"]
    return {
        item: _active(
            view["depot_file"],
            _active(view["registrar_file"], item, order["epoch"]),
            order["epoch"],
        )
        for item in order["items"]
    }


def _dispatch(view: dict[str, Any], label: str) -> dict[str, str]:
    items, bays = sorted(view["order"]["items"]), sorted(view["bays"])
    left, right = (int(index) for index in label.split(":"))
    return dict(zip(items, (bays[left], bays[right]), strict=True))


def _winner(row: World) -> str:
    winners = [label for label, score in row.scores.items() if score == 1]
    assert len(winners) == 1
    return winners[0]


def test_export_publishes_private_tables_and_compact_summary(exported):
    out, summary, printed = exported
    assert printed == ""
    assert sorted(entry.name for entry in out.parent.iterdir()) == ["tables"]
    assert sorted(entry.name for entry in out.iterdir()) == sorted(FILES)
    assert stat.S_IMODE(os.lstat(out).st_mode) == 0o700
    digests = {}
    for name in FILES:
        info = os.lstat(out / name)
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        digests[name] = (
            "sha256:" + hashlib.sha256((out / name).read_bytes()).hexdigest()
        )
    assert summary["files"] == digests
    assert summary["access"] == "private_benchmark_author"
    assert summary["path"] == str(out)
    assert summary["arms"] == list(ARMS) and summary["input_schema"] == SCHEMA
    assert (summary["worlds_per_arm"], summary["actions_per_world"]) == (4, 64)
    assert summary["observation_classes"] == {
        "neither": 1,
        "first": 2,
        "second": 2,
        "both": 4,
    }
    assert type(summary["development_seed"]) is int
    assert summary["development_seed"] >= START
    text = json.dumps(summary)
    assert all(word not in text for word in ("observation_b64", "scores", "prompt"))
    assert len(text) < 2048


def test_export_round_trips_through_generic_census_and_qualify(exported, capsys):
    out, _, _ = exported
    for name in FILES:
        root = json.loads((out / name).read_bytes())
        assert set(root) == {"schema", "scale", "worlds"} and root["schema"] == SCHEMA
        assert all(row["mass"] > 0 for row in root["worlds"])
    result = qualify(_arms(out), 1)
    assert result["values"] == {
        "neither": "1/2",
        "first": "1/2",
        "second": "1/2",
        "both": "1",
    }
    assert result["interaction"] == "1/2"
    assert result["each_source_adds_value_given_other"] is True
    code = boundary(
        ["joint", "--files", *(str(out / name) for name in FILES)]
        + ["--expect-each-needed", "yes"]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["expectation_met"] is True
    assert report["qualification"] == result


def test_requests_hide_world_keys_and_baseline_hides_physical_winners(exported):
    out, _, _ = exported
    arms = _arms(out)
    for rows in arms.values():
        for row in rows:
            assert all(key not in row.observation for key in WORLD_KEYS)
            assert row.name.encode("ascii") not in row.observation
    for row in arms["both"]:
        view = _payload(row.observation)["view"]
        assert _dispatch(view, _winner(row)) == _routes(view)
    baseline = arms["neither"]
    assert len({row.observation for row in baseline}) == 1
    view = _payload(baseline[0].observation)["view"]
    assert all(set(card["value"]) == {"?"} for card in view["depot_file"])
    dispatches = {
        json.dumps(_dispatch(view, _winner(row)), sort_keys=True) for row in baseline
    }
    assert len(dispatches) >= 2


def test_export_refuses_existing_checkout_and_git_outputs(exported):
    out, _, _ = exported
    before = {entry.name: entry.read_bytes() for entry in out.iterdir()}
    with pytest.raises(ValueError, match="already exists"):
        exporting.export(out, START)
    assert {entry.name: entry.read_bytes() for entry in out.iterdir()} == before
    inside = Path(exporting.__file__).resolve().parent / "refused-tables"
    with pytest.raises(ValueError, match="outside checkout"):
        exporting.export(inside, START)
    assert not os.path.lexists(inside)
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="jexport-git-") as workspace:
        os.mkdir(Path(workspace) / ".git")
        with pytest.raises(ValueError, match="outside git"):
            exporting.export(Path(workspace) / "tables", START)
        assert os.listdir(workspace) == [".git"]


def _replay(out: Path, monkeypatch, summary: dict[str, Any]) -> dict[str, list[World]]:
    arms = _arms(out)
    seed = summary["development_seed"]
    monkeypatch.setattr(exporting, "author_tables", lambda start: (seed, arms))
    return arms


def test_failed_write_and_raced_output_leave_no_partial_directory(
    exported, monkeypatch
):
    out, summary, _ = exported
    _replay(out, monkeypatch, summary)
    real = exporting.write_exclusive
    calls = []

    def failing(path: Path, data: dict[str, Any], mode: int) -> None:
        calls.append(path.name)
        if len(calls) == 3:
            raise OSError("injected write failure")
        real(path, data, mode)

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="jexport-fault-") as workspace:
        root = Path(workspace)
        monkeypatch.setattr(exporting, "write_exclusive", failing)
        with pytest.raises(OSError, match="injected"):
            exporting.export(root / "tables", START)
        assert calls == list(FILES[:3])
        assert os.listdir(root) == []
        monkeypatch.setattr(exporting, "write_exclusive", real)
        tables = exporting.author_tables

        def racing(start: int) -> tuple[int, dict[str, list[World]]]:
            os.mkdir(root / "tables", 0o700)
            (root / "tables" / "mine").write_bytes(b"kept")
            return tables(start)

        monkeypatch.setattr(exporting, "author_tables", racing)
        with pytest.raises(FileExistsError):
            exporting.export(root / "tables", START)
        assert os.listdir(root) == ["tables"]
        assert os.listdir(root / "tables") == ["mine"]


def test_replayed_tables_reproduce_the_same_file_digests(exported, monkeypatch):
    out, summary, _ = exported
    _replay(out, monkeypatch, summary)
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="jexport-again-") as workspace:
        again = exporting.export(Path(workspace) / "tables", START)
    assert again["files"] == summary["files"]
    assert {key: value for key, value in again.items() if key != "path"} == {
        key: value for key, value in summary.items() if key != "path"
    }
