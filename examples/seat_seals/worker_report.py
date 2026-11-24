"""Join exported worker packets against a frozen worker plan.

`join(plan, packets)` reads only the public plan and validated worker packets.
It needs no task, raw registration, Harbor, or native trial directory. The
result does not depend on receipt order. A digest is not authentication.

    schema                harbor.seat-seal.worker-report/v1
    plan, layout          plan digest and layout
    workers               planned count, received and unreceived worker ids
    slots                 count of every slot status: the native collector
                          statuses ("missing" = the worker delivered a packet
                          and has no native directory for the ticket), plus
                          "duplicate_identity" and "unreceived"
    identities            witnessed: the row binds one native trial tag;
                          claimed: no native trial tag, but the packet claims
                          native, world or cut identifiers for the ticket
                          (a weak claim, never a grade); unidentified: no tag
                          and no claim, or the worker is unreceived;
                          duplicate: a claimed or witnessed tag repeats under
                          another registered ticket in any received packet,
                          whatever the row status, so the slot grade, join
                          and gate are erased
    known_grades          slots with a supported binary grade
    paired_finals         blocks with both grades known, with or without a join
    paired_interventions  blocks whose two arms are graded and return-linked
    paired_schedules      paired interventions whose two gates are complete
                          or natural
    blocks                plan order; each block has its index, comparison
                          (schedule, intervention, finals, incomplete) and a
                          continue and seal slot {ticket, worker, status,
                          grade, joined, gate, identity}
    bounds                native_pair_bounds.summarize over every block;
                          an unknown grade is an unknown arm, never a value
"""

from typing import Any

from ..native_pair_bounds import summarize
from ..native_study import require
from .collect import GATES, MODES, STATUSES
from .worker_layout import assigned, digest, validate_plan
from .worker_packets import validate_packet

SCHEMA = "harbor.seat-seal.worker-report/v1"
DUPLICATE = "duplicate_identity"
UNRECEIVED = "unreceived"
SLOT_STATUSES = (*STATUSES, DUPLICATE, UNRECEIVED)
IDENTITIES = ("witnessed", "claimed", "duplicate", "unidentified")
SCHEDULED = ("complete", "natural")
COMPARISONS = ("schedule", "intervention", "finals", "incomplete")

Record = dict[str, Any]


def _receive(plan: Record, packets: object) -> dict[int, Record]:
    require(isinstance(packets, list), "worker packets are not a list")
    assert isinstance(packets, list)
    received: dict[int, Record] = {}
    for value in packets:
        packet = validate_packet(value, plan)
        worker = packet["worker"]
        require(worker not in received, f"worker {worker} sent a repeated packet")
        tickets = [row["ticket"] for row in packet["rows"]]
        require(tickets == assigned(plan, worker), f"worker {worker} rows differ")
        received[worker] = packet
    assert len(received) == len(packets) <= plan["workers"]
    return received


def _claims(received: dict[int, Record]) -> dict[str, set[str]]:
    claims: dict[str, set[str]] = {}
    for worker in sorted(received):
        packet = received[worker]
        rows = {row["ticket"]: row for row in packet["rows"]}
        for ticket, tag in packet["claims"]:
            require(ticket in rows, f"worker {worker} claims an unassigned ticket")
            claims.setdefault(ticket, set()).add(tag)
        for ticket, row in rows.items():
            tag = row["native_tag"]
            require(
                tag is None or tag in claims.get(ticket, set()),
                f"worker {worker} ticket {ticket} witness is not claimed",
            )
    return claims


def _quarantined(claims: dict[str, set[str]]) -> set[str]:
    owners: dict[str, set[str]] = {}
    for ticket, tags in claims.items():
        for tag in tags:
            owners.setdefault(tag, set()).add(ticket)
    repeated: set[str] = set()
    for tickets in owners.values():
        if len(tickets) > 1:
            repeated |= tickets
    assert repeated <= set(claims)
    return repeated


