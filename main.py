from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine
from models import Job
from jobs.routes import router as jobs_router
from structures.routes import router as structures_router
from enums.routes import router as enums_router
# from cluster.routes import router as cluster_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Job.__table__.create(bind=engine, checkfirst=True)

app.include_router(jobs_router)
app.include_router(structures_router)
app.include_router(enums_router)
# app.include_router(cluster_router)
