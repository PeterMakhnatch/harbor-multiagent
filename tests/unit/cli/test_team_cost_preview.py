import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench.team_account import main

MODEL = "fictitious-provider/rate-probe-1"
ROUTE = "https://route.invalid/v1"
MARKER = "nonsecret-marker-7f3a"
SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "examples"
    / "ship_handover"
    / "samples"
    / "disconnected-workers"
)
PLAN_DIGEST = "sha256:334a29d45edb19bf10f6088cf8df90e4b5464b8e87ffe794de847613c8697787"
WITHIN = "ceiling_at_or_below_declared"
ABOVE = "ceiling_above_declared"
Json = dict[str, Any]


def member(seat: str, **extra: object) -> Json:
    return {"seat": seat, "name": "terminus-2", "model_name": MODEL, **extra}


def lock(slots: int, retries: int, **author: object) -> Json:
    trial = {
        "agent": {"name": "oracle"},
        "participants": [member("critic"), member("author", **author)],
    }
    return {
        "schema_version": 3,
        "retry": {"max_retries": retries},
        "trials": [trial] * slots,
    }


def card(models: Json) -> Json:
    return {
        "schema": "harbor.rate-card/v1",
        "currency": "USD",
        "tokens_per_rate": 1_000_000,
        "models": models,
    }


PRICED = card({MODEL: {"input_usd": "1.23", "output_usd": "4.56"}})


def limits(attempts: int) -> Json:
    def seat(calls: int, tokens_in: int, tokens_out: int) -> Json:
        return {
            "agent": "terminus-2",
            "model": MODEL,
            "max_calls": calls,
            "max_input_tokens_per_call": tokens_in,
            "max_output_tokens_per_call": tokens_out,
        }

    return {
        "schema": "harbor.seat-limits/v1",
        "attempts_per_ticket": attempts,
        "seats": {"author": seat(3, 1000, 100), "critic": seat(1, 777, 1)},
    }


