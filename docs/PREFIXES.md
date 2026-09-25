# Prefixes: shared context the organism builds once and many minds start from

Status: **design, decisions recorded 2026-09-25.** Nothing here is built. It
changes how neuocytes are born, so it lands before the Harness port (plan
phase P5).

## The idea

Material the organism reasons over — an upload, a codebase, a set of
findings, Ego's own context — is assembled once into a **prefix**. Workers
then start from it, instead of each one being sent the same material cold.
Providers discount a request whose opening bytes match a recent one, so a
prefix is paid for roughly once per active period and then read at a
fraction of the price.

## How provider caching actually works

Per OpenRouter's prompt-caching documentation (read 2026-09-25 through a
summary; **re-verify before building**):

- **It is prefix caching, not storage.** A request whose leading bytes match
  a recent request reuses the provider's computation. Nothing is uploaded
  once and referenced by id.
- **It expires.** Lifetimes are roughly 5 minutes to 1 hour depending on the
  provider and tier. "Pay once" means once per active period, per endpoint.
- **Write and read prices differ.** Writes cost 0–2× normal input, reads
  0.1–0.5×. For example, Anthropic writes at 1.25× (5 min) or 2× (1 h) and
  reads at 0.1×. Several providers have no write premium.
- **There is a minimum size**, roughly 1,024 to 4,096 tokens, below which
  nothing is cached.
- **Order is everything.** The match runs from the first byte: tools, then
  system, then messages. A different tool list breaks the match before the
  system prompt is reached.
- **Some providers cache automatically; others need explicit `cache_control`
  markers.** Anthropic allows at most 4; Qwen requires them.
- **Usage reports both:** `prompt_tokens_details.cached_tokens` for reads and
  `cache_write_tokens` for writes.

So the design has a durable half and an ephemeral half, and must never
confuse them (L-CACHE).

## Three concepts

| | Concept | Durable? | What it is |
|---|---|---|---|
| **Prefix** | An organism-level object | **Yes** | A versioned, immutable, content-addressed run of messages in the record. Every block is typed and carries its provenance. Model-independent: it is exact bytes. |
| **Residency** | A provider-level state | No | Whether a prefix is currently cached at one pinned endpoint behind one exact request head. An optimization only, observed from `usage`, never assumed. |
| **Warming** | A Harness act | Its record is | A call made by the Harness (never by a model) that writes a prefix into a provider's cache, recorded and costed like any model call. |

## Provenance: every block is typed

**Decision 1.** A prefix is only as trustworthy as what went into it, so
every block states what it is, where it came from, and who is answerable
for it. Nothing enters a prefix untyped.

| Block type | What it is | Source reference | Answerable party |
|---|---|---|---|
| `input` | Bytes admitted from outside (an upload, an attachment, a file read) | The admitted input and its exact digest (I48e, I133) | The client or root that supplied it |
| `artifact` | An accepted artifact | The artifact and its promoted digest (I34) | Whoever promoted it |
| `memory` | A maintained interpretation | The memory item and its version (I3) | The governed path that accepted it |
| `board` | A blackboard post | The post, with its attempt fate as recorded when read (I95, I136) | The posting worker's attempt |
| `reasoning` | Model output: a digest, a summary, a synthesis | The turn that produced it, **and the sources it was made from** | The role or worker whose turn it was. It is an **authored claim, not a source**. |
| `ego_context` | Ego's own context | A published Ego snapshot (I12) | Ego. This kind is **privileged** (below). |

A synthesized digest is therefore allowed, and is typed `reasoning`. It
names both the turn that wrote it and every source it was written from, so a
reader can always tell a summary from what it summarizes.

When a prefix is shown to a model, each block is rendered with its type and
source, so the model sees the difference between a client's file and Ego's
summary of it.

## Authority

**Decision 2.** Minds do not warm caches; they ask the Harness to.
**Decision 4.** Ego snapshots are a privileged kind.

