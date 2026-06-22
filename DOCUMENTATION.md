## Backend Setup

### 1. Create and activate virtual environment

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

For future runs, only need to run

macOS:

```zsh
source venv/bin/activate
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

to activate virtual environment.

### 2. Install dependencies
You need to install dependencies only once after creating the virtual environment.

macOS:

`psycopg2` requires PostgreSQL's `pg_config` command when installing.

```zsh
brew install postgresql@14
export PATH="$(brew --prefix postgresql@14)/bin:$PATH"
pg_config --version
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
```

### 3. Configure environment variables
Create a local `.env` file from the example and fill in the values for your local PostgreSQL database and machine-specific directories.

macOS:

```zsh
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

For local macOS development, create and import a local PostgreSQL database:

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

Then set these values in `.env`.

The database role that imports `molmaker.sql` owns the created schema objects. To keep schema dumps portable, generate future schema-only dumps with owner and grant statements omitted:

```zsh
pg_dump --schema-only --no-owner --no-acl ...
```

Production deployments should confirm whether migrations run as the same role used by the backend or as a separate migration/admin role. If a separate role owns the schema, explicitly grant the backend role the required table privileges before starting the app.

### 4. Run the backend
After dependencies are installed, you can run the backend with the following command.
```
python -m uvicorn main:app --reload
```

> **Note:** Always activate the virtual environment before running the backend.

## API Documentation

To access the API documentation supported by FastAPI Swagger UI, you can access the local host port of the backend added with `/docs`. Example:
- `localhost:8000/docs` (if it lives in port 8000)
