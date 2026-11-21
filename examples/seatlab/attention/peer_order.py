import sys

from . import protocol, ranking

_ARMS = ("report", "utility")
_MAX_PROMPT_BYTES = 2048
_BAYS = frozenset(protocol._BAYS)


def variant(shown: str, arm: str) -> str:
    if arm not in _ARMS:
        raise ValueError("Unknown peer order arm")
    if not isinstance(shown, str) or not shown.isascii():
        raise ValueError("Expected an ASCII final receiver prompt")
    if len(shown) > _MAX_PROMPT_BYTES:
        raise ValueError("Final receiver prompt is too large")
    rules, separator, payload = shown.partition("\n")
    if not separator or rules != protocol._DECIDE_RULES:
        raise ValueError("Expected plain final receiver rules")
    rows = protocol._reply(payload, "cases")
    ranked = ranking._rank_visible_rows(rows)
    if tuple(ranked) != protocol._ALIASES:
        raise ValueError("Expected native final receiver case order")
    reported: dict[str, list[str]] = {}
    for row in rows:
        previous = row["revision_1"]
        if "revision_2" in row and (
            not isinstance(previous, list)
            or not 1 <= len(previous) <= len(_BAYS)
            or any(not isinstance(bay, str) or bay not in _BAYS for bay in previous)
            or len(set(previous)) != len(previous)
        ):
            raise ValueError("Invalid revision 1 report in final prompt case")
        reported[row["alias"]] = row.get("revision_2", previous)
    if shown != protocol._prompt(protocol._DECIDE_RULES, {"cases": rows}):
        raise ValueError("Expected canonical final receiver JSON")
    mapping = reported if arm == "report" else ranked
    return protocol._prompt(shown, {"latest_peer_reported_bays": mapping})


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in _ARMS:
        print(
            "usage: python -BS -m examples.seatlab.attention.peer_order {report,utility}",
            file=sys.stderr,
        )
        return 2
    try:
        frame = sys.stdin.buffer.read(_MAX_PROMPT_BYTES + 2).removesuffix(b"\n")
        rendered = variant(frame.decode("ascii"), sys.argv[1])
    except UnicodeDecodeError:
        print("peer_order: expected an ASCII final receiver prompt", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"peer_order: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("peer_order: cannot read the final receiver prompt", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(rendered.encode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
