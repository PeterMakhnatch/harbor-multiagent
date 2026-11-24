import json
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench.ledger_scope import (
    MAX_LABELS,
    SCHEMA,
    render,
    summarize,
)
from examples.seatlab.workbench.seat_stream import Malformed

Json = dict[str, Any]
REQUESTED = "openai/gpt-5-mini"
SERVED = "openai/gpt-5-mini-2025"
FREE = "meta-llama/llama-3.1-8b-instruct:free"
PRIVATE = "7f3a"
TAIL = (
    b'"request_sha256":"'
    + b"0" * 64
    + b'","reserved_nano":2000000,"input_token_bound":4096,"output_token_bound":512}\n'
)
AT = b'"at":"2026-11-20T15:00:00Z"'
SAVED = b"".join(
    [
        b'{"event":"started",' + AT + b',"call_id":"call-7f3a-1",'
        b'"identity":"plain/priv-7f3a-a","model":"openai/gpt-5-mini",' + TAIL,
        b'{"event":"settled",' + AT + b',"call_id":"call-7f3a-1","cost_nano":1500000,'
        b'"cost_basis":"openrouter_generation.total_cost","receipt":{"id":"gen-7f3a-1",'
        b'"total_cost":0.0015,"model":"openai/gpt-5-mini-2025","provider_name":"Azure",'
        b'"tokens_prompt":12,"tokens_completion":3}}\n',
        b'{"event":"settled",' + AT + b',"call_id":"call-7f3a-1","cost_nano":1500000,'
        b'"cost_basis":"openrouter_generation.total_cost","receipt":{"id":"gen-7f3a-1",'
        b'"total_cost":0.0015,"model":"openai/gpt-5-mini-2025","provider_name":"Azure",'
        b'"tokens_prompt":12,"tokens_completion":3}}\n',
        b'{"event":"started",' + AT + b',"call_id":"call-7f3a-2",'
        b'"identity":"plainish/priv-7f3a-b","model":"openai/gpt-5-mini",' + TAIL,
        b'{"event":"billing_unavailable",' + AT + b',"call_id":"call-7f3a-2",'
        b'"error_type":"ReadTimeout"}\n',
        b'{"event":"settled",' + AT + b',"call_id":"call-7f3a-2","cost_nano":1,'
        b'"cost_basis":"openrouter_generation.total_cost","receipt":{"id":"gen-7f3a-2",'
        b'"total_cost":0.000000001}}\n',
        b'{"event":"started",' + AT + b',"call_id":"call-7f3a-3",'
        b'"identity":"v1/priv-7f3a-c","model":"openai/gpt-5-mini",' + TAIL,
        b'{"event":"failed",'
        + AT
        + b',"call_id":"call-7f3a-3","error_type":"HTTPError",'
        b'"http_status":502,"provider_detail":null,"reservation_retained":false}\n',
        b'{"event":"started",' + AT + b',"call_id":"call-7f3a-4",'
        b'"identity":"plain","model":"openai/gpt-5-mini ",' + TAIL,
        b'{"event":"hold",' + AT + b',"call_id":"call-7f3a-4","reason":"budget"}\n',
        b'{"event":"started",' + AT + b',"call_id":"call-7f3a-5",'
        b'"identity":7,"model":"meta-llama/llama-3.1-8b-instruct:free",' + TAIL,
        b'{"event":"billing_unavailable",' + AT + b',"call_id":"call-7f3a-5",'
        b'"error_type":"HTTPError","http_status":503}\n',
        b'{"event":"started",' + AT + b',"call_id":"call-7f3a-6",'
        b'"identity":"plain/priv-7f3a-d","model":"meta-llama/llama-3.1-8b-instruct:free",'
        + TAIL,
        b'{"event":"started",' + AT + b',"call_id":"call-7f3a-6",'
        b'"identity":"plain/priv-7f3a-d","model":"meta-llama/llama-3.1-8b-instruct:free",'
        + TAIL,
        b'{"event":"settled",' + AT + b',"call_id":"call-7f3a-6","cost_nano":333333334,'
        b'"cost_basis":"openrouter_generation.total_cost","receipt":{"id":"gen-7f3a-6",'
        b'"total_cost":0.3333333333,"model":"meta-llama/llama-3.1-8b-instruct:free",'
        b'"provider_name":"Google AI Studio"}}\n',
    ]
)


