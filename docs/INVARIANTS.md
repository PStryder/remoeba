# Remoeba — what happens to Amoeba's invariants

Amoeba's invariants were written against one resident local model: a
llama.cpp context, a unified KV pool, token ids the Harness could inspect
before they landed. Remoeba drives the same organism through remote
OpenAI-format chat APIs. This document takes every Amoeba invariant and says
what becomes of it.

The full text of each invariant — including the live failure that paid for it
— is preserved verbatim in [amoeba/ARCHITECTURE.md](amoeba/ARCHITECTURE.md).
Read that before relaxing anything marked **Carry**: most of them exist
because the looser version was tried and broke.

## The one change everything else follows from

A remote chat API is **stateless and message-level**. Every call sends the
whole context as a JSON `messages` array and gets back one assistant message.
There is no session on the far side, no KV the Harness can fork or measure,
no token id it can refuse before it lands, and no tokenizer it can be sure
matches the provider's.

So in Remoeba **a session is a message list the Harness owns**, recorded
durably and content-addressed, and a model call is a pure function of that
list plus sampling settings. That single substitution:

- makes several of Amoeba's hardest problems disappear (session handles that
  go stale, token-offset splicing, KV occupancy, refcounted prefixes);
- turns snapshots into something simpler and more honest (an immutable
  message list, not a KV prefix);
- makes provenance *stronger*: the exact request body that produced a piece of
  cognition is a blob, so "what the model saw" is a stored fact rather than a
  reconstruction;
- and opens problems Amoeba never had: data leaving the machine, a credential
  to guard, money, rate limits, provider outages, and a model that can change
  behind its name.

## Verdicts

| Verdict | Meaning |
|---|---|
| **Carry** | Model-agnostic. Holds unchanged. |
| **Adapt** | The principle holds; the mechanism or the numbers are local-model specific and must be restated. |
| **Drop** | The problem it solved does not exist against a remote API. The *reason* it existed may reappear in a new R-invariant. |

| Status | Meaning |
|---|---|
| `verified` | Code and tests are in this repo **and** `scripts/verify_invariants.py` shows the tests fail with the guarantee removed. |
| `tested` | Code and tests are here; the mutant was not carried over (usually because it also mutates code not yet ported). |
| `pending` | The code that enforces it has not been ported yet. |

The verifier prints its own totals; only that output is evidence. As of the
initial copy it reported 21 of 21 carried invariants defended.

---

## State and provenance

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I1 | One writer | Carry | `verified` | |
| I2 | Raw history is append-only | Carry | `verified` | |
| I3 | History is not memory | Carry | `tested` | |
| I4 | Content before reference | Carry | `tested` | Request and response bodies become content references too — they fall under the schema-derived inventory automatically. |
| I5 | Hash chaining detects mutation, not administrators | Carry | `verified` | |
| I6 | Acknowledged means durable | Carry | `verified` | |

## Work and neuocytes

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I7 | At-least-once, idempotent commits | Carry | `verified` | |
| I8 | Fencing | Carry | `tested` | |
| I9 | Neuocyte death is always safe | Carry | `pending` | A killed neuocyte may leave an HTTP request in flight that is still billed. Safe for state; not free. See R4. |
| I10 | Retirement never destroys authoritative state | Adapt | `pending` | "Releases inference and snapshot resources only" becomes "abandons its in-flight request". There is no KV to release. |
| I11 | Stale findings are flagged | Carry | `verified` | |

## Ego snapshots

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I12 | Only Ego publishes | Adapt | `pending` | A snapshot is an immutable, content-addressed **prefix of Ego's actual message list**. Still not a merge, not a summary, and Id's list is never published. |
| I13 | Ego continues after publishing | Carry | `pending` | Trivially true: Ego keeps appending to its own list. |
| I14 | Neuocyte tails are private | Carry | `pending` | True by construction: each worker's list is its own. Provider-side prompt caching shares nothing observable. |
| I15 | Pinned for life | Adapt | `pending` | Pinned to its snapshot **and to the model binding** it was born with (endpoint + model), not to a "model generation" of local weights. |
| I16 | Referenced storage is never recycled | **Drop** | — | Existed to refcount KV cells. A snapshot is now blobs, and blobs are never reclaimed at all (I87). |
| I17 | Incompatible cached tensors are never reinterpreted | **Drop** | — | There are no tensors. A text snapshot *can* be replayed on a different model; that is allowed and must be recorded (R3), never silent. |
| I18 | KV is replaceable acceleration, not memory | Adapt | — | Restated: **a provider's prompt cache is acceleration, not memory.** Nothing may depend on a cache hit; a hit is a billing fact read from `usage`, not a guarantee. |

