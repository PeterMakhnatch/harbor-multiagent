"""Run the registered simultaneous box study once on the existing direct Flash ledger."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TypedDict

from . import boxes_run, intent_packet, intent_protocol

ROLE = "box_intent"
RECEIPT_SCHEMA = boxes_run.RECEIPT_SCHEMA


class _Stop(TypedDict):
    run: str
    reason: str | None


class _Summary(TypedDict):
    blocks: int
    scheduled: int
    accepted: int
    invalid: int
    failed: int
    unattempted: int
    skipped_revisions: int
    stopped: _Stop | None


def _run_blocks(
    cells_fd: int,
    worlds: dict[int, intent_packet.World],
    blocks: tuple[intent_protocol.Block, ...],
    spend_dir: Path,
) -> _Summary:
    counts: Counter[str] = Counter()
    skipped_revisions = 0
    scheduled = 9 * len(blocks)

    def summary(stopped: _Stop | None) -> _Summary:
        return {
            "blocks": len(blocks),
            "scheduled": scheduled,
            "accepted": counts["accepted"],
            "invalid": counts["invalid"],
            "failed": counts["failed"],
            "unattempted": scheduled - counts.total(),
            "skipped_revisions": skipped_revisions,
            "stopped": stopped,
        }

    for block in blocks:
        world = worlds[block.instance]
        proposals: intent_protocol.Choices | None = None
        for stage in ("proposal", *block.arms):
            choices: list[int | None] = []
            for label in range(3):
                run_id = intent_protocol.call_id(block, label, stage)
                try:
                    prompt = intent_protocol.prompt(
                        world, block, label, stage, proposals
                    )
                except (ValueError, RecursionError):
                    raise boxes_run.StudyError("prompt_unverified") from None
                receipt = boxes_run._record(
                    cells_fd,
                    prompt,
                    run_id=run_id,
                    role=ROLE,
                    packet_sha256=intent_packet.PACKET_SHA256,
                    numbering=block.numbering,
                    spend_dir=spend_dir,
                )
                status = receipt["status"]
                counts[status] += 1
                if status == "failed":
                    return summary({"run": run_id, "reason": receipt["reason"]})
                if stage == "proposal":
                    choices.append(receipt["original_box"])
            if stage == "proposal":
                a, b, c = choices
                if a is None or b is None or c is None:
                    skipped_revisions += 6
                    break
                # All three receipts are durable. Only this tuple feeds either arm.
                proposals = (a, b, c)
    return summary(None)


def run(study: Path, *, spend_dir: Path) -> _Summary:
    """Claim a new study and store the first response to each attempted call."""
    boxes_run._preflight_host(study, spend_dir)
    with (
        boxes_run._directory(study.parent) as parent,
        boxes_run._ledger_lock(spend_dir, filename=".box-intentions.lock"),
    ):
        boxes_run._preflight_ledger(spend_dir, prefix="bi-", role=ROLE)
        try:
            worlds = intent_packet.load(
                intent_packet.PACKET, intent_packet.PACKET_SHA256
            )
        except (OSError, ValueError, RecursionError):
            raise boxes_run.StudyError("packet_unverified") from None
        blocks = intent_protocol.blocks()
        if worlds.keys() != {block.instance for block in blocks}:
            raise boxes_run.StudyError("packet_unverified")
        with (
            boxes_run._claim(parent, study.name) as study_fd,
            boxes_run._claim(study_fd, "cells") as cells_fd,
        ):
            return _run_blocks(cells_fd, worlds, blocks, spend_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = run(args.study, spend_dir=args.spend_dir)
    except boxes_run.StudyError as error:
        result = str(error)
    except FileExistsError:
        result = "study_or_claim_exists"
    except OSError:
        result = "study_io_failed"
    except KeyboardInterrupt:
        result = "interrupted"
    else:
        print(json.dumps(summary, sort_keys=True))
        return int(summary["stopped"] is not None)
    print(json.dumps({"error": result}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
