## Backend Setup

### 1. Create and activate virtual environment
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

For future runs, only need to run

```powershell
.\venv\Scripts\Activate.ps1
```

to activate virtual environment.

### 2. Install dependencies
You need to install dependencies only once after creating the virtual environment.
```powershell
python -m pip install -r requirements.txt
```

### 3. Run the backend
After dependencies are installed, you can run the backend with the following command.
```powershell
python -m uvicorn main:app --reload
```

> **Note:** Always activate the virtual environment before running the backend.