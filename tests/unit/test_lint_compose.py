import errno
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from harbor.lint import LintRule, lint_task
from harbor.lint.compose import (
    _RUNTIME_HOST_DIRECTORY,
    _compose_env,
    _load_compose,
    lint_compose,
)
from harbor.lint.models import DEFAULT_RULES
from tests.unit.test_lint import cli as cli, configure, task as task

pytestmark = pytest.mark.unit


@pytest.fixture
def model(monkeypatch):
    data = {"services": {"main": {}}}
    monkeypatch.setattr("harbor.lint.compose._load_compose", Mock(return_value=data))
    return data


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "task/environment/docker-compose.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("# Normalized output is supplied at the Compose boundary.\n")
    return path


@pytest.mark.parametrize("field", ["privileged", "post_start", "pre_stop"])
@pytest.mark.parametrize("enabled", [False, True])
def test_privileged(model, path, field, enabled):
    model["services"]["main"][field] = (
        enabled
        if field == "privileged"
        else [{"command": "init", "privileged": enabled}]
    )
    findings = lint_compose(path)
    assert [f.rule for f in findings] == (["compose-privileged"] if enabled else [])
    assert all(f"services.main.{field}" in f.message for f in findings)


@pytest.mark.parametrize(
    "capabilities", [[], ["NET_BIND_SERVICE"], ["ALL"], ["SYS_ADMIN", "NET_ADMIN"]]
)
def test_capabilities(model, path, capabilities):
    model["services"]["main"].update(cap_add=capabilities, cap_drop=["ALL"])
    assert [f.rule for f in lint_compose(path)] == (
        ["compose-additional-capabilities"] if capabilities else []
    )


@pytest.mark.parametrize("kind", ["bind", "volume", "tmpfs"])
@pytest.mark.parametrize("read_only", [False, True])
def test_mount_types(model, path, kind, read_only):
    model["services"]["main"]["volumes"] = [
        {"type": kind, "source": "./data", "target": "/data", "read_only": read_only}
    ]
    expected = (
        {"compose-writable-bind-mount"} if kind == "bind" and not read_only else set()
    )
    assert {f.rule for f in lint_compose(path)} == expected


@pytest.mark.parametrize(
    "flags,driver_read_only",
    [("bind", False), ("rbind,ro", True), ("bind,rw,ro", True), ("bind,ro,rw", False)],
)
@pytest.mark.parametrize("read_only", [False, True])
def test_named_bind_access(model, path, flags, driver_read_only, read_only):
    model["volumes"] = {
        "inputs": {"driver": "local", "driver_opts": {"o": flags, "device": "./data"}}
    }
    model["services"]["main"]["volumes"] = [
        {
            "type": "volume",
            "source": "inputs",
            "target": "/data",
            "read_only": read_only,
        }
    ]
    findings = lint_compose(path)
    expected = [] if read_only or driver_read_only else ["compose-writable-bind-mount"]
    assert [f.rule for f in findings] == expected
    assert all(f.message.startswith("services.main.volumes[0]:") for f in findings)


@pytest.mark.parametrize("kind", ["unused", "custom", "nfs"])
def test_other_volumes_are_not_binds(model, path, kind):
    model["volumes"] = {
        "inputs": {
            "driver": "custom" if kind == "custom" else "local",
            "driver_opts": {
                "o": "addr=server" if kind == "nfs" else "bind",
                "device": "../../outside",
            },
        }
    }
    if kind != "unused":
        model["services"]["main"]["volumes"] = [
            {"type": "volume", "source": "inputs", "target": "/data"}
        ]
    assert lint_compose(path) == []


def volume(read_only: bool = False, subpath: str | None = None) -> dict[str, Any]:
    mount: dict[str, Any] = {"type": "volume", "source": "a", "target": "/data"}
    if read_only:
        mount["read_only"] = True
    if subpath is not None:
        mount["volume"] = {"subpath": subpath}
    return mount


