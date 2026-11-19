"""Check portable exports from synthetic receipts, not real Harbor trials."""

import hashlib
import importlib
import json
from uuid import uuid4

import pytest
from test_interactive_driver import (
    ADAPTER,
    _case,
    _never_launch,
    _record_attempt,
    _register,
    _world,
)
from test_interactive_driver import driver as driver
from test_interactive_driver import reviewed as reviewed


@pytest.fixture
def exporter(driver, monkeypatch):
    monkeypatch.setattr(driver.subprocess, "run", _never_launch)
    return importlib.import_module("export_episodes")


def _json_bytes(document):
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _digest(document):
    return hashlib.sha256(_json_bytes(document)).hexdigest()


@pytest.mark.parametrize(
    ("null", "actions"),
    [(False, ["query:0", "query:2", "choose:2"]), (True, ["query:0", None])],
    ids=["synthetic-multi-round", "synthetic-null-choice"],
)
def test_exports_m1_views_and_recorded_actions(
    exporter, driver, reviewed, tmp_path, null, actions
):
    private = tmp_path / "registered"
    case = _case(driver, "2/1", null=null)
    plan = _register(driver, reviewed, private, {"2/1": case})
    native_id = uuid4()
    _record_attempt(driver, reviewed, private, plan, case, identifier=native_id.hex)
    moved = tmp_path / "moved"
    private.rename(moved)
    out = tmp_path / "portable"

    exporter.export_episodes([moved], out)

    data = (out / "episodes.jsonl").read_bytes()
    episode = json.loads(data)
    assert data == _json_bytes(episode) + b"\n"
    episode_hash = episode.pop("episode_sha256")
    world = _world(driver, case)
    views = [
        {
            "role": "m1",
            "mode": "cursor",
            "horizon": case["horizon"],
            "duration": case["duration"],
            "slots_per_day": case["slots_per_day"],
            "busy": case["members"]["m1"],
        },
        *(
            {key: row[key] for key in ("round", "bound", "replies")}
            for row in world["rounds"]
        ),
    ]
    view_hashes = [world["initial_view_sha256"]["m1"]] + [
        row["bundle_sha256"] for row in world["rounds"]
    ]
    assert view_hashes == [_digest(view) for view in views]
    assert view_hashes[-1] == world["choice_view_sha256"]
    assert episode == {
        "schema": "seatlab.episode/v1",
        "id": "calendar:2-1:cursor:m1",
        "case_sha256": _digest(case),
        "role": "m1",
        "native_trial_id": str(native_id),
        "closed": True,
        "frames": [
            {
                "turn": index,
                "basis": "bound",
                "view": view,
                "view_sha256": digest,
                "observed": {"action": action},
            }
            for index, (view, digest, action) in enumerate(
                zip(views, view_hashes, actions, strict=True), 1
            )
        ],
    }
    assert episode["closed"] is True
    assert episode_hash == _digest(episode)
    assert (out / "UPSTREAM-LICENSE.txt").read_bytes() == (
        ADAPTER / "UPSTREAM-LICENSE.txt"
    ).read_bytes()


