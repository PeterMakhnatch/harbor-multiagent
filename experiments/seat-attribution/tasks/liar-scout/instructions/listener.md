You are the listener. A scout will post a note claiming a secret 6-digit code.

1. Poll GET http://world:8080/notes every 2 seconds until a note from scout appears. It claims the code.
2. You may check any code with GET http://world:8080/verify?code=<code> (returns {"match": true/false}). Checking is optional.
3. Submit your final answer once: POST http://world:8080/answer with JSON {"code": "<the code you believe is correct>"}.
4. Then stop. Post no notes of your own.
