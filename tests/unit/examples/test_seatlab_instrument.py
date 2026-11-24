import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from examples.seatlab.workbench import attention, instrument
from examples.seatlab.workbench.instrument import SCHEMA, Refused, main, prepare

pytestmark = pytest.mark.skipif(not instrument._linkable(), reason="linkat unsupported")

Tree = dict[str, tuple[int, int, bytes | str | None]]
Setup = Callable[[Path, Path, Path], tuple[Path, Path]]


@pytest.fixture
def parents(tmp_path: Path) -> tuple[Path, Path]:
    jobs, bundles = tmp_path / "native jobs", tmp_path / " private=bundles "
    for parent in (jobs, bundles):
        parent.mkdir()
        os.chmod(parent, 0o700)
    return jobs, bundles


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _tree(root: Path) -> Tree:
    found: Tree = {}
    for current, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(current, name)
            info = os.lstat(path)
            body: bytes | str | None = None
            if stat.S_ISREG(info.st_mode):
                body = path.read_bytes()
            elif stat.S_ISLNK(info.st_mode):
                body = os.readlink(path)
            found[str(path.relative_to(root))] = (info.st_mode, info.st_ino, body)
    return found


@pytest.mark.parametrize("handoffs", [False, True])
@pytest.mark.parametrize("name", ["relay job", " job=1 two", "j\u00e9"])
def test_prepares_private_bundle_for_a_future_job(
    tmp_path, parents, handoffs: bool, name: str
) -> None:
    jobs, bundles = parents
    job, bundle = jobs / name, bundles / f"{name} bundle"
    summary = prepare(job, bundle, handoffs=handoffs)
    names = ["starts", "seats", "accounts", *(["handoffs"] if handoffs else [])]
    assert summary == {
        "schema": SCHEMA,
        "status": "prepared",
        "capabilities": names,
        "spool_sha256": _digest(bundle / "spool.json"),
        "flags_sha256": _digest(bundle / "flags.json"),
    }
    assert str(tmp_path) not in json.dumps(summary)
    assert not os.path.lexists(job) and os.listdir(jobs) == []
    assert sorted(os.listdir(bundle)) == sorted([*names, "flags.json", "spool.json"])
    for directory in [bundle, *(bundle / item for item in names)]:
        found = os.lstat(directory)
        assert (
            stat.S_ISDIR(found.st_mode)
            and found.st_uid == os.geteuid()
            and _mode(directory) == 0o700
        )
    for item in ("spool.json", "flags.json"):
        found = os.lstat(bundle / item)
        assert (
            stat.S_ISREG(found.st_mode)
            and found.st_nlink == 1
            and _mode(bundle / item) == 0o600
        )
    flags = json.loads((bundle / "flags.json").read_bytes())
    assert type(flags) is list and all(type(token) is str for token in flags)
    spool = json.loads((bundle / "spool.json").read_bytes())
    row = {"job": str(job)} | {item: str(bundle / item) for item in names}
    assert spool == {"schema": attention.SPOOL, "jobs": [row]}
    expected = summary["spool_sha256"]
    assert isinstance(expected, str)
    entries, digest = attention.load_spool(str(bundle / "spool.json"), expected)
    assert digest == expected and entries[0]["starts"] == row["starts"]
    assert entries[0]["witness"] is None


@pytest.mark.parametrize("umask", [0o000, 0o077, 0o277])
@pytest.mark.parametrize("parent_mode", [0o700, 0o755, 0o1777])
def test_modes_are_exact_under_any_umask(parents, umask: int, parent_mode: int) -> None:
    jobs, bundles = parents
    os.chmod(bundles, parent_mode)
    bundle = bundles / "bundle"
    previous = os.umask(umask)
    try:
        prepare(jobs / "job", bundle, handoffs=True)
    finally:
        os.umask(previous)
    assert _mode(bundles) == parent_mode
    for name in ("", "starts", "seats", "accounts", "handoffs"):
        assert _mode(bundle / name) == 0o700
    assert _mode(bundle / "spool.json") == _mode(bundle / "flags.json") == 0o600


