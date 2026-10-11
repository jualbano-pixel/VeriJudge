# Unresolved specification discrepancies

These notes distinguish unresolved discrepancies from explicit user resolutions.

1. **RESOLVED by user, VJ-003, route C plus primary support.** The canonical
   pseudocode incorrectly made `contra_C_ok` depend on `len(S_prim) == 0`.
   The user explicitly resolved this in favor of the acceptance test:
   eligibility depends on the direct primary-record evidence, injected
   `(record_key, claim_type)`, covering period, and independence alone.
   Licensed support plus eligible route C is CONFLICTING under R5; eligible
   route C without licensed support is CONTRADICTED, route C. Production
   `CLOSURE_ELIGIBLE` remains empty. The archived specification is preserved;
   this records the user's authoritative correction.
2. **RESOLVED by user, VJ-008, lifecycle finalization.** The blanket sentence
   means freeze after lifecycle finalization. Run identity, proposition, arm,
   version, model, config and started_at are fixed at creation. While running,
   only status, stop_reason, failure_reason, rejected_evidence_count, token_in,
   token_out, cost_usd and finished_at may change. Terminal transitions are
   running to done/failed/timeout; terminal runs are entirely frozen. Startup
   reaping uses failed/orphaned with finished_at. Planned retrieval inputs are
   fixed at insertion; results_json/result_count finalize once, then freeze.
   Migration 003 enforces this without new fields: result_count=-1 denotes an
   unfinalized planned step internally, while its results_json is []. Actual
   zero-result finalization writes []/0 and cannot be repeated. The user
   explicitly resolved the inconsistency in favor of lifecycle requirements;
   the archived original specification remains unchanged.
3. **VJ-008, search budget expectation.** The infinite-candidate acceptance
   test requests exactly eight steps. The stopping rule permits stopping at
   ten successful captures or fifteen attempts, with up to three attempts
   per step; these limits can be reached before step eight.
4. **VJ-014, G13 unresolved high-risk findings.** G13 expects PENDING_REVIEW
   with provisional NOT_ESTABLISHED as one possibility; R9 explicitly
   exempts NOT_ESTABLISHED. The explicit user instruction also exempts it.
5. **VJ-002, database locator enforcement (implementation note).** Acceptance asks for excerpt
   equality with a document's text to be rejected by DB CHECKs. PostgreSQL
   CHECKs cannot directly query another table. The listed schema instead
   names this an application invariant. Database enforcement would require
   a trigger, not the listed CHECK alone. Migration 002 uses a PostgreSQL
   constraint trigger raising `check_violation` (23514), enforcing the
   stated invariant without adding domain tables or columns. The pure app
   validator independently enforces the same paragraph/excerpt invariants.

The VJ-001 `/health` route is explicitly listed in that ticket even though
the API section lists six application endpoints. It is implemented as the
specified operator-only acceptance route, with no public API docs routes.
