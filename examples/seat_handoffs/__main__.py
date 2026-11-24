import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import bespoke, calendar, core, job
from .trial import read_trial


def _job_report(path: Path, boundary: str, as_json: bool) -> int:
    try:
        snapshot = job.inspect_job(path, boundary=boundary)
    except (OSError, ValueError) as exc:
        print(f"Cannot inspect job: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(snapshot, allow_nan=False, separators=(",", ":")))
    else:
        print(f"Job: {snapshot['job_id']}   Receipt boundary: {boundary}")
        print(
            f"Registered slots: {snapshot['registered_slots']}   "
            f"Matched trials: {snapshot['matched_trials']}   "
            f"Missing slots: {snapshot['missing_slots']}"
        )
        print(
            f"Valid native grades: {snapshot['valid_native_grades']}   "
            f"Usable trials: {snapshot['usable_trials']}   "
            f"Unassigned directories: {len(snapshot['unassigned'])}"
        )
        for record in snapshot["trials"]:
            print(f"{json.dumps(record['directory'])}\t{record['status']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect team handoffs independently of team grades."
    )
    parser.add_argument("source", choices=("events", "job", "bespoke", "calendar"))
    parser.add_argument("paths", type=Path, nargs="+", metavar="PATH")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit findings, native trial links, and counts",
    )
    parser.add_argument(
        "--boundary",
        choices=core.BOUNDARIES,
        help="for event logs or native jobs: inspect prepared replies or receiver acknowledgements",
    )
    options = parser.parse_args()
    if options.boundary and options.source not in ("events", "job"):
        parser.error(
            "--boundary applies only to events and jobs; archives use recorded model requests"
        )
    boundary = options.boundary or "returned"
    if options.source == "job":
        if len(options.paths) != 1:
            parser.error("job needs one native job directory")
        return _job_report(options.paths[0], boundary, options.json)
    adapters = {"bespoke": bespoke.inspect, "calendar": calendar.inspect}
    rows: list[core.Finding] = []
    links = []
    try:
        for path in options.paths:
            if options.source == "events" and path.is_dir():
                found, link = read_trial(path, boundary=boundary)
                links.append(link)
            elif options.source == "events":
                found = core.inspect(path, boundary=boundary)
            else:
                found = adapters[options.source](path)
            rows.extend(found)
        ids = [
            link["native_trial"] for link in links if link["native_trial"] is not None
        ]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate native trial identifier")
        counts = core.summarize(rows)
    except (OSError, ValueError) as exc:
        print(f"Cannot inspect handoffs: {exc}", file=sys.stderr)
        return 2
    report = {
        "schema": "harbor.handoff-inspection/v1",
        "source": options.source,
        "boundary": boundary if options.source == "events" else "model_request",
        "findings": [asdict(row) for row in rows],
        "native_trials": links,
        "summary": counts,
    }
    if options.json:
        print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    else:
        print("Receipt boundary:", report["boundary"])
        print(
            "trial\tmessage\tsender\treceiver\treceipt\ttiming\tcommit\treward\tbasis"
        )
        for row in rows:
            print(
                "\t".join(
                    "unknown" if value is None else str(value)
                    for value in asdict(row).values()
                )
            )
        if links:
            print("Native trial links:", json.dumps(links, sort_keys=True))
        print("Summary:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