| Act | Ego | Id | Neuocyte | External client | Operator |
|---|---|---|---|---|---|
| Have an upload become a prefix | — (automatic at admission, see below) | — | — | Indirectly, by uploading | — |
| Request a prefix built from recorded items | Yes, into its own lineage | Yes, into its own lineage | **No.** It posts the need to the board (below). | No | Yes |
| Request a prefix be warmed | Yes | Yes | **No.** It posts the need to the board. | No | Yes |
| Publish an `ego_context` prefix | **Only through Ego's publish path** | No | No | No | No |

- **`ego_context` cannot be ordered any other way.** It is not a value any
  prefix-building or warming verb accepts, so asking for it through those
  verbs is unsayable rather than refused (I48b, I49). The only way one comes
  into being is Ego publishing a snapshot of its own actual context, which is
  I12 unchanged. Id cannot publish one; a neuocyte cannot publish one; the
  operator cannot fabricate one.
- **A neuocyte's need goes on the board** as a post of a new type,
  `prefix_need`, naming what it wanted and why. Under I92 a board post about
  work a role originated wakes that role, so the need reaches the Ego or Id
  that can act on it, and whether to act stays that role's decision.
- **Lineage (I137).** Ego's prefixes feed `ego.neuocyte` workers and Id's
  feed `id.neuocyte`.
- **External uploads are organism resources (decided 2026-09-25).** A file
  uploaded from outside starts out used by the interaction that brought it.
  It becomes **eligible for sharing** with other interactions and work, and
  is offered and warmed for them **when the worth-it check says reuse pays**
  (below). See "Sharing external uploads".
- **Egress (R2).** A prefix is sent to a provider, so a no-egress source, or
  anything derived from one, cannot be a block. The build refuses and names
  the tainted source.

## Uploads become prefixes automatically

Every admitted upload above the endpoint's minimum cacheable size gets a
`document` prefix at admission. Building one is cheap: it is a record, not a
model call. **Warming** it is a separate decision (below). So "anything
uploaded is ready to be shared" costs nothing until the organism actually
uses it.

## Sharing external uploads

**Decision (2026-09-25):** files uploaded from outside are eligible for
sharing across interactions when it is worth it.

- **What changes.** Carried invariants I89 and I133 scoped an attachment to
  the interaction that admitted it, and refused it to any other ("no such
  input" / "not yours"). For external uploads that scope is lifted: they are
  organism-level resources. This is the trust-domain rule applied knowingly
  ("clients sharing one Amoeba share a mind"): **one client's answer may be
  shaped by, and may quote, another client's file.**
- **Eligible is not shared.** An upload is offered to other interactions and
  warmed for them only when the worth-it check predicts reuse across
  interactions. The share is recorded with its estimate, like any warm
  (PX9).
- **Visibility, not prevention.** Every `input` block names the client and
  interaction it came from, and every answer records which prefixes it used
  (I139). Cross-interaction influence is always on the record, and Id can see
  it.
- **Unchanged:** I89's output side (a result still reaches only the client
  that asked), and egress. A no-egress upload is never placed in a prefix,
  shared or not (PX5).
- **Discovery** goes through search. Shared uploads join the search corpus
  available to other interactions (PLATFORM_PLAN.md §4.2); unshared ones stay
  in their own interaction's corpus.

### The per-upload opt-out: `interaction_only`

**Decided 2026-09-25.** A client may mark an upload `interaction_only` when
it sends it.

- **Set at admission, never changed.** The mark is part of the input's
  admitted record, like `no_egress`. History is not rewritten (I87), so it
  cannot be added or removed afterwards. A client that wants a different
  choice uploads again.
- **It is a provenance taint.** Everything derived from the upload carries it:
  a `reasoning` digest of it, a tool result computed from it, an artifact made
  from it. Otherwise a summary would carry the file across the line the mark
  drew.
