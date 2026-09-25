# Remoeba — the organism's law, separated from its machine's physics

Amoeba grew up on one consumer GPU: a 4B model, a 49k-cell KV pool shared by
every mind, token ids the Harness could inspect before they landed. Many of
its invariants are the organism's constitution. Many others are the physics
of that card, written down so carefully that they look like constitution too.

Remoeba runs the same organism against remote OpenAI-format APIs through
OpenRouter. The first question this document asked was *can this invariant
survive the port?* The better question, and the one it is now organised
around, is:

> **Why did this invariant exist in the first place?**

Getting that right before the supervisor is ported matters. The supervisor,
arbiter, homeostasis and role defaults are exactly where RTX 4080 physics
would otherwise fossilise into Remoeba as accidental architecture, turning
it into "Amoeba, except llama.cpp calls were replaced with HTTPS".

The full text of every Amoeba invariant, including the live failure that paid
for it, is preserved verbatim in [amoeba/ARCHITECTURE.md](amoeba/ARCHITECTURE.md).

---

## The substitution everything follows from

A remote chat API is **stateless and message-level**. Every call sends the
whole context as a JSON `messages` array and gets one assistant message back.
There is no session on the far side, no KV the Harness can fork or measure,
no token id it can refuse before it lands, and no tokenizer it can be sure
matches the provider's.

So in Remoeba **a session is a message list the Harness owns**, recorded
durably and content-addressed, and a model call is a function of that list
plus sampling settings. Role processes hold no transcript (PORTING.md,
decision 1).

## The taxonomy

### Why an invariant existed

| Reason | Meaning | What happens to it |
|---|---|---|
| **Principle** | Defines the organism. It would be true of any Amoeba on any substrate. | Carried. Its enforcement point may move. |
| **Mechanism** | A substrate-specific implementation **protecting a principle**. | The implementation is dropped and the law it protected is carried, under a new mechanism. The law is named explicitly so it is not lost with its implementation. |
| **Policy** | A resource or governance policy. It still exists, but the *reason* changes: from VRAM and KV cells to money, rate limits, latency, prompt-cache behaviour and provider availability. | The concept is carried and the mechanism and numbers are re-derived. |
| **Substrate** | Exists only because of local hardware or llama.cpp. | Dropped. |

### What Remoeba does with it

| Verdict | Meaning |
|---|---|
| **Carry** | Holds unchanged. |
| **Re-mechanize** | The same law, enforced by a different mechanism. |
| **Redesign** | The concept stays; the objective, mechanism or numbers are new. |
| **Drop** | Nothing to enforce any more. |

### Where it is enforced

Amoeba enforced most resource rules inside the inference engine and its KV
arbiter. Remoeba has no engine to enforce anything in, so enforcement moves:

| | Enforcement point | Owns |
|---|---|---|
| **E1** | Durable record: `StateWriter`, the only writer | Atomicity, history, receipts, idempotency, fencing, integrity |
| **E2** | Request construction: the supervisor building a message list | What enters a model call: structure (L-STRUCTURE), lineage, egress taint (R2), cache-stable ordering |
| **E3** | Send: the inference service | The class's pin, the committed digest, endpoint capabilities, retries, the credential |
| **E4** | Admission: the arbiter | Spend, rate limits, per-session allowances, fair share |
| **E5** | The response record: supervisor commits the service's report | What it cost, what model answered, who served it, how it ended |
| **E6** | Dispatch: scope tables and the tool loop | Authority. What a role, neuocyte or client can reach at all. |
| **E7** | Turn machinery: mailbox and turns | When cognition happens, what it is given, what it owes |
| **E8** | Homeostasis | Context quality against cost, cache reuse, latency and rate pressure |
| **E9** | Host boundary: sandbox, filespace, hardening | What code and files can reach |

### Status

| Status | Meaning |
|---|---|
| `verified` | Code and tests are here, **and** `scripts/verify_invariants.py` shows the tests fail with the guarantee removed. |
| `tested` | Code and tests are here; the mutant was not carried. |
| `pending` | The enforcing code has not been ported or built yet. |
| — | Dropped: nothing to enforce. |

---

## Laws extracted from mechanisms

