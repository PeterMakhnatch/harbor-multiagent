import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ..workbench.seat_stream import _constant, _read, _unique
from . import fanout_panel, mailbox_deadline, mailbox_events, mailbox_panel
from .panel import require

RESULTS = Path(__file__).resolve().parents[1] / "results" / "gather-time"
ROWS = RESULTS / "rows.json"
CUTS = RESULTS / "mailbox-cuts.json"
BUNDLE = "seatlab.fanin-mailbox-cuts/v1"
INSPECT = "seatlab.fanin-mailbox-team/v1"
MAX_DOCUMENT = 2 << 20
STEPS = mailbox_panel.STAGES
PHASES = mailbox_panel.PHASES
Row = dict[str, Any]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _document(path: Path) -> Row:
    raw = _read(str(path), MAX_DOCUMENT, "mailbox file")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
    )
    require(type(value) is dict, "document_shape")
    return value


def _receipts(source: Row) -> list[Row]:
    if source.get("schema") == fanout_panel.ROW:
        return [source]
    fanout_panel.summarize(source)
    return [*source["rows"], *source["controls"]]


def _export(options: argparse.Namespace) -> Row:
    raw = _read(str(options.events), mailbox_events.MAX_BYTES, "native world journal")
    manual = (options.sha256, options.booker, options.reporters)
    if any(v is not None for v in manual):
        require(
            options.rows is None and all(v is not None for v in manual),
            "supply_pin_and_full_roster_or_rows",
        )
        return mailbox_events.project(
            raw,
            expected_sha256=options.sha256,
            booker=options.booker,
            reporters=tuple(options.reporters),
        )
    digest = hashlib.sha256(raw).hexdigest()
    enrolled = [
        r
        for r in _receipts(_document(options.rows or ROWS))
        if isinstance(r.get("inputs"), dict) and r["inputs"].get("events") == digest
    ]
    require(len(enrolled) == 1, "journal_has_no_unique_registered_row")
    row = enrolled[0]
    require(
        row.get("arm") in fanout_panel.ARMS and isinstance(row.get("reporters"), list),
        "row_not_stock_gather",
    )
    cut = mailbox_events.project(
        raw,
        expected_sha256=digest,
        booker=row["booker"],
        reporters=tuple(row["reporters"]),
    )
    source = {"schema": fanout_panel.ENVELOPE, "rows": [], "controls": []}
    source["rows" if row.get("tag") == fanout_panel.LIVE else "controls"] = [row]
    mailbox_panel.summarize(source, [cut])
    return cut


def _cuts(paths: list[Path]) -> list[Row]:
    require(len(paths) <= mailbox_panel.MAX_CUTS, "too_many_files")
    cuts: list[Row] = []
    for path in paths:
        doc = _document(path)
        if doc.get("schema") == mailbox_panel.CUT:
            cuts.append(doc)
        else:
            require(
                doc.keys() == {"schema", "cuts"}
                and doc["schema"] == BUNDLE
                and type(doc["cuts"]) is list,
                "cut_bundle_shape",
            )
            require(
                len(doc["cuts"]) <= mailbox_panel.MAX_CUTS - len(cuts), "too_many_cuts"
            )
            require(all(isinstance(c, dict) for c in doc["cuts"]), "cut_bundle_entry")
            cuts.extend(doc["cuts"])
    require(len(cuts) <= mailbox_panel.MAX_CUTS, "too_many_cuts")
    return cuts


def _audit(options: argparse.Namespace) -> int:
    derived = _export(options)
    received = _cuts([options.cut])
    matches = [
        c for c in received if c.get("events_sha256") == derived["events_sha256"]
    ]
    require(len(matches) <= 1, "cut_duplicate")
    if not matches:
        print("No cut in this file matches the independently pinned journal.")
        return 3
    mailbox_panel._cut(matches[0])
    if derived != matches[0]:
        print("Cut differs from the native journal. Do not use these timings.")
        return 1
    print(
        "Verified: every exported stage and the capture boundary match the native journal."
    )
    return 0