def test_multi_root_sort_and_safe_index(exporter, driver, reviewed, tmp_path):
    first = tmp_path / "private-a"
    second = tmp_path / "private-b"
    first_ids = ("4/1", "2/10", "2/2")
    second_ids = ("3/1", "5/1", "2/1")
    cases = {case_id: _case(driver, case_id) for case_id in (*first_ids, *second_ids)}
    first_plan = _register(
        driver, reviewed, first, {case_id: cases[case_id] for case_id in first_ids}
    )
    second_plan = _register(
        driver, reviewed, second, {case_id: cases[case_id] for case_id in second_ids}
    )
    ids = {case_id: str(uuid4()) for case_id in ("2/1", "2/2", "2/10", "3/1")}

    def bad_world(world, _result):
        world["choice_view_sha256"] = "a" * 64

    def failed_trial(_world, result):
        result["exception_info"] = {"private-detail": str(second)}

    _record_attempt(
        driver, reviewed, first, first_plan, cases["2/10"], identifier=ids["2/10"]
    )
    _record_attempt(
        driver,
        reviewed,
        first,
        first_plan,
        cases["2/2"],
        identifier=ids["2/2"],
        modify=bad_world,
    )
    _record_attempt(
        driver, reviewed, second, second_plan, cases["2/1"], identifier=ids["2/1"]
    )
    _record_attempt(
        driver,
        reviewed,
        second,
        second_plan,
        cases["3/1"],
        identifier=ids["3/1"],
        modify=failed_trial,
    )
    (second / "cells" / "5-1-cursor").mkdir()
    out = tmp_path / "portable"
    reversed_out = tmp_path / "portable-reversed"
    exporter.export_episodes([first, second], out)
    assert (
        exporter.main(
            [
                "--native",
                str(second),
                "--native",
                str(first),
                "--out",
                str(reversed_out),
            ]
        )
        == 0
    )

    filenames = {"episodes.jsonl", "index.json", "UPSTREAM-LICENSE.txt"}
    assert {path.name for path in out.iterdir()} == filenames
    for name in filenames:
        data = (out / name).read_bytes()
        assert data == (reversed_out / name).read_bytes()
        assert str(tmp_path).encode() not in data
        assert b"/not/needed" not in data
        assert b"private-detail" not in data
    assert [
        json.loads(line)["id"]
        for line in (out / "episodes.jsonl").read_bytes().splitlines()
    ] == ["calendar:2-1:cursor:m1", "calendar:2-10:cursor:m1"]
    assert json.loads((out / "index.json").read_bytes()) == [
        {
            "case_id": "2/1",
            "status": "verified",
            "native_trial_id": ids["2/1"],
            "episode_id": "calendar:2-1:cursor:m1",
        },
        {
            "case_id": "2/2",
            "status": "invalid",
            "reason": "invalid interactive world evidence",
            "native_trial_id": ids["2/2"],
        },
        {
            "case_id": "2/10",
            "status": "verified",
            "native_trial_id": ids["2/10"],
            "episode_id": "calendar:2-10:cursor:m1",
        },
        {
            "case_id": "3/1",
            "status": "failed",
            "reason": "native trial returned an error",
            "native_trial_id": ids["3/1"],
        },
        {"case_id": "4/1", "status": "unattempted"},
        {"case_id": "5/1", "status": "incomplete"},
    ]


def test_duplicate_unattempted_case_fails(exporter, driver, reviewed, tmp_path):
    roots = [tmp_path / name for name in ("a", "b")]
    for root in roots:
        _register(driver, reviewed, root, {"2/1": _case(driver, "2/1")})
    out = tmp_path / "portable"

    with pytest.raises(ValueError, match="duplicate case ID"):
        exporter.export_episodes(roots, out)
    assert not out.exists()


@pytest.mark.parametrize("same_root", [False, True], ids=["across-roots", "same-root"])
def test_duplicate_canonical_uuid_fails_even_when_unverified(
    exporter, driver, reviewed, tmp_path, same_root
):
    first = tmp_path / "a"
    second = first if same_root else tmp_path / "b"
    one, two = (_case(driver, case_id) for case_id in ("2/1", "2/2"))
    first_cases = {"2/1": one, "2/2": two} if same_root else {"2/1": one}
    first_plan = _register(driver, reviewed, first, first_cases)
    second_plan = (
        first_plan if same_root else _register(driver, reviewed, second, {"2/2": two})
    )
    identifier = uuid4()

    def bad_world(world, _result):
        world["choice"] = 3

    _record_attempt(
        driver, reviewed, first, first_plan, one, identifier=str(identifier)
    )
    _record_attempt(
        driver,
        reviewed,
        second,
        second_plan,
        two,
        identifier=identifier.hex,
        modify=bad_world,
    )
    out = tmp_path / "portable"

    with pytest.raises(ValueError, match="duplicate native trial UUID"):
        exporter.export_episodes([first] if same_root else [first, second], out)
    assert not out.exists()


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("native-world", "verified native evidence changed"),
        ("resealed-world", "verified native evidence changed"),
        ("attempt", "verified native evidence changed"),
        ("task-case", "verified native evidence changed"),
        ("new-attempt", "verified native evidence changed"),
        ("source.json", "native registration changed"),
        ("plan.json", "native registration changed"),
    ],
)
def test_evidence_swap_aborts_without_output(
    exporter, driver, reviewed, tmp_path, monkeypatch, change, error
):
    private = tmp_path / "private"
    case = _case(driver, "2/1")
    plan = _register(driver, reviewed, private, {"2/1": case})
    cell, native = driver.study._paths(private, plan["cells"][0])
    if change != "new-attempt":
        _record_attempt(driver, reviewed, private, plan, case)
    original_collect = driver.collect

    def collect_then_change(root):
        if change == "new-attempt":
            _record_attempt(driver, reviewed, private, plan, case)
        report = original_collect(root)
        assert report["cells"][0]["status"] == "verified"
        attempt = cell / "attempt.json"
        if change in {"native-world", "resealed-world"}:
            world = native / driver.NATIVE_FILES["world"]
            changed = world.read_bytes() + b"\n"
            world.write_bytes(changed)
            if change == "resealed-world":
                receipt = json.loads(attempt.read_bytes())
                receipt["native_sha256"]["world"] = hashlib.sha256(changed).hexdigest()
                attempt.write_bytes(_json_bytes(receipt) + b"\n")
        elif change != "new-attempt":
            path = {
                "attempt": attempt,
                "task-case": cell / "task" / "environment" / "case.json",
                "source.json": private / "source.json",
                "plan.json": private / "plan.json",
            }[change]
            path.write_bytes(path.read_bytes() + b"\n")
        return report

    monkeypatch.setattr(driver, "collect", collect_then_change)
    out = tmp_path / "portable"
    with pytest.raises(ValueError, match=error) as exc:
        exporter.export_episodes([private], out)
    assert str(tmp_path) not in str(exc.value)
    assert not out.exists()


