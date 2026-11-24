import json
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from examples.native_study import (
    TASK_FILES,
    native,
    record_task_files,
    task_files_digest,
    task_identity,
)
from examples.ship_handover import (
    collect,
    design,
    study,
    transfer,
    transfer_contract,
    transfer_snapshot,
)
from examples.ship_handover.arm_inputs import REPO, ROLE_PACKAGE_DIGEST
from examples.ship_inflight.materialize import BASE, materialize
from harbor.models.task.task import Task
from harbor.publisher.packager import Packager

RELEASED = REPO / "examples" / "ship_handover" / "samples" / "two-worlds"
ROSTER = REPO / "examples" / "ship_handover" / "samples" / "control-team.json"
WORLDS = ("stock", "inflight")
TEAM = "recovery"
ROLE_FILES = ("instructions/outgoing.md", "instructions/relief.md")
STOCK_ROLE = {
    "name": "harbor/ship-handover",
    "digest": "sha256:001d1b8d1b3355be39b2aec51680f3b867e1932b9141e792862894662bfd27ad",
    "checksum": "4bc30f977321363eb46432239b5df43dcae88cce405aaef671bf978b9283bf40",
}
STOCK_ROLE_TREE = (
    "sha256:89d5678e934a321abd2ca5f560293f1157eeab5702c4ff532ccf7e34dcbbecec"
)
INFLIGHT_LEGACY = {
    "name": "harbor/ship-inflight",
    "digest": "sha256:9a712fac1b44e4b70c91cb717b9b2cb2cbc2a3a43198839cc4f75be38c1c713e",
    "checksum": "411796f5aee862b63b0c3a9c1a6a43149813422db5d715d8634ab865f09f8bda",
}
INFLIGHT_ROLE = {
    **INFLIGHT_LEGACY,
    "digest": "sha256:0950daefa93ba321d4936cbc2d600b8eb8b3980805f1ba73efd88fa11b921218",
}
LEGACY_TREE = "sha256:cb5294288f62b79fe69c780c2e5287bfef0dbb7b05fb3e17b03f1ebbd4d0d94c"
ROLE_TREE = "sha256:f94b7de0f0910757e88e7f0d302f3f4afbd48db1ea25246421ba042f09a08817"
EXPECTED = {
    "stock": (STOCK_ROLE, STOCK_ROLE_TREE, 12),
    "inflight": (INFLIGHT_ROLE, ROLE_TREE, 14),
}
UNKNOWN_SPAN = {"low": "0/1", "high": "1/1"}
GENERATION_MIX = "different task package generations"


class Fresh(NamedTuple):
    task: Path
    staged: dict[str, Any]
    paths: dict[str, Path]
    summaries: dict[str, dict[str, Any]]
    task_dirs: dict[str, Path]
    task_args: dict[str, Path | None]
    workspaces: dict[str, Path]


@pytest.fixture(scope="module")
def fresh(tmp_path_factory: pytest.TempPathFactory) -> Fresh:
    root = tmp_path_factory.mktemp("generations").resolve()
    task = root / "ship-inflight"
    staged = materialize(task)
    assert study.STOCK.task_dir is not None
    task_dirs = {"stock": study.STOCK.task_dir, "inflight": task}
    task_args: dict[str, Path | None] = {"stock": None, "inflight": task}
    paths: dict[str, Path] = {}
    summaries: dict[str, dict[str, Any]] = {}
    workspaces: dict[str, Path] = {}
    for world in WORLDS:
        extra = [] if task_args[world] is None else ["--task", str(task)]
        paths[world] = root / f"{world}.json"
        summaries[world] = design.register(
            design.parser().parse_args(
                ["--world", world, "--team", TEAM, "--participants", str(ROSTER)]
                + ["--blocks", "1", "--out", str(paths[world]), *extra]
            )
        )
        workspaces[world] = root / f"{world}-w0"
        written = study.emit(
            study.parser().parse_args(
                ["emit", str(paths[world]), "--out", str(workspaces[world])]
                + ["--shard", "0/1", *extra]
            )
        )
        assert len(written) == 4
    return Fresh(task, staged, paths, summaries, task_dirs, task_args, workspaces)


