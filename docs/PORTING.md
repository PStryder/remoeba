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

1. **Does a role process hold its transcript?** **Decided 2026-09-25: no.**
   Each turn reads its message list from the durable record. I94 (handover of
   session handles) and most of I93/I122 disappear, and a role restart loses
   nothing because it held nothing. The cost is re-reading the list each turn,
   which is cheap next to a remote call. Consequence for the rewrite: a role
   process holds only its scope credential and the turn it has claimed. Any
   transcript state that lives in a role process is a defect.
2. **Where does model choice live?** **Decided 2026-09-25: split.** Amoeba's
   README says *model selection is cognitive policy; model execution is a
   resource*. So a governed profile names a model **class** (for example
   `ego.reasoning`), and configuration maps that class to an endpoint and
   model. Approving a profile can change which class a mind uses; repointing a
   class is an operator resource decision. Consequences for the rewrite:
   - The class is a profile setting, inherited and governed like the model
     variables. A profile naming a class that configuration does not map is
     refused at binding, not given a default model. An unknown name is refused
     rather than dropped (the model-variables rule).
   - The incarnation binding (I57) records the class **and** what it resolved
     to at birth (endpoint and model), plus what the first response reported
     (R3). Repointing a class changes what is born next, not what is alive
     (I53). A running mind keeps the model it was bound to.
   - The class-to-model mapping is configuration, so it is not in the prompt
     library and is not reachable from any role scope. No mind can repoint its
     own class.
3. **Native tool calling only, or a text fallback?** **Decided 2026-09-25:
   native only.** `tools` is a required capability (R7). A model class whose
   pinned endpoint does not support it is refused when configuration loads,
   not discovered mid-turn. There is no text-parsing fallback: it would bring
   back the whole I113/I115/I124 family. Tool-call-shaped text in `content` is
   still recognised and refused (I115), because a model can write one without
   using the tools channel.
4. **Egress controls (R2).** **Decided 2026-09-25: built in from the start.**
   - Every filespace root must state `egress = "allowed"` or `"denied"`. There
     is no default, and a root without one is refused when configuration loads,
     the same stance as I37 (no default destination).
   - An external client may mark an attachment `no_egress` when it is
     admitted. The default is allowed, because the client chose to send it to
     an organism that thinks remotely. The mark is part of the input's
     admitted record and cannot be changed later (I87).
   - The mark is a **provenance taint**, not a content check. Anything derived
     from a denied source carries the taint: a sandbox run that read it, a
     tool result computed from it, an artifact produced from it. A tainted
     result can be stored, proposed and promoted, but it can never be placed
     in model context. The model is told a result exists and is withheld, and
     why. It never sees the content.
   - The last point is the check. The Harness asserts it when it builds a
     request, which is also the point where the request body is committed
     (R2). A tainted byte that reaches a request body is an integrity failure,
     not a policy warning.
   - Stated plainly: denied data is useful only for work that does not need a
     model to read it, such as computation whose result goes to a file rather
     than to a mind. Anything a mind has to reason about leaves the machine.
5. **Which providers?** **Decided 2026-09-25: OpenRouter.** See the next
   section. It is OpenAI-format, so a direct OpenAI or local vLLM endpoint
   remains possible later behind the same seam.

## OpenRouter

These facts were checked against OpenRouter's documentation and its public
`/api/v1/models` endpoint on 2026-09-25. They shape the inference service
directly. Re-check them when the service is written, because they come from
a third party and can change.

**One model ID is many deployments.** `meta-llama/llama-3.3-70b-instruct` has
11 upstream endpoints (`/api/v1/models/<id>/endpoints`), differing in
quantization (`fp8`, `bf16`), context length (12,288 vs 131,072), maximum
output, and supported parameters. One of them does not support `tools`. By
default OpenRouter may route any call to any of them. So:

| OpenRouter default | Consequence if left alone | Remoeba setting |
|---|---|---|
| `provider.allow_fallbacks: true` | Consecutive calls from one mind may run on different quantizations with different context windows. The model a mind is bound to (I15, I57) would be a fiction. | A model class pins `provider.order` (or `only`) with `allow_fallbacks: false`. A pinned endpoint being unavailable is `provider_unavailable` (R5), not a silent switch. |
| Unsupported parameters are **ignored** | A profile binding `top_k` or `seed` runs without it while the binding claims it applied. That is exactly the failure I135 records. | `provider.require_parameters: true` on every call, plus the check at binding (R7) against the pinned endpoint's `supported_parameters`. |
| `provider.data_collection: "allow"` | Calls may be routed to providers that store prompts. | `data_collection: "deny"` by default. A model class may relax it only by stating so in configuration, and the binding records which. `zdr: true` is available where a class needs it. |

**What a response gives, and where it goes:**

| Field | Used for |
|---|---|
| `id` | Response id on the turn (I70); key for `/api/v1/generation?id=` |
| `model` | The reported model (R3) |
| `finish_reason` (normalized: `stop`, `length`, `tool_calls`, `content_filter`, `error`) | Stop reason (I66). `content_filter` is R9. `error` is a provider failure, never `model_stop`. |
| `native_finish_reason` | Recorded verbatim beside the normalized one |
| `usage.prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens` | Measured context and spend (I96–I98, I121). Counted by the model's native tokenizer, per OpenRouter. |
| `usage.cost` | Spend (R4). Documented as optional, so a response without it is **unpriced**, and R4's rule applies. |
| `system_fingerprint` | Recorded when present (R3) |

**Not yet confirmed:** whether a chat response names the upstream provider
that served it. The overview schema does not list such a field. The
generation-stats endpoint is the documented source. The service must record
the serving provider one way or the other, and must not infer it from the
pin, because a pin is a request, not evidence.

**Errors:** `402` means credits are exhausted. It is its own stop reason
(`credits_exhausted`), distinct from Remoeba's own spend ceilings. `429` is
`rate_limited` (R5). Error bodies carry `code`, `message` and optional
`metadata` with the upstream's raw error, which is recorded verbatim.

**Capabilities (R7)** come from the pinned endpoint's `supported_parameters`
and `max_completion_tokens`, not from the model-level list. The model-level
list is a union across endpoints: the model above lists `tools` although one
of its endpoints lacks it. Across the 458 models listed on 2026-09-25, 390
support `tools`, 359 `seed`, 217 `top_k`, and 12 `parallel_tool_calls`.

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
