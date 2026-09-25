# Porting Amoeba to remote inference

## Where this code came from

Everything under `src/remoeba/`, the carried tests, `scripts/verify_invariants.py`
and `docs/amoeba/` were copied from **Amoeba at commit `371fd64`, plus that
repository's staged-but-uncommitted changes as of 2026-09-25** (the
portability work: relative state paths, the `test_repo_is_portable` guard).
They were copied from Amoeba's git index, not its working tree, because
Amoeba's mutation verifier was editing the working tree at the time.

The package was renamed `amoeba` → `remoeba`, environment variables
`AMOEBA_*` → `REMOEBA_*`, and the sandbox's AppContainer name `Amoeba.<id>` →
`Remoeba.<id>`. Nothing else was changed in the copied modules.

## Approach: start over, on the foundation that was already proven

Moving to remote APIs changes the inference contract that almost every
cognitive module is written against, so the Harness is **not** copied
wholesale and patched. Instead:

1. **Carried now** — the layer that has no idea a model exists. Copied, renamed,
   and shown to still pass its tests and its mutation checks here.
2. **Port next** — Harness modules whose logic is model-agnostic but which
   import the supervisor and so cannot come across alone. Bring each one over
   with its tests, once the pieces it depends on are here.
3. **Rewrite** — modules whose whole purpose was the local token/KV contract.
   Use the Amoeba source as a specification of the *behaviour* the invariants
   require, not as code to adapt line by line.
4. **Leave behind** — llama.cpp-specific code with no remote equivalent.

## 1. Carried now

| Module | Notes |
|---|---|
| `errors`, `ids`, `identifiers`, `logging_setup`, `argcheck`, `actions`, `conditions`, `room` | Pure utilities. |
| `store/` (`blobs`, `db`, `events`, `writer`, `memory_repo`, `board_repo`, `work_repo`) | Durable state. **Local residue:** `db.py` still has the `snapshots` / `snapshot_refs` tables, `agents.session_handle`, and KV columns; `work_repo.py` still publishes and refcounts KV snapshots. Replace these when snapshots become message lists (I12, I16). |
| `mind` | The state facade. |
| `results` | Bounded projections, issued references, evidence roots (I86, I123, I138). |
| `retention` | Evidence is never pruned (I87, I88). Its tests need the mailbox. |
| `promptlib/` + shipped prompts | Governance code is here; its tests need the role layer. The shipped prompts still describe a local organism, and they change only through governance (I50). |
| `filespace`, `sandbox`, `security` | The Windows host boundary. Unaffected by where inference runs. |
| `scopes` | The authority tables. **Local residue:** snapshot verbs, which change with I12. |
| `rpc` | Loopback JSON-lines RPC between processes. |
| `config` | **Local residue:** `BackendConfig` (llama.cpp paths, `n_ctx`, KV types), `runtime_dir`, `models_dir`, `BatchingConfig`, `kv_admission_reserve_fraction`, and occupancy-based `HomeostasisSettings`. Replace these with the inference and spend configuration below. `config.example.toml` covers only what is real today. |

Carried tests: `test_durable_foundation`, `test_filespace`,
`test_filesystem_hardening`, `test_sandbox`, `test_repo_is_portable`.
`tests/conftest.py` is a slimmed version: Amoeba's also started live process
stacks against a deterministic backend, and neither exists here yet.

## 2. Port next (model-agnostic Harness)

Bring each over **with its Amoeba tests**, and add the matching entries from
Amoeba's `scripts/verify_invariants.py` back into this one.

| Module | Enforces | Amoeba tests that come with it |
|---|---|---|
| `mailbox`, `turn_api`, `waking` | I64–I83, I85, I92, I107, I108, I129, I140, I141 | `test_persistent_turns`, `test_work_lifecycle`, `test_interaction_answers`, `test_answer_dependencies`, `test_result_delivery` |
| `io_api`, `http_api`, `mcp_api`, `operator_api`, `dashboard` | I48–I48f, I89, I103–I106, I130, I133 | `test_external_interfaces`, `test_external_delivery`, `test_operator_console`, `test_converse_panel` |
| `ego_api`, `id_api`, `pulse`, `heartbeat` | I44–I47e, I100–I102, I125–I128 | `test_ego_senses_and_effectors`, `test_id_senses_and_effectors`, `test_heartbeat_digest`, `test_condition_wakes`, `test_role_health`, `test_conclusions_and_audits` |
| `harness_api`, `tools` | I23–I23e, I34, I38–I43 | `test_tools`, `test_store_boundaries`, `test_receipt_ground_truth`, `test_filespace_harness` |
| `prompt_api`, `role_env`, `resources`, `vocabularies` | I49–I63, I117 | `test_prompt_library`, `test_role_environment`, `test_affordance_vocabulary` |
| `supervisor`, `supervisor_api`, `reset`, `__main__` | I9, I25–I28, I73, I131 | `test_supervisor_lock`, `test_reset`, `test_harness_features`. Strip snapshot publication, KV-pool admission and inference-process spawning as they come across. |

