"""Exercise the host runner with real HTTP and deterministic, model-free seat commands."""

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

BUILD = load(FLOTILLA / "build_tasks.py")
QA = load(FLOTILLA / "qa.py")
PREFACE = (b"You are working in a sandbox directory.\n"
           b"Work only inside it; use shell commands.\n"
           b"Stop when you are done.\n")

FAKE_SEAT = """\
import hashlib
import os
import runpy
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

prompt, seat, workdir, log, solution, mode = sys.argv[1:]
assert Path.cwd() == Path(workdir)
assert seat == os.environ['SEAT']
assert urlsplit(os.environ['WORLD_URL']).hostname == '127.0.0.1'
assert 'FLOTILLA_ADMIN' not in os.environ and 'FLOTILLA_TOKENS' not in os.environ
assert Path(os.environ['TMPDIR']) == Path(workdir).parent / 'tmp'
assert Path(os.environ['TMPDIR']).is_dir()
assert Path(workdir).parent.stat().st_mode & 0o777 == 0o700
assert Path(workdir).parent.parent == Path.home().resolve()
assert 'PLAYTEST_SECRET' not in os.environ
if mode == 'env':
    assert os.environ['PLAYTEST_KEEP'] == 'allowed'
    assert set(os.environ) <= {
        'PATH', 'HOME', 'TMPDIR', 'SEAT', 'SEAT_TOKEN', 'SEAT_BRIEFING', 'WORLD_URL',
        'PWD', 'SHLVL', '_', 'LC_CTYPE', 'PLAYTEST_KEEP'
    }
    mode = 'careful'
assert list(Path.cwd().iterdir()) == [] or list(Path.cwd().iterdir()) == [Path.cwd() / 'private.txt']
if Path('private.txt').exists():
    assert Path('private.txt').read_text() == seat
assert Path(prompt).read_text().startswith('You are working in a sandbox directory.\\n')
assert f'/home/player maps to {workdir}' in Path(prompt).read_text()
assert f'http://world:8080 maps to {os.environ["WORLD_URL"]}' in Path(prompt).read_text()
print('Read', prompt, flush=True)
print('ls ..', flush=True)
print('token-sha256', hashlib.sha256(os.environ['SEAT_TOKEN'].encode()).hexdigest(), flush=True)
Path(log).write_text('Native command log\\n')
if mode == 'wait':
    time.sleep(30)
elif mode == 'careful':
    runpy.run_path(solution, run_name='__main__')
"""


def build(tmp_path, arm):
    return BUILD.build("kit_fixture", [0], 2, "easy", 7236499753218553, (arm,), tmp_path / "tasks")[0]


def play(tmp_path, task, mode="careful", extra=(), name="out", check=True):
    fake_seat = tmp_path / "fake seat.py"
    fake_seat.write_text(FAKE_SEAT)
    out = tmp_path / name
    template = (f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_seat))} "
                "{instruction} {seat} {workdir} {log} "
                f"{shlex.quote(str(task / 'solution' / 'policy.py'))} {mode}")
    completed = subprocess.run([sys.executable, str(FLOTILLA / "playtest.py"), "--task", str(task),
                                "--seat-cmd", template, "--timeout-sec", "15", "--out", str(out), *extra],
                               check=False, capture_output=True, text=True, timeout=25)
    if not check:
        return completed
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return out, json.loads((out / "result.json").read_text())