## Roles and authority

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I19 | Neither half is the harness | Carry | `pending` | |
| I20 | Id audits the record, not Ego | Carry | `pending` | |
| I21 | Disagreement, not overwrite | Carry | `pending` | |
| I22 | The side channel changes nothing | Carry | `pending` | |
| I23 | A model can request a tool call; it cannot perform one | Adapt | `pending` | Requests arrive as native `tool_calls`, not parsed out of text. Validation, permission checks, Harness execution and receipts are unchanged. |
| I23b | A neuocyte cannot widen its own permissions | Carry | `pending` | |
| I23c | A model cannot name a sandbox | Carry | `pending` | |
| I23d | A fenced neuocyte cannot still run code | Carry | `pending` | |
| I23e | The tool loop is bounded three ways | Adapt | `pending` | Turns, tokens and deadline stay, and **spend** joins them as a fourth bound (R4). Token counts come from the provider's `usage`, after the fact. |

## Reporting

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I24 | No overclaiming | Adapt | `pending` | The capability vocabulary changes completely (see R7). Nothing about the provider's hardware, batching or caching can be verified from this side, so none of it is claimed. A simulated backend still labels every result. |
| I25 | Health stays answerable | Carry | `pending` | More important now: "provider down" is an ordinary condition, not a crash. |
| I26 | A client is not the mind | Carry | `pending` | |
| I27 | Cancelling stops future work; it does not undo the past | Adapt | `pending` | Cancelling closes the HTTP stream. The provider may still bill the tokens it generated, and that has to be recorded rather than assumed away. |
| I28 | Supervision keeps making passes | Carry | `pending` | |

## Inward filesystem boundary and sandbox

All Windows-host properties. They are unaffected by where inference runs.

| | Invariant | Verdict | Status |
|---|---|---|---|
| I29 | State directories unreachable from outside | Carry | `verified` |
| I30 | Hardening fails safe, never locked | Carry | `verified` |
| I31 | Every state directory is hardened | Carry | `verified` |
| I32 | Sandboxed code cannot modify its interpreter | Carry | `verified` |
| I33 | Hardening is reported, not assumed | Carry | `verified` |
| I34 | The Harness promotes the bytes it reviewed | Carry | `pending` |
| I35 | Concurrent sandboxes share no handles | Carry | `verified` |
| I36 | Sandboxes run concurrently, measured as overlap | Carry | `verified` |

The sandbox's AppContainer profile is now named `Remoeba.<id>`, so an Amoeba
and a Remoeba on one machine cannot collide.

## Host files

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I37 | Nothing outside a filespace root is reachable | Carry | `verified` | |
| I37b | Containment survives links | Carry | `verified` | |
| I37c | A read-only root is read-only | Carry | `verified` | |
| I37d | Containment is about files, not only paths | Carry | `verified` | |
| I37e | One file has one identity | Carry | `tested` | |
| I38 | No write destroys | Carry | `pending` | |
| I39 | A neuocyte has no verb that reaches the host filesystem | Carry | `pending` | |
| I40 | Nothing is read that was not named | Carry | `pending` | Gains a second meaning: what is read may be **sent to a third party**. See R2. |
| I41 | A receipt's digest is ground truth, verifiable from inside | Carry | `pending` | |
| I42 / I42b | Destroying a sandbox destroys nothing that matters | Carry | `pending` | |
| I43 | Sandboxed code cannot mutate Filespace, blobs or state | Carry | `pending` | |

