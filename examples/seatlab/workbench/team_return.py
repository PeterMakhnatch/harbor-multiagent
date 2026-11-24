import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ...native_evidence import Incomplete, Invalid
from ...seat_handoffs import work_book, work_report
from ...seat_handoffs.native_records import read_record_with_digest
from ...seat_handoffs.work_jobs import MEASURED
from ...seat_seals import worker_layout, worker_packets, worker_report

SCHEMA = "seatlab.workbench.team-return/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench.team_return"
KINDS = ("prompt", "completion")
LATER = ("certain_later_write", "no_later_write", "possible_later_write")
NOT_ESTABLISHED = (
    "pooled_archived_and_live_effect",
    "model_or_script_identity_from_plan_labels",
    "provider_usage",
    "money_saved",
    "lost_grades",
    "causal_confidence_interval",
)
CAVEAT = (
    "The archived book and the live study are separate samples and are not pooled. "
    "Plan labels do not identify a model or script. "
    "No bound here measures provider usage, money saved, lost grades, or a causal confidence interval."
)

Record = dict[str, Any]


def _bounded_path(path: Path, label: str) -> Path:
    size = len(os.fsencode(path))
    if not 0 < size <= worker_layout.MAX_PATH_BYTES:
        raise ValueError(f"{label} path exceeds {worker_layout.MAX_PATH_BYTES} bytes")
    return path


def _book(path: Path) -> tuple[Record, str]:
    path = _bounded_path(path, "post-return work book")
    try:
        read = read_record_with_digest(path, work_book.MAX_BYTES)
    except (OSError, ValueError) as error:
        raise ValueError("invalid post-return work book: unreadable file") from error
    if read is None:
        raise ValueError("post-return work book is missing")
    book, sha256 = read
    work_book.check(book)
    return book, sha256


def _tokens(work: Record) -> Record:
    tokens: Record = {}
    for kind in KINDS:
        count = work[kind]
        low, high = count["bounds"]
        assert 0 <= low <= high <= count["native"]
        tokens[kind] = {
            "certain_after": int(count["after"]),
            "certain_before": int(count["before"]),
            "crossing": int(count["crossing"]),
            "unplaced": int(count["unplaced"]),
            "native": int(count["native"]),
            "bounds": [int(low), int(high)],
        }
    return tokens


def _work(group: Record) -> Record:
    meters = group["meter_statuses"]
    work = group["work_on_measured_teams"]
    metered = group["measured_teams"]
    unmetered = {k: n for k, n in sorted(meters.items()) if k != MEASURED}
    assert (work is None) == (metered == 0)
    assert metered + sum(unmetered.values()) == group["teams"]
    return {
        "teams": group["teams"],
        "metered": metered,
        "unmetered": unmetered,
        "positive_certain_prompt_and_completion": group[
            "positive_certain_prompt_and_completion"
        ],
        "tokens": None if work is None else _tokens(work),
    }


def _archived(report: Record) -> Record:
    groups = report["groups"]
    every = groups["all"]
    observed = groups["observed_return"]["teams"]
    fates = {fate: groups[fate]["teams"] for fate in work_report.FATES}
    writes = {name: groups[name]["teams"] for name in LATER}
    assert sum(fates.values()) == observed == sum(writes.values())
    assert report["selected_slots"] == report["all_registered_slots"]
    return {
        "report_schema": report["schema"],
        "population": (
            "completed_and_graded_timeouts"
            if report["include_timeouts"]
            else "completed"
        ),
        "slots": {
            "enrolled": report["all_registered_slots"],
            "missing": report["missing_slots"],
            "ambiguous": report["ambiguous_slots"],
            "statuses": report["stock_statuses"],
        },
        "teams": {
            "graded": every["teams"],
            "completed": groups["completed"]["teams"],
            "graded_timeout": groups["graded_timeout"]["teams"],
            "final_one": every["native_successes"],
        },
        "returns": {
            "strict_clean": observed,
            "absent": groups["absent_return"]["teams"],
            "unknown": groups["unknown_return"]["teams"],
        },
        "work": _work(every),
        "board": {
            "standing_grade": fates if observed else None,
            "later_writes": writes if observed else None,
            "forced_losses": _work(groups["forced_loss"]) if observed else None,
        },
    }


