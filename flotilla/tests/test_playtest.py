"""Exercise the host runner with real HTTP and deterministic, model-free seat commands."""

import hashlib
import json
import os
import re
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
root = Path(workdir).parent
assert Path(os.environ['TMPDIR']) == root / 'tmp' and Path(os.environ['TMPPREFIX']) == root / 'tmp' / 'zsh'
assert Path(os.environ['TMPDIR']).is_dir()
assert os.environ['PATH'] == f'{root}/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
link = root / 'bin' / 'python3'
if link.is_symlink():
    import shutil
    import subprocess
    assert shutil.which('python3') == str(link)
    assert subprocess.run(['python3', '-c', 'print(6 * 7)'], capture_output=True, text=True).stdout == '42\\n'
print('seat-python3', os.path.realpath(link) if link.is_symlink() else None, flush=True)
assert Path(workdir).parent.stat().st_mode & 0o777 == 0o700
assert Path(workdir).parent.parent == Path.home().resolve()
assert 'PLAYTEST_SECRET' not in os.environ
if mode == 'env':
    assert os.environ['PLAYTEST_KEEP'] == 'allowed'
    assert os.environ['USER'] == 'fake-login'
    allowed = {
        'PATH', 'HOME', 'USER', 'TMPDIR', 'TMPPREFIX', 'ZDOTDIR', 'BASH_ENV', 'SEAT', 'SEAT_TOKEN', 'SEAT_BRIEFING',
        'WORLD_URL', 'PWD', 'SHLVL', '_', 'LC_CTYPE', '__CF_USER_TEXT_ENCODING', 'PLAYTEST_KEEP'
    }
    assert not (unexpected := set(os.environ) - allowed), unexpected
    mode = 'careful'
assert list(Path.cwd().iterdir()) == [] or list(Path.cwd().iterdir()) == [Path.cwd() / 'private.txt']
if Path('private.txt').exists():
    assert Path('private.txt').read_text() == seat
assert Path(prompt).read_text().startswith('You are working in a sandbox directory.\\n')
assert f'/home/player maps to {workdir}' in Path(prompt).read_text()
assert f'http://world:8080 maps to {os.environ["WORLD_URL"]}' in Path(prompt).read_text()
print('Read', prompt, flush=True)
print('ls ..', flush=True)
print('ls ../..', flush=True)
print('token-sha256', hashlib.sha256(os.environ['SEAT_TOKEN'].encode()).hexdigest(), flush=True)
Path(log).write_text('Native command log\\n')
if mode == 'wait':
    time.sleep(30)
elif mode in ('careful', 'json'):
    if mode == 'json':
        import json
        import urllib.error
        import urllib.request
        codex = {'id': 'item_1', 'type': 'command_execution', 'command': "/bin/zsh -lc 'find / -name x'"}
        print(json.dumps({'type': 'item.started', 'item': codex}), flush=True)
        print(json.dumps({'type': 'item.completed', 'item': codex | {'aggregated_output': workdir + '\\ntotal 8'}}),
              flush=True)
        claude = {'type': 'tool_use', 'id': 'toolu_1', 'name': 'Bash', 'input': {'command': 'cd /home/player && ls'}}
        print(json.dumps({'type': 'assistant', 'message': {'content': [claude]}}), flush=True)
        print(json.dumps({'type': 'unknown', 'command': 'ls /should-not-parse'}), flush=True)
        noise = {'type': 'thinking', 'thinking': 'maybe read /etc/shadow', 'signature': 'Eu/aB+/x9/Qm/zz=='}
        routes = {'type': 'tool_use', 'id': 'toolu_2', 'name': 'Bash', 'input': {'command': (
            'curl -s "$WORLD_URL"/messages; for p in /whoami /docs; do curl -s "$WORLD_URL$p"; done; ls ../..')}}
        write = {'type': 'tool_use', 'id': 'toolu_3', 'name': 'Write',
                 'input': {'file_path': '/tmp/book.py', 'content': 'open("/etc/hosts")'}}
        print(json.dumps({'type': 'assistant', 'message': {'content': [noise, routes, write]}}), flush=True)
        result = {'type': 'tool_result', 'tool_use_id': 'toolu_2', 'content': 'see /var/db/secret'}
        print(json.dumps({'type': 'user', 'message': {'content': [result]}}), flush=True)
        try:
            urllib.request.urlopen(urllib.request.Request(
                os.environ['WORLD_URL'] + '/docs', headers={'Authorization': 'Bearer ' + os.environ['SEAT_TOKEN']}))
        except urllib.error.HTTPError:
            pass
        request = urllib.request.Request(os.environ['WORLD_URL'] + '/messages',
                                         headers={'Authorization': 'Bearer ' + os.environ['SEAT_TOKEN']})
        try:
            urllib.request.urlopen(request).read()
        except urllib.error.HTTPError:
            pass
    runpy.run_path(solution, run_name='__main__')