def refuse_native(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an archived reader consulted the installed packager")

    monkeypatch.setattr(Packager, "compute_content_hash", staticmethod(refuse))
    monkeypatch.setattr(Task, "__init__", refuse)


def released_argv(team: str) -> list[str]:
    argv = [str(RELEASED / f"{world}-{team}.json") for world in WORLDS]
    for world in WORLDS:
        for worker in ("w0", "w1"):
            packet = RELEASED / f"{world}-{team}-{worker}.json"
            argv += [f"--{world}-packet", str(packet)]
    return argv


def test_fresh_pair_binds_the_role_aware_packages_with_archived_files(fresh: Fresh):
    harbor = native()
    released = study.load_registration(RELEASED / "stock-recovery.json")
    registrations = {w: study.load_registration(fresh.paths[w]) for w in WORLDS}
    for world, (identity, tree, count) in EXPECTED.items():
        spec, task_dir = study.SPECS[world], fresh.task_dirs[world]
        registration = registrations[world]
        assert registration["task"] == identity
        assert task_identity(harbor, task_dir, identity["name"]) == identity
        content, files = Packager.compute_content_hash(task_dir)
        names = {f.relative_to(task_dir.resolve()).as_posix() for f in files}
        assert "sha256:" + content == identity["digest"]
        assert len(names) == count and set(ROLE_FILES) <= names
        assert task_files_digest(task_dir, identity) == tree
        assert study.accepted_task(spec, identity, tree)
        assert registration["teams"] == released["teams"]
        assert sorted((c[spec.axis], c["channel"]) for c in registration["cells"]) == (
            sorted((s, c) for s in spec.states for c in study.CHANNELS)
        )
    pin = json.loads(study.INFLIGHT_PIN.read_text())
    assert pin["task"] == INFLIGHT_LEGACY and pin["task_files"] == LEGACY_TREE
    assert fresh.staged["files"] == pin["files"]
    assert STOCK_ROLE == {**BASE, "digest": ROLE_PACKAGE_DIGEST}
    assert INFLIGHT_ROLE["digest"] == study.INFLIGHT_ROLE_DIGEST
    assert fresh.summaries["inflight"]["task_files"] == ROLE_TREE
    transfer_contract.check_pair(registrations["stock"], registrations["inflight"])


def test_emit_stamps_each_world_with_its_own_tree(fresh: Fresh):
    for world, (_, tree, _) in EXPECTED.items():
        workspace = fresh.workspaces[world]
        registration = study.load_registration(fresh.paths[world])
        assert (workspace / TASK_FILES).read_text() == tree + "\n"
        assert not (workspace / "trials").exists()
        configs = workspace / "configs"
        assert sorted(p.name for p in configs.iterdir()) == sorted(
            cell["ticket"] + ".json" for cell in registration["cells"]
        )
        for cell in registration["cells"]:
            config = json.loads((configs / f"{cell['ticket']}.json").read_text())
            assert config == {
                "task": {"path": str(fresh.task_dirs[world])},
                "trial_name": cell["ticket"],
                "trials_dir": str(workspace / "trials"),
                "environment": {
                    "type": "docker",
                    "delete": True,
                    "env": study.world_env(cell),
                },
                "participants": registration["teams"][TEAM],
            }


def test_collect_reads_a_fresh_enrollment_as_unreturned(fresh: Fresh):
    for world, (identity, _, _) in EXPECTED.items():
        report = collect.collect(
            fresh.paths[world], [fresh.workspaces[world]], fresh.task_args[world]
        )
        assert report["task"] == identity
        assert report["registered"] == 4 and report["valid"] == 0
        assert report["statuses"] == {
            status: 4 if status == "no_result" else 0 for status in report["statuses"]
        }
        assert report["directories"] == {
            "workspaces": 1,
            "workspaces_with_trials": 0,
            "claimed_entries": 0,
            "unregistered_entries": 0,
            "relocated_workspaces": 0,
            "unanchored_workspaces": 0,
            "unstamped_workspaces": 0,
        }
        assert all(row["grade"] is None for row in report["cells"])
        assert all(group["valid"] == 0 for group in report["groups"])


def test_snapshot_and_transfer_keep_every_unreturned_ticket_missing(
    fresh: Fresh, tmp_path: Path
):
    packets: dict[str, list[Path]] = {}
    for world in WORLDS:
        registration = study.load_registration(fresh.paths[world])
        packet = transfer_snapshot.snapshot(
            fresh.paths[world], [fresh.workspaces[world]], fresh.task_args[world]
        )
        assert packet == {
            "schema": transfer_contract.PACKET_SCHEMA,
            "registration_sha256": transfer_contract.registration_digest(registration),
            "rows": [],
        }
        packets[world] = [tmp_path / f"{world}-packet-0.json"]
        packets[world][0].write_text(transfer_snapshot.render(packet))
    pair = (fresh.paths["stock"], fresh.paths["inflight"])
    claimed = transfer.transfer(*pair, packets)
    bare = transfer.transfer(*pair, {world: [] for world in WORLDS})
    for world in WORLDS:
        side = claimed["worlds"][world]
        assert len(side.pop("packets")) == 1
        assert bare["worlds"][world].pop("packets") == []
        totals = side["totals"]
        counted = ("registered", "reported", "missing", "eligible", "unknown")
        assert [totals[key] for key in counted] == [4, 0, 4, 0, 4]
        assert totals["physical_known"] == 0
        assert all(s["positive"] == s["zero"] == 0 for s in side["strata"])
        assert all(s["success"] == UNKNOWN_SPAN for s in side["strata"])
    assert claimed == bare
    assert claimed["transfer"] == {
        state: {"interval": {"low": "-2/1", "high": "2/1"}, "comparison": "unresolved"}
        for state in transfer.STATES
    }


def test_a_claimed_zero_differs_from_a_missing_return(fresh: Fresh, tmp_path: Path):
    registration = study.load_registration(fresh.paths["inflight"])
    cell = registration["cells"][0]
    claim = tmp_path / "inflight-claim.json"
    row = {"ticket": cell["ticket"], "status": "zero"}
    row |= {"world_grade": 0, "grade_source": "native_agreed"}
    claim.write_text(
        json.dumps(
            {
                "schema": transfer_contract.PACKET_SCHEMA,
                "registration_sha256": transfer_contract.registration_digest(
                    registration
                ),
                "rows": [row],
            }
        )
    )
    report = transfer.transfer(
        fresh.paths["stock"],
        fresh.paths["inflight"],
        {"stock": [], "inflight": [claim]},
    )
    totals = report["worlds"]["inflight"]["totals"]
    assert (totals["reported"], totals["missing"], totals["eligible"]) == (1, 3, 1)
    claimed = [
        s
        for s in report["worlds"]["inflight"]["strata"]
        if (s["fate"], s["channel"]) == (cell["fate"], cell["channel"])
    ]
    assert len(claimed) == 1 and claimed[0]["zero"] == 1
    assert claimed[0]["success"] == {"low": "0/1", "high": "0/1"}
    evidence = collect.collect(
        fresh.paths["inflight"], [fresh.workspaces["inflight"]], fresh.task
    )
    found = [r["status"] for r in evidence["cells"] if r["ticket"] == cell["ticket"]]
    assert found == ["no_result"]


@pytest.mark.parametrize("team", ["recovery", "sender-only"])
def test_released_pairing_still_reads_cold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], team: str
):
    refuse_native(monkeypatch)
    stock = study.load_registration(RELEASED / f"stock-{team}.json")
    inflight = study.load_registration(RELEASED / f"inflight-{team}.json")
    assert stock["task"] == BASE and inflight["task"] == INFLIGHT_LEGACY
    assert transfer.main([*released_argv(team), "--json"]) == 0
    assert capsys.readouterr().out == (RELEASED / f"transfer-{team}.json").read_text()