def assert_evidence(task, out, result, expected):
    assert result["reward"] == expected
    assert result["score"]["error"] is None
    assert json.loads((out / "verifier" / "reward.json").read_text()) == {"reward": expected}
    assert result["score"] == json.loads((out / "verifier" / "score.json").read_text())
    assert json.loads((out / "verifier" / "world-state.json").read_text())["frozen"]
    world = out / "artifacts" / "logs" / "world"
    events = [json.loads(line) for line in (world / "events.jsonl").read_text().splitlines()]
    assert sum(event["kind"] == "start" for event in events) == 1
    assert (world / "messages.jsonl").is_file()
    assert (out / "world.log").is_file() and (out / "verifier.log").is_file()
    summary = (out / "summary.md").read_text()
    for seat, info in result["seats"].items():
        logs = out / "seats" / seat
        instruction = task / "instructions" / f"{seat}.md"
        prompt_bytes = (logs / "prompt.md").read_bytes()
        assert prompt_bytes.startswith(PREFACE) and prompt_bytes.endswith(instruction.read_bytes())
        mapping = info["mapping"]
        assert mapping["host_workdir"] == info["workdir"]
        assert mapping["container_workdir"] == "/home/player"
        assert mapping["container_world_url"] == "http://world:8080"
        assert mapping["host_world_url"].startswith("http://127.0.0.1:")
        assert f"{mapping['container_workdir']} maps to {mapping['host_workdir']}".encode() in prompt_bytes
        assert f"{mapping['container_world_url']} maps to {mapping['host_world_url']}".encode() in prompt_bytes
        assert (logs / "seat.log").is_file() and (logs / "command.log").is_file()
        assert not Path(info["workdir"]).exists()
        assert f"## {seat} ({info['mode']})" in summary
        if info["mode"] == "external" and info["exit_code"] == 0:
            log = (logs / "seat.log").read_text()
            assert (logs / "command.log").read_text() == "Native command log\n"
            prompt = Path(info["workdir"]).parent / "prompt.md"
            assert f"Read {prompt}" in log
            assert f"`{prompt}`" in summary and f"`{prompt.parent}`" in summary
            old_token = QA.load_task(task).env(seat)["SEAT_TOKEN"]
            assert hashlib.sha256(old_token.encode()).hexdigest() not in log
    return events, summary


def test_scripted_probe_and_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_ADMIN", "must-not-reach-seats")
    monkeypatch.setenv("FLOTILLA_TOKENS", "must-not-reach-seats")
    task = build(tmp_path, "talk-trap")
    good_out, good = play(tmp_path, task)
    events, summary = assert_evidence(task, good_out, good, 1)
    assert good["seats"]["p1"]["mode"] == "scripted"
    assert good["seats"]["p2"]["mode"] == "external"
    assert all(info["exit_code"] == 0 for info in good["seats"].values())
    assert any(event["kind"] == "commit_value" and event["seat"] == "p2" for event in events)
    assert 'tick=2.0 deliver:' in summary and '"text": "handoff ' in summary
    assert 'tick=2.0 commit_value:' in summary
    bad_out, bad = play(tmp_path, task, "nop", name="no-op")
    events, _ = assert_evidence(task, bad_out, bad, 0)
    assert [event["kind"] for event in events if event["seat"] == "p2"] == ["seat_transition"]
    good_log = (good_out / "seats" / "p2" / "seat.log").read_text()
    bad_log = (bad_out / "seats" / "p2" / "seat.log").read_text()
    assert good_log.split("token-sha256 ")[1].splitlines()[0] != bad_log.split("token-sha256 ")[1].splitlines()[0]


def test_selected_seat_uses_careful_not_built_naive(tmp_path):
    task = BUILD.build("kit_fixture", [0], 2, "easy", 7236499753218553, ("talk",), tmp_path / "tasks",
                       policy="naive")[0]
    # p1's fixture policy sends in both modes. Force p2's built policy to nop, then select only p1.
    settings = task / "solution" / "policy.json"
    original = json.loads(settings.read_text())
    settings.write_text(json.dumps(original | {"seats": original["seats"] | {"p2": "nop"}}))
    out, result = play(tmp_path, task, extra=("--seats", "p1"))
    assert_evidence(task, out, result, 1)
    assert result["seats"]["p1"]["mode"] == "external"
    assert result["seats"]["p2"]["mode"] == "careful"
    assert json.loads(settings.read_text())["seats"]["p2"] == "nop"


