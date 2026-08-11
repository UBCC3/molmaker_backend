# Backend Data Flow

This document shows how information moves through the Molmaker backend. For
endpoint inputs and response schemas, use Swagger. For access rules, see
[Ownership and Permissions](ownership-and-permissions.md).

## Main Components

| Component | Responsibility |
|---|---|
| FastAPI routers | Parse HTTP requests, declare response models, and call shared services. |
| Auth0 | Issues access tokens and provides the signing keys used to verify them. |
| Permission and service modules | Apply ownership rules, perform business operations, and serialize responses. |
| SQLAlchemy and PostgreSQL | Store users, groups, requests, jobs, structures, tags, and durable orchestration state. |
| Backend staging | Temporarily holds calculation inputs until the accepted Slurm ID is saved. |
| Reconciler processes | Submit jobs, poll Slurm, request cancellation, and finalize artifacts. |
| Alliance dispatch | Provides the restricted, machine-readable boundary to Slurm and the cluster uploader. |
| S3 | Stores structure files and images, result artifacts, and job archives. |

## Overall Flow

![Backend request and data flow](diagrams/backend-data-flow.svg)

*Figure 1. Public requests, durable backend state, file storage, and the
asynchronous calculation path use separate but connected data flows.*

The orange file-transfer path copies `input.xyz` and optional `keywords.json`
from backend staging into the Alliance job directory. A separate orchestration
call then asks `dispatch.py` to submit that already-staged calculation to Slurm.

The normal API path and calculation execution are deliberately separated.
PostgreSQL connects them: the request commits a job in `submitting`, and the
reconcilers advance it later.

PostgreSQL is authoritative for identity context, ownership, metadata, and job
state. S3 is authoritative for files. Backend staging and Alliance job
directories are temporary working storage, not public API state.

## Authentication and Authorization

1. The client sends an Auth0 bearer token.
2. `auth.py` obtains Auth0's JSON Web Key Set and verifies the token signature,
   issuer, audience, and configured algorithm.
3. The verified token's `sub` identifies the caller.
4. The route loads that person's local `users` row. Auth0 proves identity; the
   database supplies role and group membership.
5. Shared permission functions decide whether the caller may read, edit,
   delete, change visibility, or transfer the requested resource.

Asset decisions use the job or structure's saved `user_sub` and `group_id`, not
the owner's current group membership. This preserves deliberate co-ownership
when a user later leaves a group.

## Ordinary API Request

For a typical read or edit:

1. FastAPI validates path, query, form, and JSON values.
2. A dependency opens one SQLAlchemy session for the request.
3. The route verifies the token and loads the local caller.
4. A service loads the target row and applies the shared permission predicate.
5. The service reads or changes related rows in PostgreSQL.
6. Writes commit through `commit_or_rollback`; failed saves roll back.
7. A serializer returns only fields allowed by the public response schema.
8. The request dependency closes the database session.

Job serializers intentionally remove orchestration-only fields. The same
`JobResponse` shape is used by calculation creation and ordinary job APIs;
administrator lists add `user_email` and `group_name` for ownership context.

## Calculation Creation

Both calculation endpoints call `create_calculation_job`:

1. Validate and normalize calculation metadata.
2. Require exactly one molecule source: an uploaded XYZ file or an accessible
   stored structure.
3. If a structure is referenced, read its S3 location and download the XYZ
   file. If a file is uploaded, copy it directly.
4. Write `input.xyz` and optional `keywords.json` under
   `BACKEND_WORK_DIR/jobs/{job_id}` without holding a database transaction open.
5. Create the `jobs` row with `status=submitting`, its creator and group owners,
   normalized tags, and at most one linked source structure.
6. Commit the complete job and return it with `201 Created`.

If staging or the database save fails, the partially created backend staging directory is
removed. Alliance submission and result-upload URL generation never run in the
HTTP request.

The database relationship currently permits a list of linked structures even
though calculation creation uses at most one source structure. Replacing that
legacy many-to-many relationship with one source-structure field is deferred.

## Asynchronous Calculation Flow

After creation, three processes use the job row as a durable queue:

1. The submission reconciler copies staged input to Alliance and saves the
   accepted internal Slurm ID.
2. The status reconciler asks Alliance for allocation states in batches and
   stores public status and runtime updates.