def test_sibling_jobs_get_independent_bundles(parents) -> None:
    jobs, bundles = parents
    first = prepare(jobs / "job a", bundles / "bundle a", handoffs=False)
    kept = _tree(bundles / "bundle a")
    second = prepare(jobs / "job b", bundles / "bundle b", handoffs=True)
    assert _tree(bundles / "bundle a") == kept
    assert first["spool_sha256"] != second["spool_sha256"]
    assert first["flags_sha256"] != second["flags_sha256"]
    for label, other in (("a", "b"), ("b", "a")):
        bundle = bundles / f"bundle {label}"
        written = (bundle / "flags.json").read_text() + (
            bundle / "spool.json"
        ).read_text()
        assert str(jobs / f"job {label}") in written
        assert str(jobs / f"job {other}") not in written
        assert str(bundles / f"bundle {other}") not in written
    assert os.listdir(jobs) == []


def test_relative_paths_become_canonical_absolute(
    tmp_path, parents, monkeypatch
) -> None:
    jobs, bundles = parents
    monkeypatch.chdir(tmp_path)
    prepare(Path(jobs.name) / "job", Path(bundles.name) / "bundle", handoffs=False)
    spool = json.loads((bundles / "bundle" / "spool.json").read_bytes())
    assert spool["jobs"][0]["job"] == str(jobs / "job")
    assert spool["jobs"][0]["seats"] == str(bundles / "bundle" / "seats")


def _dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def _file(path: Path) -> Path:
    path.write_text("x")
    return path


def _locked(path: Path) -> Path:
    os.chmod(path, 0o500)
    return path


def _symlink(path: Path, target: Path) -> Path:
    path.symlink_to(target)
    return path


SETUPS: dict[str, tuple[Setup, str]] = {
    "job dir": (lambda r, j, b: (_dir(j / "job"), b / "bundle"), "job_exists"),
    "job link": (
        lambda r, j, b: (_symlink(j / "job", r / "nowhere"), b / "bundle"),
        "job_exists",
    ),
    "bundle dir": (lambda r, j, b: (j / "job", _dir(b / "bundle")), "bundle_exists"),
    "bundle link": (
        lambda r, j, b: (j / "job", _symlink(b / "bundle", r / "nowhere")),
        "bundle_exists",
    ),
    "same path": (lambda r, j, b: (j / "job", j / "job"), "nested"),
    "case alias": (lambda r, j, b: (j / "Relay", j / "relay"), "nested"),
    "inside job": (lambda r, j, b: (j / "job", j / "job" / "bundle"), "parent_refused"),
    "missing parent": (
        lambda r, j, b: (j / "later" / "job", b / "bundle"),
        "parent_refused",
    ),
    "linked parent": (
        lambda r, j, b: (j / "job", _symlink(r / "alias", b) / "bundle"),
        "parent_refused",
    ),
    "file parent": (
        lambda r, j, b: (_file(j / "plain") / "job", b / "bundle"),
        "parent_refused",
    ),
    "read-only parent": (
        lambda r, j, b: (j / "job", _locked(b) / "bundle"),
        "parent_refused",
    ),
    "dot dot": (
        lambda r, j, b: (Path(f"{j}/../{j.name}/job"), b / "bundle"),
        "unsafe_path",
    ),
    "control": (lambda r, j, b: (j / "job", b / "bun\ndle"), "unsafe_path"),
    "long name": (lambda r, j, b: (j / ("j" * 256), b / "bundle"), "unsafe_path"),
    "root": (lambda r, j, b: (j / "job", Path("/")), "unsafe_path"),
    "job ends in space": (lambda r, j, b: (j / "job ", b / "bundle"), "unsafe_path"),
    "job ends in nbsp": (
        lambda r, j, b: (j / "job\u00a0", b / "bundle"),
        "unsafe_path",
    ),
}


