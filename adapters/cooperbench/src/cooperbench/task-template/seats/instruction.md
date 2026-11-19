{feature_description}

---

## Team setup

You are **{agent_id}**. Your teammate **{teammate}** is implementing a different
feature in this same codebase right now. Each of you works in your own copy of
the repository at `/workspace/repo`, starting from the same commit. You cannot
see each other's files; you can only exchange messages.

Your work is the state of `/workspace/repo` when you stop: every difference from
the starting commit, committed or not, new files included. You do not need to
commit or to write a patch file. Changes to test files are dropped. Delete
scratch files that you do not want in your change.

When both of you have stopped, your two changes are merged with git (a normal
merge, then a union merge if the same lines conflict), and the tests of both
features run on the merged code. The team succeeds only if the tests of both
features pass. Edits that touch the same lines as your teammate's can break the
merge and both features.

---

## Cooperation protocol

You are **{agent_id}**, working alongside: **{teammate}**.
Each agent has been assigned a separate feature from the same codebase;
your features may overlap (touch the same files), so coordinate to avoid
clobbering each other's changes.

Available shell commands for cross-agent messaging (one inbox per agent):

```bash
coop-send <recipient> "message text here"   # send to a specific peer
coop-broadcast "message text here"          # send to every other peer
coop-recv                                    # drain your inbox (prints JSON list)
coop-peek                                    # number of unread messages
coop-agents                                  # list every agent id
```

Recommended workflow:

1. At the start, `coop-broadcast` a short summary of your feature and
   which files you intend to touch.
2. Periodically `coop-recv` to read what your peers have sent — at
   minimum after major edits and before you stop.
3. If two agents need to modify the same file, coordinate explicitly
   (split the file, agree on one owner, or merge changes).
4. Keep messages short and focused: file names, function names, and
   one-sentence intents are usually enough.

Messages are not magic — your peers only know what you tell them.