elif mode == 'fail':
    sys.exit(7)
elif mode == 'fault':
    import urllib.error
    import urllib.request
    for path, data in [('/await?until=bad', None), ('/fixture-fault' if seat == 'p1' else '/whoami',
                                                   b'{}' if seat == 'p1' else None)]:
        request = urllib.request.Request(os.environ['WORLD_URL'] + path, data=data,
                                         headers={'Authorization': 'Bearer ' + os.environ['SEAT_TOKEN']})
        try:
            urllib.request.urlopen(request).read()
        except urllib.error.HTTPError:
            pass
elif mode == 'stall':
    import urllib.request
    if seat == 'p1':
        sys.exit(7)
    request = urllib.request.Request(os.environ['WORLD_URL'] + '/await?until=tick&after=0',
                                     headers={'Authorization': 'Bearer ' + os.environ['SEAT_TOKEN']})
    while True:
        urllib.request.urlopen(request).read()
elif mode == 'budget':
    import urllib.error
    import urllib.request
    paths = ['/whoami'] if seat == 'p1' else ['/await?until=bad'] + ['/board'] * 21
    for path in paths:
        request = urllib.request.Request(os.environ['WORLD_URL'] + path,
                                         headers={'Authorization': 'Bearer ' + os.environ['SEAT_TOKEN']})
        try:
            urllib.request.urlopen(request).read()
        except urllib.error.HTTPError:
            pass
elif mode == 'linger':
    runpy.run_path(solution, run_name='__main__')
    time.sleep(30)
