You are the right reviewer. Send `GET http://world:8080/review/right`.

The response is a JSON object with an `allowed` array. It contains 1 pair of allowed kit IDs for each of the 24 cases, in case order.

Send the same JSON object to `POST http://world:8080/review/right` with `Content-Type: application/json`. Copy all pairs without changes. Stop after a successful response.
