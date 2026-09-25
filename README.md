# remoeba
A remote API implementation of an AI swarm harness - for 'experimentation'....

---

Remoeba is [Amoeba](https://github.com/PStryder/amoeba)'s persistent cognitive
architecture — Ego, Id, disposable neuocytes, and a Harness that owns reality —
driven by **remote OpenAI-format chat APIs** instead of one resident local
model.

Amoeba's premise carries over unchanged:

> **Persistence is the mind. Models are things the mind uses to think.**

Swapping a local model for a remote one should be a clean test of that
premise, but it is not a drop-in change. Amoeba's inference contract was
token-level and stateful: a KV session the Harness could fork, measure, and
refuse individual token ids in. A remote API is stateless and message-level.
In Remoeba a session is therefore a message list the Harness owns, and every
model call is a recorded request against it.

## Status

**Foundation only.** What is here was carried from Amoeba unchanged apart from
the rename, and is shown to still hold:

- durable state: the event chain, receipts, blobs, maintained memory, the work
  queue, the blackboard store;
- the prompt library's governance code;
- the Windows host boundary: filespace containment, AppContainer sandboxes,
  state-directory hardening;
- loopback RPC and the authority scope tables.

There is **no inference and no Harness process yet**. Nothing here can think.

The carried test suite passes, and `scripts/verify_invariants.py` confirms that
each carried invariant's tests fail when the guarantee is removed.

## Documents

| Document | Contents |
|---|---|
| [docs/INVARIANTS.md](docs/INVARIANTS.md) | Every Amoeba invariant, marked **Carry**, **Adapt** or **Drop** for remote inference, plus the new invariants remote inference needs (credential, egress, spend, rate limits, provider refusals). |
| [docs/PORTING.md](docs/PORTING.md) | What was copied, what to port next, what to rewrite, what to leave behind; the proposed inference seam; the decisions to make first. |
| [docs/amoeba/](docs/amoeba/) | Amoeba's documents, frozen. They describe the local-model ancestor. |

## Running the tests

Windows, Python 3.11.

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\verify_invariants.py
```

## License

MIT