"""


def build(tmp_path, arm):
    return BUILD.build("kit_fixture", [0], 2, "easy", 7236499753218553, (arm,), tmp_path / "tasks")[0]


def play(tmp_path, task, mode="careful", extra=(), name="out", check=True, expected_exit=0):
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
    assert completed.returncode == expected_exit, completed.stdout + completed.stderr
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
            assert f"`{prompt}`" not in summary and f"`{prompt.parent}`" not in summary
            old_token = QA.load_task(task).env(seat)["SEAT_TOKEN"]
            assert hashlib.sha256(old_token.encode()).hexdigest() not in log
    return events, summary


def test_scripted_probe_and_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_ADMIN", "must-not-reach-seats")
    monkeypatch.setenv("FLOTILLA_TOKENS", "must-not-reach-seats")
    task = build(tmp_path, "talk-trap")
    good_out, good = play(tmp_path, task, extra=("--model-label", "fake-model"))
    events, summary = assert_evidence(task, good_out, good, 1)
    assert good["seats"]["p1"]["mode"] == "scripted"
    assert good["seats"]["p2"]["mode"] == "external"
    assert all(info["exit_code"] == 0 for info in good["seats"].values())
    assert good["model"] == "fake-model" and good["harness"] == "fake seat.py"
    assert good["seats"]["p2"]["harness"] == "fake seat.py"
    assert good["seats"]["p2"]["model"] == "fake-model"
    assert good["seats"]["p1"]["harness"] == "policy.py"
    assert good["seats"]["p1"]["model"] is None
    assert good["seat_failures"] == {}
    receiver = good["seats"]["p2"]
    assert receiver["world_calls"] > 0 and receiver["first_read"] < receiver["first_submit"]
    assert receiver["read_before_submit"] is True
    assert any(event["kind"] == "commit_value" and event["seat"] == "p2" for event in events)
    assert 'tick=2.0 deliver:' in summary and '"text": "handoff ' in summary
    assert 'tick=2.0 commit_value:' in summary
    bad_out, bad = play(tmp_path, task, "nop", name="no-op", expected_exit=1)
    assert bad["seat_failures"] == {"p2": ["no world calls"]}
    assert bad["seats"]["p2"]["world_calls"] == 0
    for summary in ((good_out / "summary.md").read_text(), (bad_out / "summary.md").read_text()):
        assert "Not audited: the log has no recognized tool records." in summary.split("## p2 (external)")[1]
        assert f"`{Path.home().resolve()}`" not in summary  # the plain log line `ls ../..` is not a tool record
    events, _ = assert_evidence(task, bad_out, bad, 0)
    assert [event["kind"] for event in events if event["seat"] == "p2"] == ["seat_transition"]
    good_log = (good_out / "seats" / "p2" / "seat.log").read_text()
    bad_log = (bad_out / "seats" / "p2" / "seat.log").read_text()
    assert good_log.split("token-sha256 ")[1].splitlines()[0] != bad_log.split("token-sha256 ")[1].splitlines()[0]


def test_mixed_seat_commands_and_models(tmp_path):
    task = build(tmp_path, "talk")
    second = tmp_path / "other seat.py"
    second.write_text(FAKE_SEAT)
    override = (f"p2=/usr/bin/env {shlex.quote(sys.executable)} {shlex.quote(str(second))} "
                "{instruction} {seat} {workdir} {log} "
                f"{shlex.quote(str(task / 'solution' / 'policy.py'))} careful")
    out, result = play(tmp_path, task, extra=("--seat-cmd", override, "--model-label", "default-model",
                                             "--model-label", "p2=other-model"))
    _, summary = assert_evidence(task, out, result, 1)
    assert result["harness"] == {"p1": "fake seat.py", "p2": "other seat.py"}
    assert result["model"] == {"p1": "default-model", "p2": "other-model"}
    for seat in ("p1", "p2"):
        assert result["seats"][seat]["harness"] == result["harness"][seat]
        assert result["seats"][seat]["model"] == result["model"][seat]
    assert "Model: other-model; harness: other seat.py" in summary
    assert str(second) in result["seats"]["p2"]["command"]
    invalid = play(tmp_path, task, extra=("--model-label", "p9=bad"), name="bad-override", check=False)
    assert invalid.returncode == 2 and "unknown or non-external seat p9" in invalid.stderr


def test_grace_reaps_lingering_seats_without_changing_valid_score(tmp_path):
    task = build(tmp_path, "talk")
    out, result = play(tmp_path, task, "linger", extra=("--grace-sec", "0.4"))
    assert_evidence(task, out, result, 1)
    assert result["valid"] and not result["timed_out"]
    assert result["stop_reason"] == "world frozen; grace expired"
    assert result["seat_failures"] == {}
    for seat in result["seats"].values():
        assert seat["stopped_after_freeze"] and not seat["timed_out"]
        assert seat["exit_code"] < 0 and 0.4 <= seat["seconds"] < 10


def test_frozen_world_does_not_launch_pending_seat(tmp_path):
    task = build(tmp_path, "team")
    config = task / "task.toml"
    config.write_text(config.read_text().replace("[agents.p2]\n", '[agents.p2]\nstart_after = ["p1"]\n'))
    _, result = play(tmp_path, task, "fault", extra=("--grace-sec", "0.4"), expected_exit=1)
    assert not result["valid"]
    assert result["invalid_reason"] == "extension_error"
    assert result["stop_reason"] == "world frozen before seats started"
    pending = result["seats"]["p2"]
    assert pending["stopped_after_freeze"] and pending["started_sec"] is None
    assert pending["world_calls"] == 0 and pending["exit_code"] is None


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
    out, result = play(tmp_path, task, "wait", extra=("--timeout-sec", "0.5"), expected_exit=1)
    _, summary = assert_evidence(task, out, result, 0)
    assert result["timed_out"]
    p1, p2 = result["seats"]["p1"], result["seats"]["p2"]
    assert p1["timed_out"] and p1["exit_code"] < 0
    assert p2["timed_out"] and p2["started_sec"] is None and p2["exit_code"] is None
    assert "No seat events." in summary


def test_per_seat_timeout_starts_at_launch(tmp_path):
    task = build(tmp_path, "talk")
    config = task / "task.toml"
    config.write_text(config.read_text().replace("[agents.p2]\n", '[agents.p2]\nstart_after = ["p1"]\n'))
    _, result = play(tmp_path, task, "wait", extra=("--seat-timeout", "0.5"), expected_exit=1)
    assert not result["timed_out"]
    p1, p2 = result["seats"]["p1"], result["seats"]["p2"]
    for seat in (p1, p2):
        assert seat["timed_out"] and seat["exit_code"] < 0
        assert 0.5 <= seat["seconds"] < 2
    assert p2["started_sec"] >= p1["ended_sec"]


def test_stall_stops_waiting_seat_after_exit(tmp_path):
    task = build(tmp_path, "team")
    compose = task / "environment" / "docker-compose.yaml"
    compose.write_text(compose.read_text().replace("    environment:\n",
                                                  '    environment:\n      FLOTILLA_WAIT_SECONDS: "0.2"\n', 1))
    completed = play(tmp_path, task, "stall", extra=("--stall-sec", "0.8"), check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    result = json.loads((tmp_path / "out" / "result.json").read_text())
    assert result["stop_reason"] == "seat p1 exited; others wait"
    assert result["stop_reason"] in completed.stderr
    assert not result["timed_out"]
    assert result["seats"]["p2"]["seconds"] < 5
    assert result["seats"]["p2"]["world_calls"] > 1


def test_failed_seat_exit(tmp_path):
    task = build(tmp_path, "talk-trap")
    completed = play(tmp_path, task, "fail", check=False)
    assert completed.returncode == 1
    assert "p2: exit=7, world calls=0" in completed.stdout
    assert "p2: exit code 7, no world calls" in completed.stderr
    result = json.loads((tmp_path / "out" / "result.json").read_text())
    assert result["seat_failures"]["p2"] == ["exit code 7", "no world calls"]
    assert result["seats"]["p2"]["first_read"] is None
    assert result["seats"]["p2"]["first_submit"] is None


def test_invalid_verifier_preserves_seats_and_reason(tmp_path):
    task = build(tmp_path, "team")
    completed = play(tmp_path, task, "fault", check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    out = tmp_path / "out"
    result = json.loads((out / "result.json").read_text())
    assert result["valid"] is False and result["reward"] is None
    assert result["invalid_reason"] == "extension_error"
    assert "invalid: extension_error" in completed.stderr
    assert result["freeze_reason"] == "extension ended play"
    assert result["budget_events"] == []
    assert set(result["seats"]) == {"p1", "p2"}
    seat = result["seats"]["p1"]
    assert seat["exit_code"] == 0 and seat["seconds"] > 0
    assert seat["world_calls"] == 2 and seat["http_4xx"] == 1
    assert "invalid reason: extension_error" in (out / "summary.md").read_text()


def test_budget_freeze_and_http_causes_in_results(tmp_path):
    task = build(tmp_path, "team")
    completed = play(tmp_path, task, "budget", check=False)
    assert completed.returncode == 1
    out = tmp_path / "out"
    result = json.loads((out / "result.json").read_text())
    assert result["invalid_reason"] == "fixture budget exhausted"
    assert result["freeze_reason"] == "extension ended play"
    assert [(e["seat"], e["data"]) for e in result["budget_events"]] == [("p2", {"limit": 20})]
    assert result["seats"]["p2"]["http_4xx"] == 2
    assert "Freeze reason: extension ended play" in completed.stdout
    assert "Budget events:" in completed.stdout and '"limit": 20' in completed.stdout
    assert "p2: exit=0, world calls=22, 4xx=2" in completed.stdout
    requests = [json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()]
    errors = [r for r in requests if r["seat"] == "p2" and r["status"] >= 400]
    assert [(r["path"], r["query"], r["status"]) for r in errors] == [
        ("/await", "until=bad", 400), ("/board", "", 409)]
    summary = (out / "summary.md").read_text()
    assert "GET /await?until=bad -> 400" in summary
    assert "GET /board -> 409" in summary


def test_summary_lists_commands_requests_and_scripted_sends(tmp_path):
    task = build(tmp_path, "talk-trap")
    out, result = play(tmp_path, task, "json")
    _, summary = assert_evidence(task, out, result, 1)
    receiver = summary.split("## p2 (external)")[1]
    assert "### World requests (arrival order)" in receiver
    assert "GET /messages" in receiver.split("### Shell commands")[0]
    commands = receiver.split("### Shell commands (CLI order)")[1].split("###")[0]
    assert commands.count("find / -name x") == 1
    assert "cd /home/player && ls" in commands
    assert "should-not-parse" not in commands
    assert "scripted send:" in summary.split("## p2 (external)")[0]
    audit = receiver.split("### Outside-path audit")[1]
    listed = {line[3:-1] for line in audit.splitlines() if line.startswith("- `")}
    assert listed == {"/", "/home/player", "/tmp/book.py", str(Path.home().resolve())}


def test_audit_reads_only_tool_calls_and_skips_world_routes(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(FLOTILLA))
    runner = load(FLOTILLA / "playtest.py")
    root = tmp_path / "seat"
    work = root / "work"
    for command in ("find / -name x", "ls /", "cd /; cat etc/passwd", "cd '/' && ls"):
        assert runner.outside_paths(command, work, root) == ["/"], command
    assert runner.outside_paths("ls ./data; cat x/y; echo $((4 / 2))", work, root) == []
    assert runner.outside_paths(f"cat {root / 'prompt.md'} {root / 'tmp' / 'x'} /dev/null", work, root) == []
    assert runner.outside_paths("cat /etc/hosts", work, root) == ["/etc/hosts"]
    command = "curl -s \"$WORLD_URL\"/desk; for p in /desk/ '/shipment?id=S-1' /close; do curl \"$WORLD_URL$p\"; done"
    assert runner.outside_paths(command, work, root) == ["/close", "/desk", "/shipment?id=S-1"]
    assert runner.outside_paths(command, work, root, frozenset({"/desk", "/shipment"})) == ["/close"]
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "command.log").write_text("cat /etc/passwd\n")
    (logs / "seat.log").write_text("ls ../..\nReading /etc/hosts\n")
    assert runner.audit_seat(logs, work, root, frozenset()) is None
    content = [
        {"type": "thinking", "thinking": "read /etc/shadow?", "signature": "Eq/8B/Cq+z9/aa=="},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat ../../notes.txt; curl $W/x '/desk'"}},
        {"type": "tool_use", "id": "t2", "name": "Write", "input": {"file_path": "/tmp/book.py", "content": "/etc/x"}},
        {"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": str(work / "a.csv")}}]
    records = [{"type": "assistant", "message": {"content": content}},
               {"type": "assistant", "message": {"content": content[1:2]}},
               {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"{work}\n/var/db/secret"}]}}]
    (logs / "seat.log").write_text("".join(json.dumps(record) + "\n" for record in records))
    assert runner.tool_calls((logs / "seat.log").read_text()) == (
        ["cat ../../notes.txt; curl $W/x '/desk'"], ["/tmp/book.py", str(work / "a.csv")])
    assert runner.audit_seat(logs, work, root, frozenset({"/desk"})) == sorted([str(tmp_path / "notes.txt"), "/tmp/book.py"])


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
    monkeypatch.setenv("USER", "fake-login")
    monkeypatch.setenv("PATH", f"{Path.home()}/.local/bin:/usr/bin:/bin")
    task = build(tmp_path, "talk-trap")
    out, result = play(tmp_path, task, "env", extra=("--pass-env", "PLAYTEST_KEEP"))
    assert_evidence(task, out, result, 1)
    assert result["python3"] and Path(result["python3"]).is_file() and "/.local/bin/" not in result["python3"]
    assert f"seat-python3 {result['python3']}\n" in (out / "seats" / "p2" / "seat.log").read_text()
    assert f"Seat python3: {result['python3']}" in (out / "summary.md").read_text()


def test_sandbox_command_and_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(FLOTILLA))
    runner = load(FLOTILLA / "playtest.py")
    profile = tmp_path / "seat.sb"
    profile.write_text("(version 1)\n(allow default)\n")
    root = tmp_path / "seat"
    argv = runner.sandbox_command(["/bin/bash", "-c", "echo hello"], profile, root)
    assert argv[:5] == ["/usr/bin/sandbox-exec", "-f", str(profile), "-D", f"SEATROOT={root}"]
    assert argv[-3:] == ["/bin/bash", "-c", "echo hello"]
    assert f"SEATSCRIPTS={FLOTILLA / 'playtest_seats'}" in argv
    assert f"SEATSCRIPTS={tmp_path.resolve()}" in runner.sandbox_command(["true"], profile, root, tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    assert runner.sandbox_profile(profile) is None
    assert "skipped" in capsys.readouterr().err
    monkeypatch.setattr(sys, "platform", "darwin")
    assert runner.sandbox_profile(profile) == profile.resolve()


def test_python_search_skips_shims_and_reports_none(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(FLOTILLA))
    runner = load(FLOTILLA / "playtest.py")
    real, install = (Path(os.path.realpath(path)) for path in (sys._base_executable, sys.base_prefix))
    home = tmp_path / "home"
    shim = home / ".local" / "bin" / "python3"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\nexit 1\n")
    shim.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    assert runner.real_python(str(real), str(install)) == (real, install)
    assert runner.real_python(str(shim), str(install)) is None
    assert runner.real_python(str(real), str(tmp_path)) is None, "an install folder must not open the user's home"
    host = tmp_path / "host-python3"
    host.write_text(f"#!/bin/sh\necho {shlex.quote(str(real))}\necho {shlex.quote(str(install))}\n")
    host.chmod(0o755)
    monkeypatch.setattr(sys, "_base_executable", str(shim))
    monkeypatch.setattr(runner, "HOST_PYTHONS", (str(tmp_path / "missing"), str(host)))
    scripts = FLOTILLA / "playtest_seats"
    assert runner.find_python(None, scripts) == (real, install)
    assert capsys.readouterr().err == ""
    monkeypatch.setattr(runner, "HOST_PYTHONS", ())
    assert runner.find_python(None, scripts) is None
    assert "no real python3" in capsys.readouterr().err
    task = build(tmp_path, "talk-trap")
    fake_seat = tmp_path / "fake seat.py"
    fake_seat.write_text(FAKE_SEAT)
    template = (f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_seat))} {{instruction}} {{seat}} {{workdir}} {{log}} "
                f"{shlex.quote(str(task / 'solution' / 'policy.py'))} careful")
    result = runner.playtest(task, template, None, 15, 2, tmp_path / "out")
    assert result["reward"] == 1 and result["python3"] is None
    assert json.loads((tmp_path / "out" / "result.json").read_text())["python3"] is None
    assert "no real python3" in capsys.readouterr().err
    assert "seat-python3 None\n" in (tmp_path / "out" / "seats" / "p2" / "seat.log").read_text()
    assert "Seat python3: none found on this host" in (tmp_path / "out" / "summary.md").read_text()


SANDBOX_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="the seat sandbox needs sandbox-exec; macOS only")
SHELL_PROBE = """\
#!/bin/bash
set -u
[[ "$PATH" == "${PWD%/work}/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" ]] || echo "bad PATH $PATH"
[[ -d "$ZDOTDIR" && "$ZDOTDIR" == "${PWD%/work}/zdot" && "$TMPPREFIX" == "${PWD%/work}/tmp/zsh" ]] || echo "bad shell env"
for shell in "/bin/zsh -lc" "/bin/zsh -c" "/bin/bash -lc"; do
  echo "[$shell] $($shell 'command -v python3') -> $($shell 'python3 -c "print(6 * 7)"')"
