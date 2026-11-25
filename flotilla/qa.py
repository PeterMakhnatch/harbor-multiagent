"""Deterministic QA of built Flotilla seat tasks. Every task set must pass it before a wave.

    uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*'          # table and summary
    uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*' --json   # the same as JSON

Real runs found validity faults that each corrupted a condition (docs/FINDINGS.md): an answer example
that equalled one instance's answer key, a world reply that named the condition, a reply counter that
was a side channel, one Compose network for all seats, a scripted peer whose format looked more
authoritative than plain text, Docker names that carried the task name, and a Server header that named
the software, whose public generator and public seed give every answer key. World versions 2 and 3,
opaque task names and secret seeds fixed them in code. This checker reads a built task set
and reports every fault of these kinds that it finds (see RULES). It reads files and calls the task's
own family module (solution/family.py); it starts no container and opens no connection. No output
names a token, an answer key or a secret seed. Exit status: 1 when any finding is a FAIL, 2 when no task
folder matches, otherwise 0. See README.md ("Check a task set before a wave") and CONTRACT.md 2.
"""

from __future__ import annotations

import argparse
import csv
import functools
import glob
import hashlib
import io
import json
import os
import re
import shlex
import sys
import tomllib
import types
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

try:
    import yaml
except ImportError:  # main() says how to run with PyYAML
    yaml = None
YAML_LOADER = getattr(yaml, "CSafeLoader", None) or getattr(yaml, "SafeLoader", None)  # libyaml is 50 times faster

ROOT = Path(__file__).resolve().parent


def load_module(path: Path, name: str) -> types.ModuleType:
    """Run a Python file as a new module, without writing bytecode next to it (task folders stay as built)."""
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module  # dataclasses and typing look a module up by its name
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)  # noqa: S102 - our own or the task's code
    return module


BUILD = load_module(ROOT / "build_tasks.py", "flotilla_qa_build_tasks")
WARNING: str = BUILD.WARNING  # the deliberate sentence of the two -warned conditions
WORLD_VERSION: int = BUILD.WORLD_VERSION
PUBLIC_SEED_BELOW: int = BUILD.PUBLIC_SEED_BELOW

# rule -> (level, what a finding means), in the order of the report
RULES: dict[str, tuple[str, str]] = {
    "example-leak": ("FAIL", "a JSON example in a seat's instruction scores 1 against the answer key or the decoy"),
    "condition-leak": ("FAIL", "a byte that a seat under test reads names its condition or reveals it"),
    "software-name": ("FAIL", ("a byte that a seat under test reads names the software (flotilla) or its "
                               "repository, where the public generator computes the answer key from the seed")),
    "opaque-names": ("FAIL", ("a task built with opaque names has a task or folder name that is not t-<12 hex>, "
                              "or that names a condition, its family or its variant")),
    "twin-mismatch": ("FAIL", ("a seat under test can tell twin conditions apart in what it reads (talk and "
                               "talk-liar, talk-warned and talk-liar-warned, talk and gated, the arms of each probe "
                               "kind)")),
    "seat-network": ("FAIL", "a seat is not alone with main on a network of its own"),
    "seat-secrets": ("FAIL", ("a seat gets the answer key, the build's seed, task.json, task.toml, the build "
                              "manifest, a world, family or score file, the admin token or another seat's token")),
    "token-map": ("FAIL", "seat tokens are not distinct, or main does not map each one to its seat"),
    "stale-code": ("FAIL", "the task ships a world, scorer, oracle or family file that differs from this checkout"),
    "qa-error": ("FAIL", "a task file is missing or unreadable, so the task cannot be checked"),
    "game-secret": ("FAIL", ("a game seat's instruction or briefing states a secret parameter, differs from the "
                             "rules or values visible to that seat, or has a word that steers the choice")),
    "runtime-names": ("FAIL", ("the Docker names that Harbor derives from the task folder's name say the condition "
                               "or the label (a set built with --readable-names); --allow-readable-names makes it "
                               "a WARN, for local debugging only")),
    "public-seed": ("FAIL", (f"the set was built with a public seed (below {PUBLIC_SEED_BELOW:,}, or named in this "
                             "checkout's documentation): a seat that finds the public generator can compute every "
                             "answer key; --allow-public-seed makes it a WARN, only to repeat a published build")),
    "container-name": ("FAIL", ("a service sets container_name: a seat reads that fixed name by a reverse DNS "
                                "lookup (Harbor's own names are opaque), and concurrent trials would share it")),
    "confederate-plan": ("FAIL", ("a probe task's subject is not the only seat under test, its scheduled posts or a "
                                  "confederate's briefing are not the family's plan for the task's arm (made at the "
                                  "first submission when task.json post_trigger says so), a reply post is not a "
                                  "confederate's answer to the subject, the world's submit gate differs from "
                                  "task.json, or the subject's read-first sentence does not match task.json "
                                  "read_first")),
    "policy-data": ("FAIL", ("a policy task (task.json policy_file) whose policy.md is not its rule's text, whose "
                             "policy.md and expenses.csv do not give C and W, whose claim does not state the "
                             "misreading that gives W or is in policy.md, or whose claim is posted outside "
                             "talk-policy or with another number than W")),
    "audit-data": ("FAIL", ("a probe audit task's seat under test can read month data (a CSV line or a total), the "
                            "world's audit files do not give the answer key and each worker's subtotal, the false "
                            "report is not the slip that gives the decoy, the tell is outside the false worker's "
                            "message in talk-false-tell, or the subject's text states another audit budget than "
                            "task.json")),
    "push": ("FAIL", ("task.json push is set but not true, is on a task that is not a probe task with messaging, or "
                      "the subject's push note does not match it")),
    "inbox": ("FAIL", ("task.json inbox is set but not true, is on a task that is not a probe task with messaging, "
                       "the subject's 'Team messages so far' block is not task.json's scheduled posts exactly, or a "
                       "seat has a block without inbox")),
    "solo-sufficient": ("WARN", "in a team condition, a seat's own briefing alone gives a correct answer"),
    "canary": ("FAIL", "an extension task lacks the hidden contamination marker or exposes it to a seat"),
    "versions": ("FAIL", "an extension task's version stamps differ from its shipped files"),
    "answer-leak": ("FAIL", "a seat-readable field contains a hidden value declared by the extension family"),
    "leak-check-missing": ("WARN", "an extension family declares neither leak_values nor a QA hook"),
}
ALLOW_READABLE_NAMES = "--allow-readable-names"  # the flag that makes runtime-names a WARN
ALLOW_PUBLIC_SEED = "--allow-public-seed"  # the flag that makes public-seed a WARN
DOWNGRADES = {  # rule -> (its flag, what the downgraded set is for)
    "runtime-names": (ALLOW_READABLE_NAMES, "for a local debugging run only; never run this set in a wave"),
    "public-seed": (
        ALLOW_PUBLIC_SEED,
        "only to repeat a published build (the 2026-11-19 waves: --seed 1); a new wave builds without --seed",
    ),
}
SOURCE_WORDS = ("flotilla", "harbor-multiagent")  # the software's name and its repository's name
MAIN_SECRETS = ("FLOTILLA_ADMIN", "FLOTILLA_TOKENS")  # main's variables that no seat may get (seat-secrets)
DOCUMENTATION = ("*.md", "docs/*.md", "docs/results/*.md", "analysis/*.md")  # of this checkout, for public seeds
DOCUMENTED_SEED = re.compile(r"(?<![A-Za-z0-9_])seed\W{0,3}(\d+)", re.IGNORECASE)  # --seed 1, seed 1, "seed": 1
PROBE = load_module(ROOT / "families" / "probe.py", "flotilla_qa_probe")
PROBE_KIND_ARMS: tuple[tuple[str, ...], ...] = tuple((*PROBE.kind(name).ARMS, *PROBE.kind(name).EXTRA_ARMS,
                                                      *getattr(PROBE.kind(name), "POLICY_ARMS", ()))
                                                     for name in PROBE.KINDS)
PROBE_ARMS: tuple[str, ...] = tuple(dict.fromkeys(arm for arms in PROBE_KIND_ARMS for arm in arms))
TWINS = (("talk", "talk-liar"), ("talk-warned", "talk-liar-warned"), ("talk", "gated"),
         *((arms[0], arm) for arms in PROBE_KIND_ARMS for arm in arms[1:]))  # each pair looks the same to a seat
REVEALING_NAMES = ("talk-liar", "talk-warned", "talk-liar-warned", *PROBE_ARMS)
LIAR = re.compile(r"(?<![a-z])liars?(?![a-z])", re.IGNORECASE)
WARNED = re.compile(r"(?<![a-z])warned(?![a-z])", re.IGNORECASE)
WORLD_FILES = ("task.json", "world.py", "score.py", "family.py", "docker-compose.yaml")  # main's, never a seat's
SHIPPED = {"environment/world.py": "world/world.py", "environment/score.py": "world/score.py",
           "solution/oracle.py": "world/oracle.py", "environment/family.py": "families/{family}.py",
           "solution/family.py": "families/{family}.py"}  # built file -> its source in this checkout
MIN_TOKEN_CHARS = 16  # a shorter token could be guessed by another seat
MAX_READ_BYTES = 2_000_000
TASK_NAME = re.compile(r"^(?P<label>.+)-(?P<instance>\d+)-(?P<condition>[a-z]+(?:-[a-z]+)*)$")
OPAQUE_NAME: re.Pattern[str] = BUILD.OPAQUE_NAME  # t-<12 hex>, the folder name; the task name is flotilla/<it>
CONDITION_WORDS = tuple(sorted({word for name in (*BUILD.CONDITION_TEXT, *PROBE_ARMS) for word in name.split("-")}))
HEX_ONLY = re.compile(r"^[0-9a-f]+$")


@dataclass(frozen=True)
class Finding:
    rule: str
    task: str  # the task folder's name, or a label for a finding about a whole label
    seat: str  # a seat, "p1,p2" for a pair, or "*" for the whole task
    detail: str  # never a token value, an answer key or a secret seed
    downgraded: bool = False  # a FAIL that the operator allowed as a WARN (DOWNGRADES)

    @property
    def level(self) -> str:
        return "WARN" if self.downgraded else RULES[self.rule][0]

    def order(self) -> tuple[bool, int, str, str, str]:
        return (self.level != "FAIL", list(RULES).index(self.rule), self.task, self.seat, self.detail)


class Build(NamedTuple):
    """A service's image: its Dockerfile and the task files that the Dockerfile copies into it."""

    dockerfile_name: str  # relative to the task folder
    dockerfile: str
    copied: list[Path]