def _inspect(report: Row, cuts: list[Row], options: argparse.Namespace) -> Row:
    key = (options.world, options.arm, options.tag)
    matches = [t for t in report["teams"] if (t["world"], t["arm"], t["tag"]) == key]
    require(len(matches) == 1, "team_not_registered")
    team = matches[0]
    cut = next((c for c in cuts if c["events_sha256"] == team["events_sha256"]), None)
    return {"schema": INSPECT, "team": team, "cut": cut, "scope": report["scope"]}


def _ms(point: list[int] | None) -> str:
    return "not recorded" if point is None else f"{point[1]} ms (event {point[0]})"


def _team_text(view: Row) -> str:
    team, cut = view["team"], view["cut"]
    lines = [
        f"World {team['world']} | {team['arm']} | {team['tag']} | {team['coverage']}",
        f"Saved native reward: {team['native_reward'] if team['native_reward'] is not None else 'unknown'}",
    ]
    if team["native_seat_stop"] is not None:
        lines.append(
            f"Saved native seat stop: {team['native_seat_stop']}. This is not a cause assigned by the mailbox."
        )
    if cut is None:
        lines.append("No mailbox timeline. A missing file is not a failed handoff.")
    else:
        lines.append(
            f"Booker: {cut['booker']}. Times start at its first accepted request."
        )
        for edge in cut["edges"]:
            lines.append(
                f"  {edge['seat']}: "
                + "; ".join(f"{stage}={_ms(edge[stage])}" for stage in STEPS)
            )
        lines.append(f"Booking: {_ms(cut['submit'])}.")
        lines.append(f"Last observed world event: {_ms(cut['through'])}.")
        if team["path"] is not None:
            lines.append(
                f"Last reply accepted: {team['path']['seat']}. Time on this path (ms):"
            )
            lines.extend(f"  {p}: {team['path']['phase_ms'][p]}" for p in PHASES)
    lines.append(
        "An inbox return does not establish that a participant read its contents."
    )
    return "\n".join(lines)


def _panel_text(report: Row) -> str:
    lines = ["Four-seat stock calendar | saved first attempts | no model runs"]
    for arm in fanout_panel.ARMS:
        row = report["arms"][arm]
        lines.append(
            f"{arm}: {row['recorded_teams']}/{row['teams']} model-team timelines; {row['teams_submitted']} observed bookings; {row['edges_reached']['reply_inbox']} reply inbox returns."
        )
        for team in row["incomplete"]:
            lines.append(
                f"  World {team['world']}: no recorded booking. Reporter stages: "
                + ", ".join(
                    f"{edge['seat']}={edge['stage'] or 'not observed'}"
                    for edge in team["stages"]
                )
            )
    controls = [t for t in report["teams"] if t["control"]]
    lines.append(
        f"Scripted controls, separate: {sum(t['coverage'] == 'recorded' for t in controls)}/{len(controls)} timelines."
    )
    lines.append(
        "Use inspect WORLD ARM for the sequence. The clocks measure world events, not model compute."
    )
    return "\n".join(lines)


def _sources(options: argparse.Namespace) -> tuple[Row, list[Row]]:
    paths = (
        options.cuts
        if options.cuts is not None
        else ([] if options.rows is not None else [CUTS])
    )
    return _document(options.rows or ROWS), _cuts(paths)


