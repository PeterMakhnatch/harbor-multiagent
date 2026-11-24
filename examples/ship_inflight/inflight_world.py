import copy
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import cast, override

from shipment import CHANNELS, IDENTITIES, ROLES, Identity, Shipment, _error
from shipment import Reply as Outcome
from world import PORT, TOKEN_ROOT, Reply, World, WorldServer, fresh_refs, publish_tokens

SCHEMA = "harbor.ship-inflight.cut/v1"
FATES = ("settles", "fails")
CUT_KEYS = ("schema", "run", "fate", "channel", "events", "replace", "settled", "drained")
UNSETTLED_START = "dropped"
SETTLING_CHOICES = ("fresh", "draft")


@dataclass(frozen=True, slots=True)
class Resolution:
    choice: str
    replace: Identity | None

    def __post_init__(self) -> None:
        assert self.replace is None or self.replace in ("original", "draft")
        assert self.replace is None or self.choice == "fresh"


class InflightShipment(Shipment):
    def __init__(
        self, fate: str, channel: str, run: str, request_ref: str, draft_ref: str
    ) -> None:
        if fate not in FATES:
            raise ValueError(f"fate must be one of {list(FATES)}")
        super().__init__(UNSETTLED_START, channel, run, request_ref, draft_ref)
        self._fate = fate
        self._replace: Identity | None = None
        self._inflight_cut: dict[str, object] | None = None

    @override
    def freeze(self) -> dict[str, object]:
        stock = super().freeze()
        if self._inflight_cut is None:
            settled = self._ledger.snapshot()
            self._inflight_cut = {
                "schema": SCHEMA,
                "run": stock["run"],
                "fate": self._fate,
                "channel": stock["channel"],
                "events": stock["events"],
                "replace": self._replace,
                "settled": settled,
                "drained": [*settled, *(["original"] if self._drains() else [])],
            }
        self._check()
        return copy.deepcopy(self._inflight_cut)

    def _drains(self) -> bool:
        return self._fate == "settles" and self._replace != "original"

    @override
    def _post_resolve(self, body: object) -> Outcome:
        parsed = self._parse_inflight(body)
        if not isinstance(parsed, Resolution):
            return parsed
        if self.frozen:
            return _error(409, "frozen")
        if self._resolved:
            return _error(409, "already resolved")
        self._resolved = True
        self._replace = parsed.replace
        if parsed.choice in SETTLING_CHOICES:
            self._ledger.accept(cast(Identity, parsed.choice))
        self._emit("resolve", parsed.choice)
        return 200, {"ok": True}

    def _parse_inflight(self, body: object) -> Resolution | Outcome:
        replacing = isinstance(body, dict) and body.get("action") == "replace"
        if replacing:
            body = {**cast(dict[str, object], body), "action": "resume"}
        parsed = self._parse_resolve(body)
        if isinstance(parsed, tuple):
            return parsed
        if replacing:
            return Resolution(choice="fresh", replace=cast(Identity, parsed))
        return Resolution(choice=parsed, replace=None)

    @override
    def _check(self) -> None:
        super()._check()
        entries = self._ledger.snapshot()
        resolves = [event.choice for event in self._trace if event.kind == "resolve"]
        assert self.prior == UNSETTLED_START and self._fate in FATES
        assert not self._ledger.contains("original")
        assert len(resolves) == int(self._resolved)
        assert entries == [choice for choice in resolves if choice in SETTLING_CHOICES]
        assert self._replace is None or resolves == ["fresh"]
        assert self._replace in (None, "original", "draft")
        assert self._inflight_cut is None or self._cut is not None
        if self._inflight_cut is None or self._cut is None:
            return
        cut = self._inflight_cut
        drained = cast(list[object], cut["drained"])
        assert tuple(cut) == CUT_KEYS and cut["schema"] == SCHEMA
        assert cut["events"] == self._cut["events"] and cut["fate"] == self._fate
        assert cut["settled"] == entries and cut["replace"] == self._replace
        assert drained == [*entries, *(["original"] if self._drains() else [])]
        assert set(drained) <= set(IDENTITIES) and len(set(drained)) == len(drained)


class InflightWorld(World):
    @override
    def freeze(self) -> Reply:
        def cut() -> tuple[object, object]:
            frozen = self._shipment.freeze()
            if not isinstance(frozen, dict) or tuple(frozen) != CUT_KEYS:
                raise TypeError("inflight cut is malformed")
            if frozen["schema"] != SCHEMA:
                raise TypeError("inflight cut schema is malformed")
            return 200, frozen

        return self._guarded(cut, self._private)


def settings(environ: dict[str, str]) -> tuple[str, str]:
    fate = environ.get("SH_FATE")
    if fate not in FATES:
        raise ValueError(f"SH_FATE must be one of {list(FATES)}")
    channel = environ.get("SH_CHANNEL")
    if channel not in CHANNELS:
        raise ValueError(f"SH_CHANNEL must be one of {list(CHANNELS)}")
    return fate, channel


def main() -> None:
    fate, channel = settings(dict(os.environ))
    run = str(uuid.uuid4())
    assert str(uuid.UUID(run)) == run
    request_ref, draft_ref = fresh_refs()
    shipment = InflightShipment(
        fate=fate,
        channel=channel,
        run=run,
        request_ref=request_ref,
        draft_ref=draft_ref,
    )
    tokens: dict[str, str] = {role: secrets.token_urlsafe(32) for role in ROLES}
    world = InflightWorld(shipment, tokens, (request_ref, draft_ref))
    publish_tokens(world.tokens, TOKEN_ROOT)
    with WorldServer(world, ("0.0.0.0", PORT)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