def counts(
    started: int,
    settled: int,
    usd: str,
    failed: int = 0,
    unavailable: int = 0,
    open_: int = 0,
) -> Json:
    assert started == settled + failed + unavailable + open_
    return {
        "started": started,
        "settled": settled,
        "unresolved": {
            "failed": failed,
            "billing_unavailable": unavailable,
            "no_outcome": open_,
        },
        "known_usd": usd,
    }


def save(tmp_path: Path, data: bytes) -> str:
    path = tmp_path / "events.jsonl"
    path.write_bytes(data)
    return str(path)


def started(call: str, model: str, identity: str = "plain/x") -> Json:
    return {
        "event": "started",
        "at": "2026-11-20T15:00:00Z",
        "call_id": call,
        "identity": identity,
        "model": model,
        "request_sha256": "0" * 64,
        "reserved_nano": 2_000_000,
        "input_token_bound": 4096,
        "output_token_bound": 512,
    }


def settled(call: str, total: float, nano: int, **route: Any) -> Json:
    return {
        "event": "settled",
        "at": "2026-11-20T15:00:02Z",
        "call_id": call,
        "cost_nano": nano,
        "cost_basis": "openrouter_generation.total_cost",
        "receipt": {"id": f"gen-{call}", "total_cost": total, **route},
    }


def rows(tmp_path: Path, records: list[Json]) -> str:
    data = "".join(json.dumps(row) + "\n" for row in records).encode()
    return save(tmp_path, data)


def test_saved_ledger_projects_alternative_scopes(tmp_path):
    path = save(tmp_path, SAVED)
    report = summarize(path, families=["plainish", "plain"], routes=True)
    assert report["schema"] == SCHEMA and report["currency"] == "USD"
    assert report["ledger"]["repeated_start_rows"] == 1
    assert report["ledger"]["repeated_settlement_rows"] == 1
    assert report["ledger"]["reservation_holds"] == 1
    assert report["all_calls"] == counts(6, 3, "0.3348333343", 1, 1, 1)
    assert report["families"] == [
        {"family": "plain", **counts(2, 2, "0.3348333333")},
        {"family": "plainish", **counts(1, 1, "0.000000001")},
        {"family": None, **counts(3, 0, "0", 1, 1, 1)},
    ]
    assert report["requested_routes"] == [
        {"model": FREE, **counts(2, 1, "0.3333333333", 0, 1)},
        {"model": REQUESTED, **counts(3, 2, "0.001500001", 1)},
        {"model": None, **counts(1, 0, "0", 0, 0, 1)},
    ]
    assert report["reported_routes"] == [
        {
            "model": FREE,
            "provider": "Google AI Studio",
            "settled": 1,
            "known_usd": "0.3333333333",
        },
        {"model": SERVED, "provider": "Azure", "settled": 1, "known_usd": "0.0015"},
        {"model": None, "provider": None, "settled": 1, "known_usd": "0.000000001"},
    ]


@pytest.mark.parametrize(
    ("tags", "plain", "plainish"),
    [
        (["plain"], counts(2, 2, "0.3348333333"), None),
        (["plainish"], None, counts(1, 1, "0.000000001")),
        ([], None, None),
    ],
)
def test_family_is_exact_first_segment_never_a_prefix(tmp_path, tags, plain, plainish):
    report = summarize(save(tmp_path, SAVED), families=tags, routes=False)
    families = {row.pop("family"): row for row in report["families"]}
    assert list(families) == [*tags, None]
    assert families.get("plain") == plain
    assert families.get("plainish") == plainish
    other = families[None]
    matched = sum(row["started"] for row in (plain, plainish) if row)
    assert other["started"] == 6 - matched
    assert sum(row["started"] for row in families.values()) == 6


def test_routes_off_leaks_no_route_label(tmp_path):
    report = summarize(save(tmp_path, SAVED), families=["plain"], routes=False)
    assert report["requested_routes"] is None and report["reported_routes"] is None
    text = render(report)
    assert "routes: not collected" in text
    for label in (REQUESTED, SERVED, FREE, "Azure", "Google AI Studio", "openai"):
        assert label not in json.dumps(report) + text


def test_outputs_omit_call_generation_and_identity_detail(tmp_path):
    report = summarize(save(tmp_path, SAVED), families=["plain", "v1"], routes=True)
    text = render(report)
    hidden = (PRIVATE, "call-", "gen-", "priv", "0" * 64, "ReadTimeout", "budget")
    for private in hidden:
        assert private not in json.dumps(report) + text
    for claim in ("Harbor", "grade", "reward", "trial-", "invoice"):
        assert claim not in json.dumps(report) + text


