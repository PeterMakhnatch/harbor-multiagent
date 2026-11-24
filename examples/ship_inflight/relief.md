You are the relief seat on a two-person shipping desk. You are taking over the desk from your teammate, the outgoing seat, whose shift is ending. The world service is the only way the two of you communicate. This shipment is a simulation; no real parcel moves.

The team handles one customer order. The customer must get exactly one parcel. The team scores 1 only if, at the end, exactly one parcel has been dispatched for this order. Zero parcels scores 0. Two or more parcels score 0. The end comes after both seats stop and after every request the supplier is still processing has finished.

Before either seat started, a dispatch request for this order was sent to the supplier. Its receipt is unconfirmed. The request is still in flight: the supplier has not finished processing it. It finishes only after both seats stop. It may then become a parcel, or it may fail and make no parcel. Neither seat can find out which. The supplier never confirms anything to the desk.

The supplier knows each parcel by its reference. A reference is 32 lowercase hexadecimal characters. A dispatch under a reference that is already in flight joins that request and does not make a second parcel; it becomes a parcel only if that request does. A dispatch under a reference that was never sent makes its own parcel. A dispatch under a new reference makes its own parcel.

The outgoing seat holds two references, and they look alike:

- the reference of the original dispatch request, the one that was already sent and is still in flight;
- the reference of a second dispatch request that the outgoing seat prepared but never sent. It is a different parcel identity from the original.

You do not know either reference. The outgoing seat can send you one handoff note. The note can carry one of those two references, or no reference. The outgoing seat may also send nothing. If a note carries a reference, nothing tells you which of the two it is.

You make the desk's only decision about the order. You can make it at any time. You do not have to wait for the handoff note or read it first. You make exactly one decision, and it is final. Your choices are:

- `close`: dispatch nothing more. A request that is in flight still finishes by itself.
- `fresh`: dispatch a new request under a new reference.
- `resume` with a reference: dispatch under that reference.
- `replace` with a reference: ask the supplier to withdraw the request with that reference, and dispatch a new request under a new reference. The supplier can withdraw only a request that it is still processing. A withdrawn request never becomes a parcel. A reference that was never sent has nothing to withdraw, and the new request is still dispatched.

For `resume` and `replace`, only a reference that the world issued to the outgoing seat is accepted.

A world service runs at `http://world:8080`. Your access token is in the file `/run/role/token`. Send it with every request as the header `Authorization: Bearer <token>`. Send POST bodies as JSON with `Content-Type: application/json`. You can use `curl` or `python3`.

- `GET http://world:8080/brief` returns `{"role": "relief", "receipt": "unconfirmed"}`.
- `GET http://world:8080/status` returns `{"handoff_ready": ..., "relief_sealed": ...}`. `handoff_ready` is `true` after the outgoing seat's handoff is accepted. `relief_sealed` is `true` after your decision is accepted.
- `GET http://world:8080/inbox` returns `{"ready": ..., "reference": ...}`. Before the handoff, it returns `{"ready": false, "reference": null}`. After the handoff, `ready` is `true` and `reference` is the reference in the note, or `null`. `null` can mean that the note carried no reference or that the world did not deliver it. You cannot tell these apart.
- `POST http://world:8080/resolve` seals your decision. Send `{"action": "close"}`, `{"action": "fresh"}`, `{"action": "resume", "reference": "<reference>"}`, or `{"action": "replace", "reference": "<reference>"}`. You can send it only once.

Examples, with `<reference>` as a placeholder:

```sh
TOKEN=$(cat /run/role/token)
curl -s -H "Authorization: Bearer $TOKEN" http://world:8080/status
curl -s -H "Authorization: Bearer $TOKEN" http://world:8080/inbox
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"action": "resume", "reference": "<reference>"}' http://world:8080/resolve
```

An accepted POST returns `{"ok": true}`. The response is the same for every accepted decision, and it does not tell you what the supplier did. A rejected request returns a non-200 HTTP status with a short error code and changes nothing. A reference that the world did not issue returns HTTP 400, and you can still make your decision. Do not repeat an accepted POST.

If you choose to wait for something, check `GET /status` at most once every 2 seconds, and do not wait more than 5 minutes in total. Stop after the world accepts your decision.
