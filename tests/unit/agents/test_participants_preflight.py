"""A seat task runs its participants, so preflight checks each of them.

The roster is the one that showed the gap: an oracle in ``p1`` and a
terminus-2 in ``p2`` whose model has no API key in the environment.
"""

import pytest

from harbor.agents.factory import AgentFactory
from harbor.models.trial.config import Participant

# Native logins live in the home directory; keep the checks to the environment.
pytestmark = pytest.mark.usefixtures("isolated_home")

ROSTER = [
    {"seat": "p1", "name": "oracle"},
    {"seat": "p2", "name": "terminus-2", "model_name": "deepseek/deepseek-flash"},
]
NO_KEY = "Participant for seat 'p2': terminus-2: no nonempty model API key selected"


def _participants() -> list[Participant]:
    return [Participant.model_validate(participant) for participant in ROSTER]


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")


@pytest.fixture
def checked(monkeypatch) -> list[tuple[str | None, str | None, bool]]:
    """Every config AgentFactory.run_preflight sees, as (seat, agent, skip)."""
    calls = []
    run_preflight = AgentFactory.run_preflight

    def record(config, *, bridge=None, skip_credential_checks=False):
        calls.append(
            (getattr(config, "seat", None), config.name, skip_credential_checks)
        )
        run_preflight(
            config, bridge=bridge, skip_credential_checks=skip_credential_checks
        )

    monkeypatch.setattr(AgentFactory, "run_preflight", record)
    return calls


def test_missing_model_credential_names_the_seat():
    with pytest.raises(ValueError, match=NO_KEY) as error:
        AgentFactory.run_participants_preflight(_participants())

    assert "DEEPSEEK_API_KEY" in str(error.value)
    assert "p1" not in str(error.value)


def test_every_failing_seat_is_named_in_one_error():
    roster = [
        Participant(seat="p1", import_path="os:path"),
        Participant(seat="ok", name="nop"),
        *_participants()[1:],
    ]

    with pytest.raises(ValueError) as error:
        AgentFactory.run_participants_preflight(roster)

    lines = str(error.value).splitlines()
    assert lines[0] == (
        "Participant for seat 'p1': Imported agent 'os:path' must be a class"
    )
    assert lines[1].startswith(NO_KEY)
    assert len(lines) == 2


def test_skip_credential_checks_defers_only_the_credential_check():
    AgentFactory.run_participants_preflight(
        _participants(), skip_credential_checks=True
    )

    bad_format = Participant(
        seat="p2",
        name="codex",
        model_name="openai/gpt-6",
        model_api_format="anthropic_messages",
    )
    with pytest.raises(ValueError, match="seat 'p2': Unsupported API format"):
        AgentFactory.run_participants_preflight(
            [bad_format], skip_credential_checks=True
        )


def test_roster_with_credentials_passes(api_key, checked):
    AgentFactory.run_participants_preflight(_participants())

    assert checked == [("p1", "oracle", False), ("p2", "terminus-2", False)]


def test_empty_roster_checks_nothing(checked):
    AgentFactory.run_participants_preflight([])

    assert checked == []
