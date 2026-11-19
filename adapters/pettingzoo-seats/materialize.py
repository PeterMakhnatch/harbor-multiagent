"""Materialize a self-contained native PettingZoo seat task from a world spec.

Usage:
    python materialize.py SPEC OUT --policy p0=low --policy p1=high
        [--requirements FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
POLICIES = ("low", "high", "rps-copy", "rps-beat-last")
ROLE = re.compile(r"^p[0-7]$")
_PIN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:\[[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:,[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)*\])?"
    r"==(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*"
    r"(?:(?:a|b|rc|alpha|beta|c|pre|preview)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
    r"(?:\+[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*)?$"
)
DEFAULT_PINS = (
    "pettingzoo==1.26.1",
    "pygame-ce==2.5.8",
    "gymnasium==1.4.0",
    "numpy==2.5.5",
    "cloudpickle==3.1.3",
    "Farama-Notifications==0.0.6",
    "typing_extensions==4.16.0",
)
WORLD_FILES = ("engine.py", "world.py", "wire.py")
SEAT_FILES = ("actor.py", "wire.py")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object")
    roles = value.get("roles")
    if not isinstance(roles, dict) or not 2 <= len(roles) <= 8:
        raise ValueError("spec.roles must map 2 to 8 Harbor roles")
    if any(ROLE.fullmatch(role) is None for role in roles):
        raise ValueError("Harbor roles must be p0..p7")
    return value


def _pins(path: Path | None) -> list[str]:
    if path is None:
        lines = list(DEFAULT_PINS)
        where = "default pins"
    else:
        lines = path.read_text(encoding="utf-8").splitlines()
        where = str(path)
    if not lines or any(_PIN.fullmatch(line) is None for line in lines):
        raise ValueError(f"{where} must list name[extras]==exact version pins")
    names = [line.split("[", 1)[0].split("==", 1)[0].lower() for line in lines]
    if len(names) != len(set(names)):
        raise ValueError(f"{where} repeats a package name")
    return lines


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if path.suffix == ".sh":
        path.chmod(0o755)


def _copy(names: tuple[str, ...], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(RUNTIME / name, dest / name)


def _main_dockerfile() -> str:
    return (
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt engine.py world.py wire.py spec.json /app/\n"
        "RUN python -m pip install --no-cache-dir --only-binary=:all: -r /app/requirements.txt\n"
        'CMD ["python", "/app/world.py"]\n'
    )


def _player_dockerfile() -> str:
    return (
        "FROM python:3.12-slim\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates "
        "&& rm -rf /var/lib/apt/lists/*\n"
        "WORKDIR /app\nCOPY actor.py wire.py /app/\n"
        'CMD ["sleep", "infinity"]\n'
    )


def _compose(roles: list[str], policies: dict[str, str]) -> str:
    volumes = "\n".join(
        f"      - type: volume\n        source: seat_{role}_in\n"
        f"        target: /exchange/{role}/in\n"
        f"      - type: volume\n        source: seat_{role}_out\n"
        f"        target: /exchange/{role}/out"
        for role in roles
    )
    seats = []
    for role in roles:
        seats.append(
            f"  {role}:\n    build: ./player\n    user: root\n    working_dir: /app\n"
            '    command: ["sleep", "infinity"]\n    init: true\n'
            f"    volumes:\n      - type: volume\n        source: seat_{role}_in\n"
            f"        target: /in\n        read_only: true\n"
            f"      - type: volume\n        source: seat_{role}_out\n        target: /out\n"
            f"    environment:\n      SEAT: {role}\n      POLICY: {policies[role]}\n"
            "      INBOX: /in\n      OUTBOX: /out\n"
        )
    declared = "\n".join(
        f"  seat_{role}_{kind}: {{}}" for role in roles for kind in ("in", "out")
    )
    return (
        'services:\n  main:\n    command: ["python", "/app/world.py"]\n'
        "    healthcheck:\n"
        '      test: ["CMD", "python", "-c", "import pathlib; '
        "raise SystemExit(0 if pathlib.Path('/app/world.ready').is_file() else 1)\"]\n"
        "      interval: 1s\n      timeout: 5s\n      retries: 30\n      start_period: 2s\n"
        f"    volumes:\n{volumes}\n" + "\n".join(seats) + f"\nvolumes:\n{declared}\n"
    )


def _task_toml(name: str, roles: list[str]) -> str:
    agents = "\n".join(f"[agents.{role}]\nunder_test = true" for role in roles)
    return (
        'schema_version = "1.4"\nartifacts = ["/logs/world/pettingzoo.json"]\n\n'
        "[task]\n"
        f'name = "seatlab/{name}"\n'
        'description = "Independent seats play one installed PettingZoo game through private file inboxes."\n'
        'keywords = ["seats", "pettingzoo", "multi-agent"]\n\n'
        '[metadata]\ndifficulty = "medium"\ncategory = "multi-agent"\n'
        'tags = ["seats", "pettingzoo"]\n\n'
        "[verifier]\ntimeout_sec = 180.0\n\n[agent]\ntimeout_sec = 300.0\n\n"
        "[environment]\nbuild_timeout_sec = 600.0\n\n" + agents + "\n"
    )


def _instructions(role: str, policy: str) -> str:
    return (
        f"You are seat `{role}` in a PettingZoo game. The world writes your private requests to `/in`.\n\n"
        "Use only your own files:\n"
        "- Read `/in/request.json` when it names your current step. It has `step`, `view`, and `view_sha256`.\n"
        "- Check `view_sha256`. Canonical JSON uses `sort_keys=True`, `separators=(',', ':')`, `ensure_ascii=True`, and `allow_nan=False`.\n"
        '- Choose one integer from `view["legal_actions"]`.\n'
        "- Write `/out/action.json` with `step`, `view_sha256`, and `action`. Replace the file in one step.\n"
        "- Ignore an older request. Never read another seat's files. Stop when `/in/done.json` exists.\n\n"
        f"The installed reference actor is `python /app/actor.py`. `POLICY` is `{policy}`.\n"
        "`low` picks the first legal action. `high` picks the last. `rps-copy` copies the observed previous move, else 0. "
        "`rps-beat-last` plays one above that move, modulo 3, else 0. The actor appends accepted actions to `/logs/agent/pz_actions.jsonl`.\n"
    )


def _solve() -> str:
    return "#!/bin/bash\nset -euo pipefail\nexec python /app/actor.py\n"


def _test(roles: list[str]) -> str:
    listed = ", ".join(repr(role) for role in roles)
    return f"""#!/bin/bash