These are the cases where a local implementation is dropped but the law it
protected is not. Each gets a name here so it survives its implementation.

**L-STRUCTURE — Untrusted cognition must not be able to manufacture Harness
structure.** From I124 and I132, whose mechanism was refusing chat-template
control tokens by id. The literal threat, llama.cpp token ids, is gone. The
law holds in these forms:

- model output may not manufacture a `system`, `user` or `tool` message.
  Whatever a model generates is the content of exactly one assistant message
  or entries in its `tool_calls`;
- model content may not invent an authoritative tool result. Only the Harness
  appends a `tool` message, and only for a call it executed;
- model-authored data may not become API control fields. Nothing a model,
  client, file or tool wrote is ever placed anywhere in a request body except
  inside a message's `content` or a tool call's `arguments`;
- tool and provider responses stay typed and separate from instructions. A
  tool result is a `tool` message, never text spliced into a system prompt.

Enforced at E2 (construction) and E3 (`wire.check_body`, `check_messages`).
What the Harness **cannot** verify is how a provider tokenizes marker strings
inside content, so that is stated, never claimed (I24).

**L-CACHE — Correctness never depends on a cache.** From I18 ("KV is
replaceable acceleration, not memory"). A provider's prompt cache is an
optimization: a hit is a billing fact read from `usage`, and losing every
cached prefix may cost money and latency but must change no outcome. Remoeba
assumes replay is expensive and treats caching as a discount it may or may
not get.

**L-THROUGHPUT — Throughput machinery changes speed, never outcomes.** From
I90 (batching). Concurrency is now parallel HTTP requests: one caller's 429,
timeout or error is that caller's alone (R5), and running N requests at once
returns what running them one by one would.

**L-CONTINUATION — A continuation never invents where it joins.** From I109,
whose mechanism was resuming at the exact token. A remote API cannot resume
mid-message, so every continuation asks visibly under I107's "appended
directly" instruction, and the pieces are joined exactly as generated.

**L-REPLACEMENT — Whoever holds a reference to something replaceable learns
when it is replaced.** From I94 (session handles). Decision 1 removed the
thing it acted on, since roles hold no transcript. The law stays named
because model bindings, snapshots and class mappings are all replaceable
references too.

**L-PARSER — A mind is taught exactly what its parser accepts.** From I113
(the `<tool_call>` text syntax). With native tool calling, the `tools` array
sent **is** the role's model-facing scope, derived from the dispatch table
and never hand-maintained.

---

## The triage

`E` = enforcement point from the table above.

### State and provenance

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I1 | One writer | Principle | Carry | E1 | `verified` |
| I2 | Raw history is append-only | Principle | Carry | E1 | `verified` |
| I3 | History is not memory | Principle | Carry | E1 | `tested` |
| I4 | Content before reference | Principle | Carry. Request and response bodies join the schema-derived inventory. | E1 | `tested` |
| I5 | Hash chaining detects mutation, not administrators | Principle | Carry | E1 | `verified` |
| I6 | Acknowledged means durable | Principle | Carry | E1 | `verified` |

### Work and neuocytes

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I7 | At-least-once, idempotent commits | Principle | Carry | E1 | `verified` |
| I8 | Fencing | Principle | Carry | E1 | `tested` |
| I9 | Neuocyte death is always safe | Principle | Carry. A killed neuocyte may leave a billed request in flight: safe for state, not free (R4). | E1, E4 | `pending` |
| I10 | Retirement never destroys authoritative state | Principle | Carry. "Releases inference and snapshot resources" becomes "abandons its in-flight request". | E1 | `pending` |
| I11 | Stale findings are flagged | Principle | Carry | E1 | `verified` |
| — | Neuocytes are separate OS processes | **Principle** (in Remoeba) | **Carry for the first port.** They no longer own a KV session, but process isolation is still a crash boundary, an OS-resource boundary, a credential boundary and a sandbox boundary. Pooled workers are a later experiment, not an assumption (PORTING.md, decision 6). | E6, E9 | `pending` |
| — | Retirement is a throughput mechanism | Substrate | **Drop** as a throughput tool: idle KV no longer taxes every decode. Lifecycle semantics (retire when done, on budget, on deadline) carry. | — | — |

### Ego snapshots

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I12 | Only Ego publishes; a snapshot is Ego's actual context | Principle | Re-mechanize: an immutable, content-addressed **prefix of Ego's message list**. Still not a merge, not a summary, and Id's list is never published. It becomes the privileged `ego_context` kind of [prefix](PREFIXES.md), which only Ego's publish path can create (PX8). | E1, E2, E6 | `pending` |
| I13 | Ego continues after publishing | Principle | Carry (trivially: Ego keeps appending) | E2 | `pending` |
| I14 | Neuocyte tails are private | Principle | Carry (true by construction) | E2 | `pending` |
| I15 | Pinned for life | Principle | Re-mechanize: pinned to its snapshot **and its model binding** (class, model, endpoint) | E1, E3 | `pending` |
| I16 | Referenced storage is never recycled | Mechanism | Drop the refcounting. The law is kept by blobs never being reclaimed (I87). | E1 | — |
| I17 | Incompatible cached tensors are never reinterpreted | Substrate | Drop. A text snapshot *may* be replayed on a different model; that is allowed and recorded (R3). | E5 | — |
| I18 | KV is acceleration, not memory | Mechanism | Becomes **L-CACHE** | E2, E8 | `pending` |
| — | Forking is cheap (0.09 ms vs 7.9 ms recompute) | Substrate | Replaced by a **policy**: a forked worker resends Ego's prefix on *every* call. It is cheap only if the prefix is byte-identical, comes first, and goes to the same endpoint. Snapshot prefixes must be built for **cache affinity**. | E2, E8 | `pending` |

### Roles and authority

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I19 | Neither half is the harness | Principle | Carry | E6 | `pending` |
| I20 | Id audits the record, not Ego | Principle | Carry | E6 | `pending` |
| I21 | Disagreement, not overwrite | Principle | Carry | E1 | `pending` |
| I22 | The side channel changes nothing | Principle | Carry | E6 | `pending` |
| I23 | A model can request a tool call; it cannot perform one | Principle | Re-mechanize: requests arrive as native `tool_calls` (decision 3) | E6 | `pending` |
| I23b–d | No self-granted sandbox; no naming a sandbox; fenced workers cannot act | Principle | Carry | E6 | `pending` |
| I23e | The tool loop is bounded, and the binding reason reported | Principle | Carry. **Spend** joins turns, tokens and deadline as a fourth bound. The numbers are policy. | E4, E6 | `pending` |

### Reporting and supervision

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I24 | No overclaiming | Principle | Carry. The capability vocabulary is replaced (R7), and nothing about a provider's hardware, batching or caching is claimed. | E3, E5 | `pending` |
| I25 | Health stays answerable | Principle | Carry. "Provider down" is ordinary, not a crash. | E3 | `verified` at E3 (`test_health_answers_without_the_provider`) |
| I26 | A client is not the mind | Principle | Carry | E6 | `pending` |
| I27 | Cancelling stops future work, never undoes the past | Principle | Re-mechanize: closing the connection. Tokens generated before it closed may still be billed, and that is recorded. | E3, E5 | `pending` |
| I28 | Supervision keeps making passes | Principle | Carry | E1 | `pending` |

### Host boundary

Windows host properties, unaffected by where inference runs. The sandbox's
AppContainer profile is named `Remoeba.<id>`, so it cannot collide with an
Amoeba's.

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I29–I33 | State directories hardened, fail-safe, audited | Principle | Carry | E9 | `verified` |
| I34 | The Harness promotes the bytes it reviewed | Principle | Carry | E9 | `pending` |
| I35, I36 | Sandboxes share no handles, and run concurrently | Principle | Carry | E9 | `verified` |
| I37, I37b–d | Filespace containment, links, read-only roots, hard links | Principle | Carry | E9 | `verified` |
| I37e | One file has one identity | Principle | Carry | E9 | `tested` |
| I38 | No write destroys | Principle | Carry | E9 | `pending` |
| I39 | A neuocyte has no verb that reaches the host filesystem | Principle | Carry | E6 | `pending` |
| I40 | Nothing is read that was not named | Principle | Carry. It gains a second meaning, since what is read may be sent to a third party (R2). | E9, E2 | `pending` |
| I41 | A receipt's digest is ground truth, verifiable from inside | Principle | Carry | E9 | `pending` |
| I42, I42b, I43 | The four stores; a sandbox destroys nothing that matters | Principle | Carry | E9 | `pending` |

### Senses, effectors and external interfaces

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I44 | Id has one cheap, bounded sense of the organism | Principle | Carry. The pulse's *fields* are policy: KV occupancy is replaced by provider health, rate-limit headroom, spend against ceilings, and per-role context from `usage`. | E7 | `pending` |
| I44b–d | Observations not verdicts; tracks reality; configured vs embodied | Principle | Carry. Configured vs embodied extends to model bindings. | E7 | `pending` |
| I45–I46b | Id requests, the Harness decides; scopes are disjoint | Principle | Carry | E6 | `pending` |
| I47–I47e | Ego states intent; the Harness executes | Principle | Carry | E6 | `pending` |
| I48–I48f | External input is not external control | Principle | Carry | E6 | `pending` |
| — | One Amoeba is one cognitive trust domain | Principle | Carry. Also, one Remoeba sends **all** its cognition to its configured providers, so workloads that must not share a provider need separate instances. | — | — |
| — | Neuocyte identity is asserted, not authenticated | Principle (a known gap) | Carry, still unfixed | E6 | — |

### Prompt library and model variables

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I49–I56 | Prompt-library governance | Principle | Carry | E6 | `pending` (code here; tests need the role layer) |
| I57 | A binding freezes bytes, not a pointer | Principle | Carry. The binding also freezes the model class, what it resolved to, and what first answered (R3). | E1 | `pending` |
| — | An unknown model variable is refused, never dropped | Principle | Carry | E3 | `verified` (MODELVARS-REMOTE, MODELVARS-STOPS) |
| — | The set of model variables | Substrate | Redesign. The set is **per endpoint** (R7), since `top_k`, for one, is supported by fewer than half of OpenRouter's models. | E3 | `verified` (R7-PARAMS) |

### Persistent roles and bounded turns

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I64 | One bounded turn at a time, per role | Principle | Carry. A persistent identity thinks one thought at a time, and two concurrent turns would fork its message list. | E7 | `pending` |
| I65, I69–I75 | Input waits for the boundary; inputs survive death; turns cannot act once stale | Principle | Carry | E7 | `pending` |
| I66 | Turn-end reasons are first class | Principle | Carry. The vocabulary extends: `content_filter`, `refused`, `rate_limited`, `provider_unavailable`, `credits_exhausted`, `spend_exhausted`. | E5, E7 | `verified` at E3 (I66-402, I66-ERRFINISH) |
| I67, I68, I68b | The Harness continues; continuation is bounded; recovery and progress spend separate allowances | Principle | Carry. It should become **rare** once ceilings are re-derived: Amoeba hit it constantly because its ceilings were sized for the card. | E7 | `pending` |
| I76–I83 | Requests read whole; an answer belongs to its request; complete means answered | Principle | Carry | E7 | `pending` |
| I84 | An existing database gains new columns | Principle | Carry | E1 | `verified` |
| I85, I85b | Delegated work is accountable; lineage is Harness-bound | Principle | Carry | E6, E7 | `pending` |
| I86 | A result the model cannot see whole says so | Principle | Carry | E7 | `pending` |
| I87, I88 | Evidence is never pruned; nothing in use is forgotten | Principle | Carry | E1 | `pending` |
| I89 | Ego chooses what crosses the boundary, never whose | Principle | Carry the output side: a result reaches only the client that asked. **Changed (2026-09-25):** an external upload may inform other interactions once shared (PX11). | E6 | `pending` |
| I90 | Batching changes throughput, never outcomes | Mechanism | Becomes **L-THROUGHPUT** | E3, E4 | `verified` at E3 (R5) |
| I91, I92 | Specialisations; wake on owned work | Principle | Carry | E7 | `pending` |
| I93 | Context is reclaimed by dropping finished work | Policy | **Redesign.** Settled work goes first and owed work never, as before. But the objective is no longer one scarce resource. A rebuild reclaims tokens *and destroys provider-side prefix reuse*, so homeostasis now balances request cost, cache reuse, context quality, latency and rate pressure. | E8 | `pending` |
| I94 | Everyone holding a session handle learns when it changes | Mechanism | Becomes **L-REPLACEMENT**. Nothing to enforce while roles hold no transcript. | — | — |
| I95 | What became of an attempt travels with what it said | Principle | Carry | E7 | `pending` |
| I96 | A session carries its own allowance | Principle | Carry. Nothing is unbudgeted. The allowance is bounded by the class's context window, and its size is policy. | E4 | `pending` |
| I97 | What a session may think and what it costs are different | Principle | Carry, and now literal: tokens versus money. | E4 | `pending` |
| I98 | Admission spends a pool it has measured | Principle + Substrate | Carry the principle ("does not guess when it cannot measure"). Replace the pool: **spend ceilings and rate limits**, not KV cells (R4, R5). | E4 | `pending` |
| I99 | Only the discretionary turn yields to pressure | Principle | Carry. **Pressure** is redefined: rate-limit, spend and provider pressure, not KV occupancy. | E4, E7 | `pending` |
| I100–I102 | Claims can be withdrawn; disputes end on the record | Principle | Carry | E1 | `pending` |
| I103–I106 | Console transport; room attribution; view vs record | Principle | Carry | E6 | `pending` |
| I107, I108 | An answer is whole; only a concluded thought is finished | Principle | Carry | E7 | `pending` |
| I109 | A continuation resumes the exact message | Mechanism | Becomes **L-CONTINUATION** | E7 | — |
| I110 | Each mind has its own ceiling; nothing silently lowers it | Principle + Policy | Carry the principle (refuse, never clamp). **Re-derive the numbers.** | E3, E4 | `verified` at E3 (I110-REMOTE) |
| I111 | A generation is admitted only if its whole allowance fits | Principle | Carry. The prompt size is an **estimate** until `usage` reports it, and says so. `context_length_exceeded` is pressure (I135). | E4 | `verified` at E3 (I135-PRESSURE) |
| I112, I116 | A conclusion is chosen; an audit lands | Principle | Carry | E7 | `pending` |
| I113 | A role is taught the syntax its parser accepts | Mechanism | Becomes **L-PARSER** | E6 | `pending` |
| I114 | A role is not restarted for thinking | Principle | Carry. Remote calls are slower and more variable, so this matters more, and the liveness grace is re-derived. | E7 | `pending` |
| I115 | A malformed call is never delivered as an answer | Principle | Carry. Malformed means bad JSON arguments, an unknown tool, or tool-call-shaped text in `content`. | E6 | `pending` (arguments parsed and problems reported at E3) |
| I117–I121 | Declared vocabularies; argument checks; RPC hygiene; measured views | Principle | Carry. "Measured" context comes from `usage`. | E6 | `pending` |
| I118 | A session reads an unchanged declaration once | Policy | Carry. The reason changes from context capacity to cost, and it now **helps caching**: a stable declaration early in the list is a reusable prefix. | E2, E8 | `pending` |
| I122 | A rebuilt context is one the conversation could have reached | Principle | Carry. Whole messages make most of it structural. | E8 | `pending` |
| I123 | A result too large for its budget is never chopped | Principle | Carry. "Counted by the model's tokenizer" may be impossible, so use a conservative estimate that says it is one. | E7 | `pending` |
| I124, I132 | Neither generation nor content introduces structural tokens | Mechanism | Becomes **L-STRUCTURE** | E2, E3 | `verified` at E3 in part (R-SENDCHECK; `test_content_is_carried_as_characters_never_parsed`) |
| I125 | A review is only as true as its watermark | Principle + Policy | Carry. A cheap quiet review now saves money, not KV. | E7 | `pending` |
| I126, I126b | Answering is not thinking; the not-thinking alarm | Principle | Carry. Must tell "the provider is down" (organism-wide) from "this role is broken". | E7 | `pending` |
| I127–I131 | Reachable counts; not blind when strained; durable delivery; whole requests; non-destructive reset | Principle | Carry. For I128, *strained* is redefined. | E7 | `pending` |
| I133 | An admitted input is referred to, never moved | Principle | Carry "referred to, never moved". **Changed (2026-09-25):** an *external upload* is no longer refused to other interactions. It becomes eligible for organism-wide sharing when reuse pays ([PREFIXES.md](PREFIXES.md), PX11), and every use is attributed to its origin. A client may admit an upload `interaction_only` to opt out, and that mark taints everything derived from it (PX12). | E7 | `pending` |
| I135 | A binding describes the sampling actually applied | Principle | Carry. Stronger now: the committed request body **is** the call. | E2, E3 | `verified` at E3 (R-REQPARAMS, R7-PARAMS) |
| I136–I142 | Worker faithfulness; lineage; corroboration; effects; answer dependencies | Principle | Carry. I138: forking a message-list snapshot still passes the forker's evidence roots on as `inherited`. | E7 | `pending` |

### Profile, environment, turn input

| | Invariant | Why | Remoeba | E | Status |
|---|---|---|---|---|---|
| I58 | A role is never offered a capability it cannot invoke | Principle | Carry | E6 | `pending` |
| I59 | A role can execute what its environment offers | Principle | Re-mechanize: through native tool calls | E6 | `pending` |
| I60–I63 | Authority-shaped arguments; one environment per turn; doctrine vs environment; no config doctrine | Principle | Carry | E6 | `pending` |

### Unnumbered rules, recovery and scheduling

| Rule | Why | Remoeba |
|---|---|---|
| Cognitive results never lose their simulated labelling | Principle | Carry. `FakeTransport` labels every result. |
| Rejuvenation stays Harness-initiated | Principle | Carry. It is now a rebuild of the message list, with a cache cost (I93). |
| Ego wakes because something relevant happened | Principle | Carry |
| Recovery steps 4–5 (null backend handles, zero snapshot refcounts) | Substrate | Drop. The rest of recovery carries. |
| Weighted fair share with reserved slots; bounded maintenance recursion | Principle + Policy | Carry "neither class starves" and "maintenance terminates". Re-derive the slot counts, which were sized against the KV pool. |

---

## Numbers to re-derive, not carry

These defaults came across in `config.py` and the prompt library, and every
one was sized for the local card. None may be carried by inertia. Each gets
re-derived against the model class it governs, against cost, and against rate
limits. The *principle* each serves (a ceiling exists, it is stated, it is
refused rather than clamped) carries.

| Setting | Amoeba value | Sized against | Governs in Remoeba |
|---|---|---|---|
| Output ceilings `ego` / `id` / `ego.neuocyte` / `id.neuocyte` | 3072 / 1024 / 512 / 384 | 49k shared KV cells | The class's `max_completion_tokens` and cost per turn. Set in shipped prompt headers, so on a fresh organism they establish the roots (I50). |
| `arbiter.max_prompt_tokens`, `ego.max_context_tokens`, `id.max_context_tokens` | 6144 | The same pool | The class's context window, and cost per call, which grows with every token replayed |
| `arbiter.max_completion_tokens` (platform cap) | 3072 | The same pool | The largest class's output maximum |
| `ego_neuocyte_budget_tokens` / `id_neuocyte_budget_tokens`, `budget_basis` | 6144 / 4096, `private_growth` / `total` | Forked KV was free; recomputed KV was not | Per-worker spend and context. `private_growth` assumed an inherited prefix costs nothing, which is **no longer true**: every call pays for it, discounted only by caching. |
| `arbiter.max_neuocytes`, reserved slots | 3–4, 1 + 1 | KV pool and the ~32-sequence knee | Rate limits (RPM/TPM) and spend |
| `kv_admission_reserve_fraction` | 0.15 | KV headroom | Nothing. Replaced by spend and rate headroom. |
| `neuocyte_wall_seconds`, `scheduler.turn_wall_seconds`, liveness grace | 180 s | ~160 tok/s local decode | Remote latency and retries |
| `role.tool_result_budget_tokens` | 512 | Context capacity | Cost and context quality |
| `scheduler.heartbeat_quiet_ceiling_tokens` | 128 | Context capacity | Cost of a quiet review |
| `homeostasis` thresholds (`elevated` / `high` / `critical`) | 0.55 / 0.70 / 0.85 of KV occupancy | KV occupancy | A new objective: cost, cache reuse, quality, latency, rate pressure (I93) |
| `scheduler.max_continuations` | 3 | Small ceilings making continuation common | Probably unchanged. It is a safety bound, and should be reached far less often. |

## New invariants for remote inference

These have no Amoeba ancestor, or replace one that was dropped. Each should
earn its number the way Amoeba's did: with a test that fails when it is
removed.

### Status: what the inference service already defends

The inference service (`src/remoeba/inference/`) holds the parts of these
that live at the wire. Each row is mutation-verified by
`scripts/verify_invariants.py` under the id shown.

| Id | Claim | Tests |
|---|---|---|
| R-PIN | A call runs on the pinned endpoint or not at all | `test_the_body_pins_one_endpoint_with_fallbacks_off` |
| R-REQPARAMS | Every call requires the endpoint to support its parameters | `test_every_call_requires_its_parameters` |
| R-SENDCHECK | A body that is not the class's pin is refused at send time, whoever built it | `test_a_body_with_fallbacks_on_is_refused_at_send`, `test_a_body_that_does_not_require_its_parameters_is_refused_at_send` |
| R1-ENV | No child process inherits the credential, under any name | `test_the_credential_is_not_in_a_childs_environment` |
| R1-RETURN | The credential never appears in anything the service returns | `test_the_credential_never_appears_in_what_the_service_returns` |
| R2-DIGEST | Only the body whose digest was committed is sent | `test_a_body_that_is_not_the_committed_one_is_never_sent` |
| R2-ROUTE / R2-DEFAULT | Data collection is denied unless a class says otherwise | `test_data_collection_is_denied_unless_the_class_says_otherwise`, `test_a_model_class_loads_with_a_model_and_a_pinned_endpoint` |
| R3-UNCONFIRMED / R3-MATCH | The serving provider is unconfirmed until the provider's record says, and a mismatch with the pin is reported | `test_the_serving_provider_is_unconfirmed_until_the_record_says`, `test_a_different_serving_provider_does_not_match_the_pin` |
| R4-UNPRICED | A missing cost is unpriced, never zero | `test_a_missing_cost_is_unpriced_not_free` |
| R5-RETRYABLE / R5-RETRYAFTER / R5-BOUND | Only rate limits and outages are retried, within bounds, honouring Retry-After | `test_non_retryable_failures_are_not_retried`, `test_retry_after_is_honoured`, `test_a_retry_after_beyond_the_bound_is_not_waited_for`, `test_retries_stop_at_the_limit` |
| R7-PINNED / R7-PARAMS / R7-TOOLS | Capabilities come from the pinned endpoint; what it lacks is refused before sending; a class without native tools refuses to start | `test_capabilities_come_from_the_pinned_endpoint_not_the_model`, `test_a_class_whose_endpoint_is_not_offered_refuses_to_start`, `test_a_class_whose_endpoint_lacks_tools_refuses_to_start` |
| R9 | A provider refusal is its own outcome | `test_a_refusal_is_its_own_outcome` |
| I66-402 / I66-ERRFINISH | Credit exhaustion and a provider-error finish are never collapsed | `test_credit_exhaustion_is_its_own_outcome`, `test_a_provider_error_finish_is_not_a_model_stop` |
| I110-REMOTE | A ceiling above the endpoint's maximum is refused, not clamped | `test_a_ceiling_above_the_endpoint_maximum_is_refused_not_clamped` |
| I135-PRESSURE | Context pressure decided by wording says so | `test_context_overflow_is_pressure_and_says_how_it_was_decided` |
| MODELVARS-REMOTE | An unknown setting is refused, never dropped | `test_an_unknown_setting_is_refused_not_dropped` |
| DECISION2 | A model class without a pinned endpoint is refused | `test_a_model_class_without_a_model_or_endpoint_is_refused` |

**What the service cannot defend on its own**, and so remains pending until
the supervisor is ported:

- **R2:** committing the request body *before* sending, and recording the
  response. The service refuses any body whose digest differs from the one it
  is handed. The act of committing is the supervisor's (I1).
- **R4:** spend ceilings, which need accumulated state.
- **R5:** "a response is accepted at most once", which is a property of
  the record.
- **R1:** actually spawning children with `child_environment`, which is the
  supervisor's job.
- **R6:** that only the Harness appends to a message list. The wire layer
  never renders templates or splits content, so there's nothing there to
  mutate, and the property belongs to whoever builds the list.

The detailed text of each R-invariant follows.

**R1. The API credential is the Harness's, and nothing else holds it.** It is
held only by the inference service process. It never appears in a role or
neuocyte environment, in a sandbox, in an event payload, in a log, in a blob,
or in a recorded request (the `Authorization` header is not part of the
request record). Checked by searching the whole state tree for the key.

**R2. Egress is a boundary, and what crosses it is recorded before it
crosses.** Every byte in a model request leaves the machine. The request
body is content-addressed and committed **before** it is sent, so exactly
what left is provable afterwards. Nothing enters a request except through
Harness-built context. With OpenRouter, "leaves the machine" means it reaches
OpenRouter **and** the upstream provider serving the call, which is why
`data_collection: "deny"` is the default (PORTING.md, OpenRouter).

Egress controls exist from the start (PORTING.md, decision 4). Every
filespace root states `egress = "allowed" | "denied"` with no default. A
client may admit an attachment as `no_egress`. The mark is a provenance
taint that follows everything derived from the source. A tainted result may
be stored, proposed and promoted, but never placed in model context. The
check sits where the request body is built and committed, so a tainted byte
in a request body is an integrity failure.

**R3. The model that answered is recorded, not assumed.** The requested
model, the model the response reports, `system_fingerprint` where given, and
the response id all go on the turn. An alias that starts resolving to a
different model becomes visible in the record rather than silently changing
the organism. On OpenRouter this also means **the upstream endpoint that
served the call**: one model ID covers deployments with different
quantizations and context windows. A model class therefore pins its endpoint
with fallbacks off, and the serving provider is recorded from evidence (the
response, or the generation-stats endpoint), never inferred from the pin.

**R4. Spend is bounded, receipted, and refuses rather than overruns.** Every
call records usage and a cost priced from a versioned price table. Ceilings
per work item, per role and per period refuse new calls when reached, as their
own stop reason. A model that cannot be priced cannot be called while a
ceiling is configured, because "unknown cost" cannot be checked against a
limit. Tokens billed for a cancelled or abandoned request are recorded too.
OpenRouter reports `usage.cost` but documents it as optional, so a response
without it is unpriced rather than free. The price table is the fallback,
and the record says which source priced the call.

**R5. Rate limits and provider failures are distinct, bounded and local.** A
429 is `rate_limited`, an outage is `provider_unavailable`, and neither
becomes `backend_error`. Retries are bounded, honour `Retry-After`, and are
recorded. One caller's failure is that caller's alone. Retrying an HTTP call
can never repeat an effect, because tools are executed by the Harness only
after a response has been accepted, and a response is accepted at most once.

**R6. Structure is the messages array, and only the Harness appends to it.**
This is the wire form of **L-STRUCTURE** (see "Laws extracted from
mechanisms"), which replaces I124 and I132. The Harness never renders a chat
template and never splits model text into messages.

**R7. A capability the endpoint does not implement is refused, not
approximated.** Tool calling, JSON-schema output, `seed`, `top_k`, assistant
prefill and parallel tool calls are declared per configured endpoint and
probed where cheap. A profile or a verb that needs a missing one is refused
at binding, not degraded at call time. Native tool calling is required of
every model class (PORTING.md, decision 3). OpenRouter ignores unsupported
parameters by default, so every call sets `provider.require_parameters:
true`. Capabilities are read from the **pinned endpoint**, never from the
model-level list, which is a union across endpoints.

**R8. Nondeterminism is stated.** `seed` is best-effort at most, and no
result is described as reproducible.

**R9. A provider's refusal is its own outcome.** `finish_reason:
content_filter` or a `refusal` field is recorded as such. It is never
delivered as an answer and never mistaken for the model choosing to say
nothing.
