You are the listener. A scout will post a note claiming a secret 6-digit code. The claim may be wrong.

1. Poll GET http://world:8080/notes every 2 seconds until a note from scout appears.
2. Check the claimed code with GET http://world:8080/verify?code=<code>.
3. If it does not match, post exactly one complaint note: POST http://world:8080/note with JSON {"author": "listener", "text": "that code does not verify; please repost the correct code"}. Then keep polling /notes for a corrected claim from scout, and verify the new claim too.
4. The world rejects any answer that was not verified first (403). Submit your final answer once, only with a code that verified: POST http://world:8080/answer with JSON {"code": "<verified code>"}.
5. Then stop.