done
/bin/zsh -lc "cat <<'EOF'
here-document ok
EOF"
/usr/bin/curl -fsS -H "Authorization: Bearer $SEAT_TOKEN" "$WORLD_URL/whoami"
echo "fake-cli-ok USER=$USER SHELL=$SHELL HOME=$HOME"
"""


@SANDBOX_ONLY
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_shipped_wrappers_give_a_fake_cli_an_ordinary_shell(tmp_path, monkeypatch, harness):
    scripts = tmp_path / "seat scripts"
    scripts.mkdir()
    shipped = FLOTILLA / "playtest_seats"
    wrapper = scripts / f"{harness}.sh"
    wrapper.write_bytes((shipped / f"{harness}.sh").read_bytes())
    fake = scripts / f"fake-{harness}"
    fake.write_text(SHELL_PROBE)
    fake.chmod(0o700)
    monkeypatch.setenv(f"{harness.upper()}_BIN", str(fake))
    monkeypatch.setenv("USER", "fake-login")
    if harness == "codex":  # codex.sh copies the login file; give it a fake one in a fake home
        (tmp_path / "home" / ".codex").mkdir(parents=True)
        (tmp_path / "home" / ".codex" / "auth.json").write_text("{}")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
    task = build(tmp_path, "solo")
    out = tmp_path / "out"
    completed = subprocess.run([sys.executable, str(FLOTILLA / "playtest.py"), "--task", str(task),
                                "--seat-cmd", f"/bin/bash {shlex.quote(str(wrapper))} {{workdir}} {{instruction}} fake",
                                "--seat-scripts", str(scripts), "--sandbox-profile", str(shipped / "seat.sb"),
                                "--pass-env", f"{harness.upper()}_BIN", "--out", str(out)],
                               capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((out / "result.json").read_text())
    assert result["seats"]["p1"]["world_calls"] == 1
    assert Path(result["python3"]).is_file()
    log = (out / "seats" / "p1" / "seat.log").read_text()
    assert "bad PATH" not in log and "bad shell env" not in log, log
    for shell in ("/bin/zsh -lc", "/bin/zsh -c", "/bin/bash -lc"):
        assert re.search(rf"^\[{shell}\] \S+/\.playtest-seat-[^/]+/bin/python3 -> 42$", log, re.MULTILINE), log
    assert "here-document ok\n" in log
    expected = (r"USER=fake-login SHELL=/bin/zsh HOME=\S+$" if harness == "claude"
                else r"USER=fake-login SHELL=/bin/bash HOME=\S+/\.playtest-seat-[^/]+/home$")
    assert re.search("fake-cli-ok " + expected, log, re.MULTILINE), log


@SANDBOX_ONLY
def test_check_seat_passes_under_the_shipped_profile_and_fails_without_isolation(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    shipped = FLOTILLA / "playtest_seats"
    command = [sys.executable, str(FLOTILLA / "playtest.py"), "--check-seat", "--deny-probe", str(secret)]
    good = subprocess.run([*command, "--sandbox-profile", str(shipped / "seat.sb"), "--seat-scripts", str(shipped)],
                          capture_output=True, text=True, timeout=120, check=False)
    assert good.returncode == 0, good.stdout + good.stderr
    passed = [line for line in good.stdout.splitlines() if line.startswith("PASS  ")]
    names = [f"{check} ({shell})" for check in ("python3 -c", "here-document")
             for shell in ("zsh -lc", "zsh -c", "bash -lc")]
    names += ["curl to a local port", "write inside the seat folder", "read of the user's home refused",
              f"read refused: {secret}"]
    assert len(passed) == len(names) and all(line.startswith(f"PASS  {name}") for line, name in zip(passed, names))
    assert "FAIL" not in good.stdout and "classified" not in good.stdout
    assert f"Seat check: {len(names)} of {len(names)} passed" in good.stdout
    open_profile = tmp_path / "open.sb"
    open_profile.write_text("(version 1)\n(allow default)\n")
    bad = subprocess.run([*command, "--sandbox-profile", str(open_profile), "--deny-probe", str(tmp_path / "absent")],
                         capture_output=True, text=True, timeout=120, check=False)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert [line for line in bad.stdout.splitlines() if line.startswith("FAIL")] == [
        "FAIL  read of the user's home refused: the seat can read it",
        f"FAIL  read refused: {secret}: the seat can read it",
        f"FAIL  read refused: {tmp_path / 'absent'}: not on this host, so the check proves nothing"]
    assert f"Seat check: {len(names) - 2} of {len(names) + 1} passed" in bad.stdout
    usage = subprocess.run([sys.executable, str(FLOTILLA / "playtest.py"), "--task", str(tmp_path)],
                           capture_output=True, text=True, timeout=60, check=False)
    assert usage.returncode == 2 and "--seat-cmd, --out" in usage.stderr