def _slot(ticket: str, worker: int, row: Record | None, claimed: bool) -> Record:
    slot: Record = {"ticket": ticket, "worker": worker}
    blank = {"grade": None, "joined": None, "gate": None}
    if row is None:
        assert not claimed
        return {**slot, "status": UNRECEIVED, **blank, "identity": "unidentified"}
    assert row["ticket"] == ticket
    require(
        row["grade"] is None or row["native_tag"] is not None,
        f"ticket {ticket} has a grade without a native witness",
    )
    assert row["status"] in STATUSES and row["gate"] in GATES
    identity = "claimed" if claimed else "unidentified"
    if row["native_tag"] is not None:
        identity = "witnessed"
    fields = {field: row[field] for field in ("status", "grade", "joined", "gate")}
    return {**slot, **fields, "identity": identity}


def _quarantine(slot: Record) -> Record:
    assert slot["status"] != UNRECEIVED
    blank = {"grade": None, "joined": None, "gate": None}
    return {**slot, "status": DUPLICATE, **blank, "identity": "duplicate"}


def _linked(slot: Record) -> bool:
    known = slot["status"] == "graded" and slot["grade"] is not None
    return known and slot["joined"] is True


def _block(index: int, slots: dict[str, Record]) -> Record:
    arms = [slots[mode] for mode in MODES]
    finals = all(arm["grade"] is not None for arm in arms)
    linked = finals and all(_linked(arm) for arm in arms)
    scheduled = linked and all(arm["gate"] in SCHEDULED for arm in arms)
    comparison = COMPARISONS[[scheduled, linked, finals, True].index(True)]
    return {"index": index, "comparison": comparison, **slots}


def _owners(plan: Record) -> dict[str, int]:
    owners = {
        ticket: worker
        for worker in range(plan["workers"])
        for ticket in assigned(plan, worker)
    }
    assert len(owners) == 2 * len(plan["blocks"])
    return owners


def _count(values: list[str], labels: tuple[str, ...]) -> dict[str, int]:
    assert set(values) <= set(labels)
    return {label: values.count(label) for label in labels}


def join(plan: Record, packets: list[Record]) -> Record:
    plan = validate_plan(plan)
    received = _receive(plan, packets)
    rows = {
        row["ticket"]: row
        for worker in sorted(received)
        for row in received[worker]["rows"]
    }
    claims = _claims(received)
    repeated = _quarantined(claims)
    owners = _owners(plan)
    blocks = []
    for index, block in enumerate(plan["blocks"]):
        tickets = {mode: block[mode] for mode in MODES}
        arms: dict[str, Record] = {}
        for mode, ticket in tickets.items():
            row = rows.get(ticket)
            arm = _slot(ticket, owners[ticket], row, ticket in claims)
            arms[mode] = _quarantine(arm) if ticket in repeated else arm
        blocks.append(_block(index, arms))
    slots = [block[mode] for block in blocks for mode in MODES]
    comparisons = _count([block["comparison"] for block in blocks], COMPARISONS)
    finals = len(blocks) - comparisons["incomplete"]
    linked = comparisons["schedule"] + comparisons["intervention"]
    unreceived = [worker for worker in range(plan["workers"]) if worker not in received]
    grades = [(block["continue"]["grade"], block["seal"]["grade"]) for block in blocks]
    bounds = summarize(grades)
    statuses = _count([slot["status"] for slot in slots], SLOT_STATUSES)
    assert bounds["paired_finals"] == finals
    assert statuses[UNRECEIVED] == len(slots) - len(rows)
    return {
        "schema": SCHEMA,
        "plan": digest(plan),
        "layout": plan["layout"],
        "workers": {
            "planned": plan["workers"],
            "received": sorted(received),
            "unreceived": unreceived,
        },
        "slots": statuses,
        "identities": _count([slot["identity"] for slot in slots], IDENTITIES),
        "known_grades": sum(slot["grade"] is not None for slot in slots),
        "paired_finals": finals,
        "paired_interventions": linked,
        "paired_schedules": comparisons["schedule"],
        "blocks": blocks,
        "bounds": bounds,
    }