SEAT_VOLUMES = {
    "own-volume": ({"p1": [volume()]}, []),
    "seats-read": (
        {"main": [volume()], "p1": [volume(True)], "p2": [volume(True)]},
        [],
    ),
    "main-reads-seat": ({"main": [volume(True)], "p1": [volume()]}, []),
    "seat-reads-main": ({"main": [volume()], "p1": [volume(True)]}, []),
    "one-seat-twice": ({"p1": [volume(), volume(True)]}, []),
    "separate-subpaths": (
        {
            "main": [volume(True)],
            "p1": [volume(subpath="p1")],
            "p2": [volume(subpath="./p2/")],
        },
        [],
    ),
    "seats-write": (
        {"p1": [volume()], "p2": [volume()]},
        [("p1", "mounted by seat p2"), ("p2", "mounted by seat p1")],
    ),
    "seat-reads-seat": (
        {"p1": [volume()], "p2": [volume(True)]},
        [("p1", "mounted by seat p2")],
    ),
    "main-writes": (
        {"main": [volume()], "p1": [volume()]},
        [("p1", "written by main")],
    ),
    "service-writes": (
        {"db": [volume()], "p1": [volume()], "p2": [volume(True)]},
        [("p1", "mounted by seat p2"), ("p1", "written by db")],
    ),
    "volume-and-subpath": (
        {"p1": [volume()], "p2": [volume(subpath="p2")]},
        [("p1", "mounted by seat p2"), ("p2", "mounted by seat p1")],
    ),
    "subpath-traversal": (
        {"p1": [volume(subpath="p1")], "p2": [volume(True, "p2/../p1")]},
        [("p1", "mounted by seat p2")],
    ),
}


def volume_findings(path: Path) -> list[Any]:
    """Findings for seats p1 and p2, without seat-shared-network."""
    return [
        f
        for f in lint_compose(path, seats=["p1", "p2"])
        if f.rule != "seat-shared-network"
    ]


@pytest.mark.parametrize(
    "mounts,expected", SEAT_VOLUMES.values(), ids=list(SEAT_VOLUMES)
)
def test_seat_shared_volume(model, path, mounts, expected):
    for service, volumes in mounts.items():
        model["services"].setdefault(service, {})["volumes"] = volumes
    assert [
        (f.rule, f.message.split(":")[0], f.message.split(" also ")[1].split(";")[0])
        for f in volume_findings(path)
    ] == [
        ("seat-shared-volume", f"services.{service}.volumes[0]", sharing)
        for service, sharing in expected
    ]
    assert lint_compose(path) == []  # Tasks without seats have no seat findings.


@pytest.mark.parametrize(
    "flags,expected",
    [
        ("bind,ro", set()),
        ("bind", {"compose-writable-bind-mount", "seat-shared-volume"}),
    ],
)
def test_seat_shared_bind_volume_access(model, path, flags, expected):
    model["volumes"] = {"a": {"driver_opts": {"o": flags, "device": "./data"}}}
    model["services"].update(p1={"volumes": [volume()]}, p2={"volumes": [volume()]})
    assert {f.rule for f in volume_findings(path)} == expected


def shared_mount_messages(path: Path) -> list[str]:
    return [
        finding.message.split(";")[0]
        for finding in lint_compose(
            path, task_root=path.parent.parent, seats=["p1", "p2"]
        )
        if finding.rule == LintRule.SEAT_SHARED_VOLUME
    ]


@pytest.mark.parametrize(
    "first,second,read_only,where",
    [
        ("./data", "./data", False, "'environment/data'"),
        ("./data", "./data/child", False, "'environment/data/child'"),
        ("./data/child", "./data", False, "'environment/data/child'"),
        ("./data", "./data-other", False, None),
        ("./data", "./data", True, None),
        ("../../host-input", "../../host-input", False, "outside the task directory"),
    ],
    ids=["same", "parent", "child", "separate", "both-read-only", "external"],
)
def test_seat_shared_host_bind_paths(model, path, first, second, read_only, where):
    bind = {"type": "bind", "source": first, "target": "/data"}
    model["services"].update(
        main={"volumes": [bind]},
        p1={"volumes": [{**bind, "read_only": read_only}]},
        p2={"volumes": [{**bind, "source": second, "read_only": True}]},
    )
    assert shared_mount_messages(path) == (
        [
            f"services.p1.volumes[0]: Seat p1 can write a host path {where} also mounted by seat p2"
        ]
        if where
        else []
    )


