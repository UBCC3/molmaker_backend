from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import uuid

import pytest

from asset_service import set_asset_tags
from conftest import TestingSessionLocal, engine
from models import Job, Tags


pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="requires PostgreSQL conflict handling",
)


def test_concurrent_jobs_can_share_a_new_tag(db, user_factory):
    owner = user_factory(user_sub="auth0|tag-owner")
    owner_sub = owner.user_sub
    barrier = Barrier(2)

    def create_tagged_job():
        session = TestingSessionLocal()
        try:
            job = Job(
                job_id=uuid.uuid4(),
                filename="input.xyz",
                status="pending",
                calculation_type="energy",
                method="hf",
                basis_set="sto-3g",
                charge=0,
                multiplicity=1,
                submitted_at=datetime.now(timezone.utc),
                user_sub=owner_sub,
                is_deleted=False,
                is_public=False,
                is_uploaded=False,
            )
            session.add(job)
            barrier.wait(timeout=10)
            set_asset_tags(session, job, owner_sub, ["shared"])
            session.commit()
            return job.job_id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_tagged_job) for _ in range(2)]
        job_ids = [future.result(timeout=20) for future in futures]

    db.expire_all()
    shared_tags = (
        db.query(Tags)
        .filter_by(user_sub=owner_sub, name="shared")
        .all()
    )
    assert len(shared_tags) == 1

    jobs = db.query(Job).filter(Job.job_id.in_(job_ids)).all()
    assert len(jobs) == 2
    assert [[tag.tag_id for tag in job.tags] for job in jobs] == [
        [shared_tags[0].tag_id],
        [shared_tags[0].tag_id],
    ]