- **What it withholds.** A tainted item is never shared across interactions,
  never enters another interaction's search corpus, and is never warmed for
  another interaction. The worth-it check is not consulted, because the
  client's choice outranks the economics.
- **What it does not withhold.** It is not `no_egress`. The upload still goes
  to the provider for its own interaction's work, since that is what the
  client uploaded it for. A client that wants neither sets both.
- **Its limit, stated.** The mark governs the organism's sharing machinery:
  prefixes, search and warming. It does **not** erase what a persistent mind
  has read. If Ego read the file while answering this interaction, Ego still
  knows it afterwards. That is the carried trust-domain rule: lineage is
  routing, not confidentiality, and workloads that need true isolation need
  separate instances. The admission reply says so, so a client is not led to
  believe the flag does more than it does.
- **The same check point as egress.** Whether a block may enter a request for
  a given interaction is decided where the request body is built and
  committed (E2). A tainted block in another interaction's request body is an
  integrity failure, not a policy warning.

## Layout: what makes siblings share a cache

The Harness (E2) builds every request in one canonical order:

```
tools        stable per lineage and tool family
system       the governed profile, rendered from the binding
prefix       the prefix's blocks, in order, typed           <- cache boundary
tail         this worker's own instructions and turns
```

Siblings with the same lineage, binding, tool family and prefix version
share every byte up to the cache boundary, so the first writes and the rest
read.

- **The residency key** is the digest of the exact leading bytes up to the
  boundary, computed at E2. It, not the prefix id, is what a provider
  actually caches, so residency is tracked per (endpoint, key).
- **Tool families.** I23b makes the sandbox tools absent when a work item did
  not allow them, so each lineage has two tool families and therefore two
  residency keys per prefix. That is a design fact, not a defect.
- **The cache marker is structure.** Where an endpoint needs explicit
  markers, the Harness places `cache_control` at the boundary. Nothing a
  model, client or file wrote can place one (L-STRUCTURE). That needs a wire
  change: `content` becomes an array of text blocks, which `wire.py` does not
  accept today.
- **A prefix is data, never doctrine.** It follows the governed system prompt
  and never sits in `system`. An upload can contain prompt injection, and a
  prefix sends it to every worker that uses it, so its reach grows but its
  authority must not (I63).

## Warming: explicit, automatic, and checked

**Decision 3: both.** Warming is always a Harness call (`max_tokens: 1` on
the exact head) and is recorded and costed like any other call.

- **Explicit.** Ego, Id or the operator requests it, typically for a large
  upload or a codebase about to be worked on. It proceeds subject to spend
  ceilings (R4). The worth-it estimate is recorded beside it but does not
  veto it.
- **Automatic.** The Harness warms when queued work references a prefix and
  the estimate says it pays. It keeps a prefix warm while that demand lasts,
  and lets it lapse otherwise.
- **Warm, then fan out.** Workers launched together on a cold prefix all miss
  and all pay the write. When several are about to start, the Harness warms
  first and releases them after the warm call returns.

### The worth-it check

For a prefix of `T` tokens on an endpoint with input price `p`, cache-write
price `p_write` and cache-read price `p_read`, with `N` expected reads while
it is cached:

```
cost without a prefix     N · T · p
cost with a warmed prefix T · p_write  +  N · T · p_read
worth it when             N · (p − p_read)  >  p_write
```

With Anthropic's 5-minute tier (1.25× write, 0.1× read), that is `N ≥ 2`.

- **Prices** come from the pinned endpoint's listing (`input_cache_read` is
  listed; `input_cache_write` where given). A missing price makes the
  estimate **unpriced**: explicit warming still proceeds under ceilings, but
  automatic warming does not, the same rule as R4.
- **`N` is empirical.** It comes from the record: how many readers prefixes
  of this kind and size actually had within one lifetime at this endpoint. A
  new organism with no history uses the queued demand it can see.
