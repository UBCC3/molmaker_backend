Create virtual environment

- python -m venv venv
- .\venv\Scripts\Activate.ps1

Install the required module first

- python -m pip install uvicorn fastapi

Install dependencies

- python -m pip install -r requirements.txt

Run with uvicorn

- python -m uvicorn main:app --reload