def _live(joined: Record) -> Record:
    bounds = joined["bounds"]
    pairs = bounds["denominator"]
    assert pairs == len(joined["blocks"])
    assert joined["known_grades"] == bounds["known_arms"]
    assert joined["paired_finals"] == bounds["paired_finals"]
    workers = joined["workers"]
    return {
        "report_schema": joined["schema"],
        "workers": {
            "planned": workers["planned"],
            "received": len(workers["received"]),
            "unreceived": workers["unreceived"],
        },
        "arms": {
            "enrolled": 2 * pairs,
            "known_grades": bounds["known_arms"],
            "unknown_grades": bounds["missing_arms"],
            "statuses": joined["slots"],
            "identities": joined["identities"],
        },
        "pairs": {
            "enrolled": pairs,
            "finals": joined["paired_finals"],
            "interventions": joined["paired_interventions"],
            "schedules": joined["paired_schedules"],
        },
        "seal_minus_continue": {
            "denominator": pairs,
            "net_sum": bounds["net_sum"],
            "positive_pairs": bounds["positive"],
            "negative_pairs": bounds["negative"],
        },
    }


def _packets(paths: list[Path], plan: Record) -> list[Record]:
    if len(paths) > plan["workers"]:
        raise ValueError("more packets than planned workers")
    return [worker_packets.load_packet(path, plan) for path in paths]


def build(
    book_path: Path,
    plan_path: Path,
    packet_paths: list[Path],
    *,
    guard: int,
    board_ms: int | None,
) -> Record:
    if len(packet_paths) > worker_layout.MAX_WORKERS:
        raise ValueError(f"more than {worker_layout.MAX_WORKERS} worker packets")
    book, book_sha = _book(book_path)
    summary = work_report.summarize(book, guard=guard, board_ms=board_ms)
    plan = worker_layout.load_plan(plan_path)
    packets = _packets(packet_paths, plan)
    joined = worker_report.join(plan, packets)
    received = sorted(
        (packet["worker"], worker_layout.digest(packet)) for packet in packets
    )
    assert [worker for worker, _ in received] == joined["workers"]["received"]
    return {
        "schema": SCHEMA,
        "settings": {
            "host_guard_ms": summary["host_guard_ms"],
            "board_clock_ms": summary["board_clock_ms"],
        },
        "inputs": {
            "book": {
                "schema": book["schema"],
                "sha256": book_sha,
                "producer": book["producer"],
                "include_timeouts": book["include_timeouts"],
                "jobs": len(book["jobs"]),
            },
            "plan": {
                "schema": plan["schema"],
                "digest": joined["plan"],
                "layout": joined["layout"],
                "workers": plan["workers"],
                "pairs": len(plan["blocks"]),
            },
            "packets": [{"worker": w, "digest": d} for w, d in received],
        },
        "archived_book": _archived(summary),
        "live_study": _live(joined),
        "not_established": list(NOT_ESTABLISHED),
    }


def _counts(values: dict[str, int], skip: tuple[str, ...] = ()) -> str:
    shown = [f"{k} {n}" for k, n in sorted(values.items()) if n and k not in skip]
    return ", ".join(shown) or "none"


def _token_lines(work: Record, prefix: str) -> list[str]:
    tokens = work["tokens"]
    if tokens is None:
        return [f"{prefix}tokens after return: unavailable, no metered peer."]
    return [
        f"{prefix}{kind} tokens after return: {tokens[kind]['bounds'][0]}..{tokens[kind]['bounds'][1]} (unplaced {tokens[kind]['unplaced']})."
        for kind in KINDS
    ]


