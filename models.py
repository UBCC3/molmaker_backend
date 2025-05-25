# models.py
from sqlalchemy import Column, String, DateTime, Text, Table, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base
import uuid
from datetime import datetime

job_structures = Table(
    'jobs_structures',
    Base.metadata,
    Column(
        'job_id',
        UUID(as_uuid=True),
        ForeignKey('jobs.job_id', ondelete='CASCADE'),
        primary_key=True
    ),
    Column(
        'structure_id',
        UUID(as_uuid=True),
        ForeignKey('structures.structure_id', ondelete='CASCADE'),
        primary_key=True
    ),
)

class Job(Base):
    __tablename__ = "jobs"

    job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_name = Column(Text, nullable=True)
    filename = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    calculation_type = Column(String, nullable=False)
    method = Column(String, nullable=False)
    basis_set = Column(String, nullable=False)
    charge = Column(Integer, nullable=False)
    multiplicity = Column(Integer, nullable=False)
    submitted_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    user_sub = Column(String, nullable=False)
    slurm_id = Column(String, nullable=True)

    structures = relationship(
        'Structure',
        secondary=job_structures,
        back_populates='jobs',
        cascade="all, delete"
    )

class Structure(Base):
    __tablename__ = "structures"

    structure_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_sub = Column(String, nullable=False)
    name = Column(Text, nullable=False)
    location = Column(Text, nullable=False)

    jobs = relationship(
        'Job',
        secondary=job_structures,
        back_populates='structures'
    )