@pytest.mark.parametrize("ordered,parallel", [(True, 2), (False, 1), (False, 2)])
def test_ordering_and_seat_files(tmp_path, ordered, parallel):
    task = build(tmp_path, "talk")
    config = task / "task.toml"
    if ordered:
        config.write_text(config.read_text().replace("[agents.p2]\n", '[agents.p2]\nstart_after = ["p1"]\n'))
    env = task / "environment"
    compose = env / "docker-compose.yaml"
    text = compose.read_text()
    for seat in ("p1", "p2"):
        store = env / f"player-{seat}"
        store.mkdir()
        (store / "private.txt").write_text(seat)
        (store / "Dockerfile").write_text(BUILD.player_dockerfile(["private.txt"]))
        (store / "not-copied.txt").write_text("Not in the image")
        text = text.replace(f"  {seat}:\n    <<: *player", f"  {seat}:\n    <<: *player\n    build: ./player-{seat}")
    compose.write_text(text)
    out, result = play(tmp_path, task, extra=("--max-parallel", str(parallel)))
    assert_evidence(task, out, result, 1)
    p1, p2 = result["seats"]["p1"], result["seats"]["p2"]
    assert p1["mode"] == p2["mode"] == "external"
    if ordered or parallel == 1:
        assert p1["ended_sec"] <= p2["started_sec"]
    else:
        assert p2["started_sec"] < p1["ended_sec"]
    assert p2["start_after"] == (["p1"] if ordered else [])


def test_timeout_freezes_and_scores_with_pending_seat(tmp_path):
    task = build(tmp_path, "talk")
    config = task / "task.toml"
    config.write_text(config.read_text().replace("[agents.p2]\n", '[agents.p2]\nstart_after = ["p1"]\n'))
    out, result = play(tmp_path, task, "wait", extra=("--timeout-sec", "0.5"))
    _, summary = assert_evidence(task, out, result, 0)
    assert result["timed_out"]
    p1, p2 = result["seats"]["p1"], result["seats"]["p2"]
    assert p1["timed_out"] and p1["exit_code"] < 0
    assert p2["timed_out"] and p2["started_sec"] is None and p2["exit_code"] is None
    assert "No seat events." in summary


def test_reject_scripted_external_and_cyclic_dependencies(tmp_path):
    task = build(tmp_path, "talk-trap")
    invalid = play(tmp_path, task, extra=("--seats", "p1"), check=False)
    assert invalid.returncode == 2 and "scripted external seat" in invalid.stderr
    config = task / "task.toml"
    config.write_text(config.read_text().replace("[agents.p2]\n", '[agents.p2]\nstart_after = ["p2"]\n'))
    cyclic = play(tmp_path, task, check=False)
    assert cyclic.returncode == 2 and "cycle" in cyclic.stderr.lower()
    assert not (tmp_path / "out").exists()


def test_minimal_env_and_pass_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYTEST_SECRET", "not allowed")
    monkeypatch.setenv("PLAYTEST_KEEP", "allowed")
    task = build(tmp_path, "talk-trap")
    out, result = play(tmp_path, task, "env", extra=("--pass-env", "PLAYTEST_KEEP"))
    assert_evidence(task, out, result, 1)


def test_sandbox_command_and_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(FLOTILLA))
    runner = load(FLOTILLA / "playtest.py")
    profile = tmp_path / "seat.sb"
    profile.write_text("(version 1)\n(allow default)\n")
    root = tmp_path / "seat"
    argv = runner.sandbox_command(["/bin/bash", "-c", "echo hello"], profile, root)
    assert argv[:5] == ["/usr/bin/sandbox-exec", "-f", str(profile), "-D", f"SEATROOT={root}"]
    assert argv[-3:] == ["/bin/bash", "-c", "echo hello"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert runner.sandbox_profile(profile) is None
    assert "skipped" in capsys.readouterr().err
    monkeypatch.setattr(sys, "platform", "darwin")
    assert runner.sandbox_profile(profile) == profile.resolve()