- **Estimated and realised.** Each warm records its estimate. The realised
  savings are computed afterwards from `usage` (`cached_tokens` on the reads
  that followed). The difference is a measurement of the policy itself, and
  Id can see it in the pulse.
- **Keeping warm** costs one read of the prefix per lifetime window (for
  example 12 × 0.1 = 1.2× the prefix for an hour at a 5-minute lifetime),
  which is compared with the rewrite it avoids.
- **Lifetime and minimum size** are not in OpenRouter's endpoint listing, so
  a model class declares them in configuration (`cache_ttl_seconds`,
  `cache_min_tokens`, and `cache_mode` = `automatic` | `explicit` | `none`).
  The declared lifetime is checked against the record: a read that misses
  inside the declared lifetime is evidence the declaration is wrong, and is
  reported.
- **Too small to cache is reported.** A prefix under the minimum is still
  valid context. The Harness says it cannot be discounted, and does not pay
  to warm it.

## Rules (proposed; each needs a test that fails without it)

| Id | Rule | Enforcement point |
|---|---|---|
| **PX1** | Every block of a prefix is typed and carries its provenance. There is no untyped block, and a `reasoning` block names its turn and its sources. | E1, E2 |
| **PX2** | A prefix is data, never doctrine. It follows the governed system prompt and is never placed in `system`. | E2, E3 |
| **PX3** | Prefixes are immutable. An update is a new version, and a worker stays on the version it was born with (I15, I53). | E1 |
| **PX4** | Prefix content is inherited evidence. Restating it is never corroboration (I138). | E1, E7 |
| **PX5** | A prefix obeys the egress taint. No tainted source, or anything derived from one, can be a block (R2). | E2 |
| **PX6** | Residency is observed, never assumed, and correctness never depends on it (L-CACHE). A cold prefix changes the cost, never the outcome. | E5, E8 |
| **PX7** | Only the Harness warms. A model can request warming (Ego, Id) or post the need (neuocyte). It cannot perform a warm, and it cannot place a cache marker. | E3, E6 |
| **PX8** | `ego_context` is privileged. It comes into being only through Ego's publish path, and no other verb can name it. | E6 |
| **PX9** | Every warm is recorded with its estimate. Realised savings are measured from `usage` afterwards, and the difference is reported. | E4, E5 |
| **PX10** | Explicit warming is bounded only by spend ceilings. Automatic warming also requires a priced estimate that pays. | E4 |
| **PX11** | An external upload becomes shared across interactions only when the worth-it check says reuse pays, and the share is recorded. Every use of a shared upload is attributed to its origin client and interaction. | E1, E4, E7 |
| **PX12** | An upload admitted `interaction_only`, and everything derived from it, never enters another interaction's prefix, search corpus or warm. The mark is set at admission and never changed. The admission reply states that it does not erase what a persistent mind has read. | E1, E2 |

## What this changes

- **I12 generalizes.** An Ego snapshot becomes the privileged `ego_context`
  prefix kind. Neuocyte birth is "start from a prefix version", so the P5
  neuocyte and snapshot code is written against prefixes from the start.
- **The wire (E3)** needs content blocks and `cache_control`, set only by
  the Harness. The service also needs to record `cache_write_tokens`; it
  records `cached_tokens` today.
- **Endpoint capabilities (R7)** gain the caching declarations above.
- **Model class choice** now includes caching behaviour. A long-lifetime,
  cheap-read endpoint suits a heavily shared prefix; an automatic endpoint
  with no write premium suits tails that change a lot.
- **Homeostasis (E8)** gets a concrete objective. A rebuild that changes a
  prefix's head throws away its residency, and that is now a priced cost.
- **The board** gains the `prefix_need` post type.

## Open

- The exact rendering of typed blocks: how a model is shown "this is a
  client's file" versus "this is Ego's summary", so the distinction survives
  into its reasoning.
