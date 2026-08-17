# Molmaker Backend

This repository contains the FastAPI backend for Molmaker. It manages users,
groups, membership requests, jobs, structures, file storage, and access to
calculation results.

## Documentation

- [Backend data flow](docs/backend-data-flow.md) explains how requests move
  through authentication, services, PostgreSQL, the reconcilers, Alliance, and
  S3.
- [Job orchestration](docs/job-orchestration.md) explains job statuses, the
  three reconcilers, cluster dispatch, retries, and recovery.
- [Ownership and permissions](docs/ownership-and-permissions.md) explains asset
  ownership, roles, transfers, cancellation, and file access.
- [Membership requests](docs/membership-requests.md) explains invites, join
  requests, de-member requests, expiry, and history.
- Swagger UI documents the available API endpoints and their request and response fields. After starting the backend, open
  [http://localhost:8000/docs](http://localhost:8000/docs).

## Requirements

- Python 3.11
- PostgreSQL 14
- The environment values listed in `.env.example`
- For production calculation processing, a compatible reviewed
  `Cluster-API-QC` dispatch deployment on Alliance

## Local Setup

### 1. Create and activate a virtual environment

macOS:

```zsh
python3 -m venv venv
source venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install dependencies

On macOS, install PostgreSQL first because `psycopg2` needs its `pg_config`
command:

```zsh
brew install postgresql@14
export PATH="$(brew --prefix postgresql@14)/bin:$PATH"
python -m pip install -r requirements.txt
```

On Windows:

```powershell
python -m pip install -r requirements.txt
```

### 3. Configure the environment

Copy the example environment file and replace its placeholders with values for
your machine and services.

macOS:

```zsh
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

For local macOS development, start PostgreSQL and create a database:

```zsh
export PATH="$(brew --prefix postgresql@14)/bin:$PATH"
brew services start postgresql@14

DB_USER="$(whoami)"
DB_NAME="molmaker_local"
DB_PASSWORD="molmaker_local_password"

psql -d postgres -c "ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
createdb -O "${DB_USER}" "${DB_NAME}"
```

Set the matching database values in `.env`, then create the current schema:

```zsh
python -m database
```

`.env.example` is the complete backend settings reference. AWS credentials are
loaded through boto3's standard credential provider chain rather than stored in
backend-specific settings.

Settings are loaded once and cached separately by the API and each reconciler
process. Restart those processes after changing `.env`.

### 4. Start the API and reconcilers

Start the API in one terminal:

```zsh
python -m uvicorn main:app --reload
```

The API is available at `http://localhost:8000` by default.

#### Deploy the Alliance dispatch interface

Before enabling the reconcilers, deploy the complete, reviewed
`Cluster-API-QC` repository at one exact commit:

```text
/home/thachuk/ubchemica/Cluster-API-QC
```

Set `CLUSTER_WORK_DIR=/home/thachuk/ubchemica` in the backend environment. The
backend sends one versioned JSON request to
`Cluster-API-QC/runner/dispatch.py` over SSH stdin for each cluster operation.
The supported operations and security boundary are described in
[Restricted Alliance Dispatch](docs/job-orchestration.md#restricted-alliance-dispatch).

Deployment requires explicit approval:

1. Review and commit the complete cluster change, including the runner,
   protocol, calculation code, and uploader.
2. Stop the reconcilers. From an authorized Alliance login, confirm the cluster
   checkout is clean, record its current commit for rollback, and check out the
   reviewed commit.
3. Restrict the backend SSH key to this exact no-argument command:
   `python3 /home/thachuk/ubchemica/Cluster-API-QC/runner/dispatch.py`.
4. Through that restricted connection, smoke-test submission and recovery,
   batched status checks, cancellation, artifact upload, invalid JSON, and a
   shared-service failure.
5. If any check fails, check out the recorded previous cluster commit and
   restore the previous allow-list rule. Remove only the smoke-test jobs and
   directories created during these checks.

The complete JSON contract, smoke-test examples, and rollback procedure live
in `Cluster-API-QC/runner/README.md` and must be reviewed with the matching
cluster release.

#### Install the reconciler services

On the server, ensure the environment file is available at `/home/backend/.env`.
Then install and start the `systemd` services for the first time from the
repository root:

```zsh
sudo cp deploy/systemd/molmaker-reconciler@.service /etc/systemd/system/
sudo cp deploy/systemd/molmaker-reconcilers.target /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now molmaker-reconcilers.target
```

Systemd then runs exactly one copy of each reconciler and starts them again when
the server boots. After installation, restart all three with:

```zsh
sudo systemctl restart molmaker-reconcilers.target
```

The service definitions are in `deploy/systemd`. Logs are available through
the system journal.

> For local debugging, run one reconciler directly by choosing `submission`,
> `status`, or `finalisation` in the module name. For example:
>
> ```zsh
> python -m orchestration.submission_reconciler
> ```
>
> Add `--once` to run one round and exit. It returns a failure if that round
> cannot complete, and it does not start the other two reconcilers.

### Reconciler operations

Check the services or follow a reconciler's logs with:

```zsh
sudo systemctl status molmaker-reconcilers.target
sudo systemctl status 'molmaker-reconciler@*.service'
sudo journalctl -u molmaker-reconciler@status.service -f
```

When diagnosing one job, inspect its saved status, attempt count, failure
fields, and Slurm ID. If many jobs pause together, first check for a shared
PostgreSQL, SSH, Slurm, or S3 outage.

Restarting a reconciler is safe because PostgreSQL retains the job state and
calculation inputs, while cluster job directories and S3 artifacts use stable
job-specific names.

## Tests

Tests require a dedicated PostgreSQL database. Create one, set its URL, then
run the suite:

```zsh
python -m pip install -r requirements-dev.txt
createdb molmaker_test
export TEST_DATABASE_URL="postgresql://<user>:<password>@localhost:5432/molmaker_test"
python -m pytest -q
```

Each run creates a uniquely named schema inside that database and drops it at
the end. Pull requests run the full suite against PostgreSQL only.

## Database Schema

The SQLAlchemy models are the authoritative schema. This repository does not
carry historical database dumps or migrations. Start with an empty database
and run `python -m database` before starting the API or reconcilers.