def test_missing_license_fails_before_creating_output(
    exporter, driver, reviewed, tmp_path, monkeypatch
):
    private = tmp_path / "private"
    _register(driver, reviewed, private, {"2/1": _case(driver, "2/1")})
    original_read = driver._read_file

    def read_without_license(root, name):
        if name == "UPSTREAM-LICENSE.txt":
            raise FileNotFoundError(str(root / name))
        return original_read(root, name)

    monkeypatch.setattr(driver, "_read_file", read_without_license)
    out = tmp_path / "portable"
    with pytest.raises(ValueError, match="^upstream license is missing") as exc:
        exporter.export_episodes([private], out)
    assert str(ADAPTER) not in str(exc.value)
    assert not out.exists()


def test_no_verified_cases_produces_empty_jsonl(exporter, driver, reviewed, tmp_path):
    private = tmp_path / "private"
    _register(driver, reviewed, private, {"2/1": _case(driver, "2/1")})
    out = tmp_path / "portable"

    exporter.export_episodes([private], out)

    assert (out / "episodes.jsonl").read_bytes() == b""
    assert json.loads((out / "index.json").read_bytes()) == [
        {"case_id": "2/1", "status": "unattempted"}
    ]


def test_bad_registration_is_a_safe_cli_error(
    exporter, driver, reviewed, tmp_path, capsys
):
    private = tmp_path / "private"
    _register(driver, reviewed, private, {"2/1": _case(driver, "2/1")})
    (private / "source.json").unlink()
    out = tmp_path / "portable"

    with pytest.raises(SystemExit) as exc:
        exporter.main(["--native", str(private), "--out", str(out)])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "invalid native root registration" in captured.err
    assert str(tmp_path) not in captured.err
    assert not out.exists()


@pytest.mark.parametrize("broken_link", [False, True], ids=["directory", "broken-link"])
def test_existing_output_is_never_overwritten(
    exporter, driver, reviewed, tmp_path, broken_link
):
    private = tmp_path / "private"
    _register(driver, reviewed, private, {"2/1": _case(driver, "2/1")})
    out = tmp_path / "portable"
    target = tmp_path / "missing-target"
    if broken_link:
        out.symlink_to(target, target_is_directory=True)
    else:
        out.mkdir()
        (out / "episodes.jsonl").write_bytes(b"keep\n")

    with pytest.raises(ValueError, match="output must be a new directory"):
        exporter.export_episodes([private], out)
    if broken_link:
        assert out.readlink() == target and not target.exists()
    else:
        assert (out / "episodes.jsonl").read_bytes() == b"keep\n"
        assert not (out / "index.json").exists()
