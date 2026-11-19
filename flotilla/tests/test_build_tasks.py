"""The task builder (build_tasks.py): valid task folders, isolated seats, nothing secret where seats look."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import FAMILIES, FLOTILLA, instance, load

BUILD = load(FLOTILLA / "build_tasks.py")
HARBOR_BASE_COMPOSE = FLOTILLA.parent / "src" / "harbor" / "environments" / "docker" / "docker-compose-build.yaml"


def compose_services(text: str) -> dict[str, dict]:
    """The generated compose file, read back: service -> {"env": {...}, "networks": [...]}.

    The builder writes a fixed layout, so a line reader is enough (the standard library has no YAML).
    Values are JSON strings with `$` written as `$$`, which Compose reads back as `$`."""
    services: dict[str, dict] = {}
    section = current = block = None
    for line in text.splitlines():
        if not line.startswith(" "):
            section = line.rstrip(":")
            continue
        if section != "services":
            continue
        if re.fullmatch(r"  \S+:", line):
            current, block = line.strip().rstrip(":"), None
            services[current] = {"env": {}, "networks": []}
        elif line.startswith("    networks: ["):
            services[current]["networks"] = [n.strip() for n in line.split("[", 1)[1].rstrip("]").split(",")]
        elif re.fullmatch(r"    \S+:", line):
            block = line.strip().rstrip(":")
        elif block == "environment" and line.startswith("      "):
            name, value = line.strip().split(": ", 1)
            services[current]["env"][name] = json.loads(value).replace("$$", "$")
        elif block == "networks" and re.fullmatch(r"      \S+:", line):
            services[current]["networks"].append(line.strip().rstrip(":"))
    return services


def build(tmp_path: Path, family_name: str, n_seats: int = 2, difficulty: str = "easy", **options) -> list[Path]:
    return BUILD.build(family_name, [0, 1], n_seats, difficulty, 1, ("solo", "silent", "talk"), tmp_path, **options)


def seat_visible_texts(task: Path, seat: str, services: dict) -> list[str]:
    """Everything a seat's agent can read: its instruction, its container's files and environment."""
    texts = [(task / "instructions" / f"{seat}.md").read_text()]
    texts += [path.read_text() for path in (task / "environment" / "player").rglob("*") if path.is_file()]
    texts += list(services[seat]["env"].values())
    return texts


@pytest.mark.parametrize("family_name", FAMILIES)
@pytest.mark.parametrize("n_seats", [2, 3])
def test_built_tasks_are_valid_and_seats_match_the_condition(tmp_path, family_name, n_seats):
    built = build(tmp_path, family_name, n_seats)
    assert len(built) == 6
    tokens_seen: set[str] = set()
    for task in built:
        config = tomllib.loads((task / "task.toml").read_text())
        meta = config["metadata"]
        condition = meta["condition"]
        seats = ["p1"] if condition == "solo" else [f"p{i + 1}" for i in range(n_seats)]
        assert config["task"]["name"] == f"flotilla/{family_name}-{meta['instance']:03d}-{condition}"
        assert task.name == config["task"]["name"].split("/", 1)[1]
        assert meta | {} == {"family": family_name, "variant": "", "instance": meta["instance"], "condition": condition,
                             "n_seats": len(seats), "team_seats": n_seats, "difficulty": "easy", "seed": 1,
                             "max_text": 4000, "max_messages": 200, "play_timeout_sec": 900.0}
        assert sorted(config["agents"]) == seats
        assert sorted(p.stem for p in (task / "instructions").iterdir()) == seats
        services = compose_services((task / "environment" / "docker-compose.yaml").read_text())
        assert sorted(services) == ["main", *seats]
        main = services["main"]["env"]
        assert main["FLOTILLA_CONDITION"] == condition and main["FLOTILLA_SEATS"] == ",".join(seats)
        assert (main["FLOTILLA_MAX_TEXT"], main["FLOTILLA_MAX_MESSAGES"]) == ("4000", "200")
        tokens = {seat: services[seat]["env"]["SEAT_TOKEN"] for seat in seats}
        assert main["FLOTILLA_TOKENS"] == ",".join(f"{seat}:{tokens[seat]}" for seat in seats)
        assert len({*tokens.values(), main["FLOTILLA_ADMIN"]}) == len(seats) + 1  # every token differs
        assert all(re.fullmatch(r"[0-9a-f]{32}", token) for token in tokens.values())
        assert not tokens_seen & set(tokens.values())  # and differs from every other task's tokens
        tokens_seen |= set(tokens.values())
        inst = instance(family_name, meta["instance"], n_seats, "easy")
        for seat in seats:
            env = services[seat]["env"]
            assert env["SEAT"] == seat and env["WORLD_URL"] == "http://world:8080"
            expected = inst["solo_private"] if condition == "solo" else inst["private"][seat]
            assert json.loads(env["SEAT_BRIEFING"]) == json.loads(json.dumps(expected))
        task_json = json.loads((task / "environment" / "task.json").read_text())
        assert task_json["answer_key"] == inst["answer_key"] and task_json["seats"] == seats
        assert (task_json["world_version"], task_json["max_text"], task_json["max_messages"]) == (2, 4000, 200)
        for script in (task / "tests" / "test.sh", task / "solution" / "solve.sh"):
            assert script.stat().st_mode & 0o111


@pytest.mark.parametrize("family_name", FAMILIES)
def test_seats_cannot_see_the_answer_key(tmp_path, family_name):
    for task in build(tmp_path, family_name, 3):
        key = json.loads((task / "environment" / "task.json").read_text())["answer_key"]
        secrets = {json.dumps(key), json.dumps(key, sort_keys=True), json.dumps(key, separators=(",", ":")),
                   *(json.dumps(v) for v in key.values() if isinstance(v, dict))}
        services = compose_services((task / "environment" / "docker-compose.yaml").read_text())
        for seat in services:
            if seat == "main":
                continue
            for text in seat_visible_texts(task, seat, services):
                assert not any(secret in text for secret in secrets), (task.name, seat)
            assert "FLOTILLA_ADMIN" not in services[seat]["env"] and "FLOTILLA_TOKENS" not in services[seat]["env"]
            others = [services[s]["env"]["SEAT_TOKEN"] for s in services if s not in ("main", seat)]
            assert not any(token in text for token in others for text in seat_visible_texts(task, seat, services))
        assert sorted(p.name for p in (task / "environment" / "player").iterdir()) == ["Dockerfile"]
        assert "task.json" not in (task / "environment" / "player" / "Dockerfile").read_text()
        assert sorted(p.name for p in (task / "solution").iterdir()) == ["family.py", "oracle.py", "solve.sh"]
        for path in (task / "solution").iterdir():  # the oracle agent uploads solution/ into every seat
            assert not any(secret in path.read_text() for secret in secrets)


def test_each_seat_has_its_own_network_shared_only_with_the_world(tmp_path):
    for task in build(tmp_path, "ledger", 3):
        services = compose_services((task / "environment" / "docker-compose.yaml").read_text())
        seats = [s for s in services if s != "main"]
        assert services["main"]["networks"] == [f"seat-{seat}" for seat in seats]
        for seat in seats:
            assert services[seat]["networks"] == [f"seat-{seat}"]
        text = (task / "environment" / "docker-compose.yaml").read_text()
        assert text.count("aliases: [world]") == len(seats)


def test_dollar_signs_reach_the_seat_unchanged():
    briefing = {"note": "costs $5; $HOME and ${USER} are text, and so is $$"}
    text = BUILD.compose("talk", ["p1"], {"p1": "a" * 32}, "b" * 32, {"p1": briefing})
    line = next(line for line in text.splitlines() if "SEAT_BRIEFING" in line)
    assert "$$HOME" in line and "${USER}" not in line.replace("$${USER}", "")
    assert json.loads(compose_services(text)["p1"]["env"]["SEAT_BRIEFING"]) == briefing


@pytest.mark.skipif(shutil.which("docker") is None or not HARBOR_BASE_COMPOSE.exists(),
                    reason="needs the docker CLI and Harbor's base compose file")
def test_docker_compose_reads_the_briefing_back_unchanged(tmp_path):
    """Compose itself (no containers): `$NAME` in a briefing must not become a host variable."""
    task = build(tmp_path, "ledger")[1]
    path = task / "environment" / "docker-compose.yaml"
    services = compose_services(path.read_text())
    briefing = {"note": "costs $5; $HOME and ${USER} are text"}
    seats = [s for s in services if s != "main"]
    tokens = {s: services[s]["env"]["SEAT_TOKEN"] for s in seats}
    path.write_text(BUILD.compose("silent", seats, tokens, services["main"]["env"]["FLOTILLA_ADMIN"],
                                  {s: briefing for s in seats}))
    result = subprocess.run(
        ["docker", "compose", "--project-directory", str(path.parent), "-f", str(HARBOR_BASE_COMPOSE), "-f",
         str(path), "config", "--format", "json"],
        capture_output=True, text=True, env={"CONTEXT_DIR": str(path.parent), "PATH": "/usr/bin:/bin:/usr/local/bin:"
                                             "/opt/homebrew/bin", "HOME": str(Path.home()), "USER": "someone"},
    )
    if result.returncode != 0 and "Cannot connect" in result.stderr:
        pytest.skip("docker is not running")
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    printed = config["services"]["p1"]["environment"]["SEAT_BRIEFING"]  # `config` prints `$` as `$$`
    assert "someone" not in printed and str(Path.home()) not in printed  # no host variable was used
    assert json.loads(printed.replace("$$", "$")) == briefing
    assert sorted(config["services"]["p1"]["networks"]) == ["seat-p1"]


def test_message_limits_reach_the_world_the_metadata_and_the_talk_instructions(tmp_path):
    for task in build(tmp_path, "puzzle", max_text=200, max_messages=3):
        config = tomllib.loads((task / "task.toml").read_text())
        condition = config["metadata"]["condition"]
        assert (config["metadata"]["max_text"], config["metadata"]["max_messages"]) == (200, 3)
        task_json = json.loads((task / "environment" / "task.json").read_text())
        assert (task_json["max_text"], task_json["max_messages"]) == (200, 3)
        main = compose_services((task / "environment" / "docker-compose.yaml").read_text())["main"]["env"]
        assert (main["FLOTILLA_MAX_TEXT"], main["FLOTILLA_MAX_MESSAGES"]) == ("200", "3")
        sentence = "Each seat may send at most 3 messages of at most 200 characters."
        assert (sentence in (task / "instructions" / "p1.md").read_text()) == (condition == "talk")
    for task in build(tmp_path / "defaults", "puzzle"):
        assert "Each seat may send" not in (task / "instructions" / "p1.md").read_text()


def test_one_message_limit_reads_correctly(tmp_path):
    inst = instance("ledger", 0, 2, "easy")
    text = BUILD.instruction(BUILD.load_family("ledger"), inst, "p1", ["p1", "p2"], "talk", 1, 1)
    assert "Each seat may send at most 1 message of at most 1 character." in text


@pytest.mark.parametrize("condition", ["talk", "talk-liar", "talk-warned", "talk-liar-warned"])
def test_every_talk_condition_gets_the_messaging_instructions(condition):
    inst = instance("schedule", 0, 2, "easy")
    text = BUILD.instruction(BUILD.load_family("schedule"), inst, "p1", ["p1", "p2"], condition)
    assert "$WORLD_URL/messages" in text
    assert ("One of your teammates may give you wrong information." in text) == condition.endswith("-warned")


def test_variants_get_their_own_label(tmp_path):
    task = build(tmp_path, "ledger", variant="hard")[0]
    config = tomllib.loads((task / "task.toml").read_text())
    assert config["task"]["name"] == "flotilla/ledger.hard-000-solo"
    assert task.parent.name == "ledger.hard" and config["metadata"]["variant"] == "hard"


def test_instructions_are_deterministic_and_tokens_are_not(tmp_path):
    first = build(tmp_path / "a", "puzzle")
    second = build(tmp_path / "b", "puzzle")
    for a, b in zip(first, second):
        assert (a / "instructions" / "p1.md").read_text() == (b / "instructions" / "p1.md").read_text()
        assert (a / "environment" / "task.json").read_text() == (b / "environment" / "task.json").read_text()
        assert (a / "environment" / "docker-compose.yaml").read_text() != (
            b / "environment" / "docker-compose.yaml").read_text()


def test_command_line(tmp_path):
    run = [sys.executable, str(FLOTILLA / "build_tasks.py"), "--family", "ledger", "--instances", "0-1,3",
           "--seats", "3", "--out", str(tmp_path), "--max-messages", "5"]
    result = subprocess.run(run, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == f"built 9 tasks under {tmp_path}"
    assert sorted(p.name for p in (tmp_path / "ledger").iterdir())[:3] == [
        "ledger-000-silent", "ledger-000-solo", "ledger-000-talk"]
    bad = subprocess.run([*run[:-2], "--max-messages", "0"], capture_output=True, text=True)
    assert bad.returncode == 2 and "positive" in bad.stderr
    unknown = subprocess.run([*run, "--conditions", "talk,shout"], capture_output=True, text=True)
    assert unknown.returncode == 2 and "unknown conditions" in unknown.stderr
    scripted = subprocess.run(
        [sys.executable, str(FLOTILLA / "build_tasks.py"), "--family", "schedule", "--scripted", "--instances", "0",
         "--conditions", "talk,talk-liar,talk-warned,talk-liar-warned", "--out", str(tmp_path)],
        capture_output=True, text=True, check=True)
    assert scripted.stdout.strip() == f"built 4 tasks under {tmp_path}"
    lying = {p.name: json.loads(compose_services((p / "environment" / "docker-compose.yaml").read_text())["p3"]["env"][
        "SEAT_BRIEFING"])["script"] for p in (tmp_path / "schedule.liar").iterdir()}
    assert lying == {"schedule.liar-000-talk": "honest", "schedule.liar-000-talk-warned": "honest",
                     "schedule.liar-000-talk-liar": "liar", "schedule.liar-000-talk-liar-warned": "liar"}