def test_render_names_alternative_scopes_and_labels(tmp_path):
    report = summarize(save(tmp_path, SAVED), families=["plain"], routes=True)
    text = render(report)
    assert report["caveat"] in text
    assert 'family="plain"' in text and "family=null" in text
    assert f'model="{SERVED}" provider="Azure"' in text
    assert "model=null provider=null" in text
    assert "0.3348333343  all" in text


def test_zero_observations_keep_other_bucket(tmp_path):
    path = save(tmp_path, b"")
    report = summarize(path, families=["plain"], routes=True)
    assert report["all_calls"] == counts(0, 0, "0")
    assert report["families"] == [
        {"family": "plain", **counts(0, 0, "0")},
        {"family": None, **counts(0, 0, "0")},
    ]
    assert report["requested_routes"] == [] and report["reported_routes"] == []
    assert "family=null" in render(report)


def test_exact_decimal_ignores_nano_ceiling_and_binary_floats(tmp_path):
    path = rows(
        tmp_path,
        [
            started("c1", REQUESTED),
            settled("c1", 0.1, 100_000_000),
            started("c2", REQUESTED),
            settled("c2", 0.2, 200_000_000),
            started("c3", REQUESTED),
            settled("c3", 0.0000000001, 1),
        ],
    )
    report = summarize(path, families=["plain"], routes=True)
    assert report["all_calls"]["known_usd"] == "0.3000000001"
    assert report["families"][0]["known_usd"] == "0.3000000001"
    assert report["reported_routes"] == [
        {"model": None, "provider": None, "settled": 3, "known_usd": "0.3000000001"}
    ]


def test_recovered_billing_unavailable_counts_once_as_settled(tmp_path):
    unavailable = {
        "event": "billing_unavailable",
        "at": "2026-11-20T15:00:01Z",
        "call_id": "c1",
        "error_type": "ReadTimeout",
    }
    path = rows(
        tmp_path,
        [
            started("c1", REQUESTED),
            unavailable,
            settled("c1", 0.002, 2_000_000, model=SERVED),
            unavailable,
            settled("c1", 0.002, 2_000_000, model=SERVED),
        ],
    )
    report = summarize(path, families=[], routes=True)
    assert report["all_calls"] == counts(1, 1, "0.002")
    assert report["requested_routes"] == [{"model": REQUESTED, **counts(1, 1, "0.002")}]
    assert report["reported_routes"] == [
        {"model": SERVED, "provider": None, "settled": 1, "known_usd": "0.002"}
    ]


@pytest.mark.parametrize(
    ("families", "reason"),
    [
        (("plain",), "invalid family tag list"),
        (["pla/in"], "invalid family tag"),
        ([""], "invalid family tag"),
        (["plain "], "invalid family tag"),
        (["pl\u00e4in"], "invalid family tag"),
        ([7], "invalid family tag"),
        (["plain", "plain"], "repeated family tag"),
        ([f"t{index}" for index in range(17)], "more than 16 family tags"),
    ],
)
def test_invalid_family_tags_refuse_without_echo(tmp_path, families, reason):
    with pytest.raises(Malformed) as error:
        summarize(save(tmp_path, SAVED), families=families, routes=False)
    assert str(error.value) == reason


def test_route_option_must_be_boolean(tmp_path):
    with pytest.raises(Malformed, match="invalid route option"):
        invalid: Any = 1
        summarize(save(tmp_path, SAVED), families=[], routes=invalid)


def test_requested_labels_beyond_bound_refuse_instead_of_truncating(tmp_path):
    records = [started(f"c{index}", f"vendor/m{index}") for index in range(MAX_LABELS)]
    path = rows(tmp_path, records)
    report = summarize(path, families=[], routes=True)
    assert len(report["requested_routes"]) == MAX_LABELS
    path = rows(tmp_path, [*records, started("c-extra", "vendor/extra")])
    with pytest.raises(Malformed) as error:
        summarize(path, families=[], routes=True)
    assert str(error.value) == f"more than {MAX_LABELS} requested route labels"
    assert summarize(path, families=[], routes=False)["all_calls"]["started"] == 257


def test_reported_labels_beyond_bound_refuse_instead_of_truncating(tmp_path):
    records: list[Json] = []
    for index in range(MAX_LABELS + 1):
        call = f"c{index}"
        route = settled(call, 0.001, 1_000_000, model=f"v/m{index}")
        records += [started(call, REQUESTED), route]
    with pytest.raises(Malformed) as error:
        summarize(rows(tmp_path, records), families=[], routes=True)
    assert str(error.value) == f"more than {MAX_LABELS} reported route labels"