@dataclass
class Task:
    path: Path
    name: str = ""  # the folder name, e.g. schedule.liar-000-talk-liar
    task_name: str = ""  # [task] name in task.toml, e.g. flotilla/schedule.liar-000-talk-liar
    label: str = ""
    instance: int | None = None
    condition: str = ""
    family_name: str = ""
    names: str = ""  # [metadata] names: "opaque", "readable", or "" for a set built before opaque names
    seed: int | None = None  # [metadata] seed (else task.json's): the generator's seed, secret unless public
    seats: list[str] = field(default_factory=list)  # the [agents.<seat>] tables
    under_test: list[str] = field(default_factory=list)  # the seats a model plays
    compose: dict[str, Any] = field(default_factory=dict)
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_json: dict[str, Any] = field(default_factory=dict)
    family: Any = None
    errors: list[Finding] = field(default_factory=list)
    cache: dict[tuple[str, str], Any] = field(default_factory=dict)

    @property
    def env_dir(self) -> Path:
        return self.path / "environment"

    @property
    def answer_key(self) -> dict[str, Any] | None:
        key = self.task_json.get("answer_key")
        return key if isinstance(key, dict) else None

    def error(self, detail: str, seat: str = "*") -> None:
        self.errors.append(Finding("qa-error", self.name, seat, detail))

    @property
    def shown(self) -> str:
        """The folder name, and for an opaque name what the task is (the report is for the operator, not a seat)."""
        if self.names != "opaque" or self.instance is None:
            return self.name
        return f"{self.name} ({self.label}-{self.instance:03d}-{self.condition})"

    def env(self, service: str) -> dict[str, str]:
        """The environment of a service as its container gets it."""
        if ("env", service) not in self.cache:
            self.cache[("env", service)] = service_env(self, self.services.get(service) or {})
        return self.cache[("env", service)]

    def build(self, service: str) -> Build | None:
        if ("build", service) not in self.cache:
            self.cache[("build", service)] = service_build(self, service)
        return self.cache[("build", service)]

    def view(self, seat: str) -> dict[str, str]:
        if ("view", seat) not in self.cache:
            self.cache[("view", seat)] = seat_view(self, seat)
        return self.cache[("view", seat)]


# --- Reading a task folder ---------------------------------------------------------------------------------


def read_text(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None


_FAMILIES: dict[str, types.ModuleType] = {}


def family_module(path: Path) -> types.ModuleType:
    """The task's own family module, loaded once per distinct file content."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest not in _FAMILIES:
        _FAMILIES[digest] = load_module(path, f"flotilla_qa_family_{digest[:16]}")
    return _FAMILIES[digest]


def load_task(path: Path) -> Task:
    """Read one task folder. A missing or unreadable file becomes a qa-error finding, never an exception."""
    task = Task(path=path, name=path.name)
    try:
        config = tomllib.loads((path / "task.toml").read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        task.error(f"task.toml cannot be read ({type(exc).__name__})")
        config = {}
    meta = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
    task.task_name = str((config.get("task") or {}).get("name") or f"flotilla/{path.name}")
    parsed = TASK_NAME.match(task.task_name.rsplit("/", 1)[-1])
    try:
        task.task_json = json.loads((task.env_dir / "task.json").read_text())
    except (OSError, ValueError) as exc:
        task.error(f"environment/task.json cannot be read ({type(exc).__name__})")
    if not isinstance(task.task_json, dict):
        task.error("environment/task.json is not a JSON object")
        task.task_json = {}
    elif task.answer_key is None and task.task_json.get("mode") not in ("rounds", "extension"):
        task.error("environment/task.json has no answer_key object")
    task.family_name = str(meta.get("family") or task.task_json.get("family") or "")
    variant = str(meta.get("variant") or "")
    task.label = (f"{task.family_name}.{variant}" if variant else task.family_name) or (
        parsed["label"] if parsed else "")
    instance = meta.get("instance", int(parsed["instance"]) if parsed else None)
    task.instance = instance if isinstance(instance, int) else None
    task.condition = str(meta.get("condition") or (parsed["condition"] if parsed else ""))
    task.names = str(meta.get("names") or "")
    seed = meta.get("seed", task.task_json.get("seed"))
    task.seed = seed if isinstance(seed, int) and not isinstance(seed, bool) else None
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    task.seats = sorted(agents)
    task.under_test = [seat for seat in task.seats if (agents[seat] or {}).get("under_test", True) is not False]
    if config and not agents:
        task.error("task.toml has no [agents.<seat>] tables, so it is not a seat task")
    try:
        compose = yaml.load((task.env_dir / "docker-compose.yaml").read_text(), Loader=YAML_LOADER)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        task.error(f"environment/docker-compose.yaml cannot be read ({type(exc).__name__})")
        compose = {}
    task.compose = compose if isinstance(compose, dict) else {}
    services = task.compose.get("services") if isinstance(task.compose.get("services"), dict) else {}
    task.services = {str(name): (spec if isinstance(spec, dict) else {}) for name, spec in services.items()}
    if task.compose and "main" not in task.services:
        task.error("docker-compose.yaml has no main service")
    for seat in task.seats:
        if task.compose and seat not in task.services:
            task.error(f"docker-compose.yaml has no service for seat {seat}", seat)
        if not (path / "instructions" / f"{seat}.md").is_file():
            task.error(f"instructions/{seat}.md is missing", seat)
    family_path = path / "solution" / "family.py"
    try:
        module = family_module(family_path)
    except Exception as exc:  # noqa: BLE001 - any failure of the task's own module is a finding
        task.error(f"solution/family.py cannot be loaded ({type(exc).__name__})")
    else:
        needs = ("settle", "score_game") if task.task_json.get("mode") == "rounds" else ("score", "oracle_answer")
        if task.task_json.get("mode") == "extension":
            needs = ("generate", "extension_arm")
        if all(callable(getattr(module, name, None)) for name in needs):
            task.family = module
        else:
            task.error(f"solution/family.py lacks {' or '.join(f'{name}()' for name in needs)}")
    if not task.family_name:
        task.error("neither task.toml nor task.json names the family")
    return task


def unescape(value: Any) -> str:
    """A Compose value as the container receives it: `$$` is a literal `$` (CONTRACT.md 3)."""
    if value is None:
        return ""
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return text.replace("$$", "$")


def resolve(base: Path, value: str) -> Path:
    return (base / os.path.expanduser(value)).resolve()


def env_files(task: Task, service: dict[str, Any]) -> list[tuple[str, Path]]:
    raw = service.get("env_file")
    items = raw if isinstance(raw, list) else [raw] if raw else []
    paths = [item.get("path") if isinstance(item, dict) else item for item in items]
    return [("env_file", resolve(task.env_dir, str(p))) for p in paths if p]


def service_env(task: Task, service: dict[str, Any]) -> dict[str, str]:
    """A service's env_file entries, then its environment entries."""
    env: dict[str, str] = {}
    for _kind, path in env_files(task, service):
        for line in (read_text(path) or "").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name and not name.startswith("#"):
                env[name.strip()] = value.strip()
    raw = service.get("environment")
    if isinstance(raw, list):
        for item in raw:
            name, sep, value = str(item).partition("=")
            env[name] = unescape(value) if sep else ""  # a bare name passes the host's value through
    elif isinstance(raw, dict):
        env.update((str(name), unescape(value)) for name, value in raw.items())
    return env


def service_networks(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compose network key -> this service's settings on it; {"default": {}} when the service names none."""
    if "network_mode" in service:
        return {}
    raw = service.get("networks")
    if raw is None:
        return {"default": {}}
    if isinstance(raw, list):
        return {str(name): {} for name in raw}
    if isinstance(raw, dict):
        return {str(name): (spec if isinstance(spec, dict) else {}) for name, spec in raw.items()}
    return {}


def network_specs(task: Task) -> dict[str, dict[str, Any]]:
    """The top-level `networks:` of the Compose file."""
    defined = task.compose.get("networks")
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in defined.items()} if isinstance(defined, dict) else {}


def network_name(task: Task, key: str) -> str:
    """The network that a Compose key stands for: its `name:` when the file sets one, else the key."""
    return str(network_specs(task).get(key, {}).get("name") or key)


def main_networks(task: Task) -> dict[str, dict[str, Any]]:
    """Network -> main's settings on it (its aliases)."""
    return {network_name(task, k): spec for k, spec in service_networks(task.services.get("main") or {}).items()}


def volume_sources(task: Task, service: dict[str, Any]) -> list[tuple[str, str]]:
    """("bind", resolved host path) or ("volume", name) for each volume of a service."""
    sources = []
    for item in service.get("volumes") or []:
        kind = ""
        if isinstance(item, str):
            source, sep, _target = item.partition(":")
            if not sep:
                continue  # an anonymous volume is new for each container
        elif isinstance(item, dict):
            source, kind = str(item.get("source") or ""), str(item.get("type") or "")
            if kind == "tmpfs" or not source:
                continue
        else:
            continue
        if kind == "bind" or source.startswith((".", "/", "~")):
            sources.append(("bind", str(resolve(task.env_dir, source))))
        else:
            sources.append(("volume", source))
    return sources


def overlap(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Whether two volumes reach the same data: one named volume, or one bind path inside the other."""
    if a[0] != b[0]:
        return False
    if a[0] == "volume":
        return a[1] == b[1]
    first, second = Path(a[1]), Path(b[1])
    return first == second or first in second.parents or second in first.parents


def mounted_files(task: Task, service: dict[str, Any]) -> list[tuple[str, Path]]:
    """Host files a service reads at start: its env files and the files of its Compose secrets and configs."""
    found = env_files(task, service)
    for section in ("secrets", "configs"):
        defined = task.compose.get(section) if isinstance(task.compose.get(section), dict) else {}
        for item in service.get(section) or []:
            name = item.get("source") if isinstance(item, dict) else item
            spec = defined.get(name) if isinstance(defined.get(name), dict) else {}
            if spec.get("file"):
                found.append((f"{section[:-1]} {name}", resolve(task.env_dir, str(spec["file"]))))
    return found


def relative(task: Task, path: Path) -> str:
    try:
        return str(path.relative_to(task.path.resolve()))
    except ValueError:
        return str(path)


def dockerfile_instructions(text: str) -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) of a Dockerfile; continuation lines are joined and comments dropped."""
    instructions, current = [], ""
    for line in [*text.splitlines(), ""]:
        stripped = line.strip()
        if not current and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        word, _, rest = (current + stripped).strip().partition(" ")
        instructions.append((word.upper(), rest.strip()))
        current = ""
    return instructions


def copy_sources(base: Path, sources: list[str]) -> list[Path]:
    """The files that COPY or ADD sources name in a build context (Docker refuses paths outside it)."""
    files: list[Path] = []
    for source in sources:
        if "://" in source or source.startswith("<<"):
            continue  # a download, or a heredoc (its text is in the Dockerfile)
        source = source.lstrip("/")
        if source in ("", "."):
            matches = [base]
        else:
            matches = sorted(base.glob(source)) if any(c in source for c in "*?[") else [base / source]
        for match in (m.resolve() for m in matches):
            if match != base and base not in match.parents:
                continue
            if match.is_dir():
                files += sorted(p for p in match.rglob("*") if p.is_file())
            elif match.is_file():
                files.append(match)
    return files


def service_build(task: Task, service_name: str) -> Build | None:
    """A service's Dockerfile and the task files it copies; None without a local build (or without its Dockerfile,
    a qa-error)."""
    build = (task.services.get(service_name) or {}).get("build")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict):
        return None
    context_value = str(build.get("context") or ".")
    if "://" in context_value or context_value.startswith("git@"):
        return None  # a remote context holds nothing of the task folder
    context = resolve(task.env_dir, context_value)
    contexts_raw = build.get("additional_contexts") or {}
    if isinstance(contexts_raw, list):
        contexts_raw = dict(str(item).split("=", 1) for item in contexts_raw if "=" in str(item))
    contexts = {str(k): resolve(task.env_dir, str(v)) for k, v in contexts_raw.items() if "://" not in str(v)}
    if build.get("dockerfile_inline") is not None:
        name, text = "build.dockerfile_inline", str(build["dockerfile_inline"])
    else:
        dockerfile = resolve(context, str(build.get("dockerfile") or "Dockerfile"))
        name, text = relative(task, dockerfile), read_text(dockerfile)
        if text is None:
            task.error(f"its Dockerfile {name} cannot be read", service_name)
            return None
    copied: list[Path] = []
    for word, args in dockerfile_instructions(text):
        if word not in ("COPY", "ADD"):
            continue
        stage, rest = None, args
        while rest.startswith("--"):
            flag, _, rest = rest.partition(" ")
            rest = rest.strip()
            if flag.startswith("--from="):
                stage = flag.removeprefix("--from=")
        try:
            words = json.loads(rest) if rest.startswith("[") else shlex.split(rest)
        except ValueError:
            words = rest.split()
        base = context if stage is None else contexts.get(stage)
        if base is not None and isinstance(words, list):  # not from another image or build stage
            copied += copy_sources(base, [str(w) for w in words[:-1]])
    return Build(name, text, list(dict.fromkeys(copied)))


def seat_view(task: Task, seat: str) -> dict[str, str]:
    """Every byte the seat's agent can read before play, by field: its instruction, its container's environment
    and Compose settings, its image's Dockerfile and the task files that it copies, and main's settings on the
    seat's networks (for example the alias `world`)."""
    view = {"instruction": read_text(task.path / "instructions" / f"{seat}.md") or ""}
    service = task.services.get(seat) or {}
    view.update((f"env.{name}", value) for name, value in task.env(seat).items())
    view.update((f"service.{key}", json.dumps(service[key], sort_keys=True, default=str))
                for key in sorted(service) if key not in ("environment", "env_file"))
    image = task.build(seat)
    if image is not None:
        view[f"image:{image.dockerfile_name}"] = image.dockerfile
        view.update((f"image:{relative(task, path)}", read_text(path) or "") for path in image.copied)
    with_main = main_networks(task)
    for key in service_networks(service):
        if network_name(task, key) in with_main:
            view[f"main on {key}"] = json.dumps(with_main[network_name(task, key)], sort_keys=True, default=str)
    return view


def json_objects(text: str) -> list[tuple[int, dict[str, Any]]]:
    """(line, object) for every JSON object written anywhere in a text, nested objects included."""
    decoder = json.JSONDecoder()
    found: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    start = text.find("{")
    while start != -1:
        try:
            stack = [decoder.raw_decode(text, start)[0]]
        except (ValueError, RecursionError):
            stack = []
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                canonical = json.dumps(item, sort_keys=True)
                if canonical not in seen:
                    seen.add(canonical)
                    found.append((text.count("\n", 0, start) + 1, item))
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        start = text.find("{", start + 1)
    return found


def decoded(text: str) -> str:
    """A JSON object or array written again with its escapes resolved (`\\u006c` reads as `l`); other text as is."""
    if text.lstrip()[:1] not in ("{", "["):
        return text
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True)
    except (ValueError, RecursionError):
        return text