def test_seat_shared_host_bind_paths_resolve_symlinks(model, path):
    task_root = path.parent.parent
    data = task_root / "data"
    data.mkdir()
    (path.parent / "alias").symlink_to(data, target_is_directory=True)
    bind = {"type": "bind", "source": "./alias", "target": "/data"}
    model["services"].update(
        p1={"volumes": [bind]},
        p2={"volumes": [{**bind, "source": str(data), "read_only": True}]},
    )
    assert shared_mount_messages(path) == [
        "services.p1.volumes[0]: Seat p1 can write a host path 'data' also mounted by seat p2"
    ]


@pytest.mark.parametrize("second_name", ["first", "second"])
def test_seat_shared_local_driver_device(model, path, second_name):
    driver = {
        "driver": "local",
        "driver_opts": {"type": "none", "o": "bind", "device": "./data"},
    }
    model["volumes"] = {
        "a": {**driver, "name": "first"},
        "b": {**driver, "name": second_name},
    }
    model["services"].update(
        p1={"volumes": [volume()]},
        p2={"volumes": [{**volume(True), "source": "b"}]},
    )
    assert shared_mount_messages(path) == [
        "services.p1.volumes[0]: Seat p1 can write a host path 'environment/data' "
        "also mounted by seat p2"
    ]


@pytest.mark.parametrize("subpath,shared", [("p1", True), ("p2", False)])
def test_seat_direct_and_local_driver_bind_subpaths(model, path, subpath, shared):
    model["volumes"] = {
        "a": {"driver_opts": {"type": "none", "o": "bind,ro", "device": "./data"}}
    }
    model["services"].update(
        p1={"volumes": [{"type": "bind", "source": "./data/p1", "target": "/data"}]},
        p2={"volumes": [volume(subpath=subpath)]},
    )
    assert shared_mount_messages(path) == (
        [
            "services.p1.volumes[0]: Seat p1 can write a host path 'environment/data/p1' "
            "also mounted by seat p2"
        ]
        if shared
        else []
    )


@pytest.mark.parametrize(
    "volumes,shared",
    [
        (
            {
                "a": {"name": "x", "external": True},
                "b": {"name": "x", "external": True},
            },
            True,
        ),
        ({"a": {"name": "x"}, "b": {"name": "x"}}, True),
        ({"a": {"name": "harbor-lint_a"}, "b": {"name": "harbor-lint_b"}}, False),
        ({}, False),
    ],
    ids=["one-external-volume", "one-name", "own-volumes", "undeclared-keys"],
)
def test_seat_shared_volume_compares_docker_names(model, path, volumes, shared):
    model["volumes"] = volumes
    model["services"].update(
        p1={"volumes": [volume()]},
        p2={"volumes": [{**volume(True), "source": "b"}]},
    )
    assert [f.message.split(";")[0] for f in volume_findings(path)] == (
        ["services.p1.volumes[0]: Seat can write a volume also mounted by seat p2"]
        if shared
        else []
    )


SEAT_NETWORKS = {
    "default-network": ({"p1": {}, "p2": {}}, ["p1", "p2"]),
    "one-network": (
        {"p1": {"networks": {"play": None}}, "p2": {"networks": ["play"]}},
        ["p1", "p2"],
    ),
    "own-networks": (
        {
            "main": {"networks": {"p1-net": None, "p2-net": None}},
            "p1": {"networks": {"p1-net": None}},
            "p2": {"networks": {"p2-net": None}},
        },
        [],
    ),
    "one-docker-network": (
        {"p1": {"networks": {"a": None}}, "p2": {"networks": {"b": None}}},
        ["p1", "p2"],
    ),
    "bridge": (
        {"p1": {"network_mode": "bridge"}, "p2": {"network_mode": "bridge"}},
        ["p1", "p2"],
    ),
    "no-network": ({"p1": {"network_mode": "none"}, "p2": {}}, []),
    "shared-namespaces": (
        {"p1": {"network_mode": "host"}, "p2": {"network_mode": "service:main"}},
        [],
    ),
}


@pytest.mark.parametrize(
    "services,expected", SEAT_NETWORKS.values(), ids=list(SEAT_NETWORKS)
)
def test_seat_shared_network(model, path, services, expected):
    model["services"].update(services)
    # Both keys name one Docker network only in the one-docker-network case.
    model["networks"] = {"a": {"name": "x"}, "b": {"name": "x"}, "p1-net": {}}
    findings = lint_compose(path, seats=["p1", "p2"])
    assert [f.message for f in findings if f.rule == "seat-shared-network"] == [
        f"services.{seat}."
        f"{'network_mode' if 'network_mode' in services[seat] else 'networks'}: "
        f"Seat shares a network with seat {'p2' if seat == 'p1' else 'p1'}; "
        "give each seat its own network, shared only with main."
        for seat in expected
    ]


