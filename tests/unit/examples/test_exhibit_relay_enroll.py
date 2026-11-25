import hashlib
import json
import stat
from collections import Counter
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.exhibit_enroll import enroll
from examples.seatlab.exhibit_packet import audit
from examples.seatlab.exhibit_pairs import analyze
from examples.seatlab.exhibit_wire import encode_request, render_turn

_TASK = "examples.tasks.exhibit-relay"
make_case = import_module(f"{_TASK}.environment.exhibit_relay.cases").make_case
Session = import_module(f"{_TASK}.environment.exhibit_relay.engine").Session
act = import_module(f"{_TASK}.solution.policies").act
_CHECKOUT = (
    Path(import_module("examples.seatlab.exhibit_enroll").__file__).resolve().parents[2]
)
_ARMS = ("masked", "bundled")


def _commitment(world: dict[str, Any]) -> str:
    body = json.dumps(world, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(body.encode("ascii")).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _roster(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read(root / "manifest.json")
    schedule = _read(root / "schedule.json")
    assert manifest["schema"] == "seatlab.exhibit-pairs/v1"
    assert schedule["schema"] == "seatlab.exhibit-schedule/v1"
    assert manifest["profile"] == "coupling"
    assert set(manifest["arms"]) == set(_ARMS)
    cases = schedule["cases"]
    pairs = manifest["pairs"]
    assert len(cases) == len(pairs) == 8
    by_id = {pair["id"]: pair for pair in pairs}
    assert len(by_id) == 8
    assert {case["id"] for case in cases} == by_id.keys()
    assert Counter((c["layout"], tuple(c["order"])) for c in cases) == {
        (layout, order): 2 for layout in ("a", "b") for order in (_ARMS, _ARMS[::-1])
    }
    assert len({c["seed"] for c in cases}) == 8
    paths = set()
    commitments = set()
    for case in cases:
        seed = case["seed"]
        assert type(seed) is int and 0 <= seed < 2**63
        world = make_case(seed, "coupling", layout=case["layout"])
        pair = by_id[case["id"]]
        assert pair["case_commitment"] == _commitment(world)
        commitments.add(pair["case_commitment"])
        for side in ("left", "right"):
            relative = Path(pair[side])
            assert not relative.is_absolute() and ".." not in relative.parts
            directory = root / relative
            assert directory.resolve().is_relative_to(root.resolve())
            assert directory != root and directory not in paths
            assert directory.parent.is_dir() and not directory.exists()
            assert not directory.is_symlink()
            paths.add(directory)
    assert len(commitments) == 8
    assert not any(a in b.parents for a in paths for b in paths)
    for directory in [root, *(p for p in root.rglob("*") if p.is_dir())]:
        assert not directory.is_symlink()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    return manifest, schedule


def test_development_enrollment_replays_and_preserves_denominator(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    enroll(first, 8, development_seed=419)
    enroll(second, 8, development_seed=419)
    _roster(first)
    _roster(second)
    files = {p.relative_to(first) for p in first.rglob("*") if p.is_file()}
    assert files == {p.relative_to(second) for p in second.rglob("*") if p.is_file()}
    for relative in sorted(files):
        a = (first / relative).read_bytes().replace(str(first).encode(), b"<output>")
        b = (second / relative).read_bytes().replace(str(second).encode(), b"<output>")
        assert a == b, relative
    report = analyze(first / "manifest.json")
    assert report["pair_count"] == 8
    for counts in report["outcome_counts"].values():
        assert counts["complete"] == 0
        assert sum(counts.values()) == 8
    assert report["invalid_or_missing_count"] == 16
    for metric in report["metrics"].values():
        assert metric["difference_bounds"] == ["-1", "1"]
        assert metric["coverage"]["paired"] == "0"


def test_enrollment_rejects_overwrite_and_checkout(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    enroll(root, 8, development_seed=7)
    original = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    with pytest.raises((ValueError, FileExistsError)):
        enroll(root, 8, development_seed=8)
    assert original == {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    alias = tmp_path / "checkout"
    alias.symlink_to(_CHECKOUT, target_is_directory=True)
    forbidden = alias / f".enrollment-probe-{tmp_path.name}"
    assert not forbidden.exists()
    with pytest.raises(ValueError):
        enroll(forbidden, 8, development_seed=7)
    assert not forbidden.exists()


def _expected_bundles(case: dict[str, Any], masked: bool) -> list[dict[str, Any]]:
    result = []
    for brief in case["briefs"]:
        epoch = brief["epoch"]
        keys = []
        for item in brief["items"]:
            matches = [
                c
                for c in case["registrar"]
                if c["key"] == item and c["start"] <= epoch <= c["end"]
            ]
            assert len(matches) == 1
            keys.append(matches[0]["value"])
        result.append(
            {
                "epoch": epoch,
                "keys": sorted(keys),
                "weight": 0 if masked else brief["weight"],
            }
        )
    return sorted(result, key=lambda row: (row["epoch"], row["keys"]))


def _view(case: dict[str, Any], arm: str) -> dict[str, Any]:
    session = Session(case, arm)
    nomination = act(session.view("loader"), "portfolio")
    assert set(nomination["slips"]) == {b["id"] for b in case["briefs"]}
    session.apply("loader", nomination)
    filing = act(session.view("registrar"), "portfolio")
    assert len(filing["ids"]) == len(set(filing["ids"])) == 4
    session.apply("registrar", filing)
    return json.loads(json.dumps(session.view("depot"), sort_keys=True))


def _wire(instruction: str, view: dict[str, Any]) -> bytes:
    return encode_request(render_turn(instruction, view, None))


def test_layout_changes_world_and_only_expected_packet_digits() -> None:
    instruction = (
        _CHECKOUT / "examples/tasks/exhibit-relay/instructions/depot.md"
    ).read_text()
    for seed in (0, 1, 7, 19, 419, 2**63 - 1):
        a, b = (make_case(seed, "coupling", layout=layout) for layout in ("a", "b"))
        assert _commitment(a) != _commitment(b)
        assert {k: v for k, v in a.items() if k not in ("briefs", "realized")} == {
            k: v for k, v in b.items() if k not in ("briefs", "realized")
        }
        for left, right in zip(a["briefs"], b["briefs"], strict=True):
            assert {k: v for k, v in left.items() if k != "weight"} == {
                k: v for k, v in right.items() if k != "weight"
            }
            assert {left["weight"], right["weight"]} == {1, 4}
        hidden = []
        for world in (a, b):
            masked, bundled = (_view(world, arm) for arm in _ARMS)
            expected_hidden = _expected_bundles(world, True)
            expected_visible = _expected_bundles(world, False)
            assert masked["bundles"] == expected_hidden
            assert bundled == {**masked, "bundles": expected_visible}
            assert (
                masked["briefs"]
                == masked["registrar_file"]
                == masked["depot_file"]
                == []
            )
            assert masked["order"] is None
            before, after = _wire(instruction, masked), _wire(instruction, bundled)
            assert before != after and len(before) == len(after)
            edits = [(x, y) for x, y in zip(before, after, strict=True) if x != y]
            assert Counter(edits) == {(ord("0"), ord("1")): 2, (ord("0"), ord("4")): 2}
            assert after == _wire(instruction, {**masked, "bundles": expected_visible})
            hidden.append(before)
        assert hidden[0] == hidden[1]
        assert isinstance(audit(seed, instruction), dict)
