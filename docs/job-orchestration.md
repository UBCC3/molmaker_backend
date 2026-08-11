# Job Orchestration

This document explains the calculation-job lifecycle. For endpoint schemas, use
Swagger. For the surrounding backend components, see
[Backend Data Flow](backend-data-flow.md).

## Overview

- Calculation creation stages `input.xyz` and optional `keywords.json`, commits
  a job in `submitting`, and returns `201 Created` without waiting for Alliance.
- Clients use only the application `job_id`. Slurm identifiers, retry counters,
  `terminal_status`, and upload bookkeeping remain internal.
- PostgreSQL is the durable hand-off between the API and three independently
  supervised reconciler processes.

![Database-backed job orchestration](diagrams/job-orchestration.svg)

*Figure 1. The API and three reconcilers use the saved job state in PostgreSQL
to hand work from one stage to the next. Each worker can resume after a restart.*

File transfer and Slurm submission are separate operations. The submission
worker first copies the staged directory to Alliance with `scp`; only after that
succeeds does `dispatch.py submit` run `sbatch --parsable`.

## Job States

| Internal status | Public status | Meaning |
|---|---|---|
| `submitting` | `submitting` | The job and backend-staged input exist, but no accepted Slurm job is saved. |
| `submitted` | `submitted` | Slurm accepted the job and it is waiting to run. |
| `running` | `running` | Slurm reports an active state. |
| `finalising` | `running` | Slurm finished and files are being prepared. |
| `completed` | `completed` | Successful files are ready. |
| `failed` | `failed` | The calculation, cluster, or orchestration failed. |
| `cancelled` | `cancelled` | Cancellation was confirmed and finalisation finished. |

While a job is `finalising`, `terminal_status` stores its intended outcome:
`completed`, `failed`, or `cancelled`. `failure_reason` distinguishes calculation
and cluster outcomes from submission, status-check, and upload failures;
`failure_message` contains a safe explanation when available.

## Reconciler Behaviour

### Submission and Lost Responses

Figure 1 shows the normal path. The important ordering rule is that the worker
commits `attempt_count` before calling `sbatch`, then saves the returned Slurm ID
before removing the backend-staged files. This makes a lost submission response
detectable without losing the input.

![Ambiguous submission recovery](diagrams/ambiguous-submission-recovery.svg)

*Figure 2. An exact-name `squeue` search is followed by a recent `sacct` search.
The worker resubmits only when both successful lookups are empty.*

Figure 2 applies only when an attempt was recorded but no Slurm ID was saved. A
lookup error causes backoff; only two successful empty lookups permit another
submission. A cancellation requested before any attempt needs no Slurm call;
after an attempt, recovery runs first.

### Batched Status Checks

The status reconciler includes soft-deleted `submitted` and `running` jobs. One
`sacct --allocations` call checks each `STATUS_BATCH_SIZE` batch:

- queued Slurm states remain `submitted`;
- active states become `running`; and
- completed, cancelled, and recognized failure states become `finalising` with
  the appropriate `terminal_status`.

Missing, malformed, or unknown results consume one job attempt. After
`MAX_ATTEMPTS`, the backend attempts cancellation, records
`status_check_failed`, and raises an orphan-job alert if cancellation cannot be
confirmed.

If `cancel_requested` is set, the reconciler checks the latest state first. It
sends the repeat-safe `scancel --quiet` command only when the job is not already
terminal.

### Finalisation and Files

Figure 1 shows the finalisation sequence. Before uploading, the worker checks
the deterministic S3 keys so it can recover when an earlier upload succeeded
but the database update did not. Each attempt uses fresh presigned URLs in a
temporary `0600` manifest; the manifest is neither logged nor stored in
PostgreSQL. The terminal status is published and the Alliance job directory is
removed only after all required files are confirmed in S3.

Every terminal Slurm outcome requires an archive. Individual downloads depend
on the outcome:

| Outcome | Individually downloadable |
|---|---|
| `completed` | `result.json` and calculation-specific outputs |
| `failed` with `calculation_failed` | `result.err` |
| Other failure or `cancelled` | None |

The archive retains available inputs, outputs, Slurm logs, and partial results.
For non-calculation failures, `result.err` is included when present but is not
required. If orchestration ends without ready files, storage endpoints return
`409 Job files are not ready`.

## Retries and Shared Outages

- A job-specific retryable failure increments `attempt_count` for that job and
  resets it when the current stage succeeds. At `MAX_ATTEMPTS`, the responsible
  reconciler records a stage-specific failure. Invalid data can fail immediately.
- A shared PostgreSQL, SSH, Slurm, or S3 failure stops the current round without
  changing individual attempt counts. The process doubles its sleep from
  `RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS` up to
  `RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS`, then resets after recovery.

Polling intervals are measured from the start of one round to the next; rounds
within one reconciler process never overlap.

## Cancellation and Soft Deletion

`POST /jobs/{job_id}/cancel` stores `cancel_requested=true`; it does not call
Slurm from the HTTP request. It returns `202` while cancellation is pending,
`200` when already cancelled, and `409` when cancellation is no longer allowed.

![Job cancellation flow](diagrams/cancellation-flow.svg)

*Figure 3. The responsible reconciler checks the saved submission state and the
latest Slurm state before acting.*

`DELETE /jobs/{job_id}` only hides the job. It does not request cancellation.
All three reconcilers continue processing soft-deleted active jobs, so cancel
first when the calculation itself should stop.

## Configuration and Processes

[`.env.example`](../.env.example) is the settings reference. Each API or
reconciler process caches its own validated settings, so restart all affected
processes after an environment change.

The deployed backend runs exactly one submission, status, and finalisation
process with `molmaker-reconciler@.service`; `molmaker-reconcilers.target`
manages them as a group. Installation and commands are in the
[README](../README.md#4-start-the-api-and-reconcilers). Only the submission
process checks at startup that backend staging exists, is writable, and has the
configured minimum free space.

## Restricted Alliance Dispatch

The backend host connects through the `cluster` SSH alias. Its key can invoke
only `dispatch.py`; arbitrary remote commands are rejected. The canonical source
is `Cluster-API-QC/runner/dispatch.py`.

The backend and Alliance deployments are compatible only when these operations
match:

| Dispatch operation | Alliance action |
|---|---|
| `submit` | `sbatch --parsable`; application UUID used as the Slurm job name |
| `find-active` | Exact-name `squeue` lookup |
| `find-accounting` | Exact-name `sacct` lookup over the last 24 hours |
| `status-batch` | Allocation-only batched `sacct` lookup |
| `cancel-slurm-job` | Repeat-safe `scancel --quiet` |
| `upload-artifacts` | Run the job-scoped uploader with a temporary URL manifest |

The matching script must be deployed before enabling the reconcilers. See the
[README](../README.md#deploy-the-alliance-dispatch-script) for deployment,
verification, smoke testing, rollback, and operational commands.
