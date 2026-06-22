# models.py
from sqlalchemy import CheckConstraint, Column, Index, String, DateTime, Text, Table, ForeignKey, Integer, Interval, Boolean
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
    __table_args__ = (
        CheckConstraint("user_sub IS NOT NULL OR group_id IS NOT NULL", name="ck_jobs_owner_present"),
        Index("idx_jobs_user_active_submitted", "user_sub", "is_deleted", "submitted_at"),
        Index("idx_jobs_group_active_submitted", "group_id", "is_deleted", "submitted_at"),
    )

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
    user_sub = Column(String, ForeignKey('users.user_sub'), nullable=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey('groups.group_id'), nullable=True)
    slurm_id = Column(String, nullable=True)
    runtime = Column(Interval, nullable=True)
    is_deleted = Column(Boolean, nullable=False)
    is_public = Column(Boolean, nullable=False, default=False)
    is_uploaded = Column(Boolean, nullable=False)

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

    user = relationship(
        "User",
        back_populates="jobs"
    )
    group = relationship(
        "Group",
        back_populates="jobs"
    )

class Structure(Base):
    __tablename__ = "structures"
    __table_args__ = (
        CheckConstraint("user_sub IS NOT NULL OR group_id IS NOT NULL", name="ck_structures_owner_present"),
        Index("idx_structures_user_active_uploaded", "user_sub", "is_deleted", "uploaded_at"),
        Index("idx_structures_group_active_uploaded", "group_id", "is_deleted", "uploaded_at"),
    )

    structure_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_sub = Column(String, ForeignKey('users.user_sub'), nullable=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey('groups.group_id'), nullable=True)
    name = Column(Text, nullable=False)
    formula = Column(Text, nullable=False)
    location = Column(Text, nullable=False)
    notes = Column(Text, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    is_deleted = Column(Boolean, nullable=False)
    is_public = Column(Boolean, nullable=False, default=False)

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

    user = relationship(
        "User",
        back_populates="structures"
    )
    group = relationship(
        "Group",
        back_populates="structures"
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

class Group(Base):
    __tablename__ = "groups"

    group_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)

    # relationships
    users = relationship("User", back_populates="group")
    jobs = relationship("Job", back_populates="group")
    structures = relationship("Structure", back_populates="group")
    requests = relationship("Request", back_populates="group")

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("idx_users_group_role", "group_id", "role"),
    )

    user_sub = Column(String, primary_key=True)  # From Auth0
    email = Column(String, nullable=False, unique=True)
    role = Column(String, nullable=False, default='member')  # 'admin', 'group_admin', or 'member'
    group_id = Column(UUID(as_uuid=True), ForeignKey('groups.group_id'), nullable=True)
    member_since = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))

    # relationships
    group = relationship("Group", back_populates="users")
    jobs = relationship("Job", back_populates="user")
    structures = relationship("Structure", back_populates="user")
    sent_requests = relationship(
        "Request",
        foreign_keys="Request.sender_sub",
        back_populates="sender",
        cascade="all, delete-orphan"
    )
    received_requests = relationship(
        "Request",
        foreign_keys="Request.receiver_sub",
        back_populates="receiver",
        cascade="all, delete-orphan"
    )

class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        Index("idx_requests_receiver_status", "receiver_sub", "status"),
        Index("idx_requests_sender_status", "sender_sub", "status"),
        Index("idx_requests_group_status_type", "group_id", "status", "request_type"),
    )

    request_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status = Column(String, nullable=False, default='pending') # 'pending', 'approved', 'rejected'
    request_type = Column(String, nullable=False, default='invite')
    requested_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    sender_sub = Column(String, ForeignKey('users.user_sub'), nullable=False)
    receiver_sub = Column(String, ForeignKey('users.user_sub'), nullable=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey('groups.group_id'), nullable=False)

    sender = relationship("User", foreign_keys=[sender_sub], back_populates="sent_requests")
    receiver = relationship("User", foreign_keys=[receiver_sub], back_populates="received_requests")
    group = relationship("Group", back_populates="requests")