set -euo pipefail
python - <<'PY'
import json, math, sys
from pathlib import Path
sys.path.insert(0, "/app")
from wire import canonical_digest, read_json
ROLES = ({listed})
SPEC = read_json(Path("/app/spec.json"))
report = Path("/logs/world/pettingzoo.json")
verifier = Path("/logs/verifier")
def fail(reason):
    print("VERIFICATION FAILED: " + reason, file=sys.stderr)
    raise SystemExit(1)
if not report.is_file():
    fail("missing /logs/world/pettingzoo.json")
world = read_json(report)
need = {{"schema", "case_sha256", "roles", "steps", "returns", "terminated", "truncated"}}
if world.keys() != need or world.get("schema") != "seatlab.pz-native/v1":
    fail("schema")
if world.get("case_sha256") != canonical_digest(SPEC):
    fail("case digest")
if world.get("roles") != SPEC.get("roles") or set(world["roles"]) != set(ROLES):
    fail("roles")
steps, receipts = world.get("steps"), {{role: [] for role in ROLES}}
if not isinstance(steps, list) or not steps:
    fail("steps")
for index, row in enumerate(steps, 1):
    if row.keys() != {{"step", "decisions"}} or type(row.get("step")) is not int or row["step"] != index:
        fail(f"step {{index}} shape")
    decisions = row["decisions"]
    if not isinstance(decisions, dict) or not decisions:
        fail(f"step {{index}} decisions")
    for role, decision in decisions.items():
        if role not in ROLES or decision.keys() != {{"view", "view_sha256", "action"}}:
            fail(f"decision {{index}} {{role}}")
        view, digest, action = decision["view"], decision["view_sha256"], decision["action"]
        if not isinstance(view, dict) or digest != canonical_digest(view):
            fail(f"view {{index}} {{role}}")
        if type(action) is not int or action not in view.get("legal_actions", []):
            fail(f"action {{index}} {{role}}")
        receipts[role].append({{"step": index, "view_sha256": digest, "action": action}})
if not all(receipts.values()):
    fail("missing role decision")
returns, terminated, truncated = (world[k] for k in ("returns", "terminated", "truncated"))
for role in ROLES:
    value = returns.get(role) if isinstance(returns, dict) and set(returns) == set(ROLES) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        fail(f"return {{role}}")
    if not isinstance(terminated, dict) or set(terminated) != set(ROLES) or type(terminated.get(role)) is not bool:
        fail(f"terminated {{role}}")
    if not isinstance(truncated, dict) or set(truncated) != set(ROLES) or type(truncated.get(role)) is not bool:
        fail(f"truncated {{role}}")
    if not (terminated[role] or truncated[role]):
        fail(f"role {{role}} has no final state")
    audit = Path(f"/logs/seats/{{role}}/agent/pz_actions.jsonl")
    if not audit.is_file():
        fail(f"missing actor audit for {{role}}")
    observed = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    if canonical_digest(observed) != canonical_digest(receipts[role]):
        fail(f"actor audit disagrees with accepted decisions for {{role}}")
verifier.mkdir(parents=True, exist_ok=True)
(verifier / "reward.txt").write_text("1\\n", encoding="utf-8")
PY
"""


def materialize(
    spec_path: Path, out: Path, policies: dict[str, str], requirements: Path | None
) -> None:
    spec = _load(spec_path)
    roles = list(spec["roles"])
    if set(policies) != set(roles) or any(
        item not in POLICIES for item in policies.values()
    ):
        raise ValueError("bind every role to low, high, rps-copy, or rps-beat-last")
    if out.exists():
        raise ValueError(f"{out} already exists")
    pins = _pins(requirements)
    env = out / "environment"
    _copy(WORLD_FILES, env)
    _write(env / "requirements.txt", "".join(f"{pin}\n" for pin in pins))
    _write(env / "spec.json", json.dumps(spec, sort_keys=True, indent=2) + "\n")
    _write(env / "Dockerfile", _main_dockerfile())
    _copy(SEAT_FILES, env / "player")
    _write(env / "player" / "Dockerfile", _player_dockerfile())
    _write(env / "docker-compose.yaml", _compose(roles, policies))
    _write(out / "task.toml", _task_toml(out.name, roles))
    _write(out / "solution" / "solve.sh", _solve())
    _write(out / "tests" / "test.sh", _test(roles))
    for role in roles:
        _write(out / "instructions" / f"{role}.md", _instructions(role, policies[role]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--policy", action="append", default=[], metavar="ROLE=POLICY")
    parser.add_argument("--requirements", type=Path)
    args = parser.parse_args()
    policies: dict[str, str] = {}
    for item in args.policy:
        role, sep, policy = item.partition("=")
        if not sep:
            raise SystemExit(f"bad --policy {item!r}")
        policies[role] = policy
    materialize(args.spec, args.out, policies, args.requirements)


if __name__ == "__main__":
    main()
