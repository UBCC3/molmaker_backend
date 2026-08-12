# Backend Data Flow

This document shows how information moves through the Molmaker backend. For
endpoint schemas, use Swagger. For access rules, see
[Ownership and Permissions](ownership-and-permissions.md).

## Main Components

| Component | Responsibility |
|---|---|
| FastAPI routers | Validate HTTP requests, declare response models, and call shared services. |
| Auth0 | Issues access tokens and provides the signing keys used to verify them. |
| Permission and service modules | Apply ownership rules, perform business operations, and serialize responses. |
| PostgreSQL | Stores users, groups, jobs, structures, tags, orchestration state, and retained calculation inputs. |
| Reconciler processes | Submit jobs, poll Slurm, request cancellation, and finalise artifacts. |
| Alliance dispatch | Accepts one validated JSON request per cluster operation and provides the restricted boundary to Slurm. |
| Alliance job directory | Holds the input, script, logs, and results while a calculation is being processed. |
| S3 | Stores structure objects, result artifacts, and job archives. |

## Overall Flow

![Backend request and data flow](diagrams/backend-data-flow.svg)

*Figure 1. Synchronous API requests and asynchronous calculations share
PostgreSQL state. Calculation inputs travel directly from PostgreSQL to the
restricted cluster interface; no backend file-staging step is involved.*

The normal API path returns quickly. Calculation work continues later because
the API commits a job in `submitting` and the reconcilers select jobs by saved
status. PostgreSQL is therefore the hand-off between separate operating-system
processes, not an active coordinator.

PostgreSQL is authoritative for identity, ownership, metadata, job state, and
the exact calculation input. S3 holds durable structure objects and job
artifacts. Alliance job directories are temporary working storage created from
a submission request and removed after artifact upload succeeds.

## Authentication and Authorization

1. The client sends an Auth0 bearer token.
2. `auth.py` obtains Auth0's JSON Web Key Set and verifies the token signature,
   issuer, audience, and configured algorithm.
3. The verified token's `sub` identifies the caller.
4. The route loads that person's local `users` row. Auth0 proves identity; the
   database supplies role and group membership.
5. Shared permission functions decide whether the caller may read or change the
   requested resource.

Asset decisions use the job or structure's saved `user_sub` and `group_id`, not
the owner's current group membership. This preserves deliberate co-ownership
when a user later leaves a group.

## Ordinary API Request

For a typical read or edit:

1. FastAPI validates path, query, form, and JSON values.
2. A dependency opens one SQLAlchemy session for the request.
3. The route verifies the token and loads the local caller.
4. A service loads the target and applies the shared permission predicate.
5. The service reads or changes PostgreSQL rows.
6. Writes commit through `commit_or_rollback`; failed saves roll back.
7. A serializer returns only fields allowed by the response schema.
8. The request dependency closes the session.

Job responses intentionally omit orchestration-only fields. Calculation
creation and ordinary job APIs use the same `JobResponse`; administrator lists
add owner email and group name for context.

## Calculation Creation

Both calculation endpoints call `create_calculation_job`:

1. Validate and normalize calculation metadata.
2. Require exactly one molecule source: an uploaded XYZ file or an accessible
   stored structure.
3. Read and validate the bounded XYZ text. For a stored structure, download its
   current S3 object and keep that exact content as the calculation snapshot.
4. Parse the optional keywords file as a JSON object.
5. Create the `jobs` row with `status=submitting`, ownership, tags, and at most
   one linked source structure.
6. Create the one-to-one `job_inputs` row with `input_xyz` and optional
   `keywords`.
7. Commit the job, inputs, tags, and relationship together, then return
   `201 Created`.

If the save fails, the transaction rolls back all of those rows. Cluster
submission and result-upload URL generation never run in the HTTP request.

The database relationship still permits a list of linked structures even
though calculation creation uses at most one source. Replacing that legacy
many-to-many relationship with one source-structure field is deferred.

## Asynchronous Calculation Flow

After creation, three processes use the job row as a durable queue:

1. The submission reconciler loads `job_inputs` and sends one JSON request with
   the inputs and calculation settings.
2. Cluster dispatch safely creates the Alliance job directory, writes
   `input.xyz`, optional `keywords.json`, and `slurm.sh`, then submits to Slurm.
3. The status reconciler requests allocation states in batches and stores the
   public status and runtime.
4. Terminal Slurm outcomes enter the internal `finalising` state.
5. The finalisation reconciler creates fresh S3 PUT URLs and sends them directly
   in one JSON upload request.
6. The cluster uploader sends the archive and available result files to S3;
   the backend then publishes the terminal status.
7. Job endpoints return the state in PostgreSQL. Storage endpoints authorize
   the caller and create fresh S3 download URLs when files are ready.

See [Job Orchestration](job-orchestration.md) for retries, cancellation, and
recovery.

## Structure and Artifact Storage

PostgreSQL stores structure metadata and an S3 URI in `structures.location`.
S3 stores the structure XYZ object and image. Dedicated endpoints create
short-lived presigned download URLs.

Job artifacts use deterministic keys below `S3_BUCKET_ROOT`:

```text
{S3_BUCKET_ROOT}/jobs/{job_id}/...
{S3_BUCKET_ROOT}/archive/{job_id}.zip
```

The API and reconcilers use the same S3 bucket settings. AWS credentials are
not copied into backend settings; boto3 uses its standard credential provider
chain.

Presigned URLs are short-lived capabilities:

- finalisation creates new PUT URLs for each upload attempt and sends them only
  through the JSON request body;
- artifact endpoints create new GET URLs for each download request; and
- presigned URLs are never written to disk, stored in PostgreSQL, or logged.

## Ownership, Tags, and Lists

Jobs and structures can be user-owned, group-owned, or co-owned. Personal lists
query `user_sub`; group lists query the caller's current `group_id`.
Soft-deleted resources are absent from public lists and lookups.

Tags are case-insensitive and stored in lowercase. Tag rows are unique per
user, so two users can each own a `water` row. Assets expose unique attached tag
names to every authorized reader, regardless of who owns the underlying rows.
Additive updates reuse or create the caller's tag rows without duplicating a
visible name; replacement removes all asset-tag links before attaching the
supplied names in the caller's namespace.

The complete permission matrix is in
[Ownership and Permissions](ownership-and-permissions.md).

## Process and Configuration Boundary

The API, submission reconciler, status reconciler, and finalisation reconciler
are four separate processes. Each imports `settings.py` and caches its own
validated `BackendSettings`, so environment changes require a restart.

There is no in-memory queue or shared configuration process. PostgreSQL retains
the information needed for the next process or restarted worker to continue.

## Code Map

| Path | Main responsibility |
|---|---|
| `main.py` | Builds FastAPI and attaches routers. |
| `auth.py` | Verifies Auth0 access tokens. |
| `permissions.py` | Contains reusable permission predicates. |
| `asset_service.py` | Lists, serializes, tags, transfers, and soft-deletes assets. |
| `calculation/service.py` | Validates calculation inputs and saves jobs with `job_inputs`. |
| `jobs/routes.py` | Reads, edits, cancels, and soft-deletes jobs. |
| `s3/routes.py` and `storage.py` | Authorize and create artifact URLs. |
| `database.py` and `models.py` | Configure SQLAlchemy and define persistent data. |
| `settings.py` | Loads and validates backend environment settings. |
| `orchestration/` | Contains the JSON cluster client and three reconcilers. |
| `deploy/systemd/` | Runs one supervised process for each reconciler. |

## Adding a Backend Operation

When adding an endpoint:

1. define the request and response without exposing internal identifiers;
2. verify identity and load the local user;
3. use the shared permission functions and saved resource ownership;
4. keep external work outside database transactions;
5. use `commit_or_rollback` for request-path database writes;
6. update the endpoint docstring; and
7. add tests for permissions, response shape, rollback, and external failures.

Long-running or restart-sensitive work should be represented by durable
database state and handled by a supervised process instead of an in-process
FastAPI background task.