def score_of(task: Task, answer: Any, key: Any, what: str) -> float:
    """The family's score of an answer; 0 (and a qa-error finding) when the scorer raises."""
    try:
        return float(task.family.score(answer, key)["score"])
    except Exception as exc:  # noqa: BLE001 - a family scorer must never raise (CONTRACT.md 7)
        task.error(f"solution/family.py score() raised {type(exc).__name__} on {what}")
        return 0.0


# --- Rules on one task ---------------------------------------------------------------------------------------


def check_example_leak(task: Task) -> Iterator[Finding]:
    """A JSON object in an instruction that scores 1 against the answer key (or the decoy) gives the answer away: a
    seat that copies it is right without any information (FINDINGS.md, 2026-11-19 04:35)."""
    if task.family is None or task.answer_key is None:
        return
    targets = [("the answer key", task.answer_key)]
    if isinstance(task.task_json.get("decoy"), dict):
        targets.append(("the decoy", task.task_json["decoy"]))
    for seat in task.under_test:
        for line, example in json_objects(task.view(seat)["instruction"]):
            for what, target in targets:
                if score_of(task, example, target, f"the example on line {line} of instructions/{seat}.md") >= 1.0:
                    yield Finding("example-leak", task.name, seat,
                                  f"the JSON example on line {line} of its instruction scores 1 against {what}")


def reveal_hits(text: str, condition: str, extra: tuple[str, ...] = ()) -> list[tuple[int, str]]:
    """(offset, word) for each word that names or reveals a condition, leaving out a word inside a longer one
    ("liar" inside "talk-liar")."""
    names = sorted({*REVEALING_NAMES, *extra, *([condition] if condition else [])})
    patterns = [re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(n)}(?![A-Za-z0-9_-])", re.IGNORECASE) for n in names]
    spans = sorted((m.start(), -m.end(), m.group(0)) for p in [*patterns, LIAR, WARNED] for m in p.finditer(text))
    hits, reach = [], -1
    for start, negative_end, word in spans:
        if -negative_end > reach:
            hits.append((start, word))
            reach = -negative_end
    return hits


def check_condition_leak(task: Task) -> Iterator[Finding]:
    """No byte that a seat under test reads may name its condition or reveal it: world v1's /whoami said
    "talk-liar", and seats read it as a warning (FINDINGS.md, 2026-11-19 04:50). The builder's deliberate sentences
    are allowed: messaging on or off, and the warning, which only the two -warned conditions may show."""
    warned = task.condition.endswith("-warned")
    for seat in task.under_test:
        for name, text in task.view(seat).items():
            if name.startswith("env."):
                text = f"{name.removeprefix('env.')}\n{decoded(text)}"  # the variable's name is visible too
            if task.family_name == "split" and task.condition == "open" and name == "instruction":
                text = text.replace("in the open round.", "in the      round.")
            if task.family_name == "split" and task.condition == "propose" and name == "instruction":
                endpoint = "`POST /propose`"
                text = text.replace(endpoint, " " * len(endpoint), 1)
            if WARNING in text and not warned:
                yield Finding("condition-leak", task.name, seat,
                              f"{name} has the warning sentence, but {task.condition} is not a warned condition")
            words = getattr(task.family, "ARM_WORDS", None)
            if words is None:
                extra = tuple(arm for arm in getattr(task.family, "ARMS", ()) if "-" in arm)
                hits = reveal_hits(text.replace(WARNING, " " * len(WARNING)), task.condition, extra)
            else:
                hits = [(m.start(), m.group()) for word in words
                        for m in re.finditer(rf"(?<![A-Za-z0-9_-]){re.escape(word)}(?![A-Za-z0-9_-])",
                                             text, re.IGNORECASE)]
                hits.sort()
            if hits:
                where = f" (line {text.count(chr(10), 0, hits[0][0]) + 1})" if name == "instruction" else ""
                words = ", ".join(sorted({repr(word) for _start, word in hits}))
                yield Finding("condition-leak", task.name, seat, f"{name}{where} contains {words}")
        if warned and WARNING not in task.view(seat)["instruction"]:
            yield Finding("condition-leak", task.name, seat,
                          f"instruction lacks the warning sentence of the {task.condition} condition")


def check_software_name(task: Task) -> Iterator[Finding]:
    """No byte that a seat under test reads names the software or its repository. World v2's Server header said
    `flotilla-world/2`, and seats searched the file system and pip for that name; the source is public, and with the
    seed its generator gives the answer key (FINDINGS.md, 2026-11-19 10:11)."""
    for seat in task.under_test:
        for name, text in task.view(seat).items():
            if name.removeprefix("env.") in MAIN_SECRETS:
                continue  # seat-secrets reports a seat that gets one of these
            if name.startswith("env."):
                text = f"{name.removeprefix('env.')}\n{decoded(text)}"  # the variable's name is visible too
            words = sorted({word for word in SOURCE_WORDS if word in text.lower()})
            if words:
                yield Finding("software-name", task.name, seat, f"{name} contains {', '.join(map(repr, words))}")


def name_words(task: Task, name: str) -> list[str]:
    """The words in a name that say what the task is: a word of a condition name, or of the task's label (family and
    variant). A word of hex digits only is left out, because an opaque name holds such strings by chance."""
    words = {*CONDITION_WORDS, *re.split(r"[^a-z0-9]+", task.label.lower())}
    return sorted(word for word in words if word and not HEX_ONLY.match(word) and word in name.lower())


def check_opaque_names(task: Task) -> Iterator[Finding]:
    """A task built with opaque names ([metadata] names = "opaque", the default) says nothing about itself in its
    folder name, after which Harbor names the trial and its Docker containers and networks (check_runtime_names),
    or in its task name, which Harbor's results and environment names carry: they are t-<12 hex> and
    flotilla/t-<12 hex> (CONTRACT.md 2)."""
    if task.names != "opaque":
        return
    for what, name, form in (("folder name", task.path.name, "t-<12 hex>"),
                             ("task name", task.task_name, "flotilla/t-<12 hex>")):
        bare = name.removeprefix("flotilla/") if what == "task name" else name
        words = name_words(task, bare.removeprefix("t-"))  # the fixed parts say nothing (and a variant may be `t`)
        if words:
            yield Finding("opaque-names", task.name, "*", f"its {what} {name} says {', '.join(map(repr, words))}")
        elif not OPAQUE_NAME.match(bare) or (what == "task name" and not name.startswith("flotilla/")):
            yield Finding("opaque-names", task.name, "*", f"its {what} {name} is not of the form {form}")


