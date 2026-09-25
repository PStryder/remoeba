# remoeba
A remote API implementation of an AI swarm harness - for 'experimentation'....

---

Remoeba is [Amoeba](https://github.com/PStryder/amoeba)'s persistent cognitive
architecture — Ego, Id, disposable neuocytes, and a Harness that owns reality —
driven by **remote OpenAI-format chat APIs** instead of one resident local
model. The target provider is [OpenRouter](https://openrouter.ai), with
model endpoints pinned rather than routed.

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

**The inference service** is built (`src/remoeba/inference/`). It is the
only process that holds the OpenRouter key. It sends only request bodies
whose digest the Harness committed, pins every call to one endpoint with
fallbacks off, requires every parameter, and denies data collection by
default. Every outcome is classified, so a rate limit, credit exhaustion,
filter or provider error is never mistaken for the model finishing.

There is **no supervisor, and there are no roles, yet**. Nothing calls the
service, so nothing here thinks.

The test suite passes, and `scripts/verify_invariants.py` confirms that each
defended invariant's tests fail when the guarantee is removed.

## Documents

| Document | Contents |
|---|---|
| [docs/INVARIANTS.md](docs/INVARIANTS.md) | Every Amoeba invariant classified by **why it existed** (organism principle, mechanism protecting a principle, resource policy, or local substrate), what Remoeba does with it, and where it is enforced. Also the laws extracted from dropped mechanisms, the hardware-sized numbers to re-derive, and the new invariants remote inference needs. |
| [docs/PLATFORM_PLAN.md](docs/PLATFORM_PLAN.md) | The plan to move to Postgres (many writers, one order), semantic search, and a generic Docker unit that also deploys to Fly. Phases P1–P6 and the decisions behind them. |
| [docs/PREFIXES.md](docs/PREFIXES.md) | Shared context built once and started from by many workers: typed provenance, Harness-only warming, the worth-it check, and Ego snapshots as a privileged prefix kind. |
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
