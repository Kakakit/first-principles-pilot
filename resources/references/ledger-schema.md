# Ledger schema

`run.json` is the intent source of truth. `ledger.jsonl` is the execution source of truth. `state.json` and reports are derived.

Each ledger line is a JSON object with:

- `event_id`: caller-provided or generated idempotency key.
- `timestamp`: UTC ISO-8601 timestamp.
- `run_id`
- `stage_id`
- `attempt_id`
- `type`
- `payload`

The v1.3 normal `hpc-submit.v1` event records the compact outcome of one
explicit submit call after input audit, final live preflight, qsub, and exact
qstat. The old `preview.operation`/authorization/receipt model remains
readable for `hpc-batch.v1` compatibility calls; the normal path does not
repeat those ceremonies.

Authorization payloads contain `operation`, `target`, `input_sha256`, and `expires_at`. Receipts repeat those fields and link the authorization event. A receipt is valid only when all fields match and the authorization was unexpired when the operation occurred.

Scheduler/resource receipts additionally record `host`, `scheduler`, `selected_node`, `selected_node_available_cores`, `requested_cores`, `request_spec`, `resource_policy`, `exception_type`, `exception_reason`, `exception_evidence`, and node-level evidence. Full-available-core is the default. A lower request is valid only for an evidenced `scheduler_admin_cap` or `user_override`.

Calculation terminal/audit payloads keep these fields distinct:

- `automation_status`: status of the automation/parser/audit chain;
- `calculation_status`: `SUCCEEDED`, `INCOMPLETE`, or `FAILED` from calculation evidence;
- `program_status`: parser status, `COMPLETE`, `INCOMPLETE`, or `FAILED`;
- `decision`: `PROMOTE`, `REVISE`, or `STOP` after artifact and scientific gates.

For PBS, a job-bound walltime artifact may add `termination_reason=PBS_WALLTIME_EXCEEDED`, `walltime.used`, `walltime.limit`, `walltime.used_seconds`, and `walltime.limit_seconds`. A standalone audit reports `audit_status=SUCCEEDED` when local auditing completed; only the receipt/ledger aggregation layer may report workflow-wide `automation_status=SUCCEEDED`. Neither status proves VASP completion.

Scheduler evidence uses `fpilot.job-artifact.v1`: exact `job_id`, `scheduler`, `receipt_id`, `receipt_sha256`, `run_id`, `stage_id`, `attempt_id`, `authorization_id`, `source_path`, `source_sha256`, `wrapper_sha256`, `collected_at`, `collection_command`, and `text`. The parser reports `wrapper_integrity_verified`, `receipt_fields_matched`, and `evidence_package_verified`; an evidence package proves only that the local package is self-consistent with the supplied receipt. It does not prove that a remote command actually ran. `collection_command` is a declared collection field, not remote attestation.

When a stage uses an extension, ledger/receipt payloads record the validated extension `name`, `version`, directory `hash`, selected `entrypoint`, `dependency_check`, and `authorization_route`, alongside its input/output contracts and declared side effects. Remote writes and scheduler submission use the `hpc-job` route; the normal v1.3 submit call is the explicit operation authorization, while the old preview/authorization route remains for compatibility.

Batch authorization is a pure bookkeeping transaction for `stage` or `qsub` only. Its parent payload records `batch_id`, the canonical `manifest_sha256`, operation, expiry, and every explicit item. Each `batch.operation.receipt` repeats and binds `batch_id`, `manifest_sha256`, `item_id`, operation, target, and `input_sha256`; unlisted, duplicate, replayed, expired, or cross-operation items are rejected. Cancellation, overwrite, retry, and promotion remain separate authorizations.

`state.json` is rebuilt from the ledger and is compact: it keeps event-type
counts, `last_event_id`, stage/attempt status summaries, receipt and batch
indexes, DAG eligibility, and the cache fingerprint. It does not copy full
events; `ledger.jsonl` remains the complete history and old ledger lines stay
readable. The cache fingerprint contains only stable host/software/MPI/compiler/
Python/extension/input-hash/reviewer evidence. Node `CPUS_FREE`, queue, remote
directory, qstat, and job ID are excluded from cache-hit decisions.