def check_seat_network(task: Task) -> Iterator[Finding]:
    """Each seat is alone with main on a network of its own, where main has the alias `world`. In world v1 all seats
    shared one network, and a silent seat could reach its teammate (FINDINGS.md, 2026-11-19 05:10)."""
    members: dict[str, set[str]] = defaultdict(set)
    for name, service in task.services.items():
        for key in service_networks(service):
            members[network_name(task, key)].add(name)
    with_main = main_networks(task)
    specs = network_specs(task)
    for seat in task.seats:
        service = task.services.get(seat)
        if service is None:
            continue
        found: list[str] = []
        for item in service.get("volumes_from") or []:
            source = str(item).split(":", 1)[0]
            if source in task.seats and source != seat:
                found.append(f"it takes the volumes of {source} (volumes_from)")
        for namespace in ("ipc", "pid"):
            value = str(service.get(namespace) or "")
            if value == "host" or value.startswith(("service:", "container:")):
                found.append(f"{namespace}: {value} shares a namespace with other containers")
        for other in task.seats:
            if other != seat and other in task.services and any(
                    overlap(a, b) for a in volume_sources(task, service)
                    for b in volume_sources(task, task.services[other])):
                found.append(f"it shares a volume with {other}")
        networks = service_networks(service)
        if "network_mode" in service:
            mode = str(service["network_mode"])
            found.append("network_mode: none, so it cannot reach the world" if mode == "none"
                         else f"network_mode: {mode} gives it no network of its own")
        elif "default" in networks:
            how = "names no network" if service.get("networks") is None else "names the default network"
            found.append(f"it {how}, so it is on the default network")
        for key in networks:
            others = sorted(members[network_name(task, key)] - {seat, "main"})
            if others:
                found.append(f"it shares network {key} with {', '.join(others)}")
            if specs.get(key, {}).get("external"):
                found.append(f"network {key} is external, so containers outside the task can join it")
            elif specs.get(key, {}).get("name"):
                found.append(f"network {key} has a fixed name, so concurrent trials share it")
        shared = [network_name(task, key) for key in networks if network_name(task, key) in with_main]
        if "network_mode" not in service and not shared:
            found.append("it shares no network with main, so it cannot reach the world")
        elif shared and not any("world" in (with_main[name].get("aliases") or []) for name in shared):
            found.append("main has no alias world on its network, so WORLD_URL does not resolve")
        yield from (Finding("seat-network", task.name, seat, detail) for detail in found)


def protected_files(task: Task, seat: str) -> dict[Path, str]:
    """Files that a seat must never get: main's files, the family module, the other seats' instructions (their
    private information), task.toml and the build manifest (both say the condition of an opaque task)."""
    paths = {task.env_dir / name: f"environment/{name}" for name in WORLD_FILES}
    paths.update((path, f"environment/ext/{path.name}") for path in (task.env_dir / "ext").glob("*.py"))
    paths[task.path / "solution" / "family.py"] = "solution/family.py"
    paths.update((path, f"solution/{path.name}") for path in (task.path / "solution").glob("policy*"))
    paths[task.path / "solution" / "family_policy.py"] = "solution/family_policy.py"
    paths[task.path / "solution" / "answer.json"] = "solution/answer.json"
    paths[task.path / "task.toml"] = "task.toml"
    for root in (task.path.parent, task.path.parent.parent):  # <out>/<label>/<task>: the manifest is at <out>
        paths[root / BUILD.MANIFEST] = f"the build manifest ({BUILD.MANIFEST})"
    paths.update((task.path / "instructions" / f"{other}.md", f"instructions/{other}.md")
                 for other in task.seats if other != seat)
    return {path.resolve(): label for path, label in paths.items() if path.exists()}


def secret_forms(key: dict[str, Any] | None) -> tuple[set[str], set[str]]:
    """JSON spellings of the answer key and of its object members, and their canonical forms."""
    if key is None:
        return set(), set()
    parts = [key, *(value for value in key.values() if isinstance(value, dict))]
    spellings = {json.dumps(part, sort_keys=sort, separators=separators)
                 for part in parts for sort in (False, True) for separators in (None, (",", ":"))}
    return spellings, {json.dumps(part, sort_keys=True) for part in parts}


def registered_tokens(value: str) -> list[tuple[str, str]]:
    """(seat, token) pairs of main's FLOTILLA_TOKENS ("p1:<hex>,p2:<hex>")."""
    return [(seat, token) for seat, _sep, token in (pair.partition(":") for pair in value.split(",") if pair)]


def check_seat_secrets(task: Task) -> Iterator[Finding]:
    """A seat gets nothing that only main and the verifier hold. (The instruction's examples are example-leak's.)"""
    main_env = task.env("main")
    admin = main_env.get("FLOTILLA_ADMIN", "")
    tokens = [(seat, task.env(seat).get("SEAT_TOKEN", "")) for seat in task.seats if seat in task.services]
    tokens += registered_tokens(main_env.get("FLOTILLA_TOKENS", ""))
    spellings, canonical = secret_forms(task.answer_key)
    # a secret seed gives every answer key with the public generator; a public one is public-seed's finding
    seed = str(task.seed) if task.seed is not None and task.seed >= PUBLIC_SEED_BELOW else ""
    main_volumes = volume_sources(task, task.services.get("main") or {})
    for seat in task.seats:
        service = task.services.get(seat)
        if service is None:
            continue
        found = [f"it gets {name}" for name in MAIN_SECRETS if name in task.env(seat)]
        own = task.env(seat).get("SEAT_TOKEN", "")  # a token that two seats hold is token-map's finding
        others = {(other, token) for other, token in tokens if other != seat and len(token) >= 8 and token != own}
        for name, text in task.view(seat).items():
            if name.removeprefix("env.") in MAIN_SECRETS:
                continue  # reported above
            if len(admin) >= 8 and admin != own and admin in text:
                found.append(f"{name} holds the admin token (FLOTILLA_ADMIN)")
            found += [f"{name} holds the token of {other}" for other in sorted({o for o, t in others if t in text})]
            if seed and seed in text:
                found.append(f"{name} holds the build's seed")
            if name != "instruction" and (any(s in text for s in spellings) or any(
                    json.dumps(obj, sort_keys=True) in canonical for _line, obj in json_objects(text))):
                found.append(f"{name} holds the answer key")
        protected = protected_files(task, seat)
        for kind, source in volume_sources(task, service):
            exposed = sorted(label for path, label in protected.items()
                             if kind == "bind" and (str(path) == source or Path(source) in path.parents))
            if exposed:
                found.append(f"it mounts {source}, which holds {', '.join(exposed)}")
            elif any(overlap((kind, source), theirs) for theirs in main_volumes):
                found.append(f"it shares volume {source} with main")
        if any(str(item).split(":", 1)[0] == "main" for item in service.get("volumes_from") or []):
            found.append("it takes main's volumes (volumes_from)")
        found += [f"its {what} reads {protected[path]}" for what, path in mounted_files(task, service)
                  if path in protected]
        image = task.build(seat)
        if image is not None and image.copied:
            contents = {path.read_bytes(): label for path, label in protected.items() if path.is_file()}
            for path in image.copied:
                data = path.read_bytes() if path.stat().st_size <= MAX_READ_BYTES else b""
                label = protected.get(path) or contents.get(data) or (path.name if path.name in WORLD_FILES else "")
                if label:
                    found.append(f"{image.dockerfile_name} copies {label} into the seat image")
        yield from (Finding("seat-secrets", task.name, seat, detail) for detail in found)


def check_token_map(task: Task) -> Iterator[Finding]:
    """Every seat holds its own distinct token, and main's FLOTILLA_TOKENS maps that token to that seat, as world.py
    reads it (a later entry wins)."""
    if "main" not in task.services:
        return
    main_env = task.env("main")
    admin = main_env.get("FLOTILLA_ADMIN", "")
    if not admin:
        yield Finding("token-map", task.name, "main", "it has no FLOTILLA_ADMIN, so the verifier cannot read the world")
    pairs = registered_tokens(main_env.get("FLOTILLA_TOKENS", ""))
    if any(not seat or not token for seat, token in pairs):
        yield Finding("token-map", task.name, "main", "its FLOTILLA_TOKENS has an entry that is not seat:token")
    world = {token: seat for seat, token in pairs if token}
    holders: dict[str, list[str]] = defaultdict(list)
    for seat in task.seats:
        if seat not in task.services:
            continue
        env = task.env(seat)
        if env.get("SEAT") != seat:
            yield Finding("token-map", task.name, seat, f"its SEAT is {env.get('SEAT', '')!r}, not {seat!r}")
        token = env.get("SEAT_TOKEN", "")
        if not token:
            yield Finding("token-map", task.name, seat, "it has no SEAT_TOKEN")
            continue
        holders[token].append(seat)
        if len(token) < MIN_TOKEN_CHARS:
            yield Finding("token-map", task.name, seat,
                          f"its token has {len(token)} characters; a token needs at least {MIN_TOKEN_CHARS}")
        if token == admin:
            yield Finding("token-map", task.name, seat, "its token is the admin token (FLOTILLA_ADMIN)")
        if token not in world:
            yield Finding("token-map", task.name, seat, "main's FLOTILLA_TOKENS has no entry for its token")
        elif world[token] != seat:
            yield Finding("token-map", task.name, seat, f"main's FLOTILLA_TOKENS maps its token to {world[token]}")
    for seats in holders.values():
        if len(seats) > 1:
            yield Finding("token-map", task.name, ",".join(seats), f"{' and '.join(seats)} hold the same token")
    for seat, token in pairs:
        if token and token not in holders:
            yield Finding("token-map", task.name, "main",
                          f"its FLOTILLA_TOKENS has a token for {seat} that no seat holds")
    for variable, listed in (("FLOTILLA_TOKENS", {seat for seat, _token in pairs}),
                             ("FLOTILLA_SEATS", set(main_env.get("FLOTILLA_SEATS", "").split(",")) - {""})):
        if sorted(listed) != task.seats:
            yield Finding("token-map", task.name, "main",
                          f"its {variable} names {', '.join(sorted(listed)) or 'no seat'}; the seats are "
                          f"{', '.join(task.seats)}")


