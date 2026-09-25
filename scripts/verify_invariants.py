"""Does each invariant's test actually fail when the invariant is broken?

A test that passes is weak evidence. A test that *fails when the guarantee is
removed* is the real thing. This applies targeted mutations -- each a minimal
edit that negates precisely one claim -- runs only the tests named for that
invariant, and requires them to fail.

A mutation that leaves its tests green is the finding: that test does not
express the claim, whatever its name says.

**One mutant per run.** An invariant declares a primary mutation and any
number of `also` mutations, and each is applied *on its own*. This used to
write all of them at once and run the tests a single time, which asks only
whether removing everything together broke something -- a question one lethal
mutant answers on behalf of every inert one beside it. Demonstrated on
2026-09-24: an `also` entry that rewrote `SCAN_MULTIPLE = 20` as the same
line with a comment after it was reported GOOD. So an invariant is reported
defended only when *every* mutant it declares was individually lethal, which
is what the `note` fields have always claimed.

A trial is lethal if at least one named test fails under it. Mutants
defending different halves of a claim therefore need not each break every
test, only their own.

Source is always restored, including on interrupt.

    .\\.venv\\Scripts\\python.exe scripts\\verify_invariants.py [--only I7,I8]
                                                       [--primaries-only]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable


@dataclass
class Mutation:
    invariant: str
    claim: str
    path: str
    old: str
    new: str
    tests: list[str]
    layer: str
    note: str = ""
    # Extra edits. A guarantee defended in depth cannot be negated by a single
    # change: removing one of two redundant checks leaves the other working,
    # and the tests correctly stay green. To ask "is this claim defended at
    # all?" every defence has to come out. Two shapes:
    #   (old, new)        -- another edit in this mutation's own file
    #   (path, old, new)  -- an edit in a different file
    # The second exists because a claim can be defended across modules -- the
    # external surface is defined by both an adapter allowlist and a credential
    # scope -- and a harness that could not express that would report SKIP,
    # which proves nothing while looking like success.
    also: list[tuple[str, ...]] = field(default_factory=list)
    # Trials that are *expected* to survive, keyed by trial ("primary",
    # "also[2]" -- without the file name the report appends),
    # each with the reason. A guarantee defended at two layers has mutants
    # that cannot be observed one at a time: removing either leaves the other
    # enforcing it, so the tests rightly pass and WEAK would be the wrong
    # word. Declaring it here is not a suppression -- the run requires such a
    # trial to survive, and reports a failure if it turns out to be lethal
    # after all, because then the reason is wrong and it is a real mutant.
    masked: dict[str, str] = field(default_factory=dict)


# Each mutation removes exactly one guarantee, at the layer that owns it.
MUTATIONS: list[Mutation] = [
    Mutation(
        "I1", "One writer: state, events and receipt commit atomically",
        "src/remoeba/store/writer.py",
        "        except BaseException:\n            try:\n                self.conn.rollback()",
        "        except BaseException:\n            try:\n                pass  # MUTANT: no rollback",
        ["test_crash_during_commit_leaves_no_half_applied_mutation"],
        layer="StateWriter (the transaction itself)",
    ),
    Mutation(
        "I2", "A correction supersedes; it never rewrites earlier evidence",
        "src/remoeba/store/memory_repo.py",
        '                m.sql(\n                    "UPDATE memory_items SET status = \'superseded\', updated_at = ?,"\n'
        '                    " state_version = ? WHERE memory_id = ?",',
        '                m.sql(\n                    "UPDATE memory_items SET claim = \'REWRITTEN\', status = \'superseded\', updated_at = ?,"\n'
        '                    " state_version = ? WHERE memory_id = ?",',
        ["test_correction_supersedes_and_preserves_contrary_evidence"],
        layer="MemoryRepo (where supersession is written)",
    ),
    Mutation(
        "I5", "The hash chain detects mutation",
        "src/remoeba/store/events.py",
        '        if expect != row["event_hash"]:',
        '        if False:  # MUTANT: the recomputed hash is not compared',
        ["test_a_broken_chain_link_is_detected_not_just_a_tampered_payload",
         "test_hash_chain_detects_tampering"],
        layer="events.verify_chain (the recomputed hash, which is what detects)",
        note="Two checks overlap here and only one of them is observable. "
             "`chain_hash` folds the predecessor into every event hash, so the "
             "recomputed comparison catches tampering *and* excision on its "
             "own; that is the one a test can negate. The explicit prev_hash "
             "comparison is its twin -- see the masked entry. The function's "
             "own docstring has said so all along, and this is that statement "
             "made checkable rather than merely written down.",
        masked={"also[1]": (
            "The `prev_hash` column comparison cannot be observed while the "
            "recomputed hash is checked. Excise an event and the next row "
            "fails both checks, at the same row, returning the same event id; "
            "tamper with a payload and only the recomputed hash can see it. "
            "There is no edit that one catches and the other misses, so no "
            "test can distinguish them. It is kept because it names the "
            "offending row one comparison earlier and would carry the "
            "guarantee alone if `chain_hash` ever stopped folding the "
            "predecessor in. Verified masked on 2026-09-24."), },
        also=[('        if row["prev_hash"] != prev:',
               '        if False:  # MUTANT: the linkage column is not compared')],
    ),
    Mutation(
        "I6", "An acknowledged mutation survives restart",
        "src/remoeba/store/db.py",
        'conn.execute("PRAGMA synchronous=FULL")',
        'conn.execute("PRAGMA synchronous=OFF")  # MUTANT',
        ["test_durability_pragma_is_set_where_durability_is_configured"],
        layer="Database (durability pragma)",
        note="a clean close/reopen cannot observe fsync, so the property is "
             "asserted where it is configured instead",
    ),
    Mutation(
        "I7", "Replaying a mutation id does not re-apply it",
        "src/remoeba/store/writer.py",
        "        existing = self.receipt_for(mutation_id)\n        if existing is not None:\n            return existing, None",
        "        existing = None  # MUTANT: idempotency disabled\n        if existing is not None:\n            return existing, None",
        ["test_writer_itself_refuses_to_reapply_a_mutation_id"],
        layer="StateWriter (the idempotency check)",
        note="the work-queue test has its own short-circuit and cannot see this",
    ),
    Mutation(
        "I11", "A finding pinned to an older state version is flagged",
        "src/remoeba/store/work_repo.py",
        "            if pinned_state_ver is not None and pinned_state_ver < m.prior_version:\n"
        "                stale_against = {\"pinned\": pinned_state_ver, \"current\": m.prior_version}",
        "            if False:\n"
        "                stale_against = {\"pinned\": pinned_state_ver, \"current\": m.prior_version}",
        ["test_findings_pinned_to_an_older_state_version_are_flagged"],
        layer="WorkRepo.complete (where staleness is recorded)",
    ),
    Mutation(
        "SANDBOX", "A path escaping the sandbox root is rejected",
        "src/remoeba/sandbox.py",
        "        if target != root and root not in target.parents:\n"
        "            raise InvalidInput(\"path escapes the sandbox root\", path=relpath)",
        "        if False:\n"
        "            raise InvalidInput(\"path escapes the sandbox root\", path=relpath)",
        ["test_path_traversal_is_rejected"],
        layer="SandboxManager.resolve_inside (the path check)",
    ),
    Mutation(
        "I29", "Hardening severs inheritance, so a parent cannot re-grant",
        "src/remoeba/security.py",
        '    cmd = [str(path), "/inheritance:r"]',
        '    cmd = [str(path)]  # MUTANT: inheritance left live',
        ["test_hardening_removes_every_ace_for_everyone",
         "test_hardening_removes_inheritance_so_the_parent_cannot_regrant",
         "test_children_lose_the_permissive_access_they_inherited"],
        layer="security.harden (the icacls invocation itself)",
        note="the grants still apply; only the severing is removed. If the "
             "tests stay green they are asserting our return value rather "
             "than the DACL the OS reports.",
    ),
    Mutation(
        "I30", "Hardening that would lock the account out is rolled back",
        "src/remoeba/security.py",
        '        _icacls([str(path), "/reset", "/Q"])',
        '        pass  # MUTANT: no rollback; directory left unopenable',
        ["test_hardening_rolls_back_rather_than_locking_the_account_out"],
        layer="security.harden (the post-hardening self-check)",
    ),
    Mutation(
        "I30b", "Hardening is a single atomic icacls call",
        "src/remoeba/security.py",
        "    r = _icacls(cmd)",
        '    _icacls([str(path), "/inheritance:r", "/Q"])  # MUTANT: two-step\n'
        "    r = _icacls([c for c in cmd if c != \"/inheritance:r\"])",
        ["test_hardening_is_one_atomic_icacls_call"],
        layer="security.harden (call structure)",
        note="reproduces the two-step form that locked the state tree out "
             "when the second call failed. The end state is identical, so "
             "only a test counting calls can catch it.",
    ),
    Mutation(
        "I31", "Every state directory is hardened, not just the root",
        "src/remoeba/security.py",
        "    for t in targets:\n        results.append(harden(t, log_name=log_name))",
        "    for t in targets[:1]:  # MUTANT: root only, trust inheritance\n"
        "        results.append(harden(t, log_name=log_name))",
        ["test_every_state_directory_is_hardened_including_ones_outside_the_root"],
        layer="security.harden_state_tree (which directories are covered)",
    ),
    Mutation(
        "I32", "A destroyed container loses its grant on the shared runtime",
        "src/remoeba/sandbox.py",
        '            revoke(self.runtime_dir, sb.container_sid, log_name="sandbox")',
        "            pass  # MUTANT: stale grant left on the runtime",
        ["test_destroying_a_sandbox_revokes_its_grant_on_the_shared_runtime"],
        layer="SandboxManager.destroy (teardown of the shared grant)",
    ),
    Mutation(
        "I32b", "Sandboxed code gets execute, not write, on its runtime",
        "src/remoeba/sandbox.py",
        '               container_rights="(OI)(CI)(RX)", log_name="sandbox")',
        '               container_rights="(OI)(CI)(F)", log_name="sandbox")',
        ["test_sandboxed_code_cannot_modify_its_own_runtime"],
        layer="SandboxManager.create (the rights granted to the container)",
        note="the injection path that persists across sandboxes. Asserted "
             "from inside the container, so the kernel decides, not the ACL "
             "string we wrote.",
    ),
    Mutation(
        "I33", "The audit states residual exposure rather than claiming safety",
        "src/remoeba/security.py",
        '        "owner_can_restore_access": True,',
        '        "owner_can_restore_access": False,  # MUTANT: overclaim',
        ["test_the_audit_does_not_claim_protection_it_does_not_have"],
        layer="security.audit_path (the report itself)",
        note="honesty is a guarantee here like any other. The failure mode "
             "this defends against is the report quietly becoming an "
             "assertion of safety once the hardening starts working.",
    ),
    Mutation(
        "I35", "Concurrent sandboxes inherit no handles from each other",
        "src/remoeba/sandbox.py",
        "            if not k32.UpdateProcThreadAttribute(\n"
        "                    attrs, 0, ctypes.c_size_t(PROC_THREAD_ATTRIBUTE_HANDLE_LIST),",
        "            if False and k32.UpdateProcThreadAttribute(\n"
        "                    attrs, 0, ctypes.c_size_t(PROC_THREAD_ATTRIBUTE_HANDLE_LIST),",
        ["test_a_sandbox_does_not_inherit_another_sandboxs_handles"],
        layer="_spawn (the process-creation attribute list)",
        note="restores unrestricted handle inheritance. Every ACL test still "
             "passes with this hole open, which is the point: it is a "
             "different guarantee and needs its own test.",
    ),
    Mutation(
        "I36", "Sandbox runs are not serialised by a manager-wide lock",
        "src/remoeba/sandbox.py",
        "        return self._spawn(sb, cmd, timeout=timeout)",
        "        with self._lock:  # MUTANT: serialise every run\n"
        "            return self._spawn(sb, cmd, timeout=timeout)",
        ["test_sandboxes_run_concurrently_rather_than_serialised"],
        layer="run_python (whether runs hold the manager lock)",
        note="negated by *adding* a defence rather than removing one. The "
             "claim is the absence of serialisation, so the mutation has to "
             "introduce it.",
    ),
    Mutation(
        "I37", "A path outside its root is refused, not clamped into it",
        "src/remoeba/filespace.py",
        "        if final != root_path and root_path not in final.parents:",
        "        if False:  # MUTANT: containment check removed",
        ["test_paths_that_leave_the_root_or_name_a_device_are_refused",
         "test_a_refused_path_is_never_silently_clamped",
         "test_a_junction_pointing_out_of_the_root_is_refused",
         "test_listing_does_not_walk_through_a_junction"],
        layer="Filespace.resolve (the containment check)",
        note="the component checks catch the obvious `..` cases on their own, "
             "so this also removes them -- otherwise the claim looks defended "
             "while a resolved junction walks straight out.",
        also=[("        for part in parts:\n            _reject_component(part)",
               "        for part in parts:\n            pass  # MUTANT")],
    ),
    Mutation(
        "I37b", "A link is not a way out of a root",
        "src/remoeba/filespace.py",
        "            final = candidate.resolve()",
        "            final = candidate.absolute()  # MUTANT: links not followed",
        ["test_a_junction_pointing_out_of_the_root_is_refused"],
        layer="Filespace.resolve (full resolution before the check)",
        note="without resolving, the containment check compares a path that "
             "still looks inside the root while pointing elsewhere.",
    ),
    Mutation(
        "I37c", "A read-only root refuses writes",
        "src/remoeba/filespace.py",
        '        if need_write and root.mode != "read_write":',
        "        if False:  # MUTANT: mode ignored",
        ["test_a_read_only_root_refuses_writes"],
        layer="Filespace.resolve (the mode check)",
        note="write_bytes has its own check, so this also removes that one; "
             "the claim is defended in both places deliberately.",
        also=[('        if not resolved.writable:\n'
               '            raise FilespaceDenied("this root is read-only", root=resolved.root_name)\n'
               '        resolved.path.parent.mkdir(parents=True, exist_ok=True)',
               '        resolved.path.parent.mkdir(parents=True, exist_ok=True)')]),
    Mutation(
        "I37d", "A hard link is not a way to reach a file outside a root",
        "src/remoeba/filespace.py",
        "        if exists and not self.cfg.allow_multiply_linked:\n"
        "            links = _link_count(final)\n"
        "            if links > 1:",
        "        if False:\n"
        "            links = _link_count(final)\n"
        "            if links > 1:",
        ["test_a_hard_link_into_the_root_cannot_be_used_to_read_outside_it"],
        layer="Filespace.resolve (the link-count check)",
        note="the leak this was found by. Path containment passes and is "
             "correct about the path; only the file's link count says the "
             "record has another name the root does not cover.",
    ),
    Mutation(
        "I84", "An existing database gains the columns a release adds",
        "src/remoeba/store/db.py",
        """        self._migrate_turn_ownership()
        self.conn.executescript(SCHEMA_SQL)""",
        """        self.conn.executescript(SCHEMA_SQL)  # MUTANT: no migration""",
        ["test_a_database_from_the_previous_release_gains_the_ownership_columns"],
        layer="Database.initialize (upgrading a database written by an older release)",
        note="CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so without this the organism starts, heartbeats, and fails on its first mailbox read.",
    ),
]


