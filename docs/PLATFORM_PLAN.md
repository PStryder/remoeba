# Platform plan: Postgres, semantic search, and a containerised cognitive unit

Status: **draft plan, decisions recorded 2026-09-25** (§8). Nothing here is
built. It sequences three changes against the invariant taxonomy in
[INVARIANTS.md](INVARIANTS.md):

1. **Postgres with multiple writers**, replacing SQLite.
2. **Full semantic search**: pgvector in the unit, with embeddings from
   OpenRouter through the inference service.
3. **The whole Harness in a container**, with the database and every Remoeba
   artifact inside the unit, so one Remoeba can run on this machine or in the
   cloud unchanged. The target is **generic Docker**: a plain OCI image that
   runs under any Docker-compatible runtime. **Fly is a supported deployment**
   (a `fly.toml` and notes), not the design target.

**Confirmed:** "hosted locally" means **self-hosted inside the unit**. The
unit carries its own Postgres wherever it runs, not a managed database.
Embeddings come from OpenRouter, which the unit already calls (decision P0-5),
so no embedding model ships in the image.

---

## 1. Review: where Remoeba is

**Built and verified:**
- **The durable foundation**, carried from Amoeba: event chain, receipts,
  blobs, memory, work queue, blackboard store, prompt library.
- **The Windows host boundary**: filespace, AppContainer sandbox, hardening.
- **The inference service**: pinned OpenRouter calls, digest-checked send,
  classified outcomes, the credential boundary.
- **Tests:** 204 pass. The verifier reports 48 invariants fully defended and
  3 masked as declared.

**What that review turns up:**

| Finding | Consequence for this plan |
|---|---|
| **Nothing thinks yet.** The supervisor, roles, mailbox and about 12k lines of Harness are still in Amoeba. | **Sequencing is the whole game.** Moving to Postgres and Linux *before* the Harness port means porting it once. Porting it onto SQLite and Windows first means porting it twice. |
| The store is one total order. `StateWriter.apply` takes `BEGIN IMMEDIATE`, reads the chain tip and the global `state_version`, appends, and increments. I5, I11, I102 and I125 all read that order. | "Multi-writer" has to mean **many writers, one order**, not unordered concurrent commits (§3.2). |
| SQL: 38 tables, about 180 call sites. SQLite-isms are concentrated: about 50 in `db.py`, `events.py`, `writer.py` and `results.py`. The repositories are plain SQL with `?` placeholders. | Moving the dialect is mechanical everywhere except the writer and the schema. |
| Local-model residue is still in the schema: the `snapshots` and `snapshot_refs` tables, `agents.session_handle`, KV columns, and `ArbiterConfig`'s KV fields. | Don't translate these to Postgres. Replace them in the same step (message-list snapshots, I12). |
| Recall is `LIKE` over `claim` and `tags`. Board search is `LIKE` over title and body. | Semantic search is built, not migrated (§4). |
| **The host boundary is Windows-only**: `sandbox.py` (AppContainer, 72 native call sites), `security.py` (`icacls`), `filespace.py` (NTFS: 8.3 names, case folding). | **The largest risk in the plan.** A container means Linux, so I29–I37e must be **re-mechanized and re-verified**, not carried (§5). |
| `verify_invariants.py` edits source in place. That is why Amoeba's working tree was unsafe to copy from earlier today. | In the container, run each mutant in a throwaway copy of the tree. |
| Decision 4 (egress taint) is decided but not built. Filespace roots have no `egress` field yet. | Build it as part of the Linux filespace work (§5.3), since roots become volume mounts then anyway. |
| Amoeba's guard test that documented invariants name real tests was not carried. | Reinstate it against INVARIANTS.md before the Harness port adds about 100 invariants back. |
| Synthetic Mind and Amoeba were deliberately "no Docker, no WSL". | This plan reverses that. On this Windows box, Docker Desktop runs Linux containers in WSL2. Developing on Windows means developing in that VM, and bind-mounted Windows paths are case-insensitive there. |

---

## 2. Target shape