## 3. Rewrite (the inference seam and everything built on tokens)

| Amoeba module | Why it cannot be ported | Remoeba replacement |
|---|---|---|
| `inference_service`, `backends/base` | A stateful token-level contract: `tokenize`, `ingest`, `fork_prefix`, `restore_prefix`, `session_tokens`, `top_logits`, `vram_free`. | A remote inference service, the **only** process holding the API credential (R1), with a message-level contract (below). |
| `backends/deterministic` | Simulates the token contract. | A deterministic fake that speaks the new message-level contract and labels every result simulated. Most of the ported Harness tests run against it. |
| `roles` | Owns a KV session, ingests tokens, parses `<tool_call>` out of text, resumes at exact token positions (I109). | A turn reads its message list from the record, sends it with a `tools` array, and appends the response. The role holds no transcript (see decisions). |
| `neuocyte` | Forks or recomputes a KV prefix. | Starts from a message-list snapshot plus its own private tail. |
| `homeostasis`, `reconstitution` | Token-span accounting and template-token message splitting. | Message-range reclamation: the same policy (I93, I122) without token coordinates. |
| `arbiter` | Admission against a measured KV pool. | Admission against spend ceilings and rate limits (I98, R4, R5). |

### The new inference seam (proposal)

```
complete(request) -> response
  request:  messages[], tools[], model_binding, sampling{}, max_output_tokens,
            caller (role/work/turn), deadline
  response: message (content | tool_calls), finish_reason,
            usage{prompt, completion, cached}, reported model,
            system_fingerprint, response_id, latency, cost, retry count
cancel(call_id)
capabilities(endpoint) -> declared and probed features (R7)
health() -> reachability, rate-limit headroom, spend against ceilings
```

The Harness commits the request body as a blob **before** sending it (R2) and
the response as a blob after it arrives. The turn record points to both.

## 4. Leave behind

`backends/llama_ffi`, `backends/llama_engine`, `backends/structure`,
`framing`, `bench/*`, and in `docs/amoeba/`, `RUNTIME.md` and
`BENCHMARKS.md`. These describe the local-model ancestor and are kept for
reference only.

## Decisions to make before the rewrite

1. **Does a role process hold its transcript?** Recommended: no. If each turn
   reads its message list from the durable record, I94 (handover of session
   handles) and most of I93/I122 disappear, and a role restart loses nothing
   because it held nothing. The cost is re-reading the list each turn, which is
   cheap next to a remote call.
2. **Where does model choice live?** Amoeba's README says *model selection is
   cognitive policy; model execution is a resource*. That splits cleanly: a
   governed profile names a model **class** (for example `ego.reasoning`), and
   configuration maps that class to an endpoint and model. So approving a
   profile can change which class a mind uses, and repointing a class is an
   operator resource decision recorded in the binding (I57, R3).
3. **Native tool calling only, or a text fallback?** Recommended: native only,
   declared as a required capability (R7). A text-parsing fallback brings back
   the whole I113/I115/I124 family.
4. **Egress controls (R2)** — whether no-egress roots or attachments exist
   from the start.
5. **Which providers?** "OpenAI format" covers OpenAI, OpenRouter, and local
   servers such as vLLM or llama-server. They differ on `seed`, `top_k`, usage
   reporting, cache reporting and `system_fingerprint`. That is what R7's
   per-endpoint capability declaration is for.

## Verification

`scripts/verify_invariants.py` carries only the mutations whose code **and**
named tests exist here: 21 invariants. Add Amoeba's entries back as their
modules are ported.

Two notes for its future:

- It mutates source **in place** and restores it afterwards. That is why
  Amoeba's working tree was unsafe to copy from while it ran. Running each
  mutant in a temporary copy of the tree would remove the hazard.
- Amoeba's `tests/test_invariants_are_defended.py`, which checks that every
  documented invariant names a real test and every anchor still matches, was
  **not** carried. It parses `ARCHITECTURE.md`. Bring it back once Remoeba has
  its own invariant document in that format, rather than pointing it at the
  ancestor's.
