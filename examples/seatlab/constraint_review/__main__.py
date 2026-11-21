import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from ..visibility import deepseek, spend
from . import prefix, protocol

_ROLE = "route_review"
_PREFIX = "creview-"


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _id(world: int, rep: int, arm: str) -> str:
    return f"{_PREFIX}s{world}-r{rep}-{arm}"


def _private(path: Path) -> None:
    _require(path.is_absolute() and path.resolve(strict=True) == path, "invalid path")
    info = path.stat()
    _require(
        path.is_dir() and info.st_uid == os.getuid() and not (info.st_mode & 0o077),
        "private directory required",
    )


def _seal(path: Path, data: dict) -> None:
    payload = (json.dumps(data, sort_keys=True, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
        os.fchmod(output.fileno(), 0o400)


def _read(path: Path) -> dict:
    info = path.stat()
    _require(
        path.resolve(strict=True) == path
        and path.is_file()
        and info.st_uid == os.getuid()
        and info.st_mode & 0o777 == 0o400
        and info.st_size <= 65536,
        "invalid private record",
    )
    return json.loads(path.read_bytes())


def prepare(study: Path) -> tuple[dict, list, dict]:
    _private(study)
    cases, sources, prompts = {}, [], {}
    for world in range(6901, 6909):
        case = prefix.load(study, world)
        context = case["context"]
        original = protocol.measures(context, case["grade"], context["draft"])
        sources.append(
            {
                "world": world,
                "trial_ref": case["trial_ref"],
                "result_sha256": case["result_sha256"],
                "initial_violations": original["violations"],
                "initial_optimal": original["optimal"],
            }
        )
        for arm in ("specific", "neutral"):
            text = protocol.prompt(context, arm)
            deepseek._payload(text)
            prompts[world, arm] = text
        cases[world] = case
    _require(
        len({s["result_sha256"] for s in sources})
        == len({s["trial_ref"] for s in sources})
        == 8,
        "repeated source",
    )
    _require(
        len(protocol.grid()) == len(set(protocol.grid())) == 32, "invalid register"
    )
    return cases, sources, prompts


def _register(sources: list, prompts: dict) -> dict:
    return {
        "schema": "seatlab.constraint-review-register/v1",
        "sources": sources,
        "turns": [
            {
                "run": _id(w, r, a),
                "world": w,
                "rep": r,
                "arm": a,
                "prompt_sha256": _sha(prompts[w, a]),
            }
            for w, r, a in protocol.grid()
        ],
    }


def _meter(ledger: Path) -> dict:
    _private(ledger)
    meter = spend.report(ledger, run_prefix=_PREFIX)
    expected = {_id(*cell) for cell in protocol.grid()}
    found = {}
    for row in meter["calls"]:
        run = row["run"]
        _require(
            run in expected and run not in found and row["role"] == _ROLE,
            "unregistered provider turn",
        )
        found[run] = row
    return found


def _run(job: Path, study: Path, ledger: Path, register: dict, prompts: dict) -> None:
    _private(job.parent)
    _require(
        not job.is_relative_to(study)
        and not job.is_relative_to(ledger)
        and not job.is_relative_to(Path(__file__).resolve().parents[3]),
        "job must be outside the inputs and checkout",
    )
    _require(not _meter(ledger), "registered review calls already exist; do not retry")
    _require(
        os.environ.get("SEATLAB_SPEND_DIR") == str(ledger),
        "shared ledger binding required",
    )
    job.mkdir(mode=0o700)
    _seal(job / "register.json", register)
    deepseek.bind_host_key()
    spend.bind_host_spend_dir()
    for w, rep, arm in protocol.grid():
        run = _id(w, rep, arm)
        turn_dir = job / run
        turn_dir.mkdir(mode=0o700)
        prompt = prompts[w, arm]
        attempt = {"run": run, "prompt_sha256": _sha(prompt)}
        _seal(turn_dir / "attempt.json", attempt)
        try:
            turn = deepseek.turn(prompt, run_id=run, role=_ROLE)
        except (deepseek.SeatUnavailable, spend.SpendRefused) as exc:
            _seal(turn_dir / "failure.json", attempt | {"reason": str(exc)})
            raise ValueError(f"{run}: provider stopped; no retry") from None
        _seal(
            turn_dir / "response.json",
            attempt
            | {
                "text": turn.text,
                "served_model": turn.served_model,
                "hit": turn.cache_tokens,
                "miss": turn.input_tokens - turn.cache_tokens,
                "output": turn.output_tokens,
            },
        )


def _charge(row: dict | None) -> dict:
    value = row or {}
    return {
        "status": "absent"
        if row is None
        else "held"
        if row["estimated_usd_high"] is None
        else "settled",
        "cache_hit": value.get("cache_hit"),
        "cache_miss": value.get("cache_miss"),
        "output_tokens": value.get("output"),
        "usd_low": value.get("estimated_usd_low"),
        "usd_high": value.get("estimated_usd_high"),
        "hold_usd": value.get("unsettled_hold_usd", "0.000000000"),
    }


def _outcome(job: Path, cell: tuple, case: dict, prompt: str, meter: dict) -> dict:
    world, rep, arm = cell
    run = _id(world, rep, arm)
    record = meter.get(run)
    rate = _charge(record)
    current = {
        "world": world,
        "rep": rep,
        "arm": arm,
        "run": run,
        "status": "not_attempted",
        "prompt_sha256": _sha(prompt),
        "response_sha256": None,
        "metrics": None,
        "charge": rate,
    }
    folder = job / run
    if not folder.exists():
        _require(record is None, "orphan provider reservation")
        return current
    _private(folder)
    attempt = folder / "attempt.json"
    if not attempt.exists():
        _require(record is None and not any(folder.iterdir()), "unbound review claim")
        return current
    current["status"] = "provider_failure"
    claimed = {"run": run, "prompt_sha256": current["prompt_sha256"]}
    _require(_read(attempt) == claimed, "review source drift")
    response = folder / "response.json"
    if not response.exists():
        return current
    _require(not (folder / "failure.json").exists(), "conflicting outcomes")
    reply = _read(response)
    _require(
        record is not None
        and rate["status"] == "settled"
        and all(reply[k] == v for k, v in claimed.items()),
        "unmatched provider response",
    )
    _require(
        (reply["served_model"], reply["hit"], reply["miss"], reply["output"])
        == (
            record["served_model"],
            rate["cache_hit"],
            rate["cache_miss"],
            rate["output_tokens"],
        ),
        "usage mismatch",
    )
    current["response_sha256"] = _sha(reply["text"])
    try:
        final = protocol.choices(reply["text"])
    except ValueError:
        current["status"] = "invalid_reply"
        return current
    current.update(
        status="valid", metrics=protocol.measures(case["context"], case["grade"], final)
    )
    return current


def main() -> None:
    cli = argparse.ArgumentParser(
        description="Review fixed live Dispatch drafts once, or read their evidence offline."
    )
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in ("check", "run", "report"):
        cmd = modes.add_parser(mode)
        cmd.add_argument("--study", type=Path, required=True)
        if mode != "check":
            cmd.add_argument("--job", type=Path, required=True)
            cmd.add_argument("--ledger", type=Path, required=True)
    args = cli.parse_args()
    try:
        cases, sources, prompts = prepare(args.study)
        register = _register(sources, prompts)
        if args.mode == "run":
            _run(args.job, args.study, args.ledger, register, prompts)
            return
        if args.mode == "check":
            result = register
        else:
            _private(args.job)
            _require(
                _read(args.job / "register.json") == register,
                "registered drafts or prompts changed",
            )
            meter = _meter(args.ledger)
            rows = [
                _outcome(
                    args.job, cell, cases[cell[0]], prompts[cell[0], cell[2]], meter
                )
                for cell in protocol.grid()
            ]
            result = {
                "schema": "seatlab.constraint-review/v1",
                "sources": sources,
                "rows": rows,
            }
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"constraint review: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