def _deadline(options: argparse.Namespace) -> int:
    if options.cut is not None:
        require(
            options.rows is None and options.cuts is None,
            "single_cut_cannot_select_cohort",
        )
        result = mailbox_deadline.assess(
            _document(options.cut), milestone=options.milestone, by_ms=options.by_ms
        )
        statuses = [result["status"]]
        text = (
            f"{options.milestone} by {options.by_ms} ms: {result['status']}. "
            f"Observed through {_ms(result['observed_through'])}; target {_ms(result['target'])}.\n"
            + "; ".join(
                f"{s['seat']}={s['stage'] or 'not observed'}" for s in result["stages"]
            )
        )
    else:
        result = mailbox_deadline.cohort(
            *_sources(options), milestone=options.milestone, by_ms=options.by_ms
        )
        statuses = [
            "no_cut" if t["assessment"] is None else t["assessment"]["status"]
            for t in result["teams"]
            if t["tag"] == fanout_panel.LIVE
        ]
        lines = [f"{options.milestone} by {options.by_ms} ms after the first request:"]
        for name, counts in (
            *result["arms"].items(),
            ("scripted controls", result["controls"]),
        ):
            lines.append(
                f"  {name}: "
                + "; ".join(f"{s}={counts[s]}" for s in mailbox_deadline.STATUSES)
            )
        lines.append(
            "A recording that ends early is unknown. Controls do not determine the exit code."
        )
        text = "\n".join(lines)
    print(_json(result) if options.json else text)
    if "not_reached" in statuses:
        return 1
    return 0 if all(s == "reached" for s in statuses) else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seatlab mailbox",
        description="Inspect recorded message paths in the stock four-seat gather. No service, model or transcript is needed.",
    )
    commands = parser.add_subparsers(dest="action", required=True)
    export = commands.add_parser(
        "export", help="reduce one private stock journal to a portable cut"
    )
    audit = commands.add_parser(
        "audit",
        help="rebuild the cut from the original private journal and compare every point",
    )
    audit.add_argument(
        "--cut", type=Path, required=True, help="a worker cut or a bundle containing it"
    )
    for worker in (export, audit):
        worker.add_argument("events", type=Path)
        worker.add_argument(
            "--rows",
            type=Path,
            help="previously verified gather panel or row; defaults to the published study",
        )
        worker.add_argument(
            "--sha256",
            help="journal digest from an independent source, for an unregistered stock task",
        )
        worker.add_argument("--booker", choices=mailbox_events.SEATS)
        worker.add_argument(
            "--reporters",
            nargs=3,
            choices=mailbox_events.SEATS,
            help="expected request order, with the booker excluded",
        )
    panel = commands.add_parser(
        "report", help="report all planned teams; missing workers stay visible"
    )
    inspect = commands.add_parser(
        "inspect", help="inspect one live team or scripted control"
    )
    inspect.add_argument("world", type=int)
    inspect.add_argument("arm", choices=fanout_panel.ARMS)
    inspect.add_argument(
        "--tag", choices=("live", "control-correct", "control-wrong"), default="live"
    )
    deadline = commands.add_parser(
        "deadline", help="ask what the stock world had observed by an elapsed time"
    )
    deadline.add_argument("milestone", choices=mailbox_deadline.MILESTONES)
    deadline.add_argument(
        "--by-ms",
        type=int,
        required=True,
        help="inclusive deadline after the first accepted request",
    )
    deadline.add_argument(
        "--cut", type=Path, help="one standalone worker cut, without study registration"
    )
    for reader in (panel, inspect, deadline):
        reader.add_argument(
            "--rows",
            type=Path,
            help="gather release; by itself this supplies no mailbox cuts",
        )
        reader.add_argument(
            "--cuts",
            type=Path,
            action="append",
            help="one cut or cut bundle; repeat for independent workers",
        )
        reader.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    try:
        if options.action == "export":
            print(_json(_export(options)))
            return 0
        if options.action == "audit":
            return _audit(options)
        if options.action == "deadline":
            return _deadline(options)
        release, cuts = _sources(options)
        report = mailbox_panel.summarize(release, cuts)
        if options.action == "inspect":
            view = _inspect(report, cuts, options)
            print(_json(view) if options.json else _team_text(view))
            return 0 if view["team"]["coverage"] == "recorded" else 3
        print(_json(report) if options.json else _panel_text(report))
        return 0 if all(t["coverage"] == "recorded" for t in report["teams"]) else 3
    except (
        KeyError,
        OSError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ) as error:
        code = (
            str(error)
            if isinstance(error, (mailbox_panel.Rejected, mailbox_events.Bad))
            else "invalid_input"
        )
        print(f"Mailbox input refused: {code}.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