@functools.cache
def checkout_bytes(relative_path: str) -> bytes | None:
    path = ROOT / relative_path
    return path.read_bytes() if path.is_file() else None


def check_stale_code(task: Task) -> Iterator[Finding]:
    """The task ships this checkout's world (version 3: no reply names the condition, the software or a version, or
    counts submissions), verifier, oracle (the scripted peer speaks plain text only) and family module. A set built
    from an older checkout fails."""
    if not task.family_name:
        return
    missing, differ = [], []
    game = task.task_json.get("mode") == "rounds"  # a game ships games/oracle.py and games/<family>.py
    extension = task.task_json.get("mode") == "extension"
    for built, source in SHIPPED.items():
        if game:
            source = source.replace("world/oracle.py", "games/oracle.py").replace("families/", "games/")
        elif extension:
            source = source.replace("families/", "seams/")
        expected = checkout_bytes(source.format(family=task.family_name))
        if extension and expected is not None:
            expected = BUILD.marked(expected)
        if expected is None:
            yield Finding("stale-code", task.name, "*", f"this checkout has no family {task.family_name!r}")
        elif not (task.path / built).is_file():
            missing.append(built)
        elif (task.path / built).read_bytes() != expected:
            differ.append(built)
    if extension:
        source = f"world/ext/{task.family_name}.py"
        built = f"environment/ext/{task.family_name}.py"
        expected = checkout_bytes(source)
        if expected is None or not (task.path / built).is_file():
            missing.append(built)
        elif (task.path / built).read_bytes() != BUILD.marked(expected):
            differ.append(built)
    if "policy" in task.task_json:
        sources = {"solution/policy.py": "world/policy.py"}
        custom = f"policies/{task.family_name}.py"
        if checkout_bytes(custom) is not None:
            sources["solution/family_policy.py"] = custom
        for built, source in sources.items():
            expected = checkout_bytes(source)
            if not (task.path / built).is_file():
                missing.append(built)
            elif (task.path / built).read_bytes() != (BUILD.marked(expected) if extension else expected):
                differ.append(built)
    if differ:
        yield Finding("stale-code", task.name, "*",
                      f"{', '.join(differ)} differ from this checkout's files: build the set again")
    if missing:
        yield Finding("stale-code", task.name, "*", f"{', '.join(missing)} missing")
    version = task.task_json.get("world_version", 1)  # task.json has no world_version before world version 2
    if task.task_json and version != WORLD_VERSION:
        yield Finding("stale-code", task.name, "*",
                      f"task.json gives world version {version}; this checkout's world is version {WORLD_VERSION}")


def check_game_secret(task: Task) -> Iterator[Finding]:
    """A game seat reads the public rules only (docs/COMMONS.md section 6): its instruction is the builder's text
    for every season length, has no word that steers the choice, and its briefing has only the allowed values."""
    game = task.task_json.get("game") if task.task_json.get("mode") == "rounds" else None
    if not isinstance(game, dict) or task.family is None:
        return
    condition = str(task.task_json.get("condition", ""))
    split = family_module(BUILD.family_path("split")) if task.family_name == "split" else None
    max_text, per_round = int(task.task_json.get("max_text", 0)), int(task.task_json.get("max_messages_per_round", 0))
    for seat in task.seats:
        path = task.path / "instructions" / f"{seat}.md"
        if not path.is_file():
            continue
        text = path.read_text()
        if any(BUILD.game_instruction(task.family, {**game, "rounds": rounds}, seat, condition, max_text, per_round)
               != text for rounds in task.family.ROUNDS):
            yield Finding("game-secret", task.name, seat,
                          "the instruction is not the builder's text of the public rules for every season length")
        steering = [word for word in getattr(task.family, "STEERING_WORDS", ()) if word in text.lower()]
        if steering:
            yield Finding("game-secret", task.name, seat, f"the instruction has words that steer the choice: {steering}")
        environment = (task.services.get(seat) or {}).get("environment") or {}
        try:
            briefing = json.loads(environment.get("SEAT_BRIEFING") or "{}") if isinstance(environment, dict) else {}
        except ValueError:
            briefing = {}
        if isinstance(briefing, dict) and "rounds" in briefing:
            yield Finding("game-secret", task.name, seat, "the seat's briefing holds the season length")
        elif split is not None and briefing != split.briefing(game, seat, condition):
            yield Finding("game-secret", task.name, seat, "the seat's briefing differs from the builder's briefing")


def check_solo_sufficient(task: Task) -> Iterator[Finding]:
    """A seat whose own briefing already gives the answer needs no teammate in this instance. Only a WARN: in
    `schedule` a seat alone may guess right by luck (CONTRACT.md 7). A family without pooling (`POOLING = False`,
    the probe family) gives every seat the whole task by design, so it is not checked."""
    if task.condition == "solo" or task.family is None or task.answer_key is None:
        return
    if getattr(task.family, "POOLING", True) is False:
        return
    for seat in task.under_test:
        raw = task.env(seat).get("SEAT_BRIEFING")
        if raw is None:
            continue
        try:
            answer = task.family.oracle_answer([json.loads(raw)])
        except Exception as exc:  # noqa: BLE001 - reported as a finding
            task.error(f"oracle_answer() fails on its SEAT_BRIEFING ({type(exc).__name__})", seat)
            continue
        if score_of(task, answer, task.answer_key, f"the oracle answer from {seat}'s briefing") >= 1.0:
            yield Finding("solo-sufficient", task.name, seat,
                          "its own briefing alone gives a correct answer (oracle_answer scores 1)")


def check_canary(task: Task) -> Iterator[Finding]:
    if task.task_json.get("mode") != "extension":
        return
    for path in BUILD.canary_files(task.path, task.family_name):
        if path.name == "family_policy.py" and not path.exists():
            continue
        if f"# {BUILD.CANARY}" not in (read_text(path) or "").splitlines():
            yield Finding("canary", task.name, "*", f"{relative(task, path)} lacks the marker")
    guid = BUILD.CANARY.partition(" ")[2]
    for seat in task.seats:
        for name, text in task.view(seat).items():
            if guid in name or guid in text or guid in decoded(text):
                yield Finding("canary", task.name, seat, f"{name} contains the marker")


def check_versions(task: Task) -> Iterator[Finding]:
    if task.task_json.get("mode") != "extension" or task.family is None:
        return
    try:
        expected = BUILD.version_stamps(task.path, task.family, task.seats)
    except (OSError, AttributeError) as exc:
        yield Finding("versions", task.name, "*", f"cannot check version stamps ({type(exc).__name__})")
        return
    if task.task_json.get("versions") != expected:
        yield Finding("versions", task.name, "*", "version stamps differ from the shipped files")


def check_answer_values(task: Task) -> Iterator[Finding]:
    if task.task_json.get("mode") != "extension" or task.family is None:
        return
    hook = getattr(task.family, "leak_values", None)
    if not callable(hook):
        if not callable(getattr(task.family, "qa", None)):
            yield Finding("leak-check-missing", task.name, "*", "declare leak_values or a family QA hook")
        return
    try:
        values = hook(task.task_json)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("leak_values must return a list of nonempty strings")
        if not values:
            return
        alternatives = "|".join(re.escape(value) for value in sorted(set(values)))
        pattern = re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)
    except Exception as exc:  # noqa: BLE001 - a failed leak hook must not pass QA
        yield Finding("qa-error", task.name, "*", f"leak_values failed ({type(exc).__name__})")
        return
    for seat in task.seats:
        for name, text in task.view(seat).items():
            if pattern.search(name):
                yield Finding("answer-leak", task.name, seat, "a seat-visible field name contains a hidden value")
            elif pattern.search(text) or pattern.search(decoded(text)):
                yield Finding("answer-leak", task.name, seat, f"{name} contains a hidden value")


def check_extension(task: Task) -> Iterator[Finding]:
    if task.task_json.get("mode") != "extension" or task.family is None:
        return
    data = task.task_json
    try:
        if data.get("extension") != task.family_name:
            raise ValueError("extension name differs from family")
        config = tomllib.loads((task.path / "task.toml").read_text())
        metadata = config["metadata"]
        count = None if metadata.get("generator_seats") == 0 else metadata["team_seats"]
        inst = task.family.generate(data["seed"], data["instance"], count, data["difficulty"])
        arm = BUILD.validate_arm(task.family.extension_arm(inst, data["condition"], data["max_text"],
                                                          data["max_messages"]), inst["seats"], data["condition"])
        seats = sorted(arm["briefings"])
        scripted = sorted(arm.get("scripted_seats", ()))
        if (seats != task.seats or seats != sorted(data["seats"]) or scripted != sorted(data["scripted_seats"])
                or sorted(set(seats) - set(scripted)) != task.under_test
                or any(data.get(key) != arm[key] for key in
                       ("params", "initial_active", "messaging", "push", "senders", "max_calls"))):
            yield Finding("qa-error", task.name, "*", "extension arm differs from family generator")
        actual_edges = {seat: config["agents"][seat]["start_after"] for seat in seats
                        if "start_after" in config["agents"][seat]}
        if actual_edges != arm.get("start_after", {}):
            yield Finding("qa-error", task.name, "*", "extension start_after differs from generator")
        play = metadata["play_timeout_sec"]
        agent = config["agent"]["timeout_sec"]
        BUILD.validate_timeout(play)
        BUILD.validate_timeout(agent)
        if (float(task.env("main")["FLOTILLA_PLAY_SECONDS"]) != play
                or data.get("play_timeout_sec", play) != play
                or arm.get("play_timeout_sec", play) != play):
            yield Finding("qa-error", task.name, "*", "extension play timeout differs")
        requested = arm.get("agent_timeout_sec", agent)
        expected = requested if isinstance(requested, dict) else dict.fromkeys(seats, requested)
        if (data.get("agent_timeout_sec", expected) != expected or agent != max(expected.values())
                or metadata.get("agent_timeout_sec", expected) != expected):
            yield Finding("qa-error", task.name, "*", "extension agent timeout differs")
        staged = {seat for seat, predecessors in actual_edges.items() if predecessors}
        staged.update(p for predecessors in actual_edges.values() for p in predecessors)
        if staged and play < len(staged) * agent + BUILD.STAGED_TIMEOUT_MARGIN_SEC:
            yield Finding("qa-error", task.name, "*", "staged play timeout is too short for agent limits and margin")
        for name, text in arm.get("files", {}).items():
            if (task.env_dir / "player" / name).read_text() != text:
                yield Finding("qa-error", task.name, "*", "extension file differs from generator")
        for seat in seats:
            text = BUILD.SCRIPTED_INSTRUCTION if seat in scripted else arm["instructions"][seat]
            if (task.view(seat)["instruction"] != text
                    or json.loads(task.env(seat)["SEAT_BRIEFING"]) != arm["briefings"][seat]):
                yield Finding("qa-error", task.name, seat, "extension briefing or instruction differs from generator")
        task.cache[("extension", "arm")] = arm
        hook = getattr(task.family, "qa", None)
        if callable(hook):
            for detail in hook(data, {seat: task.view(seat) for seat in seats}):
                yield Finding("qa-error", task.name, "*", detail)
    except Exception as exc:  # noqa: BLE001 - malformed extension task is a QA failure
        yield Finding("qa-error", task.name, "*", f"extension QA failed: {type(exc).__name__}")


