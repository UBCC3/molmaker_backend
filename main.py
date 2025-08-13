from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine
from models import Job
from jobs.routes import router as jobs_router
from structures.routes import router as structures_router
from enums.routes import router as enums_router
from admin.routes import router as admin_router
from users.routes import router as user_router
from groups.routes import router as groups_router
from request.routes import router as requests_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Job.__table__.create(bind=engine, checkfirst=True)

WORK_DIR = "project/thachuk/molmaker"

app.include_router(jobs_router)
app.include_router(structures_router)
app.include_router(enums_router)
# app.include_router(cluster_router)
app.include_router(admin_router)
app.include_router(user_router)
app.include_router(groups_router)
app.include_router(requests_router)