def run_tests(names: list[str]) -> tuple[bool, str]:
    expr = " or ".join(names)
    r = subprocess.run(
        [PY, "-m", "pytest", "tests", "-q", "-p", "no:randomly", "-x",
         "--timeout=300", "-k", expr],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    return r.returncode == 0, (r.stdout or "")[-400:]


def trials_for(m: "Mutation") -> list[tuple[str, dict[str, tuple[str, str]]]]:
    """Every mutant this invariant declares, each on its own.

    One trial per mutant rather than one trial per invariant. Applying them
    together only ever asked whether removing *all* of them broke something,
    which a single lethal mutant answers on behalf of every inert one beside
    it -- and the notes claim each was separately verified.
    """
    out = [("primary", {m.path: (m.old, m.new)})]
    for i, entry in enumerate(m.also, start=1):
        if len(entry) == 3:
            rel, old, new = entry
        else:
            rel, (old, new) = m.path, entry
        out.append((f"also[{i}] {rel}", {rel: (old, new)}))
    return out


def apply_trial(edits: dict[str, tuple[str, str]]) -> dict[str, str] | str:
    """Write one mutant. Returns the originals to restore, or why it could not."""
    originals: dict[str, str] = {}
    for rel, (old, new) in edits.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        seen = text.count(old)
        if seen != 1:
            for done, orig in originals.items():
                (ROOT / done).write_text(orig, encoding="utf-8")
            if seen == 0:
                return f"anchor not found in {rel}"
            # `replace(old, new, 1)` would edit whichever came first, which is
            # not necessarily the one the claim is about. I133's primary
            # matched two byte-identical INSERTs and mutated `io_attach_input`
            # while its tests were about `io_submit`: the guarantee was
            # reported defended by a mutation that never touched it.
            return (f"anchor matches {seen} places in {rel}; it must name one")
        originals[rel] = text
        (ROOT / rel).write_text(text.replace(old, new, 1), encoding="utf-8")
    return originals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated invariant ids")
    ap.add_argument("--primaries-only", action="store_true",
                    help="skip the secondary mutants (faster, and says less)")
    ap.add_argument("--no-baseline", action="store_true",
                    help="do not check the named tests pass before mutating "
                         "(faster, and cannot tell a lethal mutant from a "
                         "test that was already failing)")
    args = ap.parse_args()
    wanted = {x.strip() for x in args.only.split(",") if x.strip()}
    muts = [m for m in MUTATIONS if not wanted or m.invariant in wanted]
    missing = wanted - {m.invariant for m in MUTATIONS}
    if missing:
        print(f"no such invariant: {', '.join(sorted(missing))}")
        return 2
    if not muts:
        print("nothing to verify")
        return 2

    backup = Path(tempfile.mkdtemp(prefix="inv_backup_"))
    touched = {m.path for m in muts}
    for m in muts:
        for entry in m.also:
            if len(entry) == 3:
                touched.add(entry[0])
    for rel in touched:
        shutil.copy2(ROOT / rel, backup / rel.replace("/", "__"))

    results = []          # (mutation, label, verdict, detail)
    try:
        for m in muts:
            # Green before red. Without this, a test that was already failing
            # fails under every mutant and reports each as lethal.
            if not args.no_baseline:
                passed, tail = run_tests(m.tests)
                if not passed:
                    results.append((m, "baseline", "BASELINE", tail))
                    print(f"BASE {m.invariant:8s} its tests do not pass "
                          f"unmutated -> {m.tests}")
                    continue
            trials = trials_for(m)
            if args.primaries_only:
                trials = trials[:1]
            for label, edits in trials:
                originals = apply_trial(edits)
                if isinstance(originals, str):
                    results.append((m, label, "SKIP", originals))
                    print(f"SKIP {m.invariant:8s} {label}: {originals}")
                    continue
                try:
                    survived, tail = run_tests(m.tests)
                finally:
                    for rel, orig in originals.items():
                        (ROOT / rel).write_text(orig, encoding="utf-8")
                # The label carries the file for readability ("also[1] a.py");
                # a declaration names the trial ("also[1]").
                excuse = m.masked.get(label.split(" ", 1)[0])
                if survived and excuse:
                    results.append((m, label, "MASKED", excuse))
                    print(f"MASK {m.invariant:8s} {label}: survives as declared "
                          f"-- {excuse}")
                elif survived:
                    results.append((m, label, "WEAK", tail))
                    print(f"WEAK {m.invariant:8s} {label}: tests PASSED with the "
                          f"guarantee removed -> {m.tests}")
                elif excuse:
                    # Declared unobservable and observed. The declaration is
                    # the thing that is wrong.
                    results.append((m, label, "MISDECLARED", excuse))
                    print(f"BAD! {m.invariant:8s} {label}: declared masked but "
                          f"its tests FAIL without it -- it is a real mutant")
                else:
                    results.append((m, label, "GOOD", ""))
                    print(f"GOOD {m.invariant:8s} {label}")
    finally:
        for rel in touched:
            shutil.copy2(backup / rel.replace("/", "__"), ROOT / rel)
        shutil.rmtree(backup, ignore_errors=True)
        print("\nsource restored")

    weak = [r for r in results if r[2] == "WEAK"]
    skipped = [r for r in results if r[2] == "SKIP"]
    masked = [r for r in results if r[2] == "MASKED"]
    misdeclared = [r for r in results if r[2] == "MISDECLARED"]
    unbaselined = [r for r in results if r[2] == "BASELINE"]
    # An invariant is defended when every mutant it declares was lethal, or
    # was declared unobservable and behaved that way.
    bad = {r[0].invariant for r in results if r[2] not in ("GOOD", "MASKED")}
    for m, _label, _v, _d in unbaselined:
        bad.add(m.invariant)
    defended = {m.invariant for m in muts} - bad

    print(f"\n{'='*70}")
    print(f"{len(results)} mutants run: {len(defended)} invariants fully defended, "
          f"{len(weak)} WEAK mutant(s), {len(masked)} masked as declared, "
          f"{len(misdeclared)} misdeclared, {len(unbaselined)} with failing "
          f"tests, {len(skipped)} skipped")
    for m, _label, _v, _d in unbaselined:
        print(f"  BASELINE {m.invariant}: its named tests do not pass "
              f"before any mutation -- nothing it reports would mean anything")
    for m, label, _, why in misdeclared:
        print(f"  MISDECLARED {m.invariant} [{label}]: it is lethal, so this "
              f"is not masked: {why}")
    for m, label, _, tail in weak:
        print(f"  WEAK {m.invariant} [{label}]: {m.claim}")
        print(f"       layer: {m.layer}")
    for m, label, _, why in skipped:
        print(f"  SKIP {m.invariant} [{label}]: {why}")
    return 1 if weak or skipped or misdeclared or unbaselined else 0


if __name__ == "__main__":
    raise SystemExit(main())