3. Terminal Slurm outcomes enter the internal `finalising` state.
4. The finalisation reconciler generates fresh upload URLs, asks Alliance to
   upload result files, and then publishes the terminal status.
5. Job GET endpoints return the latest state already saved in PostgreSQL.
6. Storage endpoints authorize against the job and return fresh presigned S3
   download URLs after its files are ready.

See [Job Orchestration](job-orchestration.md) for retry, cancellation,
deployment, and recovery details.

## Structure and File Storage

PostgreSQL stores structure metadata and an S3 URI in `structures.location`.
S3 stores the XYZ source and image under the configured bucket. Structure reads
return metadata; dedicated endpoints create short-lived presigned download
URLs.

Job artifacts use deterministic keys below `S3_BUCKET_ROOT`:

```text
{S3_BUCKET_ROOT}/jobs/{job_id}/...
{S3_BUCKET_ROOT}/archive/{job_id}.zip
```

The API and reconcilers use the same `S3_BUCKET_NAME`, `S3_REGION`, and bucket
root. AWS credentials are not copied into backend settings; boto3 uses its
standard credential provider chain.

Presigned URLs are capabilities with an expiry:

- finalisation creates new PUT URLs for each upload attempt;
- artifact endpoints create new GET URLs for each download request; and
- URL manifests are temporary, permission-restricted, removed after use, and
  never stored in PostgreSQL.

## Ownership, Tags, and Lists

Jobs and structures can be user-owned, group-owned, or co-owned. Personal list
endpoints query `user_sub`; group list endpoints query the caller's current
`group_id`. Soft-deleted resources are absent from public lists and lookups.

Tags are case-insensitive and stored in canonical lowercase form. Tag rows are
unique per user, so different users can each have a `water` tag in their own
tag namespace. An additive update does not attach a second copy of a visible
name that is already on the asset. A new name creates or reuses a tag belonging
to the person making the update and links it to the asset; it does not add that
tag to another owner's personal tag namespace.

Every reader of an accessible asset receives the unique attached tag names,
regardless of which user owns the underlying tag rows. Setting
`replace_tags=true` removes every existing tag link from that asset, including
links owned by other users, and then attaches the supplied names in the
caller's tag namespace. There is currently no server-side job tag filter;
clients can filter the tag names returned with accessible jobs.

The complete permission matrix is in
[Ownership and Permissions](ownership-and-permissions.md).

## Process and Configuration Boundary

The API, submission reconciler, status reconciler, and finalisation reconciler
are four separate operating system processes. They import the same
`settings.py`, but each process constructs and caches its own `BackendSettings`
object.

Consequences:

- all environment access is centralized in one module;
- `.env` is convenient for local development;
- systemd injects `/home/backend/.env` in production; and
- changing an environment variable requires restarting all affected processes.

There is no separate configuration service and no in-memory queue shared by
the processes. PostgreSQL is the source of truth between them.

## Code Map

| Path | Main responsibility |
|---|---|
| `main.py` | Builds FastAPI and attaches routers. |
| `auth.py` | Verifies Auth0 access tokens. |
| `permissions.py` | Contains reusable permission predicates. |
| `asset_service.py` | Lists, serializes, tags, transfers, and soft-deletes jobs and structures. |
| `calculation/service.py` | Validates and stages calculation inputs and creates jobs. |
| `jobs/routes.py` | Reads, edits, cancels, and soft-deletes jobs. |
| `s3/routes.py` and `storage.py` | Authorize and create job artifact URLs. |
| `database.py` and `models.py` | Configure SQLAlchemy and define persistent data. |
| `settings.py` | Loads and validates backend environment settings. |
| `orchestration/` | Contains the cluster client and three reconcilers. |
| `deploy/systemd/` | Runs one supervised process for each reconciler. |

## Adding a Backend Operation

When adding an endpoint:

1. define the public request and response without exposing internal identifiers;
2. verify identity and load the local user;
3. use the shared permission functions and saved resource ownership;
4. keep external work outside database transactions;
5. use `commit_or_rollback` for request-path database writes;
6. update the endpoint docstring and Swagger field descriptions; and
7. add tests for permissions, response shape, rollback, and external failures.

Long-running or restart-sensitive work should be represented by durable
database state and handled by a supervised process instead of an in-process
FastAPI background task.
