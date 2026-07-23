# Molmaker Backend

This repository contains the FastAPI backend for Molmaker. It manages users,
groups, membership requests, jobs, structures, file storage, and access to
calculation results.

## Documentation

- [Membership request design](docs/membership-requests.md) explains invites, join requests, de-member requests, request expiry, and request history.
- [Ownership and permissions](docs/ownership-and-permissions.md) explains asset ownership, role permissions, ownership transfers, and deletion behaviour.
- Swagger UI documents the available API endpoints and their request and response fields. After starting the backend, open
  [http://localhost:8000/docs](http://localhost:8000/docs).

## Requirements

- Python 3.11
- PostgreSQL 14
- The environment values listed in `.env.example`

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
psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f molmaker.sql
```

Set the matching database values in `.env`.

### 4. Start the backend

```zsh
python -m uvicorn main:app --reload
```

The API is available at `http://localhost:8000` by default.

## Tests

Install the development dependencies and run the full test suite:

```zsh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Pull requests run the full suite against both SQLite and PostgreSQL.

## Database Files

`molmaker.sql` contains the current PostgreSQL structure and saved data. The
database role that imports it owns the created objects.

Generate a replacement dump without owner or permission statements so another
database user can import it:

```zsh
pg_dump --format=plain --no-owner --no-acl --file=molmaker.sql "${DB_NAME}"
```

To update a database created from `main`, back it up and run the PR 14
migration:

```zsh
psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f migrations/001_pr14_database_changes.sql
```

Running the migration more than once is safe. Do not run it after importing
the current `molmaker.sql`; that dump already contains the changes.

In production, confirm which database role runs migrations. If a separate
migration role owns the tables, grant the backend role the permissions it
needs before starting the backend.
