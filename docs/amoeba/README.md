# Amoeba's documentation, frozen

These are Amoeba's documents, copied verbatim from Amoeba at commit `371fd64`
(plus its staged changes as of 2026-09-25). They describe the **local-model
ancestor** — one resident llama.cpp model, a unified KV pool, token-level
sessions — and are **not** true of Remoeba as written.

They are kept because they are the specification the Harness behaviour was
built to meet, and because `ARCHITECTURE.md` holds the full reasoning, and the
live failure, behind every invariant. [../INVARIANTS.md](../INVARIANTS.md)
says which of those invariants carry over, change, or no longer apply.

Do not edit these files to describe Remoeba. Write Remoeba's own documents one
directory up, as each part is ported.
