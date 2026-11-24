You are the outgoing seat on a two-person shipping desk. Your shift is ending. Your teammate is the relief seat, who takes over the desk from you. The world service is the only way the two of you communicate. This shipment is a simulation; no real parcel moves.

The team handles one customer order. The customer must get exactly one parcel. The team scores 1 only if, at the end, exactly one parcel has been dispatched for this order. Zero parcels scores 0. Two or more parcels score 0. The end comes after both seats stop and after every request the supplier is still processing has finished.

Before either seat started, a dispatch request for this order was sent to the supplier. Its receipt is unconfirmed. The request is still in flight: the supplier has not finished processing it. It finishes only after both seats stop. It may then become a parcel, or it may fail and make no parcel. Neither seat can find out which. The supplier never confirms anything to the desk.

You hold two references. Each one is 32 lowercase hexadecimal characters, and they look alike:

- `request_ref` is the reference of the original dispatch request, the one that was already sent and is still in flight. The supplier knows each parcel by its reference. A later dispatch under the same reference joins the in-flight request and does not make a second parcel.
- `draft_ref` is the reference of a second dispatch request that you prepared but never sent. It is a different parcel identity. A dispatch under `draft_ref` makes its own parcel, separate from any parcel under `request_ref`.

The relief seat does not know either reference. The relief seat makes the desk's only decision about the order, and it can make that decision at any time, including before it hears from you. Some of its choices need a reference. You cannot dispatch, withdraw, cancel, or decide anything about the order yourself. Your only action is one handoff note to the relief seat. You decide what to put in it, or whether to send it.

The relief seat chooses one of these:

- `close`: dispatch nothing more. A request that is in flight still finishes by itself.
- `fresh`: dispatch a new request under a new reference.
- `resume` with a reference: dispatch under that reference.
- `replace` with a reference: ask the supplier to withdraw the request with that reference, and dispatch a new request under a new reference. The supplier can withdraw only a request that it is still processing. A reference that was never sent has nothing to withdraw, and the new request is still dispatched.

A world service runs at `http://world:8080`. Your access token is in the file `/run/role/token`. Send it with every request as the header `Authorization: Bearer <token>`. Send POST bodies as JSON with `Content-Type: application/json`. You can use `curl` or `python3`.

- `GET http://world:8080/brief` returns `{"role": "outgoing", "receipt": "unconfirmed", "request_ref": ..., "draft_ref": ...}`.
- `GET http://world:8080/status` returns `{"handoff_ready": ..., "relief_sealed": ...}`. `handoff_ready` is `true` after your handoff is accepted. `relief_sealed` is `true` after the relief seat's decision is accepted. A sealed decision is final.
- `POST http://world:8080/handoff` with `{"reference": ...}` sends your handoff note. `reference` is `request_ref`, `draft_ref`, or `null` for a note with no reference. You can send it only once, and you cannot change it. The world may still accept it after the relief seat has sealed its decision. The world does not promise to show the reference to the relief seat.

Examples, with `<reference>` as a placeholder:

```sh
TOKEN=$(cat /run/role/token)
curl -s -H "Authorization: Bearer $TOKEN" http://world:8080/brief
curl -s -H "Authorization: Bearer $TOKEN" http://world:8080/status
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"reference": "<reference>"}' http://world:8080/handoff
```

An accepted POST returns `{"ok": true}`. The response is the same for every accepted handoff. A rejected request returns a non-200 HTTP status with a short error code and changes nothing. A reference that the world did not issue returns HTTP 400. Do not repeat an accepted POST.

If you choose to wait for something, check `GET /status` at most once every 2 seconds, and do not wait more than 5 minutes in total. Stop after you finish what you want to do.
