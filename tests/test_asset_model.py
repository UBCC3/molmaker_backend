from sqlalchemy import inspect

from models import Asset, Job, Structure, Tags


class TestAssetModel:
    def test_asset_is_abstract_and_has_no_table(self):
        assert Asset.__abstract__ is True
        assert "assets" not in Asset.metadata.tables

    def test_job_uses_common_id_with_job_id_alias(self, job_factory):
        job = job_factory()

        assert job.id == job.job_id
        assert inspect(Job).primary_key[0].name == "job_id"
        assert job.created_at == job.submitted_at
        assert Job.__table__.columns["submitted_at"].name == "submitted_at"

    def test_structure_uses_common_id_with_structure_id_alias(
        self, structure_factory
    ):
        structure = structure_factory()

        assert structure.id == structure.structure_id
        assert inspect(Structure).primary_key[0].name == "structure_id"
        assert structure.created_at == structure.uploaded_at
        assert Structure.__table__.columns["uploaded_at"].name == "uploaded_at"

    def test_job_and_structure_are_assets(self, job_factory, structure_factory):
        assert isinstance(job_factory(), Asset)
        assert isinstance(structure_factory(), Asset)

    def test_tags_are_unique_per_user_and_name(self):
        constraint_names = {
            constraint.name for constraint in Tags.__table__.constraints
        }

        assert "uq_tags_user_sub_name" in constraint_names