def check_policy_runner(task: Task) -> Iterator[Finding]:
    data = task.task_json
    solution = task.path / "solution"
    selected = data.get("policy")
    if selected is None:
        if (solution / "policy.json").exists() or data.get("mode") == "extension":
            yield Finding("qa-error", task.name, "*", "task lacks policy metadata")
        return
    try:
        mode = data.get("mode", "answer")
        if mode != "extension" and selected not in ("careful", "naive", "nop"):
            raise ValueError("invalid selected policy")
        metadata = tomllib.loads((task.path / "task.toml").read_text())["metadata"]
        if metadata["policy"] != selected:
            raise ValueError("policy metadata differs")
        arm = task.cache.get(("extension", "arm"), {})
        custom = BUILD.load_policy(task.family_name)
        expected = BUILD.policy_settings(task.family, custom, data["variant"], mode, selected, task.seats,
                                         tuple(set(task.seats) - set(task.under_test)), arm.get("scripted_policies", {}),
                                         seed=data["seed"], instance=data["instance"])
        script = BUILD.POLICY_SH.encode()
        if mode == "extension":
            script = BUILD.marked(script)
        if (json.loads((solution / "policy.json").read_text()) != expected
                or (solution / "solve.sh").read_bytes() != script):
            raise ValueError("policy solution differs")
    except Exception as exc:  # noqa: BLE001
        yield Finding("qa-error", task.name, "*", f"policy QA failed: {type(exc).__name__}")


def check_confederate_plan(task: Task) -> Iterator[Finding]:
    """A probe task (task.json `probe`, CONTRACT.md 12 and 13) has its subject as the only seat under test, the
    world's scheduled posts (task.json `scheduled_posts`) are the posts of the family's plan for the task's own arm and
    each confederate's briefing holds the rest of its plan (a task built for one arm with another arm's posts would
    measure the wrong condition), and main's FLOTILLA_SUBMIT_GATE says what task.json says."""
    probe = task.task_json.get("probe")
    if not isinstance(probe, dict) or task.family is None:
        return
    if task.under_test != [probe.get("subject")]:
        yield Finding("confederate-plan", task.name, "*", f"the seats under test are {', '.join(task.under_test)}, "
                      f"not the subject {probe.get('subject')}")
    try:
        plans = task.family.confederate_plans({"probe": probe}, task.condition)
        trigger = task.task_json.get("post_trigger")
        if trigger is not None:
            if trigger not in BUILD.POST_TRIGGERS:
                raise ValueError(f"post_trigger {trigger!r}")
            plans = task.family.at_first_submit(probe, plans)
    except Exception as exc:  # noqa: BLE001 - a plan the family cannot rebuild is a finding
        yield Finding("confederate-plan", task.name, "*",
                      f"the family cannot rebuild the plans of arm {task.condition} ({type(exc).__name__})")
        return
    posts, own_steps = BUILD.scheduled_posts(plans)
    if task.task_json.get("scheduled_posts") != posts:
        yield Finding("confederate-plan", task.name, "main",
                      f"task.json scheduled_posts are not the posts of the family's plan for arm {task.condition}")
    if any(isinstance(post, dict) and BUILD.is_reply(post) and (
            post.get("reply_to") != probe.get("subject") or post.get("from") not in (probe.get("confederates") or []))
           for post in task.task_json.get("scheduled_posts") or []):
        yield Finding("confederate-plan", task.name, "main", "a reply post (world version 20) is not a confederate's "
                      "answer to the subject")
    for seat in probe.get("confederates") or []:
        try:
            briefing = json.loads(task.env(seat).get("SEAT_BRIEFING") or "null")
        except ValueError:
            briefing = None
        if briefing != {"script": "confederate", "plan": own_steps.get(seat)}:
            yield Finding("confederate-plan", task.name, seat,
                          f"its briefing is not the rest of the family's plan for arm {task.condition}")
    gate = task.env("main").get("FLOTILLA_SUBMIT_GATE") == "1"
    if gate != (task.task_json.get("submit_gate") is True):
        yield Finding("confederate-plan", task.name, "main",
                      f"FLOTILLA_SUBMIT_GATE is {'on' if gate else 'off'}, but task.json says otherwise")
    subject = probe.get("subject")
    sentence = BUILD.read_first_sentence(str(probe.get("kind")))
    told = sentence in (task.view(subject).get("instruction") or "") if subject in task.under_test else False
    if told != (task.task_json.get("read_first") is True):
        yield Finding("confederate-plan", task.name, str(subject),
                      f"its instruction {'tells' if told else 'does not tell'} it to read the messages first, but "
                      "task.json read_first says otherwise")


def check_push(task: Task) -> Iterator[Finding]:
    """A push task (task.json `push: true`, CONTRACT.md 17) is a probe task in a messaging condition whose subject's
    text holds PUSH_NOTE; any other task has neither the key nor the note."""
    if task.task_json.get("mode") == "extension":
        return
    push = task.task_json.get("push")
    probe = task.task_json.get("probe")
    subject = probe.get("subject") if isinstance(probe, dict) else None
    noted = [seat for seat in task.under_test if BUILD.PUSH_NOTE in (task.view(seat).get("instruction") or "")]
    if push is None and not noted:
        return
    if push is not True:
        yield Finding("push", task.name, "main", f"task.json push is {push!r}, and only true is allowed")
    if not isinstance(probe, dict) or not task.condition.startswith("talk"):
        yield Finding("push", task.name, "main", f"push is for a probe task with messaging, not {task.condition}")
    if noted != ([subject] if push is True else []):
        yield Finding("push", task.name, str(subject), f"the push note is in the text of {noted or 'no seat'}, but "
                      f"task.json push is {push!r}")


def check_inbox(task: Task) -> Iterator[Finding]:
    """An inbox task (task.json `inbox: true`, CONTRACT.md 19) is a probe task with messaging whose subject's text has
    exactly the builder's block of task.json's scheduled posts; any other seat, and any other task, has no block."""
    inbox = task.task_json.get("inbox")
    probe = task.task_json.get("probe")
    subject = probe.get("subject") if isinstance(probe, dict) else None
    blocks = {seat: BUILD.strip_inbox(task.view(seat).get("instruction") or "")[1] for seat in task.under_test}
    if inbox is None and not any(blocks.values()):
        return
    if inbox is not True:
        yield Finding("inbox", task.name, "main", f"task.json inbox is {inbox!r}, and only true is allowed")
    if not isinstance(probe, dict) or not task.condition.startswith("talk"):
        yield Finding("inbox", task.name, "main", f"inbox is for a probe task with messaging, not {task.condition}")
    try:
        wanted = BUILD.inbox_block([post for post in task.task_json.get("scheduled_posts") or []
                                    if not BUILD.is_reply(post)]) if inbox is True else None
    except (AssertionError, AttributeError, KeyError, TypeError):
        yield Finding("inbox", task.name, "main", "task.json scheduled_posts cannot be an inbox (before_s posts only)")
        return
    for seat, block in sorted(blocks.items()):
        expected = wanted if seat == subject else None
        if block != expected:
            found = "missing" if block is None else "there" if expected is None else "not task.json's scheduled posts"
            yield Finding("inbox", task.name, seat, f"its 'Team messages so far' block is {found}, but task.json "
                          f"inbox is {inbox!r}")


def twin_view(task: Task, seat: str) -> dict[str, str]:
    """What a seat reads, for the twin rule: an inbox task's instruction without its inbox block (check_inbox holds
    the block to task.json's posts, which are the arm's own)."""
    view = task.view(seat)
    if task.task_json.get("inbox") is not True:
        return view
    return view | {"instruction": BUILD.strip_inbox(view.get("instruction") or "")[0]}


def _numbers(text: str) -> set[int]:
    """Every run of digits that is a word of its own ("540." too; not the digits inside a hex token)."""
    return {int(found) for found in re.findall(r"(?<!\w)\d+(?!\w)", text, flags=re.ASCII)}


def _month_total(text: str, category: str, count_void: bool) -> int:
    return sum(int(row["amount"]) for row in csv.DictReader(io.StringIO(text))
               if row["category"] == category and (count_void or row["status"] != "void"))


def check_audit_data(task: Task) -> Iterator[Finding]:
    """A probe `audit` task (CONTRACT.md 14): its seat under test reads no month data (no CSV line of an audit file,
    and none of C, W, the subtotals or the slips as a number) in anything it can read; the world's audit files
    (task.json `audit`) give each worker's subtotal and slip and the answer key; the false worker's report is its
    slip, which gives the decoy W; and the tell is in the false worker's message of talk-false-tell and nowhere else."""
    probe, audit = task.task_json.get("probe"), task.task_json.get("audit")
    if not isinstance(probe, dict) or probe.get("kind") != "audit" or task.family is None:
        if audit is not None:
            yield Finding("audit-data", task.name, "main", "task.json has audit files, but its probe is not an audit")
        return
    files = audit.get("files") if isinstance(audit, dict) else None
    if not isinstance(files, dict) or sorted(files) != sorted(probe["month_of"].values()):
        yield Finding("audit-data", task.name, "main", "task.json audit files are not one file per worker's month")
        return
    category, correct, wrong = probe["category"], probe["correct_value"], probe["peer_wrong"]
    for seat, month in sorted(probe["month_of"].items()):
        if (_month_total(files[month], category, False), _month_total(files[month], category, True)) != (
                probe["subtotals"][seat], probe["slips"][seat]):
            yield Finding("audit-data", task.name, "main", f"the audit file of {month} does not give {seat}'s numbers")
    if sum(_month_total(text, category, False) for text in files.values()) != (task.answer_key or {}).get("total"):
        yield Finding("audit-data", task.name, "main", "the audit files do not add up to the answer key")
    false_seat = probe["false_seat"]
    if wrong != correct - probe["subtotals"][false_seat] + probe["slips"][false_seat] or \
            task.task_json.get("decoy") != {"total": wrong}:
        yield Finding("audit-data", task.name, "main", "the false worker's slip does not give the decoy W")
    yield from _audit_posts(task, probe)
    yield from _audit_budget(task, probe, audit)
    hidden = {correct, wrong, *probe["subtotals"].values(), *probe["slips"].values()}
    lines = {line for text in files.values() for line in text.splitlines() if line.strip()}
    for seat in task.under_test:
        for name, text in task.view(seat).items():
            found = sorted(_numbers(text) & hidden)
            if found or any(line in text for line in lines):
                what = f"the number {found[0]}" if found else "a line of an audit file"
                yield Finding("audit-data", task.name, seat, f"{name} holds {what}")