def test_seat_shared_network_is_opt_in():
    assert LintRule.SEAT_SHARED_NETWORK not in DEFAULT_RULES


NAMESPACES = [("network_mode", "network"), ("pid", "PID"), ("ipc", "IPC")]


@pytest.mark.parametrize("key,kind", NAMESPACES)
@pytest.mark.parametrize(
    "mode,shares",
    [
        ("host", "the host's"),
        ("service:main", "another container's"),
        ("container:other", "another container's"),
        ("none", None),
        ("private", None),
        (None, None),
    ],
)
def test_seat_shared_namespace(model, path, key, kind, mode, shares):
    model["services"]["main"][key] = "host"  # main alone does not share a seat's.
    model["services"]["p1"] = {key: mode}
    findings = lint_compose(path, seats=["p1"])
    assert [(f.rule, f.message) for f in findings] == (
        [
            (
                "seat-shared-namespace",
                f"services.p1.{key}: Seat shares {shares} {kind} namespace; remove it to isolate the seat.",
            )
        ]
        if shares
        else []
    )


@pytest.mark.parametrize("key,kind", NAMESPACES)
@pytest.mark.parametrize(
    "service,mode,shared",
    [
        ("main", "service:p1", True),
        ("db", "service:p1", True),
        ("main", "container:p1-box", True),
        ("main", "service:db", False),
        ("main", "container:other", False),
        ("main", "host", False),
    ],
)
def test_service_shares_seat_namespace(model, path, key, kind, service, mode, shared):
    model["services"].update(db={}, p1={"container_name": "p1-box"})
    model["services"][service][key] = mode
    findings = lint_compose(path, seats=["p1"])
    assert [(f.rule, f.message) for f in findings] == (
        [
            (
                "seat-shared-namespace",
                f"services.{service}.{key}: Service shares a seat's {kind} namespace; remove it to isolate the seat.",
            )
        ]
        if shared
        else []
    )
    assert lint_compose(path) == []  # Tasks without seats have no seat findings.


def add_seats(task: Path, *seats: str) -> None:
    """Turn the task into a seat task with an instruction for each seat."""
    configure(task, agent={}, verifier={}, agents={seat: {} for seat in seats})
    (task / "instructions").mkdir()
    for seat in seats:
        (task / f"instructions/{seat}.md").write_text("Play your part.\n")


@pytest.mark.parametrize("output", ["text", "json"])
def test_seat_findings_never_print_compose_values(task, cli, model, output):
    # Compose interpolates these values, so they can hold host secrets.
    secret = "DO_NOT_PRINT"
    add_seats(task, "p1", "p2")
    (task / "environment/docker-compose.yaml").write_text("services: {}")
    mount = {"type": "volume", "source": secret, "target": "/data"}
    model["services"].update(
        main={"pid": "service:p1"},
        p1={"network_mode": f"container:{secret}", "volumes": [mount]},
        p2={"ipc": f"service:{secret}", "volumes": [{**mount, "read_only": True}]},
    )
    model["volumes"] = {secret: {"name": secret}}
    result = CliRunner().invoke(
        cli, [str(task), *(["--json"] if output == "json" else [])]
    )
    assert result.exit_code == 1
    assert secret not in result.stdout
    if output == "json":
        assert [
            (f["rule"], f["message"].split(":")[0]) for f in json.loads(result.stdout)
        ] == [
            ("seat-shared-namespace", "services.main.pid"),
            ("seat-shared-namespace", "services.p1.network_mode"),
            ("seat-shared-namespace", "services.p2.ipc"),
            ("seat-shared-volume", "services.p1.volumes[0]"),
        ]


def test_seat_privilege_and_docker_socket_use_existing_rules(model, path):
    model["services"]["p1"] = {
        "privileged": True,
        "volumes": [
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
                "read_only": True,
            }
        ],
    }
    reported = {f.rule for f in lint_compose(path, seats=["p1"])}
    assert "compose-privileged" in reported
    # POSIX host sources are inspected only on POSIX hosts.
    assert ("compose-external-bind-mount" in reported) == (os.name != "nt")