def test_released_inflight_collects_cold_against_a_fresh_task(
    fresh: Fresh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    refuse_native(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert task_files_digest(fresh.task, INFLIGHT_LEGACY) == LEGACY_TREE
    report = collect.collect(RELEASED / "inflight-recovery.json", [empty], fresh.task)
    assert report["task"] == INFLIGHT_LEGACY
    assert report["registered"] == 4 and report["statuses"]["no_result"] == 4
    assert report["directories"]["unstamped_workspaces"] == 1


@pytest.mark.parametrize("fresh_world", WORLDS)
def test_mixed_generation_pairs_are_refused_despite_matching_teams(
    fresh: Fresh, capsys: pytest.CaptureFixture[str], fresh_world: str
):
    paths = {world: RELEASED / f"{world}-recovery.json" for world in WORLDS}
    paths[fresh_world] = fresh.paths[fresh_world]
    stock, inflight = (study.load_registration(paths[world]) for world in WORLDS)
    assert stock["teams"] == inflight["teams"] and list(stock["teams"]) == [TEAM]
    assert len(stock["cells"]) == len(inflight["cells"]) == 4
    assert transfer_contract.bindings(stock) and transfer_contract.bindings(inflight)
    with pytest.raises(ValueError, match=GENERATION_MIX):
        transfer_contract.check_pair(stock, inflight)
    assert transfer.main([str(paths["stock"]), str(paths["inflight"])]) == 2
    assert GENERATION_MIX in capsys.readouterr().err


@pytest.mark.parametrize(
    ("world", "field", "value", "refusal"),
    [
        ("inflight", "digest", ROLE_PACKAGE_DIGEST, "neither the legacy nor"),
        ("inflight", "digest", "sha256:" + "0" * 64, "neither the legacy nor"),
        ("inflight", "checksum", BASE["checksum"], "neither the legacy nor"),
        ("stock", "digest", INFLIGHT_ROLE["digest"], "does not bind the pinned"),
        ("stock", "checksum", INFLIGHT_ROLE["checksum"], "does not bind the pinned"),
    ],
)
def test_unrelated_identity_is_refused(
    fresh: Fresh, world: str, field: str, value: str, refusal: str
):
    registration = study.load_registration(fresh.paths[world])
    registration["task"] = {**registration["task"], field: value}
    if world == "stock":
        assert study.validate_registration(registration) is registration
    with pytest.raises(ValueError, match=refusal):
        transfer_contract.bindings(registration)


def test_identity_with_another_tree_stamp_is_refused(fresh: Fresh, tmp_path: Path):
    known = study.known_tasks(study.INFLIGHT)
    assert known is not None
    assert known == {
        "legacy": (INFLIGHT_LEGACY, LEGACY_TREE),
        "role-aware": (INFLIGHT_ROLE, ROLE_TREE),
    }
    for name, (identity, _) in known.items():
        for other, (_, tree) in known.items():
            accepted = study.accepted_task(study.INFLIGHT, identity, tree)
            assert accepted is (name == other)
        assert not study.accepted_task(study.INFLIGHT, identity, STOCK_ROLE_TREE)
    stale = tmp_path / "stale"
    stale.mkdir()
    assert record_task_files(stale / TASK_FILES, LEGACY_TREE)
    report = collect.collect(
        fresh.paths["inflight"], [fresh.workspaces["inflight"], stale], fresh.task
    )
    assert report["directories"]["unstamped_workspaces"] == 1
    assert report["statuses"]["no_result"] == 4


@pytest.mark.parametrize(
    ("world", "refusal"),
    [("stock", "is unregistered"), ("inflight", "never relabels")],
)
def test_emit_never_relabels_a_released_registration(
    fresh: Fresh,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    world: str,
    refusal: str,
):
    out = tmp_path / "w0"
    task = fresh.task_args[world]
    extra = [] if task is None else ["--task", str(task)]
    argv = ["emit", str(RELEASED / f"{world}-recovery.json"), "--out", str(out)]
    assert study.main([*argv, *extra]) == 1
    assert refusal in capsys.readouterr().err
    assert not out.exists()