```
  +-------------------- one Remoeba unit (image + one volume) ----------------------+
  |                                                                                 |
  |  remoeba supervisor ──spawns──> ego, id, neuocytes (processes, decision 6)       |
  |        │                         │ (no DB credentials, no provider key)          |
  |        │ writes                  └─ sandboxes: nsjail, no network (§5.1)         |
  |        ▼                                                                         |
  |  Postgres + pgvector  <──writes── inference service ──HTTPS──> OpenRouter        |
  |        ▲                           ▲   (holds the key, R1)    chat + embeddings   |
  |        └── indexer ──embed()───────┘                          (the only egress)   |
  |            (writes vectors; no key)                                              |
  |                                                                                 |
  |  /var/lib/remoeba  (the volume): pgdata/  blobs/  artifacts/  logs/  filespace/  |
  +------------------------------------------------------------------------------------+
        ▲ MCP (Streamable HTTP) + JSON-RPC API over TLS       ▲ operator console: private network only
```

**The unit is the image plus the volume.** The image is the body and can be
replaced. The volume is the organism. Moving a Remoeba from this machine to
the cloud means moving its volume. That is the README's acceptance test
("kill the colony, keep the civilization") at the infrastructure scale, and it
is this plan's final exit criterion.

**One image or several?**
- **Decided: one image** running Postgres and the Harness, under a small init (`s6-overlay`, or an entrypoint that starts Postgres and
  then execs the supervisor). It matches "one cognitive unit", gives one
  volume to back up and move, and needs one machine in the cloud.
- A dev compose file may still split out Postgres for convenience. The
  deployable unit is the single image.

---

## 3. Postgres

### 3.1 Driver, dialect, tests

- **psycopg 3**, synchronous, matching the existing code. No ORM: the
  repositories are hand-written SQL and stay that way.
- **Postgres only, with SQLite dropped.** Two dialects would mean every
  invariant tested twice or trusted once.