@pytest.mark.parametrize(
    "source", ["./data", "../data", "../../outside", "../../task-other/data"]
)
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("named", [False, True])
def test_mount_containment(model, path, source, read_only, named):
    if named:
        model["volumes"] = {"inputs": {"driver_opts": {"o": "bind", "device": source}}}
    model["services"]["main"]["volumes"] = [
        {
            "type": "volume" if named else "bind",
            "source": "inputs" if named else source,
            "target": "/data",
            "read_only": read_only,
        }
    ]
    expected = set() if read_only else {"compose-writable-bind-mount"}
    if source.startswith("../../"):
        expected.add("compose-external-bind-mount")
    assert {
        f.rule for f in lint_compose(path, task_root=path.parent.parent)
    } == expected


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("outside", [False, True])
@pytest.mark.parametrize(
    "kind", ["file", "directory", "parent", "dangling", "chain", "parent-traversal"]
)
def test_symlink_containment(model, path, outside, kind, read_only):
    root = path.parent.parent
    target_root = root.parent / "host" if outside else root / "inputs"
    target_root.mkdir()
    target = target_root / "target"
    if kind == "file":
        target.write_text("input")
    elif kind != "dangling":
        target.mkdir()
    link = path.parent / "link"
    if kind == "chain":
        intermediate = path.parent / "intermediate"
        intermediate.symlink_to(target, target_is_directory=True)
        link.symlink_to(intermediate, target_is_directory=True)
    else:
        link.symlink_to(target, target_is_directory=kind != "file")
    source = "./link"
    if kind in ("parent", "dangling"):
        source += "/child"
    elif kind == "parent-traversal":
        source += "/../child"
        (path.parent / "child").write_text("project")
        (target_root / "child").write_text("target")
        assert (path.parent / source).read_text() == (
            "project" if os.name == "nt" else "target"
        )
    model["services"]["main"]["volumes"] = [
        {"type": "bind", "source": source, "target": "/data", "read_only": read_only}
    ]
    expected = set() if read_only else {"compose-writable-bind-mount"}
    if outside and not (kind == "parent-traversal" and os.name == "nt"):
        expected |= {"compose-external-bind-mount", "compose-symlink-bind-mount"}
    assert {f.rule for f in lint_compose(path, task_root=root)} == expected


@pytest.mark.parametrize("loop", [False, True])
def test_path_resolution_failure(model, path, monkeypatch, loop):
    original = Path.resolve

    def resolve(candidate, *args, **kwargs):
        if candidate == path.parent / "data":
            raise OSError(errno.ELOOP if loop else errno.EACCES, "DO_NOT_PRINT")
        return original(candidate, *args, **kwargs)

    model["services"]["main"]["volumes"] = [
        {"type": "bind", "source": "./data", "target": "/data"}
    ]
    monkeypatch.setattr(Path, "resolve", resolve)
    if loop:
        assert {f.rule for f in lint_compose(path)} == {
            "invalid-task-files",
            "compose-writable-bind-mount",
        }
    else:
        with pytest.raises(OSError):
            lint_compose(path)


def test_config_command(path, monkeypatch):
    data = {"services": {"main": {"privileged": True}}}
    run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(data), ""))
    monkeypatch.setattr("harbor.lint.compose.subprocess.run", run)
    assert _load_compose(path, task_env={"PRIVILEGED": "true"}) == data
    args, kwargs = run.call_args
    env = kwargs.pop("env")
    assert env["PRIVILEGED"] == "true"
    assert env["CPUS"] == "1"
    assert env["MEMORY"] == "1g"
    assert env["CONTEXT_DIR"] == str(path.parent)
    assert args[0] == [
        "docker",
        "compose",
        "--project-name",
        "harbor-lint",
        "--project-directory",
        str(path.parent),
        "--profile",
        "*",
        "-f",
        str(path),
        "config",
        "--format",
        "json",
        "--no-env-resolution",
        "--no-path-resolution",
        "--no-consistency",
    ]
    assert kwargs == {
        "cwd": path.parent,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "timeout": 30,
    }
    run.assert_called_once()


def test_compose_rejection_is_redacted(path, monkeypatch):
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 1, "DO_NOT_PRINT", "DO_NOT_PRINT"),
            subprocess.CompletedProcess([], 0, "2.40.0", ""),
        ]
    )
    monkeypatch.setattr("harbor.lint.compose.subprocess.run", run)
    findings = lint_compose(path)
    assert [f.rule for f in findings] == ["invalid-task-config"]
    assert "DO_NOT_PRINT" not in str(findings)


