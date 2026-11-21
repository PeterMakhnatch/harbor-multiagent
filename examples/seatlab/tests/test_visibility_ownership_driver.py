import json
import socket
from pathlib import Path

import pytest

from examples.seatlab.visibility import intent_packet, intent_protocol
from examples.seatlab.visibility import ownership_driver as driver
from examples.seatlab.visibility import ownership_protocol as protocol


def _calls():
    block = next(
        b for b in intent_protocol.blocks() if b.rotation == 2 and b.numbering == 1
    )
    world = intent_packet.load()[block.instance]
    proposals = protocol.seeds()[block.id]
    views = [
        (
            driver.SEATS[block.seat_index(i)],
            protocol.prompt(world, block, i, "owner", proposals),
        )
        for i in range(3)
    ]
    return block, proposals, views


def test_wrong_role_never_reaches_callback_or_resumes(tmp_path):
    block, proposals, views = _calls()
    seen = []

    def reply(seat, prompt):
        seen.append((seat, prompt))
        return '{"box":1}'

    ask = driver._respond(tmp_path, block, "owner", proposals, reply)
    with pytest.raises(ValueError, match="unregistered_model_request"):
        ask(views[0][0], views[1][1])
    with pytest.raises(ValueError, match="unregistered_model_request"):
        ask(*views[1])
    assert not seen
    assert not list((tmp_path / "turns").iterdir())


def test_grammar_invalid_model_answer_is_sealed_not_fixed(tmp_path):
    block, proposals, views = _calls()
    outputs = iter(['{"box":0}', '{"box":7}', '{"box":60}'])
    ask = driver._respond(
        tmp_path, block, "owner", proposals, lambda _seat, _prompt: next(outputs)
    )
    for seat, prompt in views:
        text = ask(seat, prompt)
        receipt = json.loads((tmp_path / "turns" / f"{seat}.json").read_bytes())
        assert (
            receipt["seat"],
            receipt["response_text"],
            receipt["prompt_sha256"],
        ) == (seat, text, driver._digest(prompt))
    assert (
        json.loads((tmp_path / "turns" / f"{views[0][0]}.json").read_bytes())[
            "response_text"
        ]
        == '{"box":0}'
    )
    with pytest.raises(ValueError, match="unregistered_model_request"):
        ask(*views[0])


def test_uncertain_callback_cannot_be_retried(tmp_path):
    block, proposals, views = _calls()
    seen = []

    def reply(seat, _prompt):
        seen.append(seat)
        raise OSError("interrupted")

    ask = driver._respond(tmp_path, block, "owner", proposals, reply)
    with pytest.raises(OSError):
        ask(*views[0])
    with pytest.raises(ValueError, match="unregistered_model_request"):
        ask(*views[1])
    assert seen == [views[0][0]]


def test_demo_docker_environment_does_not_inherit_provider_or_home(
    tmp_path, monkeypatch
):
    # macOS limits Unix socket addresses to 103 bytes. Bind relative to the
    # fixture directory; the driver still receives the full physical path.
    monkeypatch.chdir(tmp_path)
    sock = tmp_path / "docker.sock"
    monkeypatch.setattr(
        driver.os,
        "environ",
        {
            "DOCKER_HOST": f"unix://{sock}",
            "UNRELATED_PROVIDER_KEY": "fake",
            "DEEPSEEK_API_KEY": "fake",
            "HOME": "/old-home",
        },
    )
    monkeypatch.setattr(driver.sys, "dont_write_bytecode", False)
    monkeypatch.setattr(
        driver.shutil, "which", lambda *args, **kwargs: "/usr/bin/docker"
    )
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(sock.name)
        driver._configure_host(tmp_path, False)
    assert set(driver.os.environ).isdisjoint(
        {"UNRELATED_PROVIDER_KEY", "DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR"}
    )
    assert Path(driver.os.environ["HOME"]) == tmp_path / "home"
    assert driver.os.environ["DOCKER_HOST"] == f"unix://{sock}"


def test_local_gate_rejects_remote_docker_before_any_trial(tmp_path, monkeypatch):
    monkeypatch.setattr(
        driver.os, "environ", {"DOCKER_HOST": "tcp://remote.example:2375"}
    )
    with pytest.raises(ValueError, match="local_docker_required"):
        driver._configure_host(tmp_path, False)
    assert not (tmp_path / "trials").exists()