- Placeholders `?` → `%s`, `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, and
  the PRAGMA durability settings → server settings. `I6`'s pragma test is
  re-expressed as `synchronous_commit = on` and `fsync = on`, asserted at the
  layer that sets them.
- JSON columns that are queried become `jsonb`. Timestamps stay epoch
  doubles for now to limit churn, with a note for later.
- **Tests:** a session-scoped Postgres, local in dev and a service in CI. Each
  test gets a fresh database cloned from a migrated template
  (`CREATE DATABASE … TEMPLATE`), so tests stay isolated and fast.
- **Migrations (I84):** the principle carries, since an existing database
  gains what a release adds. The mechanism becomes numbered, idempotent
  migrations recorded in a `schema_migrations` table and checked against
  `information_schema`. No migration framework is needed at this size.

### 3.2 Many writers, one order

I1 is split along the taxonomy. The single-process half was SQLite
substrate. The part that defines the organism is:

> **M1. Many writers, one order.** Every consequential change is one
> transaction containing its state rows, its events and its receipt, and all
> such transactions are **totally ordered** by `state_version`.

Mechanism:
- `apply` opens a transaction and takes `SELECT version FROM state_version
  WHERE id = 1 FOR UPDATE` first. That row lock is the ordering point. The
  chain tip is read, events are appended and the version is bumped under it,
  and the lock is released at commit. Concurrent writers queue on the lock,
  never interleave, and cannot commit out of order.
- **Idempotency (I7):** `UNIQUE (mutation_id)` with `ON CONFLICT DO NOTHING`,
  then read back the original receipt. This works across processes, which the
  SQLite version never had to.
- **Fencing (I8):** `UPDATE … SET fencing_token = fencing_token + 1 … WHERE …
  RETURNING`, so the lease bump is atomic per row.
- **Readers** take snapshots (`REPEATABLE READ`) where several queries must
  agree, such as dossier digests (I102) and watermarked reviews (I125).
- Blobs stay on the volume. I4's "fsync the blob before the reference
  commits" is unchanged.

**The cost, stated:** mutating transactions serialize at the lock. For an
organism whose model calls take seconds, a millisecond-scale critical section
should not be the bottleneck. Measure it (N concurrent writers, commits per
second, chain intact) before considering anything cleverer. Per-stream chains
with a global sequence would allow parallel commits but give up the single
order I11 and I125 read. That trade is not worth making without a
measurement saying so.

**Who writes:**

| Writer | What it commits | Why multi-writer helps |
|---|---|---|
| Supervisor | Everything it does today | — |
| **Inference service** | `model.requested` (the body) **before** sending, and `model.responded` after | R2 becomes atomic inside the one process that sends. No hand-off, and no window where a body was sent but its commit is in another process's hands. |
| Indexer | Vectors and index state only. It asks the inference service for embeddings and holds no key. | Indexing doesn't queue behind the supervisor |
| Ego, Id, neuocytes | **Nothing, ever** | Authority is unchanged: minds request, and the Harness writes (I19, I47d) |

> **M2. Each writer holds exactly the database authority it needs.** One
> Postgres role per writer, with grants rather than conventions. The
> inference service can insert model-call records and blobs, and nothing
> else. The indexer can write only the index tables. Minds have no database
> credential at all. `events` gets `REVOKE UPDATE, DELETE` for every role,
> plus a trigger refusing both, so I2 is enforced by the database as well as
> by the code (defended twice, deliberately).

### 3.3 What Postgres gives the Harness beyond storage

- **LISTEN/NOTIFY for wakes**, replacing the 0.5 s scheduler poll. Mailbox
  enqueues notify their target role. The poll stays as a slow backstop, since
  a lost notification must not lose a wake.
- **`FOR UPDATE SKIP LOCKED`** for leasing work and claiming turns. Fencing
  still decides correctness; this only removes contention.
- **Advisory locks** replace `supervisor.lock` and the reset lock (I131). The
  lock then lives where the state lives.

---

## 4. Semantic search

### 4.1 Components

- **pgvector** with HNSW indexes, plus Postgres **full-text search**
  (`tsvector`, `websearch_to_tsquery`). Results are fused by reciprocal rank,
  because hybrid search beats either half for claims, names and identifiers.
- **Embeddings from OpenRouter** (`POST /api/v1/embeddings`, checked
  2026-09-25). The inference service gains an `embed` call: same credential
  (R1), same pinning through the `provider` routing object (`order`,
  `allow_fallbacks: false`, `data_collection`), request committed before
  sending (R2), cost recorded (R4). An embedding model is a **model class**
  like any other (decision 2), for example `index.default`. Candidates listed
  there include `openai/text-embedding-3-small`/`-large` and
  `qwen/qwen3-embedding-0.6b`. The choice is measured on a fixed query set.
- **An indexer**: a Harness worker process, not a mind. Embedding jobs are
  queued in the database on commit and drained in batches (the endpoint
  accepts arrays). Long bodies (artifacts, attachments) are chunked.
- **Embedding is egress.** Everything indexed, and every query, is sent to
  the embedding provider. So the egress taint (decision 4) decides what may
  be embedded, not just what may enter a chat (S6).

### 4.2 What is indexed, and for whom

Search is a new way to **read**, so it inherits every boundary a direct read
has. Indexing everything into one pool that anyone can query would bypass
half the scope tables.

| Corpus | Who may search it | Governing invariant |
|---|---|---|
| `memory_items` (maintained interpretations) | Ego recall, Id | I3: recall searches memory, never raw history |
| Conclusions, audits, disagreements | Id; Ego for its own | I20 |
| Board posts | Informed workers, Ego, Id. **Never board-naive work.** | I47c, I138: independence by construction |
| Artifacts and attachments | The interaction or work that holds them | I89, I133 |
| Raw events | Id audit and provenance only | I3 |

### 4.3 New invariants (proposed)

- **S1. The index is derived, never authoritative.** It can be rebuilt
  entirely from the record, and no decision depends on it existing (L-CACHE).
- **S2. Search respects the same boundaries as direct reads.** Scope is the
  credential's, per corpus, as in the table above.
- **S3. A search is an acquisition.** It is rooted in what it consulted
  (I138): finding the same source through search is a retrieval, never new
  independent evidence.
- **S4. A result set that is not whole or not fresh says so.** It reports
  items not yet embedded, and the index version (I86).
- **S5. One index, one embedding binding.** Model, pinned endpoint and
  dimension are fixed per index version and recorded. Vectors from different
  models or endpoints are never compared, so repointing the class means a new
  index version and a rebuild. (Different upstream deployments of "the same"
  model may be quantized differently, which is why the endpoint is pinned
  too.)
- **S6. Embedding is egress, and obeys the taint.** A no-egress source, or
  anything derived from one, is **never embedded**. It stays findable by
  full-text search, which runs inside the unit, and a semantic result set says
  how many matches it cannot include for that reason (S4). Queries are sent
  too, so a query built from tainted content is refused.

S1's "rebuildable" now has a price: a rebuild re-sends every indexed body and
is billed. It stays possible and becomes a deliberate, costed operation.

---

## 5. The host boundary on Linux (the risky part)

Each of these families is **verified today on Windows**. In the container
each is re-mechanized, and must be re-verified **from inside**, as I43
requires, before anything depends on it.

### 5.1 Sandbox (I32–I36, I41–I43)

AppContainer is replaced with a namespace jail per sandbox:
- **Recommended: nsjail.** It was built for exactly this: user, mount, PID
  and network namespaces, seccomp-bpf, cgroup limits and rlimits. No network
  namespace means **no network, in the kernel**, which is the property
  AppContainer gave.
- Alternatives: bubblewrap (smaller, fewer resource controls), or gVisor as
  the runtime for the whole unit (stronger isolation, but platform-dependent
  and heavier).
- **Spike first, in two environments: stock Docker and a Fly machine.**
  Under stock Docker the default seccomp profile often blocks the namespaces
  a jail needs, so the image ships a documented **runtime requirement**: the
  seccomp profile or capabilities to run with, plus a `docker run` and
  compose example. Fly machines are Firecracker microVMs, where this is
  normally possible, but that must be shown, not assumed. The spike's exit
  criterion: a sandbox inside the container cannot open a socket, cannot
  read `/var/lib/remoeba`, cannot see another sandbox, and cannot write its
  interpreter, each measured from inside.
- **A startup probe decides, and fails safe.** At start, the Harness
  attempts a jail and checks those four properties from inside it. If the
  runtime does not allow it, **sandboxed compute is disabled and reported as
  disabled**: work that needs it is refused with the reason, and health says
  why. It never falls back to running code unjailed. This is I30 ("fails
  safe, never locked") and I33 ("reported, not assumed") applied to the
  runtime the unit happens to be given.
- The interpreter is mounted read-only (I32). Only two file descriptors are
  passed into a sandbox (I35).

### 5.2 Hardening (I29–I33)

`icacls` is replaced by a dedicated unprivileged uid. The volume is owned by
it with `0700` directories and `umask 077`, the root filesystem is read-only,
capabilities are dropped, and there is no shell in the final image layer.

I33's honesty carries: the audit says what still has access (root in the
container, and the host or platform operator who can read the volume) instead
of claiming protection it doesn't have.

### 5.3 Filespace (I37–I40, decision 4)

- Roots become **volume mounts**.
- Links: resolve component by component with `O_NOFOLLOW` against directory
  descriptors. Python has no `openat2` wrapper; `RESOLVE_BENEATH` through a
  syscall shim is an option if the walk proves too slow.
- Hard links: `st_nlink` still detects them (I37d).
- **I37e changes meaning.** Linux is case-sensitive and has no 8.3 names, so
  the NTFS identity traps disappear. But a Windows directory bind-mounted
  through Docker Desktop is case-insensitive again. Test identity on the
  actual mount type, not on the kernel's default.
- **Egress (decision 4) is built here:** every root declares `egress`, with
  no default, and the taint follows derived results (R2).

### 5.4 Windows-native support

**Decided (P0-3): drop it.** Remoeba runs in its container. Development on this
machine happens through Docker Desktop. The AppContainer and `icacls` code
leaves the tree once its Linux replacements are verified, rather than staying
unwired.

---

## 6. A remote cognitive unit

| Concern | Plan |
|---|---|
| **External surface** | MCP over **Streamable HTTP** (stdio cannot cross a network) and the JSON-RPC API, behind TLS. Per-client API keys, where identity is the credential (I48c) and loopback was never authentication (I48d). |
| **Operator console** | Never public. It listens on the container's loopback only and is reached through a tunnel the operator provides: SSH, WireGuard or Tailscale generically, and `fly proxy` or Fly's private network on Fly. The operator session credential is still required (I48f). |
| **Provider credential** | A platform secret injected into the **inference service only**. `child_environment` still scrubs it from everything else (R1). |
| **Network egress** | R2 is enforced **in code**, wherever the unit runs: only the inference service holds the key, and only committed bodies are sent. A network-level allowlist (only OpenRouter) is a **second layer the deployment provides where it can**, since stock Docker has no per-container egress firewall without extra privileges. Health reports whether it is present, rather than assuming it (I33). Sandboxes have no network in any runtime, because they have no network namespace (§5.1). |
| **Durability** | Evidence is never pruned (I87), so the volume *is* the organism. Continuous WAL archiving (e.g. wal-g or pgBackRest) to **any S3-compatible store**, plus a blob-store backup, with a tested restore. Platform volume snapshots (Fly's daily snapshots, for example) are an extra layer, never the only one. |
| **Upgrades** | A new image plus migrations. The organism survives image replacement by design. |
| **Health** | A container health check on I25 and I28 (health answerable, supervision making passes), not merely on "the process is alive" (I126). |
| **Placement** | One container and one volume: a single-host unit, on any Docker host or on Fly. Multi-host would need the blob store in object storage and is out of scope. |
| **Fly support** | A `fly.toml`: one machine, one volume mounted at `/var/lib/remoeba`, secrets for the provider key and database passwords, the health check, and no public operator port. Plus notes on region, volume size, the WAL archive bucket, and that a Fly volume is tied to one host. Nothing in the image is Fly-specific. |

---

## 7. Sequencing

Each phase ends with its invariants **verified** (the tests fail with the
guarantee removed), not merely passing.

| Phase | Work | Exit criterion |
|---|---|---|
| **P0 — Confirm** | Answer the open questions (§8). Reinstate the invariant guard test. | Decisions recorded in PORTING.md |
| **P1 — Container baseline** | Dockerfile and dev compose. The suite runs in Linux, with the Windows-only host tests marked. **Sandbox spike** (§5.1). The verifier runs mutants in a throwaway copy. | The foundation suite is green in the container, and the spike answers "can a jail run in here, locally and on the target platform?" |
| **P2 — Postgres store** | psycopg 3, schema translation with local-model residue removed, M1 writer, M2 roles and grants, migrations, per-test databases, LISTEN/NOTIFY, advisory locks. | Store tests green on Postgres. I1–I8, I11, I84 and M1/M2 mutation-verified. Multi-process writer stress: N writers, chain intact, no lost updates, idempotent replays. |
| **P3 — Linux host boundary** | nsjail sandbox, POSIX hardening, Linux filespace with egress roots. Windows code removed. | I29–I43 re-verified from inside the container |
| **P4 — Semantic search** | pgvector + FTS, `embed` in the inference service, indexer queue, scoped search verbs | S1–S6 mutation-verified. Recall quality measured on a fixed query set against the `LIKE` baseline. |
| **P5 — Harness port** | Supervisor, mailbox, turns, roles, neuocytes, onto Postgres and Linux, once. The inference service commits its own call records. | The ported Amoeba tests and mutations are green. An organism answers a question end to end on OpenRouter. |
| **P6 — Remote unit** | Streamable HTTP MCP, TLS, loopback-only operator console, backups to S3-compatible storage, runtime requirements documented, `fly.toml` | **Move the volume from Docker on this machine to a Fly machine, restart, and resume in-flight work coherently.** The same image runs in both places. |

P5 is the bulk of the work. P1–P4 exist so that it happens once.

---

## 8. Decisions (2026-09-25)

| | Question | Decision |
|---|---|---|
| P0-1 | "Hosted locally" | **Self-hosted inside the unit.** The unit carries its own Postgres wherever it runs. |
| P0-2 | One image or compose | **One image** (Postgres + Harness under a small init) plus one volume. Compose only as a dev convenience. |
| P0-3 | Windows-native support | **Dropped** once the Linux host boundary is verified. The AppContainer and `icacls` code leaves the tree then, rather than staying unwired. |
| P0-4 | Cloud platform | **Generic Docker, with Fly supported** (revised the same day from "Fly"). The image is a plain OCI image and assumes nothing platform-specific. Fly is a supported deployment with its own `fly.toml`. Consequences: the sandbox spike must pass under stock Docker **and** on a Fly machine; a runtime that cannot jail gets sandboxing disabled and reported, never an unjailed fallback (§5.1); the network egress allowlist is a deployment-provided layer, not assumed (§6); backups go to any S3-compatible store. |
| P0-5 | Embedding source | **OpenRouter**, through the inference service. No embedding model in the image, no CPU budget for one. Embedding becomes egress (S6). |

Still open: **your API-capability ideas.** Structured outputs, prompt
caching, reasoning controls and the like mostly land in the inference service
and in E2/E8. They don't change P1–P4, but some may change P5's design, so
they come before P5.