## Id's and Ego's senses and effectors

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I44 | Id has one cheap, bounded sense of the whole organism | Adapt | `pending` | The pulse's inference and context-pressure fields become provider health, rate-limit headroom, spend against ceilings, and per-role context as reported by `usage`. |
| I44b | The pulse reports observations, never verdicts | Carry | `pending` | |
| I44c | The pulse tracks reality | Carry | `pending` | |
| I44d | Configured is distinguished from embodied | Carry | `pending` | Extends naturally to models: the model a role was bound to versus the one configured now. |
| I45 – I46b | Id requests; the Harness decides; scopes are disjoint | Carry | `pending` | |
| I47 – I47e | Ego states intent; the Harness owns execution | Carry | `pending` | |
| — | One Amoeba is one cognitive trust domain | Carry | — | Now also: one Remoeba sends **all** of its cognition to whichever providers it is configured for. Two workloads that must not share a provider need two instances. |
| — | Neuocyte identity is asserted, not authenticated | Carry | — | Unchanged, and still worth fixing when it is fixed in Amoeba. |

## External interfaces and prompt library

| | Invariant | Verdict | Status |
|---|---|---|---|
| I48 – I48f | External input is not external control | Carry | `pending` |
| I49 – I56 | Prompt-library governance | Carry | `pending` (code is here; its tests need the role layer) |
| I57 | An incarnation binding freezes bytes, not a pointer | Adapt | `pending` |

**I57** also has to freeze the **model binding**: endpoint, requested model,
and the model the first response reported (R3).

**Model variables.** The rule carries exactly — *an unknown name is refused
rather than dropped* — but the set is now per provider, not fixed. OpenAI-format
APIs accept `temperature`, `top_p`, `max_tokens`/`max_completion_tokens`,
`seed`, `stop`, `presence_penalty`, `frequency_penalty`; they do **not** accept
`top_k`. A profile binding `top_k` against such an endpoint must be refused at
binding, not silently ignored — that is precisely the failure I135 records.
**Verdict: Adapt.**