def test_runtime_variables_override_shell_but_preserve_user_variables(
    path, monkeypatch
):
    monkeypatch.setenv("CPUS", "")
    monkeypatch.setenv("MEMORY", "invalid")
    monkeypatch.setenv("HOST_VERIFIER_LOGS_PATH", str(path.parent))
    monkeypatch.setenv("USER_INPUT", "keep")
    monkeypatch.setenv("PRIVILEGED", "false")
    monkeypatch.setenv("HOST_FLAG", "true")
    monkeypatch.delenv("UNSET_FLAG", raising=False)
    env = _compose_env(
        path,
        {
            "PRIVILEGED": "${HOST_FLAG}",
            "DEFAULT_FLAG": "${UNSET_FLAG:-false}",
            "CPUS": "99",
            "MEMORY": "invalid",
            "HOST_VERIFIER_LOGS_PATH": str(path.parent),
        },
    )
    assert env["CPUS"] == "1"
    assert env["MEMORY"] == "1g"
    assert env["USER_INPUT"] == "keep"
    assert env["PRIVILEGED"] == "true"
    assert env["DEFAULT_FLAG"] == "false"
    assert not Path(env["HOST_VERIFIER_LOGS_PATH"]).is_relative_to(path.parent)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("suffix", ["", "/nested/file"])
def test_runtime_mounts_remain_external(model, path, monkeypatch, read_only, suffix):
    model["services"]["main"]["volumes"] = [
        {
            "type": "bind",
            "source": _compose_env(path)["HOST_VERIFIER_LOGS_PATH"] + suffix,
            "target": "/logs/verifier",
            "read_only": read_only,
        }
    ]
    monkeypatch.setattr(
        "harbor.lint.compose._has_symlink",
        Mock(side_effect=AssertionError("Do not inspect runtime placeholders")),
    )
    expected = {"compose-external-bind-mount"}
    if not read_only:
        expected.add("compose-writable-bind-mount")
    assert {f.rule for f in lint_compose(path)} == expected


@pytest.mark.parametrize(
    "directory",
    ["__harbor_lint_runtime__", _RUNTIME_HOST_DIRECTORY],
    ids=["literal-marker", "generated-marker"],
)
@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("symlink", [False, True])
def test_runtime_marker_in_task_paths_is_not_a_placeholder(
    model, path, directory, absolute, symlink
):
    source = path.parent / directory
    if symlink:
        target = path.parent.parent.parent / "outside"
        target.mkdir()
        source.symlink_to(target, target_is_directory=True)
    else:
        source.mkdir()
    (source / "input.txt").write_text("input")
    model["services"]["main"]["volumes"] = [
        {
            "type": "bind",
            "source": str(source / "input.txt")
            if absolute
            else f"./{directory}/input.txt",
            "target": "/data",
            "read_only": True,
        }
    ]
    expected = (
        {"compose-external-bind-mount", "compose-symlink-bind-mount"}
        if symlink
        else set()
    )
    assert {f.rule for f in lint_compose(path)} == expected


@pytest.mark.parametrize("folder", ["environment", "tests", "steps/one/tests"])
@pytest.mark.parametrize(
    "args,expected",
    [
        ([], ["root-agent"]),
        (["--ignore", "root-agent"], []),
        (
            ["--extend-select", "unpinned-image", "--ignore", "unpinned-image"],
            ["root-agent"],
        ),
    ],
)
def test_cli_non_compose_selection_without_docker(
    task, cli, monkeypatch, folder, args, expected
):
    (task / "task.toml").write_text("")
    compose = task / folder / "docker-compose.yaml"
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services: {}")
    run = Mock(side_effect=FileNotFoundError("docker"))
    monkeypatch.setattr("harbor.lint.compose.subprocess.run", run)
    result = CliRunner().invoke(
        cli, [str(task), "--json", "--select", "root-agent", *args]
    )
    assert result.exit_code == (1 if expected else 0)
    assert [finding["rule"] for finding in json.loads(result.stdout)] == expected
    run.assert_not_called()