def _board_lines(archived: Record) -> list[str]:
    board = archived["board"]
    observed = archived["returns"]["strict_clean"]
    if board["standing_grade"] is None:
        return [
            "Individually forced board losses: unavailable, no strict clean return."
        ]
    losses = board["forced_losses"]
    lines = [
        f"Individually forced board losses: {losses['teams']} of {observed} strict returns; standing grade unresolved in {board['standing_grade']['grade_unresolved']}.",
    ]
    if losses["teams"]:
        lines.append(
            f"Forced-loss peers metered {losses['metered']} of {losses['teams']}; with certain prompt and completion tokens after return: {losses['positive_certain_prompt_and_completion']}."
        )
        lines.extend(_token_lines(losses, "Forced-loss peer "))
    return lines


def _archived_lines(report: Record) -> list[str]:
    settings = report["settings"]
    book = report["inputs"]["book"]
    archived = report["archived_book"]
    slots, teams = archived["slots"], archived["teams"]
    returns, work = archived["returns"], archived["work"]
    board = settings["board_clock_ms"]
    label = (
        "unanchored"
        if board is None
        else f"fresh world, elapsed clock error <= {board} ms"
    )
    return [
        f"Archived book sha256 {book['sha256']}; producer {book['producer']}; population {archived['population']}.",
        f"Host guard: {settings['host_guard_ms']} ms. Board assumption: {label}.",
        f"Enrolled slots {slots['enrolled']}: missing {slots['missing']}, ambiguous {slots['ambiguous']}, statuses {_counts(slots['statuses'])}.",
        f"Graded teams {teams['graded']} (completed {teams['completed']}, graded timeouts {teams['graded_timeout']}); valid final 1 in {teams['final_one']}.",
        f"Strict clean returns {returns['strict_clean']}; no return {returns['absent']}; return unknown {returns['unknown']}.",
        f"Metered peers {work['metered']} of {work['teams']}; unmetered: {_counts(work['unmetered'])}.",
        f"Peers with certain prompt and completion tokens after return: {work['positive_certain_prompt_and_completion']}.",
        *_token_lines(work, "Recorded peer "),
        *_board_lines(archived),
    ]


def _live_lines(report: Record) -> list[str]:
    plan = report["inputs"]["plan"]
    live = report["live_study"]
    workers, arms = live["workers"], live["arms"]
    pairs, net = live["pairs"], live["seal_minus_continue"]
    low, high = net["net_sum"]
    unreceived = ", ".join(map(str, workers["unreceived"])) or "none"
    lines = [
        f"Live study plan {plan['digest']}; layout {plan['layout']}; {pairs['enrolled']} registered pairs.",
        f"Worker packets received {workers['received']} of {workers['planned']}; not received: {unreceived}.",
    ]
    lines.extend(
        f"Packet worker {packet['worker']} {packet['digest']}."
        for packet in report["inputs"]["packets"]
    )
    lines.extend(
        [
            f"Known arm grades {arms['known_grades']} of {arms['enrolled']}; unknown {arms['unknown_grades']}; slot statuses {_counts(arms['statuses'])}.",
            f"Pairs with both final grades {pairs['finals']} of {pairs['enrolled']}; return-linked interventions {pairs['interventions']} of {pairs['enrolled']}; supported schedules {pairs['schedules']} of {pairs['enrolled']}.",
            f"Possible sum of seal minus continue over all {net['denominator']} registered pairs: {low:+d}..{high:+d}.",
        ]
    )
    return lines


def text(report: Record) -> str:
    assert report["schema"] == SCHEMA
    return "\n".join([*_archived_lines(report), *_live_lines(report), CAVEAT])


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog=PROG,
        description="Read an archived post-return work book and a separate live two-policy study.",
    )
    cli.add_argument("book", type=Path)
    cli.add_argument("plan", type=Path)
    cli.add_argument("packets", type=Path, nargs="*")
    cli.add_argument("--guard-ms", type=int, default=0)
    cli.add_argument("--board-clock-ms", type=int)
    cli.add_argument("--json", action="store_true")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = build(
            args.book,
            args.plan,
            args.packets,
            guard=args.guard_ms,
            board_ms=args.board_clock_ms,
        )
    except (ValueError, OSError, Invalid, Incomplete) as error:
        reason = (
            error.strerror if isinstance(error, OSError) and error.strerror else error
        )
        print(f"team_return: {reason}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                report,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    else:
        print(text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