## Persistent roles and bounded turns

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I64 | One bounded turn at a time | Carry | `pending` | |
| I65 | Input during a turn waits for the next boundary | Carry | `pending` | |
| I66 | Turn-end reasons are first class | Adapt | `pending` | Derived from `finish_reason` and HTTP outcome. New distinct reasons: `content_filter`, `rate_limited`, `provider_unavailable`, `spend_exhausted`. None may collapse into `backend_error`. |
| I67 | The Harness continues an interrupted thought | Carry | `pending` | |
| I68 / I68b | Continuation is bounded; recovery and progress spend different allowances | Carry | `pending` | Unbounded continuation against a paid API is a furnace with an invoice. |
| I69 | A role that dies mid-turn does not swallow its inputs | Carry | `pending` | |
| I70 | A turn records exactly what caused it | Adapt | `pending` | Plus the exact request body (blob), response id, requested and reported model, `system_fingerprint` where given, usage and priced cost. |
| I71 | The scheduler is substrate, never cognition | Carry | `pending` | |
| I72 | One ingestion path: cognition only in claimed turns | Carry | `pending` | |
| I73 – I75 | Wedged turns, stale turns, forged mailbox attribution | Carry | `pending` | |
| I76 – I83 | Requests read whole; answers belong to their request | Carry | `pending` | |
| I84 | An existing database gains new columns | Carry | `verified` | |
| I85 / I85b | Delegated work is accountable; lineage is Harness-bound | Carry | `pending` | |
| I86 | A result the model cannot see whole says so | Carry | `pending` | |
| I87 / I88 | Evidence is never pruned; nothing in use is forgotten | Carry | `pending` | `retention.py` is here; its tests need the mailbox. |
| I89 | Ego chooses what crosses the boundary, never whose | Carry | `pending` | |
| I90 | Batching changes throughput, never outcomes | **Drop** | — | No batching. Its surviving half is R5: concurrent requests are independent, and one caller's 429 or error is that caller's alone. |
| I91 / I92 | Specialisations; wake on owned work | Carry | `pending` | |
| I93 | Context is reclaimed by dropping finished work | Adapt | `pending` | Far easier: a turn is a range of whole messages, not a token span. The policy (settled work goes oldest first; owed work never) carries unchanged. |
| I94 | Everyone holding a session handle learns when it changes | **Drop** | — | Existed because two parties held a handle to a remote KV session. If role processes hold **no transcript** and each turn reads its messages from the record, there is one holder and nothing to hand over. That is a design choice to make deliberately (see PORTING.md). |
| I95 | What became of an attempt travels with what it said | Carry | `pending` | |
| I96 | A session carries its own allowance | Adapt | `pending` | Allowance in tokens per role, bounded above by the bound model's context window. |
| I97 | What a session may think and what it costs are different | Adapt | `pending` | Now literal: tokens versus money. A worker forked from a large Ego prefix pays for that prefix **on every call**, discounted only by whatever the provider caches. |
| I98 | Admission spends a pool it has measured | Adapt | `pending` | The pool is spend ceilings and rate limits (RPM/TPM), not KV. "Does not guess when it cannot measure" carries. |
| I99 | Only the discretionary turn yields to pressure | Adapt | `pending` | Pressure means rate-limit or spend pressure. |
| I100 – I102 | Claims can be withdrawn; disputes end on the record | Carry | `pending` | |
| I103 – I106 | Console transport, room attribution, live view vs record | Carry | `pending` | |
| I107 / I108 | An answer is whole; only a concluded thought is finished | Carry | `pending` | |
| I109 | A continuation resumes the exact message it continues | **Drop** (capability-gated) | — | OpenAI-format APIs cannot resume mid-message. Every continuation asks visibly, under I107's "appended directly" instruction. A provider that supports assistant prefill may re-enable it as a declared capability. |
| I110 | Each mind has its own output ceiling; nothing silently lowers it | Carry | `pending` | Also bounded by the bound model's own maximum output, refusing rather than clamping. |
| I111 | A generation is admitted only if its whole allowance fits | Adapt | `pending` | The prompt size is an **estimate** until the provider reports it, and must be labelled one. A provider's `context_length_exceeded` must be recognised as pressure (I135). |
| I112, I116 | A conclusion is chosen; an audit lands | Carry | `pending` | |
| I113 | A role is taught the call syntax its parser accepts | Adapt | `pending` | Becomes: **the `tools` array sent is exactly the role's model-facing scope**, derived from the dispatch table, never hand-maintained. |
| I114 | A role is not restarted for thinking | Carry | `pending` | Remote calls are slower and more variable; this matters more. |
| I115 | A malformed capability call is never delivered as an answer | Adapt | `pending` | Malformed now means invalid JSON arguments, an unknown tool name, or tool-call-shaped text in `content` from a model that did not use the tools channel. |
| I117 – I121 | Declared vocabularies, argument checks, RPC hygiene, measured views | Carry | `pending` | I121's "measured" context comes from `usage`. |
| I122 | A rebuilt context is one the conversation could have reached | Adapt | `pending` | Whole messages make most of this true by construction. The rules about environment blocks and owed turns remain. |
| I123 | A tool result too large for its budget is never chopped | Adapt | `pending` | "Counted by the model's tokenizer" may be impossible for an arbitrary provider. Use an exact tokenizer where one is known to match, otherwise a conservative estimate that says it is one. |
| I124 | Generated tokens never introduce structural tokens | **Drop** (mechanism) | — | Replaced by R6. |
| I125 – I131 | Watermarked reviews, not-thinking alarm, condition wakes, durable delivery, whole requests, non-destructive reset | Carry | `pending` | I126 must tell "the provider is down" (organism-wide) apart from "this role is broken". |
| I132 | Content never introduces structural tokens | **Drop** (mechanism) | — | Replaced by R6. `framing.py` is not ported. |
| I133 | An admitted input is referred to, never moved | Carry | `pending` | |
| I135 | A binding describes the sampling actually applied | Carry | `pending` | Easier to prove: the recorded request body **is** the call. |
| I136 – I142 | Worker faithfulness, lineage, corroboration, effects, answer dependencies | Carry | `pending` | I138: forking a message-list snapshot still passes the forker's evidence roots on as `inherited`. |