def write(path: Path, record: Json) -> Path:
    path.write_text(json.dumps(record))
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str, str]:
    code = main([str(arg) for arg in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def plan(
    capsys: pytest.CaptureFixture[str],
    root: Path,
    job: Json,
    rates: Json,
    seats: Json,
    declared: str,
) -> tuple[int, str, str]:
    root.mkdir()
    paths = [
        write(root / name, record)
        for name, record in (
            ("lock.json", job),
            ("rates.json", rates),
            ("limits.json", seats),
        )
    ]
    return run(
        capsys,
        "plan",
        paths[0],
        "--rates",
        paths[1],
        "--limits",
        paths[2],
        "--declared-usd",
        declared,
    )


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def rejected(code: int, out: str, err: str, root: Path) -> None:
    assert (code, out) == (2, "")
    assert err.strip()
    for echo in (str(root), str(root.resolve()), ROUTE, MARKER, "1.23", "4.56"):
        assert echo not in err


def test_lock_plan_charges_each_retry_once_per_final_slot_and_rounds_up_to_the_cent(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, err = plan(
        capsys, tmp_path / "retry", lock(2, 1), PRICED, limits(2), "0.03"
    )
    assert (code, err) == (0, "")
    found = json.loads(out)
    root = tmp_path / "retry"
    assert found["lock"] == sha(root / "lock.json")
    assert found["lock_digest"] == "computed"
    assert found["rate_card_sha256"] == sha(root / "rates.json")
    assert found["limits_sha256"] == sha(root / "limits.json")
    assert (
        found["final_slots"],
        found["max_retries"],
        found["attempts_per_ticket"],
        found["trial_attempts"],
    ) == (2, 1, 2, 4)
    author, critic = found["seats"]
    assert (author["seat"], critic["seat"]) == ("author", "critic")
    assert (
        not author["scripted"]
        and author["model"] == MODEL
        and author["api_format"] is None
    )
    assert Decimal(author["request_ceiling_usd_exact"]) == Decimal("0.001686")
    assert Decimal(author["attempt_ceiling_usd_exact"]) == Decimal("0.005058")
    assert (author["attempt_ceiling_usd"], author["campaign_ceiling_usd"]) == (
        "0.01",
        "0.03",
    )
    assert Decimal(critic["request_ceiling_usd_exact"]) == Decimal("0.00096027")
    assert Decimal(critic["attempt_ceiling_usd_exact"]) == Decimal("0.00096027")
    assert (critic["attempt_ceiling_usd"], critic["campaign_ceiling_usd"]) == (
        "0.01",
        "0.01",
    )
    team = Decimal(found["team_attempt_ceiling_usd_exact"])
    assert team == Decimal("0.00601827")
    assert Decimal(found["ceiling_usd_exact"]) == 4 * team == Decimal("0.02407308")
    assert (found["ceiling_usd"], found["declared_usd"], found["decision"]) == (
        "0.03",
        "0.03",
        WITHIN,
    )

    code, out, err = plan(
        capsys, tmp_path / "final", lock(2, 0), PRICED, limits(1), "0.03"
    )
    assert (code, err) == (0, "")
    single = json.loads(out)
    assert (single["final_slots"], single["trial_attempts"]) == (2, 2)
    assert Decimal(single["ceiling_usd_exact"]) * 2 == Decimal(
        found["ceiling_usd_exact"]
    )
    assert single["ceiling_usd"] == "0.02"


@pytest.mark.parametrize(
    ("declared", "exit_code", "decision"), [("0.03", 0, WITHIN), ("0.02", 1, ABOVE)]
)
def test_lock_plan_cap_passes_at_equality_and_fails_one_cent_below(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    declared: str,
    exit_code: int,
    decision: str,
) -> None:
    code, out, err = plan(
        capsys, tmp_path / "cap", lock(2, 1), PRICED, limits(2), declared
    )
    assert (code, err) == (exit_code, "")
    found = json.loads(out)
    assert (found["ceiling_usd"], found["declared_usd"], found["decision"]) == (
        "0.03",
        declared,
        decision,
    )


@pytest.mark.parametrize(
    "override",
    [
        {"model_api_format": "openai"},
        {"base_url": ROUTE},
        {"kwargs": {"api_base": ROUTE}},
        {"env": {"OPENAI_BASE_URL": ROUTE}},
        {"kwargs": {f"{MARKER}_option": MARKER}},
    ],
)
def test_lock_plan_rejects_a_model_route_override(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, override: Json
) -> None:
    root = tmp_path / "route"
    code, out, err = plan(
        capsys, root, lock(2, 1, **override), PRICED, limits(2), "0.03"
    )
    rejected(code, out, err, root)


@pytest.mark.parametrize(
    "models",
    [
        {},
        {f"{MODEL}:free": {"input_usd": "1.23", "output_usd": "4.56"}},
        {f"{MODEL}-pro": {"input_usd": "1.23", "output_usd": "4.56"}},
    ],
)
def test_lock_plan_rejects_a_seat_model_without_an_exact_price(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, models: Json
) -> None:
    root = tmp_path / "price"
    code, out, err = plan(capsys, root, lock(2, 1), card(models), limits(2), "0.03")
    rejected(code, out, err, root)


def test_lock_plan_rejects_attempts_that_leave_a_retry_unpriced(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "attempts"
    code, out, err = plan(capsys, root, lock(2, 1), PRICED, limits(1), "100")
    rejected(code, out, err, root)
    assert "= 2" in err


def test_shipping_plan_prices_the_released_scripted_two_world_campaign_at_zero(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    released = json.loads((SAMPLE / "plan.json").read_text())
    scripted = {"agent": "oracle", "model": None}
    rates = write(tmp_path / "rates.json", card({}))
    seats = write(
        tmp_path / "limits.json",
        {
            "schema": "harbor.seat-limits/v1",
            "attempts_per_ticket": 1,
            "seats": {"outgoing": scripted, "relief": scripted},
        },
    )
    code, out, err = run(
        capsys,
        "shipping-plan",
        SAMPLE / "stock.json",
        SAMPLE / "inflight.json",
        SAMPLE / "plan.json",
        "--expected-plan",
        PLAN_DIGEST,
        "--rates",
        rates,
        "--limits",
        seats,
        "--declared-usd",
        "0",
    )
    assert (code, err) == (0, "")
    found = json.loads(out)
    assert found["rate_card_sha256"] == sha(rates)
    assert found["limits_sha256"] == sha(seats)
    assert (found["plan"], found["registration"]) == (
        PLAN_DIGEST,
        released["registration"],
    )
    assert (found["tickets"], found["attempts_per_ticket"], found["team_attempts"]) == (
        8,
        1,
        8,
    )
    assert [row["seat"] for row in found["seats"]] == ["outgoing", "relief"]
    for row in found["seats"]:
        assert (
            row["scripted"]
            and row["model"] is None
            and row["request_ceiling_usd_exact"] is None
        )
        assert (row["attempt_ceiling_usd"], row["campaign_ceiling_usd"]) == (
            "0.00",
            "0.00",
        )
    assert [
        (worker["worker"], worker["team_attempts"], worker["ceiling_usd"])
        for worker in found["workers"]
    ] == [(0, 4, "0.00"), (1, 4, "0.00")]
    issued = sorted(
        ticket
        for worker in found["workers"]
        for tickets in worker["tickets"].values()
        for ticket in tickets
    )
    assert issued == sorted(
        ticket for tickets in released["tickets"].values() for ticket in tickets
    )
    assert (
        Decimal(found["team_attempt_ceiling_usd_exact"])
        == Decimal(found["ceiling_usd_exact"])
        == 0
    )
    assert (found["ceiling_usd"], found["declared_usd"], found["decision"]) == (
        "0.00",
        "0.00",
        WITHIN,
    )