@pytest.mark.parametrize(
    "rule,seats",
    [
        ("compose-privileged", False),
        ("unpinned-image", False),
        ("invalid-task-config", False),
        ("invalid-task-files", False),
        ("seat-shared-volume", True),
        ("seat-shared-namespace", True),
        ("seat-shared-network", True),
    ],
)
def test_cli_compose_selection_requires_docker(task, cli, monkeypatch, rule, seats):
    if seats:
        add_seats(task, "p1")
    (task / "environment/docker-compose.yaml").write_text("services: {}")
    run = Mock(side_effect=FileNotFoundError("docker"))
    monkeypatch.setattr("harbor.lint.compose.subprocess.run", run)
    result = CliRunner().invoke(cli, [str(task), "--json", "--select", rule])
    assert result.exit_code == 2
    assert not result.stdout
    run.assert_called_once()


@pytest.mark.parametrize("folder", ["environment", "tests", "steps/one/tests"])
@pytest.mark.parametrize(
    "rule", ["seat-shared-volume", "seat-shared-namespace", "seat-shared-network"]
)
def test_cli_seat_rules_without_seats_skip_docker(task, cli, monkeypatch, folder, rule):
    compose = task / folder / "docker-compose.yaml"
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services: {}")
    run = Mock(side_effect=FileNotFoundError("docker"))
    monkeypatch.setattr("harbor.lint.compose.subprocess.run", run)
    result = CliRunner().invoke(cli, [str(task), "--json", "--select", rule])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    run.assert_not_called()


def test_task_rule_selection_preserves_unfiltered_default(task, model):
    (task / "task.toml").write_text("")
    (task / "environment/docker-compose.yaml").write_text("services: {}")
    model["services"]["main"].update(image="ubuntu:24.04", privileged=True)
    assert {finding.rule for finding in lint_task(task)} == {
        "root-agent",
        "shared-verifier",
        "unpinned-image",
        "compose-privileged",
    }
    assert [
        finding.rule
        for finding in lint_task(task, selected_rules={LintRule.UNPINNED_IMAGE})
    ] == ["unpinned-image"]
    assert lint_task(task, selected_rules=set()) == []


@pytest.mark.parametrize("failure", ["missing", "plugin", "timeout", "json", "model"])
def test_compose_operational_failures(task, cli, monkeypatch, failure):
    (task / "environment/docker-compose.yaml").write_text("services: {}")
    if failure == "missing":
        run = Mock(side_effect=FileNotFoundError("DO_NOT_PRINT"))
    elif failure == "timeout":
        run = Mock(
            side_effect=subprocess.TimeoutExpired(
                "DO_NOT_PRINT", 30, output="DO_NOT_PRINT"
            )
        )
    elif failure == "plugin":
        run = Mock(return_value=subprocess.CompletedProcess([], 1, "", "DO_NOT_PRINT"))
    else:
        run = Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, "DO_NOT_PRINT" if failure == "json" else "[]", ""
            )
        )
    monkeypatch.setattr("harbor.lint.compose.subprocess.run", run)
    result = CliRunner().invoke(cli, [str(task), "--json"])
    assert result.exit_code == 2
    assert not result.stdout
    assert "DO_NOT_PRINT" not in result.stderr
    assert "Compose" in result.stderr


