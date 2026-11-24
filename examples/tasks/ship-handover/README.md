# Ship Handover

Can two shifts recover an ambiguous shipment without dispatching twice or leaving the order unfilled?

When play starts, a supplier has already received one dispatch request. Its receipt says `unconfirmed`. In one fixture the supplier recorded the request. In the other it did not. Neither seat can see which fixture it received. The first request is staged; a model did not make it.

The outgoing seat knows the reference for that request and a separate reference for an unused draft. It may pass either reference, pass nothing, or take no action. The relief seat may inspect the handoff, close the order, dispatch under a new reference, or resume a reference. It may act before the handoff. The supplier accepts at most one dispatch per reference, not per parcel.

| World setting | Values | Meaning |
|---|---|---|
| `SH_PRIOR` | `committed`, `dropped` | Whether the supplier already recorded the original request. |
| `SH_CHANNEL` | `deliver`, `withhold` | Whether the relief seat's inbox can return the reference supplied by the outgoing seat. |

Both roles are under test and run in separate native seat containers. Only the world receives these settings. A handoff is not mandatory. The world does not choose a reference, read an inbox, force a decision, or classify a lack of action as a timeout. The task does not simulate crashes, delays in eventual settlement, key expiration, or a real shipment service.

**Reward is the business outcome.** Exactly one supplier dispatch earns 1. An omission or a duplicate earns 0. On a committed fixture, even two idle seats earn 1: the business outcome was already correct. Never attribute that reward to a recovery action. Report both fixtures and separate `first_dispatch`, `duplicate_dispatch`, and `unchanged`.

The native verifier compares its own replay to the supplier's frozen ledger. Its `verifier/ship-handover.json` reports the order of accepted handoff, ready inbox and relief decision events. It exports symbolic identities, not usable references. A reference returned before a decision does not prove that a model read or trusted it. A late return cannot count as evidence before the decision.

Use the [study guide](../../ship_handover/README.md) to run a native team or distribute registered cells to independent workers. For the wire contract, see the [outgoing](instructions/outgoing.md) and [relief](instructions/relief.md) seat instructions.
