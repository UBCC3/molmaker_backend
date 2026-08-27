import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from models import Asset, Job, JobResult, Request, Structure, Tags


class TestAssetModel:
    def test_asset_is_abstract_and_has_no_table(self):
        assert Asset.__abstract__ is True
        assert "assets" not in Asset.metadata.tables

    def test_job_uses_common_id_with_job_id_alias(self, user_factory, job_factory):
        user_factory(user_sub="auth0|testuser")
        job = job_factory()

        assert job.id == job.job_id
        assert inspect(Job).primary_key[0].name == "job_id"
        assert job.created_at == job.submitted_at
        assert Job.__table__.columns["submitted_at"].name == "submitted_at"

    def test_structure_uses_common_id_with_structure_id_alias(
        self, user_factory, structure_factory
    ):
        user_factory(user_sub="auth0|testuser")
        structure = structure_factory()

        assert structure.id == structure.structure_id
        assert inspect(Structure).primary_key[0].name == "structure_id"
        assert structure.created_at == structure.uploaded_at
        assert Structure.__table__.columns["uploaded_at"].name == "uploaded_at"

    def test_job_and_structure_are_assets(
        self, user_factory, job_factory, structure_factory
    ):
        user_factory(user_sub="auth0|testuser")
        assert isinstance(job_factory(), Asset)
        assert isinstance(structure_factory(), Asset)

    def test_tags_are_unique_per_user_and_name(self):
        constraint_names = {
            constraint.name for constraint in Tags.__table__.constraints
        }

        assert "uq_tags_user_sub_name" in constraint_names

    def test_tag_names_are_normalized_for_case_insensitive_matching(self):
        tag = Tags(user_sub="auth0|owner", name="  Important  ")

        assert tag.name == "important"

    def test_requests_have_expiry_index(self):
        index_names = {index.name for index in Request.__table__.indexes}

        assert "idx_requests_status_expires_at" in index_names

    def test_job_has_safe_orchestration_defaults(
        self,
        user_factory,
        job_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory()

        assert job.status == "submitting"
        assert job.attempt_count == 0
        assert job.cancel_requested is False
        assert job.terminal_status is None
        assert job.failure_reason is None
        assert job.failure_message is None
        assert job.optimization_type is None
        assert job.archive_upload_requested is True
        assert job.archive_uploaded is False
        assert job.archive_upload_status == "pending"

    def test_archive_upload_fields_must_remain_consistent(
        self,
        db,
        user_factory,
        job_factory,
    ):
        user_factory(user_sub="auth0|testuser")

        with pytest.raises(IntegrityError):
            job_factory(
                archive_uploaded=True,
                archive_upload_status="unavailable",
            )
        db.rollback()

    def test_job_has_partial_active_orchestration_index(self):
        index = next(
            index
            for index in Job.__table__.indexes
            if index.name == "idx_jobs_orchestration_active"
        )

        assert tuple(column.name for column in index.columns) == (
            "status",
            "submitted_at",
            "job_id",
        )
        predicate = str(index.dialect_options["postgresql"]["where"])
        for status in ("submitting", "submitted", "running", "finalising"):
            assert f"'{status}'" in predicate
        assert "is_deleted" not in predicate

    def test_slurm_id_is_nullable_but_unique_when_assigned(
        self,
        db,
        user_factory,
        job_factory,
    ):
        user_factory(user_sub="auth0|testuser")

        first_waiting_job = job_factory(slurm_id=None)
        second_waiting_job = job_factory(slurm_id=None)
        submitted_job = job_factory(slurm_id="12345")

        assert first_waiting_job.slurm_id is None
        assert second_waiting_job.slurm_id is None
        assert submitted_job.slurm_id == "12345"
        assert Job.__table__.columns["slurm_id"].nullable is True

        constraint = next(
            constraint
            for constraint in Job.__table__.constraints
            if constraint.name == "uq_jobs_slurm_id"
        )
        assert tuple(column.name for column in constraint.columns) == ("slurm_id",)

        with pytest.raises(IntegrityError):
            job_factory(slurm_id="12345")
        db.rollback()

    def test_job_omits_unneeded_orchestration_fields(self):
        assert {
            "retry_count",
            "slurm_state",
            "slurm_exit_code",
            "submission_attempted_at",
            "cancel_requested_at",
            "artifact_manifest",
        }.isdisjoint(Job.__table__.columns.keys())

    def test_structure_stores_required_content_without_a_legacy_location(self):
        assert "location" not in Structure.__table__.columns
        assert Structure.__table__.columns["content"].nullable is False
        assert Structure.__table__.columns["thumbnail"].nullable is False
        assert Structure.__table__.columns["thumbnail_media_type"].nullable is False

    def test_job_result_is_one_to_one_with_safe_artifact_default(
        self,
        user_factory,
        job_factory,
        job_result_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(status="completed", is_uploaded=True)
        result = job_result_factory(job=job, artifacts={"trajectory": "xyz"})

        assert result.job_id == job.job_id
        assert result.job is job
        assert job.job_result is result
        assert result.artifacts == {"trajectory": "xyz"}
        assert Job.job_result.property.uselist is False
        assert JobResult.__table__.columns["artifacts"].nullable is False