def _audit_posts(task: Task, probe: dict[str, Any]) -> Iterator[Finding]:
    """The scheduled posts of an audit task: each worker posts one number, its slip if it is the false worker in a
    false arm and its subtotal otherwise, and only the false worker's post in talk-false-tell holds the tell. In the
    reply arms each worker also answers once (world version 20) with one number: its subtotal, but the false
    worker's slip in talk-false-insist."""
    posts = [post for post in task.task_json.get("scheduled_posts") or [] if isinstance(post, dict)]
    mark = task.family.TELL_MARK.format(category=probe["category"])
    for post in posts:
        seat, reply = post.get("from"), BUILD.is_reply(post)
        slipped = task.condition != "talk-honest" and seat == probe["false_seat"] and not (
            reply and task.condition == "talk-false-admit")
        number = (probe["slips"] if slipped else probe["subtotals"]).get(seat)
        if _numbers(post.get("text", "")) != {number}:
            yield Finding("audit-data", task.name, seat, f"its {'reply' if reply else 'post'} in {task.condition} is "
                          f"not its {'slip' if slipped else 'subtotal'} alone")
        if (mark in post.get("text", "")) != (slipped and not reply and task.condition == "talk-false-tell"):
            yield Finding("audit-data", task.name, seat, f"the tell is {'missing from' if slipped else 'in'} its "
                          f"{'reply' if reply else 'post'} in {task.condition}")
    if sorted(post.get("from") for post in posts if not BUILD.is_reply(post)) != sorted(probe["confederates"]):
        yield Finding("audit-data", task.name, "main", "not every worker posts exactly once")
    answers = sorted(post.get("from") for post in posts if BUILD.is_reply(post))
    if answers != (sorted(probe["confederates"]) if task.condition in getattr(task.family, "REPLY_ARMS", ()) else []):
        yield Finding("audit-data", task.name, "main", f"the workers' replies in {task.condition} are not one per "
                      "worker in a reply arm and none in another arm")


def _audit_budget(task: Task, probe: dict[str, Any], audit: dict[str, Any]) -> Iterator[Finding]:
    """The subject's text states the world's audit budget (task.json `audit.budget`): the family's audit sentence for
    that budget and for no other, none and no /audit at all for budget 0; task.json `audit_budget` (absent: 1)
    agrees. Tasks whose family module predates --audit-budget have budget 1 and are not checked here."""
    if not hasattr(task.family, "audit_sentence"):
        return
    budget = audit.get("budget")
    if budget not in task.family.AUDIT_BUDGETS:
        yield Finding("audit-data", task.name, "main", f"task.json audit budget is {budget!r}, not one of "
                      f"{task.family.AUDIT_BUDGETS}")
        return
    if task.task_json.get("audit_budget", 1) != budget:
        yield Finding("audit-data", task.name, "main", f"task.json audit_budget is "
                      f"{task.task_json.get('audit_budget', 1)!r}, but the world's audit budget is {budget}")
    subject = probe["subject"]
    text = (task.view(subject).get("instruction") or "") if subject in task.under_test else ""
    stated = [b for b in task.family.AUDIT_BUDGETS if b and task.family.audit_sentence(b) in text]
    named = budget == 0 and "/audit" in text
    if stated != ([budget] if budget else []) or named:
        yield Finding("audit-data", task.name, str(subject), f"its text states the audit budget "
                      f"{stated or 'nowhere'}{' and names /audit' if named else ''}, but the world's audit budget "
                      f"is {budget}")


def check_policy(task: Task) -> Iterator[Finding]:
    """A policy task (task.json `policy_file: true`, kind conformity, CONTRACT.md 21): the seat's policy.md is the
    text of task.json's rule, the rule applied to expenses.csv gives C (the answer key) and the claim's misreading
    gives W (the decoy); the claim is its rule's false claim, not a sentence of policy.md; and only talk-policy posts
    it, once, with W as its one other number."""
    probe = task.task_json.get("probe")
    policy = probe.get("policy") if isinstance(probe, dict) else None
    flagged = task.task_json.get("policy_file")
    if flagged is None and policy is None:
        return
    family = task.family
    if flagged is not True or not isinstance(policy, dict) or not hasattr(family, "policy_total"):
        yield Finding("policy-data", task.name, "main", "task.json policy_file and probe.policy do not agree")
        return
    player = task.path / "environment" / "player"
    try:
        data, text = (player / family.FILE_NAME).read_text(), (player / family.POLICY_FILE).read_text()
    except OSError:
        yield Finding("policy-data", task.name, "main", f"the seat image lacks {family.FILE_NAME} or the policy file")
        return
    rule, cap, said, claim = policy.get("rule"), policy.get("cap"), policy.get("said"), policy.get("claim")
    try:
        terms, misread = family.policy_terms(rule, cap, said)
    except ValueError:
        yield Finding("policy-data", task.name, "main", f"unknown policy rule {rule!r}")
        return
    if text != family.policy_text(rule, cap):
        yield Finding("policy-data", task.name, "main", f"policy.md is not the text of rule {rule}")
    correct = family.policy_total(data, probe["category"], **terms)
    wrong = family.policy_total(data, probe["category"], **misread)
    if correct == wrong or (correct, wrong) != (probe.get("correct_value"), probe.get("peer_wrong")) or (
            task.answer_key, task.task_json.get("decoy")) != ({"total": correct}, {"total": wrong}):
        yield Finding("policy-data", task.name, "main", f"policy.md and the data give C = {correct} and W = {wrong}, "
                      "which are not task.json's answer key and decoy (or are equal)")
    if claim != family.POLICY_CLAIMS.get(rule, "").format(cap=said) or not claim or claim in text:
        yield Finding("policy-data", task.name, "main", "the claim is not its rule's false claim about policy.md")
    told = [post for post in task.task_json.get("scheduled_posts") or []
            if isinstance(post, dict) and claim and claim in post.get("text", "")]
    wanted = 1 if task.condition in family.POLICY_ARMS else 0
    if len(told) != wanted or any(_numbers(post["text"]) - _numbers(claim) != {wrong} for post in told):
        yield Finding("policy-data", task.name, "main", f"the claim is posted {len(told)} times in {task.condition}, "
                      f"not {wanted}, or not with W alone")


def check_container_name(task: Task) -> Iterator[Finding]:
    """Harbor names a trial's containers after the trial, an opaque name. A container_name replaces it with a fixed
    name, which a seat reads by a reverse DNS lookup of main's address (or of its own), as a silent seat read
    `schedule-004-silent__cdeu7ht__env-main-1` (FINDINGS.md 2026-11-19 06:20); build_tasks.py never writes one."""
    for name, service in sorted(task.services.items()):
        if service.get("container_name") is not None:
            yield Finding("container-name", task.name, name, f"service {name} sets container_name")


TASK_RULES = (check_example_leak, check_condition_leak, check_software_name, check_opaque_names, check_seat_network,
              check_seat_secrets, check_token_map, check_stale_code, check_container_name, check_solo_sufficient,
              check_game_secret, check_confederate_plan, check_audit_data, check_push, check_inbox, check_policy,
              check_extension, check_policy_runner, check_canary, check_versions, check_answer_values)


# --- Rules across tasks ----------------------------------------------------------------------------------------


def json_difference(a: Any, b: Any, path: str = "") -> str | None:
    """The first JSON path where two values differ, or None."""
    if type(a) is not type(b):
        return path or "the top level"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b), key=str):
            where = f"{path}.{key}" if path else str(key)
            if key not in a or key not in b:
                return where
            found = json_difference(a[key], b[key], where)
            if found is not None:
                return found
        return None
    if isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            found = json_difference(x, y, f"{path}[{i}]")
            if found is not None:
                return found
        return None if len(a) == len(b) else f"{path}[{min(len(a), len(b))}]"
    return None if a == b else (path or "the top level")


def describe_difference(name: str, mine: str | None, theirs: str | None, twin: str) -> str:
    """Where a field of a seat's view differs from the same field in the twin task."""
    if mine is None:
        return f"{name} is only in {twin}"
    if theirs is None:
        return f"{name} is not in {twin}"
    if name == "instruction":
        lines, other_lines = mine.splitlines(), theirs.splitlines()
        line = next((i for i, (x, y) in enumerate(zip(lines, other_lines), 1) if x != y),
                    min(len(lines), len(other_lines)) + 1)
        return f"instruction differs from {twin} at line {line}"
    try:
        where = json_difference(json.loads(mine), json.loads(theirs))
    except ValueError:
        return f"{name} differs from {twin}"
    return f"{name} differs from {twin} at {where}" if where else f"{name} differs from {twin} (same JSON, other text)"