## Profile, environment, turn input

| | Invariant | Verdict | Status | Remote notes |
|---|---|---|---|---|
| I58 | A role is never offered a capability it cannot invoke | Carry | `pending` | |
| I59 | A role can execute what its environment offers | Adapt | `pending` | Through native tool calling. |
| I60 – I63 | Authority-shaped arguments, one environment per turn, doctrine vs environment, no config doctrine | Carry | `pending` | |

## Unnumbered rules, recovery and scheduling

- **Cognitive results never lose their labelling** — Carry.
- **Rejuvenation stays Harness-initiated** — Carry, though with Harness-owned
  transcripts it is only a rebuild of the message list.
- **Ego wakes because something relevant happened** — Carry.
- **Recovery** steps 4–5 (null backend handles, zero snapshot refcounts) —
  Drop. The rest carries.
- **Retirement is a throughput mechanism** — Drop. It rested on idle KV
  taxing every decode. A remote worker costs nothing while idle, so retirement
  goes back to being hygiene and cost control.

---

## New invariants for remote inference

These have no Amoeba ancestor, or replace one that was dropped. They are
**proposals**: none is implemented or tested yet, and each should earn its
number the way Amoeba's did — with a test that fails when it is removed.

**R1. The API credential is the Harness's, and nothing else holds it.** It is
held only by the inference service process. It never appears in a role or
neuocyte environment, in a sandbox, in an event payload, in a log, in a blob,
or in a recorded request (the `Authorization` header is not part of the
request record). Checked by searching the whole state tree for the key.

**R2. Egress is a boundary, and what crosses it is recorded before it
crosses.** Every byte in a model request leaves the machine. The request
body is content-addressed and committed **before** it is sent, so exactly
what left is provable afterwards. Nothing enters a request except through
Harness-built context. Open question: whether a filespace root or an
attachment can be marked *no-egress*, so that its bytes may be processed in a
sandbox but never placed in model context.

**R3. The model that answered is recorded, not assumed.** The requested
model, the model the response reports, `system_fingerprint` where given, and
the response id all go on the turn. An alias that starts resolving to a
different model becomes visible in the record rather than silently changing
the organism.

**R4. Spend is bounded, receipted, and refuses rather than overruns.** Every
call records usage and a cost priced from a versioned price table. Ceilings
per work item, per role and per period refuse new calls when reached, as their
own stop reason. A model that cannot be priced cannot be called while a
ceiling is configured, because "unknown cost" cannot be checked against a
limit. Tokens billed for a cancelled or abandoned request are recorded too.

**R5. Rate limits and provider failures are distinct, bounded and local.** A
429 is `rate_limited`, an outage is `provider_unavailable`, and neither
becomes `backend_error`. Retries are bounded, honour `Retry-After`, and are
recorded. One caller's failure is that caller's alone. Retrying an HTTP call
can never repeat an effect, because tools are executed by the Harness only
after a response has been accepted, and a response is accepted at most once.

**R6. Structure is the messages array, and only the Harness appends to it.**
This replaces I124 and I132. The Harness never renders a chat template and
never splits model text into messages. Whatever a model generates is the
content of exactly one assistant message, or entries in its `tool_calls`.
Whatever a client, a file or a tool result contains is the content of exactly
one message. The Harness **cannot verify** how a provider tokenizes marker
strings inside content, so that is stated as a limitation (I24), never
claimed.

**R7. A capability the endpoint does not implement is refused, not
approximated.** Tool calling, JSON-schema output, `seed`, `top_k`, assistant
prefill and parallel tool calls are declared per configured endpoint and
probed where cheap. A profile or a verb that needs a missing one is refused
at binding, not degraded at call time.

**R8. Nondeterminism is stated.** `seed` is best-effort at most, and no
result is described as reproducible.

**R9. A provider's refusal is its own outcome.** `finish_reason:
content_filter` or a `refusal` field is recorded as such. It is never
delivered as an answer and never mistaken for the model choosing to say
nothing.