def test_cli_can_ignore_compose_finding(task, cli, model):
    (task / "environment/docker-compose.yaml").write_text("services: {}")
    model["services"]["main"]["cap_add"] = ["SYS_ADMIN"]
    result = CliRunner().invoke(
        cli, [str(task), "--json", "--ignore", "compose-additional-capabilities"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_missing_task_environment_is_operational_and_redacted(task, cli, monkeypatch):
    from tests.unit.test_lint import configure

    configure(task, environment={"env": {"FLAG": "${DO_NOT_PRINT}"}})
    (task / "environment/docker-compose.yaml").write_text("services: {}")
    monkeypatch.delenv("DO_NOT_PRINT", raising=False)
    run = Mock(side_effect=AssertionError("Compose must not run"))
    monkeypatch.setattr("harbor.lint.compose.subprocess.run", run)
    result = CliRunner().invoke(cli, [str(task), "--json"])
    assert result.exit_code == 2
    assert not result.stdout
    assert "DO_NOT_PRINT" not in result.stderr
    run.assert_not_called()


def test_seat_volumes_from_is_a_shared_volume(model, path):
    """A seat with volumes_from gets every volume of the other container (review E1)."""
    model["services"].update(
        {
            "p1": {"volumes_from": ["service:main:rw"]},
            "p2": {"volumes_from": ["service:p1:ro"]},
            "main": {},
        }
    )
    findings = lint_compose(path, seats=["p1", "p2"])
    assert sorted(f.message for f in findings if f.rule == "seat-shared-volume") == [
        "services.p1.volumes_from[0]: Seat mounts another container's volumes; "
        "mount only the seat's own named volumes.",
        "services.p2.volumes_from[0]: Seat mounts another container's volumes; "
        "mount only the seat's own named volumes.",
    ]
    # main is not a seat: its volumes_from is not a seat finding
    model["services"]["main"] = {"volumes_from": ["service:p1:ro"]}
    model["services"]["p1"] = {}
    model["services"]["p2"] = {}
    assert [
        f
        for f in lint_compose(path, seats=["p1", "p2"])
        if f.rule == "seat-shared-volume"
    ] == []


def test_seat_host_secret(model, path):
    """Values a seat takes from the runner, not the task (review E6)."""
    task_dir = path.parent
    (task_dir / "seat.env").write_text("SAFE=1\n")
    model["services"].update(
        {
            "p1": {
                "env_file": [{"path": "/etc/hosts"}, {"path": "seat.env"}],
                "environment": {"OPENAI_API_KEY": None, "SAFE": "1"},
                "secrets": [{"source": "hostkey"}, {"source": "local"}],
                "configs": [{"source": "hostcfg"}],
            },
            "main": {"environment": {"ANTHROPIC_API_KEY": None}},
        }
    )
    model["secrets"] = {
        "hostkey": {"environment": "ANTHROPIC_API_KEY"},
        "local": {"file": "seat.env"},
    }
    model["configs"] = {"hostcfg": {"file": "/etc/hosts"}}
    findings = [
        f.message
        for f in lint_compose(path, seats=["p1"])
        if f.rule == "seat-host-secret"
    ]
    assert [m.split(":", 1)[0] for m in findings] == [
        "services.p1.configs[0]",
        "services.p1.env_file[0]",
        "services.p1.environment.OPENAI_API_KEY",
        "services.p1.secrets[0]",
    ]
    # No value is printed, and main (not a seat) is not reported.
    assert not any("/etc/hosts" in m or "ANTHROPIC_API_KEY" in m for m in findings)


def test_seat_host_secret_is_a_default_seat_rule():
    from harbor.lint.models import COMPOSE_RULES, SEAT_RULES

    assert LintRule.SEAT_HOST_SECRET in DEFAULT_RULES
    assert LintRule.SEAT_HOST_SECRET in SEAT_RULES
    assert LintRule.SEAT_HOST_SECRET in COMPOSE_RULES


@pytest.mark.parametrize(
    "key,mode",
    [
        ("userns_mode", "host"),
        ("cgroup", "host"),
        ("network_mode", "Host"),
        ("pid", "SERVICE:main"),
    ],
)
def test_seat_namespace_modes_beyond_the_first_table(model, path, key, mode):
    """userns and cgroup namespaces, and modes written in another case (review E5)."""
    model["services"]["p1"] = {key: mode}
    findings = [
        f for f in lint_compose(path, seats=["p1"]) if f.rule == "seat-shared-namespace"
    ]
    assert [f.message.split(":", 1)[0] for f in findings] == [f"services.p1.{key}"]


def test_seat_volume_subpath_with_a_leading_slash(model, path):
    """A subpath written "/p1" is the same as "p1" (review E3)."""
    model["volumes"] = {"shared": {}}
    model["services"].update(
        {
            "p1": {
                "volumes": [
                    {
                        "type": "volume",
                        "source": "shared",
                        "target": "/a",
                        "volume": {"subpath": "/p1"},
                    }
                ]
            },
            "p2": {
                "volumes": [
                    {
                        "type": "volume",
                        "source": "shared",
                        "target": "/b",
                        "volume": {"subpath": "p1"},
                    }
                ]
            },
        }
    )
    findings = [
        f
        for f in lint_compose(path, seats=["p1", "p2"])
        if f.rule == "seat-shared-volume"
    ]
    assert {f.message.split(":", 1)[0] for f in findings} == {
        "services.p1.volumes[0]",
        "services.p2.volumes[0]",
    }
