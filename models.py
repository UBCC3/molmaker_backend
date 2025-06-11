# models.py
from sqlalchemy import Column, String, DateTime, Text, Table, ForeignKey, Integer, Interval
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base
import uuid
from datetime import datetime, timezone

jobs_structures = Table(
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

structures_tags = Table(
    'structures_tags',
    Base.metadata,
    Column(
        'structure_id',
        UUID(as_uuid=True),
        ForeignKey('structures.structure_id', ondelete='CASCADE'),
        primary_key=True
    ),
    Column(
        'tag_id',
        UUID(as_uuid=True),
        ForeignKey('tags.tag_id', ondelete='CASCADE'),
        primary_key=True
    ),
)

jobs_tags = Table(
    'jobs_tags',
    Base.metadata,
    Column(
        'job_id',
        UUID(as_uuid=True),
        ForeignKey('jobs.job_id', ondelete='CASCADE'),
        primary_key=True
    ),
    Column(
        'tag_id',
        UUID(as_uuid=True),
        ForeignKey('tags.tag_id', ondelete='CASCADE'),
        primary_key=True
    ),
)

class Job(Base):
    __tablename__ = "jobs"

    job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_name = Column(Text, nullable=True)
    job_notes = Column(Text, nullable=True)
    filename = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    calculation_type = Column(String, nullable=False)
    method = Column(String, nullable=False)
    basis_set = Column(String, nullable=False)
    charge = Column(Integer, nullable=False)
    multiplicity = Column(Integer, nullable=False)
    submitted_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    user_sub = Column(String, nullable=False)
    slurm_id = Column(String, nullable=True)
    runtime = Column(Interval, nullable=True)

    structures = relationship(
        'Structure',
        secondary=jobs_structures,
        back_populates='jobs',
        cascade="all, delete"
    )

    tags = relationship(
        'Tags',
        secondary=jobs_tags,
        back_populates='jobs',
        cascade="all, delete"
    )

class Structure(Base):
    __tablename__ = "structures"

    structure_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_sub = Column(String, nullable=False)
    name = Column(Text, nullable=False)
    location = Column(Text, nullable=False)
    notes = Column(Text, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))

    jobs = relationship(
        'Job',
        secondary=jobs_structures,
        back_populates='structures'
    )

    tags = relationship(
        'Tags',
        secondary=structures_tags,
        back_populates='structures',
        cascade="all, delete"
    )

class Tags(Base):
    __tablename__ = "tags"

    tag_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_sub = Column(String, nullable=False)
    name = Column(String, nullable=False)

    jobs = relationship(
        'Job',
        secondary=jobs_tags,
        back_populates='tags'
    )

    structures = relationship(
        'Structure',
        secondary=structures_tags,
        back_populates='tags'
    )