@pytest.mark.parametrize("case", sorted(SETUPS))
def test_conflicts_refuse_without_touching_anything(
    tmp_path, parents, case: str
) -> None:
    jobs, bundles = parents
    setup, code = SETUPS[case]
    job, bundle = setup(tmp_path, jobs, bundles)
    kept = _tree(tmp_path)
    try:
        with pytest.raises(Refused) as refused:
            prepare(job, bundle, handoffs=True)
        assert refused.value.code == code
        assert _tree(tmp_path) == kept
    finally:
        os.chmod(bundles, 0o700)


@pytest.mark.parametrize("handoffs", [False, True])
def test_repeat_refuses_and_keeps_the_bundle(tmp_path, parents, handoffs: bool) -> None:
    jobs, bundles = parents
    prepare(jobs / "job", bundles / "bundle", handoffs=False)
    kept = _tree(tmp_path)
    with pytest.raises(Refused) as refused:
        prepare(jobs / "job", bundles / "bundle", handoffs=handoffs)
    assert refused.value.code == "bundle_exists"
    assert _tree(tmp_path) == kept


def test_parents_on_different_devices_are_refused(
    tmp_path, parents, monkeypatch
) -> None:
    jobs, bundles = parents
    real = os.lstat

    def lstat(path, *args, **kwargs):
        found = real(path, *args, **kwargs)
        if os.fspath(path) != str(bundles):
            return found
        fields = list(found[:10])
        fields[2] += 1
        return os.stat_result(fields)

    kept = _tree(tmp_path)
    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(Refused) as refused:
        prepare(jobs / "job", bundles / "bundle", handoffs=False)
    monkeypatch.undo()
    assert refused.value.code == "device"
    assert _tree(tmp_path) == kept


def test_partial_failure_never_reports_ready(
    tmp_path, parents, monkeypatch, capsys
) -> None:
    jobs, bundles = parents
    job, bundle = jobs / "job", bundles / "bundle"
    publish = instrument._publish

    def broken(dir_fd: int, name: str, value: object) -> str:
        if name == "flags.json":
            raise OSError("disk full")
        return publish(dir_fd, name, value)

    monkeypatch.setattr(instrument, "_publish", broken)
    assert main([str(job), str(bundle)]) == 2
    out, err = capsys.readouterr()
    assert (
        out == ""
        and json.loads(err)["error"] == "incomplete"
        and str(tmp_path) not in err
    )
    assert sorted(os.listdir(bundle)) == ["accounts", "seats", "spool.json", "starts"]
    monkeypatch.undo()
    kept = _tree(tmp_path)
    with pytest.raises(Refused) as refused:
        prepare(job, bundle, handoffs=False)
    assert refused.value.code == "bundle_exists"
    assert _tree(tmp_path) == kept and not os.path.lexists(job)


def test_cli_prints_only_a_safe_summary(tmp_path, parents, capsys) -> None:
    jobs, bundles = parents
    bundle = bundles / "bundle"
    assert main([str(jobs / "job"), str(bundle), "--handoffs"]) == 0
    out, err = capsys.readouterr()
    summary = json.loads(out)
    assert err == "" and str(tmp_path) not in out
    assert summary["capabilities"] == ["starts", "seats", "accounts", "handoffs"]
    assert summary["flags_sha256"] == _digest(bundle / "flags.json")
    assert summary["spool_sha256"] == _digest(bundle / "spool.json")
    assert main([str(jobs / "other"), str(bundle)]) == 2
    out, err = capsys.readouterr()
    assert out == "" and str(tmp_path) not in err
    assert json.loads(err) == {
        "schema": SCHEMA,
        "status": "rejected",
        "error": "bundle_exists",
        "message": instrument.ERRORS["bundle_exists"],
    }


@pytest.mark.parametrize("argv", [[], ["one"], ["a", "b", "--hand"], ["a", "b", "c"]])
def test_cli_rejects_bad_arguments(
    tmp_path, monkeypatch, capsys, argv: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exited:
        main(argv)
    assert exited.value.code == 2
    out, err = capsys.readouterr()
    assert out == "" and err == "Invalid instrument arguments. Use instrument --help.\n"
    assert os.listdir(tmp_path) == []