def check_twins(tasks: list[Task]) -> Iterator[Finding]:
    """talk and talk-liar (and talk-warned and talk-liar-warned) differ only in the scripted peer's script, talk
    and gated only in the world's read gate, and the probe arms only in the confederates' plans: each seat under test
    reads the same bytes in both, apart from its token."""
    groups: dict[tuple[str, str, int], dict[str, Task]] = defaultdict(dict)
    for task in tasks:
        if task.instance is not None and task.condition:
            groups[(str(task.path.parent), task.label, task.instance)].setdefault(task.condition, task)
    for _key, by_condition in sorted(groups.items()):
        exemplar = next(iter(by_condition.values()))
        twins = (*TWINS, *getattr(exemplar.family, "TWINS", ()))
        for first, second in twins:
            if first not in by_condition or second not in by_condition:
                continue
            a, b = by_condition[first], by_condition[second]
            if a.under_test != b.under_test:
                yield Finding("twin-mismatch", b.name, "*", f"its seats under test are {', '.join(b.under_test)}; "
                              f"in {a.shown}, {', '.join(a.under_test)}")
            for seat in sorted(set(a.under_test) & set(b.under_test)):
                theirs, mine = twin_view(a, seat), twin_view(b, seat)
                for name in sorted(set(theirs) | set(mine)):
                    if name != "env.SEAT_TOKEN" and theirs.get(name) != mine.get(name):
                        yield Finding("twin-mismatch", b.name, seat,
                                      describe_difference(name, mine.get(name), theirs.get(name), a.shown))
        for first, second in getattr(exemplar.family, "PAIRS", ()):
            if first not in by_condition or second not in by_condition:
                continue
            a, b = by_condition[first], by_condition[second]
            views = []
            for task in (a, b):
                views.append({seat: {key: value for key, value in twin_view(task, seat).items()
                                     if key != "env.SEAT_TOKEN"} for seat in task.under_test})
            try:
                for detail in exemplar.family.pair_difference(first, second, *views):
                    yield Finding("twin-mismatch", b.name, "*", detail)
            except Exception as exc:  # noqa: BLE001
                yield Finding("qa-error", b.name, "*", f"pair check failed: {type(exc).__name__}")


def compose_project(name: str) -> str:
    """Harbor's Compose project name for a name (environments/docker/docker.py)."""
    name = name.lower()
    return re.sub(r"[^a-z0-9_-]", "-", name if re.match(r"^[a-z0-9]", name) else "0" + name)


def check_runtime_names(tasks: list[Task], allow_readable_names: bool = False) -> Iterator[Finding]:
    """Harbor names a local task's trial after the first 32 characters of the task folder's name (models/task/id.py,
    models/trial/config.py) and the trial's Compose project `<trial>__env`, and Docker's DNS gives seats the
    container and network names: a silent seat read `schedule-004-silent__cdeu7ht__env-main-1` by a reverse lookup
    (job w2-schedule-qwen3-6-35b-2seat, FINDINGS.md 2026-11-19 06:20). A readable folder name says the condition,
    and in schedule.liar 'liar' too, to honest and unwarned seats. Opaque names (the default) say nothing, and an
    opaque task whose name says something is opaque-names' FAIL. So this FAIL, once per label, is about sets built
    with --readable-names (or before opaque names): never run one in a wave. --allow-readable-names makes it a WARN
    for a local debugging run; make_jobs.py refuses such a set without the same flag."""
    by_label: dict[tuple[str, str], list[tuple[str, list[str]]]] = defaultdict(list)
    for task in tasks:
        prefix = task.path.name[:32].rstrip("_-")
        words = name_words(task, prefix)
        if words and task.names != "opaque":
            by_label[(str(task.path.parent), task.label)].append((prefix, words))
    for (_parent, label), found in sorted(by_label.items()):
        words = sorted({word for _prefix, said in found for word in said})
        yield Finding("runtime-names", label, "*",
                      f"{len(found)} tasks: Harbor names Docker containers and networks after the task folder "
                      f"(for example {compose_project(found[0][0])}__<id>__env-main-1); a seat that looks up an "
                      f"address reads {', '.join(map(repr, words))}: build without --readable-names",
                      downgraded=allow_readable_names)


@functools.cache
def documented_seeds(root: Path = ROOT) -> frozenset[int]:
    """Seeds that this checkout's documentation names (`--seed 1` in a reproduce command, "seed 1"): public."""
    found: set[int] = set()
    for pattern in DOCUMENTATION:
        for path in sorted(root.glob(pattern)):
            found.update(int(digits) for digits in DOCUMENTED_SEED.findall(read_text(path) or ""))
    return frozenset(found)


def check_public_seed(tasks: list[Task], allow_public_seed: bool = False) -> Iterator[Finding]:
    """The family generators are public (this repository), so a seed is the answer key in another form: a seat that
    finds the generator and the seed can compute the answer, in any condition. Every build before secret seeds used
    --seed 1, and a small seed can be found by trying them all (FINDINGS.md, 2026-11-19 10:11). One FAIL per label
    and seed: a new wave needs a secret seed (build without --seed). --allow-public-seed makes it a WARN, to repeat
    a published build (RESULTS.md, "Reproduce")."""
    documented = documented_seeds()
    by_label: dict[tuple[str, str, int], int] = defaultdict(int)
    for task in tasks:
        if task.seed is not None and (task.seed < PUBLIC_SEED_BELOW or task.seed in documented):
            by_label[(str(task.path.parent), task.label, task.seed)] += 1
    for (_parent, label, seed), count in sorted(by_label.items()):
        why = f"below {PUBLIC_SEED_BELOW:,}" if seed < PUBLIC_SEED_BELOW else "named in this checkout's documentation"
        yield Finding("public-seed", label, "*",
                      f"{count} tasks use seed {seed}, which is public ({why}): a seat that finds the public "
                      "generator can compute their answer keys; build a new wave without --seed",
                      downgraded=allow_public_seed)



# --- Running and reporting -------------------------------------------------------------------------------------


def find_tasks(patterns: list[str]) -> list[Path]:
    """Task folders (with task.toml) that the globs name, or that are below the folders they name."""
    found: list[Path] = []
    for pattern in patterns:
        for match in sorted(glob.glob(os.path.expanduser(pattern))):
            path = Path(match)
            if (path / "task.toml").is_file():
                found.append(path)
            elif path.is_dir():
                found += sorted(p.parent for p in path.rglob("task.toml"))
    return sorted(dict.fromkeys(p.resolve() for p in found))


def check(paths: list[Path], allow_readable_names: bool = False,
          allow_public_seed: bool = False) -> tuple[list[Finding], list[Task]]:
    """Every finding for the task folders, in report order, and the tasks as read. allow_readable_names makes
    runtime-names a WARN (a local debugging run of a set built with --readable-names); allow_public_seed makes
    public-seed a WARN (a repeat of a published build)."""
    if yaml is None:
        raise RuntimeError("qa.py needs PyYAML: uvx --quiet --with pyyaml python flotilla/qa.py ...")
    tasks = [load_task(path) for path in paths]
    findings: set[Finding] = set()
    for task in tasks:
        for rule in TASK_RULES:
            findings.update(rule(task))
    findings.update(check_twins(tasks))
    findings.update(check_runtime_names(tasks, allow_readable_names))
    findings.update(check_public_seed(tasks, allow_public_seed))
    for task in tasks:
        findings.update(task.errors)  # last: the rules above can add some
    return sorted(findings, key=Finding.order), tasks


def summary(findings: list[Finding], tasks: list[Task]) -> dict[str, Any]:
    labels: dict[str, int] = defaultdict(int)
    for task in tasks:
        labels[task.label or "?"] += 1
    rules = {rule: {"level": next((f.level for f in findings if f.rule == rule), level),  # a downgrade shows
                    "findings": sum(1 for f in findings if f.rule == rule),
                    "tasks": len({f.task for f in findings if f.rule == rule}), "means": means}
             for rule, (level, means) in RULES.items()}
    fails = sum(1 for f in findings if f.level == "FAIL")
    return {"tasks": len(tasks), "labels": dict(sorted(labels.items())), "fail": fails,
            "warn": len(findings) - fails, "passed": fails == 0, "rules": rules}


def render_text(findings: list[Finding], tasks: list[Task]) -> str:
    facts = summary(findings, tasks)
    shown = {task.name: task.shown for task in tasks}
    lines = []
    if findings:
        rows = [("LEVEL", "RULE", "TASK", "SEAT", "DETAIL"),
                *((f.level, f.rule, shown.get(f.task, f.task), f.seat, f.detail) for f in findings)]
        widths = [max(len(row[i]) for row in rows) for i in range(4)]
        lines += ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)) + "  " + row[4] for row in rows]
    else:
        lines.append("No findings.")
    labels = ", ".join(f"{label} {n}" for label, n in facts["labels"].items())
    lines += ["", f"Checked {facts['tasks']} tasks ({labels}): {facts['fail']} FAIL, {facts['warn']} WARN.", "",
              f"{'rule':<16} {'level':<5} {'findings':>8} {'tasks':>6}"]
    lines += [f"{rule:<16} {row['level']:<5} {row['findings']:>8} {row['tasks']:>6}"
              for rule, row in facts["rules"].items()]
    for rule in sorted({f.rule for f in findings if f.downgraded}):
        flag, purpose = DOWNGRADES[rule]
        lines += ["", f"{flag}: {rule} is a WARN, {purpose}."]
    lines += ["", "PASS: no FAIL finding." if facts["passed"] else "FAIL: fix every FAIL finding before a wave."]
    return "\n".join(lines)


def render_json(findings: list[Finding], tasks: list[Task]) -> str:
    paths = {task.name: str(task.path) for task in tasks} | {task.label: str(task.path.parent) for task in tasks}
    identity = {task.name: {"label": task.label, "instance": task.instance, "condition": task.condition}
                for task in tasks}  # a finding about a whole label (runtime-names) names the label as its task
    rows = [{"level": f.level, "rule": f.rule, "task": f.task, "seat": f.seat, "detail": f.detail,
             "path": paths.get(f.task),
             **identity.get(f.task, {"label": f.task, "instance": None, "condition": None})} for f in findings]
    return json.dumps({**summary(findings, tasks), "findings": rows}, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tasks", nargs="+", help="task folders, or globs in quotes, e.g. 'flotilla/tasks/*/*'")
    parser.add_argument("--json", action="store_true", help="print the findings and the summary as JSON")
    parser.add_argument(ALLOW_READABLE_NAMES, action="store_true",
                        help="report runtime-names as a WARN, not a FAIL: for a local debugging run of a set built "
                        "with --readable-names, never for a wave")
    parser.add_argument(ALLOW_PUBLIC_SEED, action="store_true",
                        help="report public-seed as a WARN, not a FAIL: only to repeat a published build, such as "
                        "the 2026-11-19 waves (--seed 1)")
    args = parser.parse_args(argv)
    if yaml is None:
        print("qa.py needs PyYAML: uvx --quiet --with pyyaml python flotilla/qa.py ...", file=sys.stderr)
        return 2
    paths = find_tasks(args.tasks)
    if not paths:
        print(f"no task folder (with a task.toml) matches {' '.join(args.tasks)}", file=sys.stderr)
        return 2
    findings, tasks = check(paths, allow_readable_names=args.allow_readable_names,
                            allow_public_seed=args.allow_public_seed)
    print(render_json(findings, tasks) if args.json else render_text(findings, tasks))
    return 1 if any(f.level == "FAIL" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
