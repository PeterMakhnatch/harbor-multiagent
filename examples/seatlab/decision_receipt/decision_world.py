import copy
import hmac
import os
import secrets
import uuid
from typing import cast, override

from inflight_world import CUT_KEYS as INFLIGHT_KEYS
from inflight_world import SCHEMA as INFLIGHT_SCHEMA
from inflight_world import InflightShipment, InflightWorld, settings
from shipment import ROLES, Identity, _error, is_ref
from shipment import Reply as Outcome
from world import PORT, TOKEN_ROOT, Reply, WorldServer, fresh_refs, publish_tokens

SCHEMA = "harbor.ship-decision-receipt.cut/v1"
CITATION = "cited_at_decision"
CITE_FIELD = "cite"
CUT_KEYS = (*INFLIGHT_KEYS, CITATION)
CITATIONS = (None, "original", "draft")

assert INFLIGHT_KEYS[0] == "schema" and SCHEMA != INFLIGHT_SCHEMA
assert CITATION not in INFLIGHT_KEYS


class DecisionShipment(InflightShipment):
    def __init__(
        self, fate: str, channel: str, run: str, request_ref: str, draft_ref: str
    ) -> None:
        super().__init__(fate, channel, run, request_ref, draft_ref)
        self._returned: Identity | None = None
        self._cited: Identity | None = None
        self._decision_cut: dict[str, object] | None = None

    @override
    def freeze(self) -> dict[str, object]:
        legacy = super().freeze()
        if self._decision_cut is None:
            self._decision_cut = {**legacy, "schema": SCHEMA, CITATION: self._cited}
        self._check()
        return copy.deepcopy(self._decision_cut)

    @override
    def _inbox(self) -> Outcome:
        status, payload = super()._inbox()
        reference = payload.get("reference")
        if status == 200 and reference is not None:
            identity = self._refs[cast(str, reference)]
            assert self._returned in (None, identity)
            self._returned = identity
        return status, payload

    @override
    def _post_resolve(self, body: object) -> Outcome:
        if not isinstance(body, dict) or CITE_FIELD not in body:
            return super()._post_resolve(body)
        fields = dict(cast(dict[str, object], body))
        cited = self._citation(fields.pop(CITE_FIELD))
        if isinstance(cited, tuple):
            return cited
        status, payload = super()._post_resolve(fields)
        if status == 200:
            assert self._resolved and self._cited is None
            self._cited = cited
        return status, payload

    def _citation(self, cite: object) -> Identity | Outcome:
        if not is_ref(cite):
            return _error(400, "bad request")
        returned = self._ref_of(self._returned)
        if returned is None or not hmac.compare_digest(cast(str, cite), returned):
            return _error(400, "unknown reference")
        assert self._returned in ("original", "draft")
        return self._returned

    @override
    def _check(self) -> None:
        super()._check()
        assert self._returned in CITATIONS and self._cited in CITATIONS
        assert self._returned is None or (
            self._inbox_recorded
            and self.channel == "deliver"
            and self._handoff == self._returned
        )
        assert self._cited is None or (self._resolved and self._cited == self._returned)
        assert self._decision_cut is None or self._inflight_cut is not None
        if self._decision_cut is None or self._inflight_cut is None:
            return
        cut = self._decision_cut
        legacy = self._inflight_cut
        assert tuple(cut) == CUT_KEYS and cut["schema"] == SCHEMA
        assert cut[CITATION] == self._cited
        assert all(cut[key] == legacy[key] for key in INFLIGHT_KEYS[1:])


class DecisionWorld(InflightWorld):
    def __init__(
        self, shipment: DecisionShipment, tokens: dict[str, str], refs: tuple[str, str]
    ) -> None:
        assert isinstance(shipment, DecisionShipment)
        super().__init__(shipment, tokens, refs)

    @override
    def freeze(self) -> Reply:
        def cut() -> tuple[object, object]:
            frozen = self._shipment.freeze()
            if not isinstance(frozen, dict) or tuple(frozen) != CUT_KEYS:
                raise TypeError("decision cut is malformed")
            if frozen["schema"] != SCHEMA:
                raise TypeError("decision cut schema is malformed")
            if frozen[CITATION] not in CITATIONS:
                raise TypeError("decision cut citation is malformed")
            return 200, frozen

        return self._guarded(cut, self._private)


def main() -> None:
    fate, channel = settings(dict(os.environ))
    run = str(uuid.uuid4())
    assert str(uuid.UUID(run)) == run
    request_ref, draft_ref = fresh_refs()
    shipment = DecisionShipment(
        fate=fate,
        channel=channel,
        run=run,
        request_ref=request_ref,
        draft_ref=draft_ref,
    )
    tokens: dict[str, str] = {role: secrets.token_urlsafe(32) for role in ROLES}
    world = DecisionWorld(shipment, tokens, (request_ref, draft_ref))
    publish_tokens(world.tokens, TOKEN_ROOT)
    with WorldServer(world, ("0.0.0.0", PORT)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
