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
    # -- remote inference (docs/INVARIANTS.md, R-invariants) -----------------
    Mutation(
        "R-PIN", "A call runs on the pinned endpoint or not at all",
        "src/remoeba/inference/wire.py",
        '        "allow_fallbacks": False,',
        '        "allow_fallbacks": True,  # MUTANT: fallbacks on',
        ["test_the_body_pins_one_endpoint_with_fallbacks_off"],
        layer="wire.provider_routing (where the routing block is written)",
    ),
    Mutation(
        "R-REQPARAMS", "Every call requires the endpoint to support its parameters",
        "src/remoeba/inference/wire.py",
        '        "require_parameters": True,',
        '        "require_parameters": False,  # MUTANT: parameters may be ignored',
        ["test_every_call_requires_its_parameters"],
        layer="wire.provider_routing",
    ),
    Mutation(
        "R2-ROUTE", "A body carries the class's data-collection setting",
        "src/remoeba/inference/wire.py",
        '        "data_collection": binding.data_collection,',
        '        "data_collection": "allow",  # MUTANT: collection always allowed',
        ["test_data_collection_is_denied_unless_the_class_says_otherwise"],
        layer="wire.provider_routing",
    ),
    Mutation(
        "R2-DEFAULT", "Data collection is denied unless a class states otherwise",
        "src/remoeba/config.py",
        '    data_collection: str = "deny"   # "allow" only when stated here',
        '    data_collection: str = "allow"  # MUTANT: permissive default',
        ["test_a_model_class_loads_with_a_model_and_a_pinned_endpoint"],
        layer="ModelClassConfig (the default an operator gets by saying nothing)",
    ),
    Mutation(
        "R-SENDCHECK", "A body that is not this class's pin is refused at send time",
        "src/remoeba/inference/wire.py",
        '    if body.get("provider") != provider_routing(binding):',
        '    if False:  # MUTANT: routing not checked at send',
        ["test_a_body_with_fallbacks_on_is_refused_at_send",
         "test_a_body_that_does_not_require_its_parameters_is_refused_at_send"],
        layer="wire.check_body (run by the service on whatever arrives)",
    ),
    Mutation(
        "R2-DIGEST", "Only the body whose digest was committed is sent",
        "src/remoeba/inference/service.py",
        '        if wire.digest(data) != sha256:',
        '        if False:  # MUTANT: digest not compared',
        ["test_a_body_that_is_not_the_committed_one_is_never_sent"],
        layer="InferenceService.send (the last point before bytes leave)",
    ),
    Mutation(
        "R7-PINNED", "Capabilities are read from the pinned endpoint, never another",
        "src/remoeba/inference/wire.py",
        '        if isinstance(ep, dict) and ep.get("tag") == endpoint:',
        '        if isinstance(ep, dict):  # MUTANT: first endpoint of the model',
        ["test_capabilities_come_from_the_pinned_endpoint_not_the_model",
         "test_a_class_whose_endpoint_is_not_offered_refuses_to_start"],
        layer="wire.endpoint_capabilities",
    ),
    Mutation(
        "R7-PARAMS", "A parameter the pinned endpoint lacks is refused before sending",
        "src/remoeba/inference/wire.py",
        '    missing = sorted(p for p in wanted if p not in caps.supported_parameters)',
        '    missing = []  # MUTANT: capabilities not checked',
        ["test_capabilities_come_from_the_pinned_endpoint_not_the_model"],
        layer="wire.check_capabilities",
    ),
    Mutation(
        "R7-TOOLS", "A class whose endpoint lacks native tool calling refuses to start",
        "src/remoeba/inference/wire.py",
        '    if "tools" not in caps.supported_parameters:',
        '    if False:  # MUTANT: tools not required',
        ["test_a_class_whose_endpoint_lacks_tools_refuses_to_start"],
        layer="wire.require_tools (called for every class at service start)",
    ),
    Mutation(
        "I110-REMOTE", "A ceiling above the endpoint's maximum is refused, not clamped",
        "src/remoeba/inference/wire.py",
        '    if cap is not None and body["max_tokens"] > cap:',
        '    if False:  # MUTANT: over-ceiling request sent anyway',
        ["test_a_ceiling_above_the_endpoint_maximum_is_refused_not_clamped"],
        layer="wire.check_capabilities",
    ),
    Mutation(
        "MODELVARS-REMOTE", "An unknown setting is refused, never dropped",
        "src/remoeba/inference/wire.py",
        '    checked = validate_model_vars(dict(settings or {}))',
        '    checked = {k: v for k, v in (settings or {}).items() if k in BACKEND_ARGUMENT}  # MUTANT: drop unknown',
        ["test_an_unknown_setting_is_refused_not_dropped"],
        layer="wire.build_body (where profile settings become wire parameters)",
    ),
    Mutation(
        "I66-402", "Credit exhaustion is its own outcome",
        "src/remoeba/inference/wire.py",
        '        kind = Outcome.CREDITS_EXHAUSTED',
        '        kind = Outcome.PROVIDER_UNAVAILABLE  # MUTANT: collapsed',
        ["test_credit_exhaustion_is_its_own_outcome"],
        layer="wire.classify_http",
    ),
    Mutation(
        "I66-ERRFINISH", "A provider error finish is never a model stop",
        "src/remoeba/inference/wire.py",
        '    "error": Outcome.PROVIDER_ERROR,',
        '    "error": Outcome.MODEL_STOP,  # MUTANT: error read as a stop',
        ["test_a_provider_error_finish_is_not_a_model_stop"],
        layer="wire._FINISH",
    ),
    Mutation(
        "R9", "A provider refusal is its own outcome",
        "src/remoeba/inference/wire.py",
        '        kind = Outcome.REFUSED',
        '        kind = Outcome.MODEL_STOP  # MUTANT: refusal read as an answer',
        ["test_a_refusal_is_its_own_outcome"],
        layer="wire._classify_completion",
    ),
    Mutation(
        "I135-PRESSURE", "Pressure decided by wording says it was decided that way",
        "src/remoeba/inference/wire.py",
        '        detail["decided_by"] = "error message wording"',
        '        pass  # MUTANT: the basis is not stated',
        ["test_context_overflow_is_pressure_and_says_how_it_was_decided"],
        layer="wire.classify_http",
    ),
    Mutation(
        "R4-UNPRICED", "A missing cost is unpriced, never zero",
        "src/remoeba/inference/wire.py",
        '        "cost": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,',
        '        "cost": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,  # MUTANT: free',
        ["test_a_missing_cost_is_unpriced_not_free"],
        layer="wire._classify_completion",
    ),
    Mutation(
        "R5-RETRYABLE", "Only rate limits and unavailability are retried",
        "src/remoeba/inference/wire.py",
        '    RETRYABLE = frozenset({RATE_LIMITED, PROVIDER_UNAVAILABLE})',
        '    RETRYABLE = frozenset({RATE_LIMITED, PROVIDER_UNAVAILABLE, INVALID_REQUEST})  # MUTANT',
        ["test_non_retryable_failures_are_not_retried"],
        layer="wire.Outcome",
    ),
    Mutation(
        "R5-RETRYAFTER", "A provider's Retry-After is honoured within the bound, never beyond",
        "src/remoeba/inference/service.py",
        '            return asked if asked <= self.cfg.max_retry_wait_seconds else None',
        '            return self.cfg.retry_base_seconds  # MUTANT: Retry-After ignored',
        ["test_retry_after_is_honoured", "test_a_retry_after_beyond_the_bound_is_not_waited_for"],
        layer="InferenceService._wait_for",
    ),
    Mutation(
        "R5-BOUND", "Retries stop at the limit, with no wait after the last attempt",
        "src/remoeba/inference/service.py",
        '            if outcome["kind"] not in wire.Outcome.RETRYABLE or n == self.cfg.max_retries:',
        '            if outcome["kind"] not in wire.Outcome.RETRYABLE:  # MUTANT: no stop at the limit',
        ["test_retries_stop_at_the_limit"],
        layer="InferenceService.send (the retry loop)",
        also=[('        for n in range(self.cfg.max_retries + 1):',
               '        for n in range(self.cfg.max_retries + 5):  # MUTANT: loop runs long')],
        masked={"also[1]": "The loop's range and the in-loop limit check each bound the "
                           "attempts. Widening the range alone is caught by the in-loop "
                           "check at the same attempt, so no test can see it; it is kept "
                           "because the range is what bounds the loop if the check is "
                           "ever edited."},
    ),
    Mutation(
        "R1-ENV", "No child process inherits the credential, under any name",
        "src/remoeba/inference/credentials.py",
        '        for name in [n for n, v in env.items() if v.strip() == secret]:',
        '        for name in []:  # MUTANT: copies of the key survive',
        ["test_the_credential_is_not_in_a_childs_environment"],
        layer="credentials.child_environment",
        also=[('    env.pop(inference.api_key_env, None)',
               '    pass  # MUTANT: the named variable survives')],
        masked={"also[1]": "The value scan removes every variable holding the key, "
                           "including the configured one, so removing the named pop "
                           "alone changes nothing observable. The pop stays because it "
                           "removes the variable even when it holds an empty value."},
    ),
    Mutation(
        "R1-RETURN", "The credential never appears in anything the service returns",
        "src/remoeba/inference/service.py",
        '        return redact(report, self._secret)',
        '        return report  # MUTANT: not redacted',
        ["test_the_credential_never_appears_in_what_the_service_returns"],
        layer="InferenceService.send",
    ),
    Mutation(
        "R3-UNCONFIRMED", "The serving provider is unconfirmed until the record says",
        "src/remoeba/inference/service.py",
        '            "served_by": {"status": "unconfirmed"},',
        '            "served_by": {"status": "confirmed", "provider_name": binding.endpoint},  # MUTANT: inferred from the pin',
        ["test_the_serving_provider_is_unconfirmed_until_the_record_says"],
        layer="InferenceService.send",
    ),
    Mutation(
        "R3-MATCH", "A different serving provider is reported as not matching the pin",
        "src/remoeba/inference/service.py",
        '            "matches_pin": served == caps.provider_name,',
        '            "matches_pin": True,  # MUTANT: always matches',
        ["test_a_different_serving_provider_does_not_match_the_pin"],
        layer="InferenceService.confirm_served",
    ),
    Mutation(
        "DECISION2", "A model class without a pinned endpoint is refused",
        "src/remoeba/config.py",
        '    for key in ("model", "endpoint"):',
        '    for key in ("model",):  # MUTANT: endpoint optional',
        ["test_a_model_class_without_a_model_or_endpoint_is_refused"],
        layer="config._model_class",
    ),
    # -- defects fixed in carried code ---------------------------------------
    Mutation(
        "RPC-HANDSHAKE", "A refused handshake is an RpcError, never a TypeError",
        "src/remoeba/rpc.py",
        '                    raise _remote_error(\n                        "control handshake rejected",',
        '                    raise RpcError("control handshake rejected", **err)  # MUTANT: remote names let in\n                    raise _remote_error(\n                        "control handshake rejected",',
        ["test_a_wrong_token_is_refused_as_a_refusal"],
        layer="RpcClient.connect",
    ),
    Mutation(
        "RPC-RELAY", "A refusal relayed across hops keeps every hop's code",
        "src/remoeba/rpc.py",
        '            details[renamed] = details.pop(name)',
        '            pass  # MUTANT: reserved names not renamed',
        ["test_a_refusal_relayed_across_two_hops_arrives_as_a_refusal"],
        layer="rpc._remote_error (the one place a peer's error becomes ours)",
    ),
    Mutation(
        "MODELVARS-STOPS", "Too many stop sequences are refused, never truncated",
        "src/remoeba/promptlib/model.py",
        '            if len(value) > MAX_STOP_SEQUENCES:',
        '            if False:  # MUTANT: over-long list accepted',
        ["test_too_many_stop_sequences_are_refused_not_truncated"],
        layer="promptlib.model.validate_model_vars",
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
