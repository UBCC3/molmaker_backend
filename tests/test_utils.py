from datetime import datetime, timedelta, timezone
import uuid

from fastapi import HTTPException
import pytest

from utils import clean_up_upload_cache, get_user_sub, serialize_job, serialize_structure


class TestGetUserSub:
    def test_valid_payload_returns_user_sub(self):
        """
        Should return user_sub with valid_payload.
        """
        payload = {
            "sub": "auth0|abc456efg",
            "iss": "https://your-tenant.auth0.com/",
            "aud": "https://your-api.com",
            "iat": 1716230400,
            "exp": 1716234000,
        }

        user_sub = get_user_sub(payload)

        assert user_sub == "auth0|abc456efg"

    def test_not_dict_raise_error(self):
        """
        When payload is not a dict, it should raise an error.
        """
        payload = [
            "sub", "auth0|abc456efg",
            "iss", "https://your-tenant.auth0.com/",
            "aud", "https://your-api.com",
            "iat", 1716230400,
            "exp", 1716234000,
        ]

        with pytest.raises(HTTPException) as exc_info:
            get_user_sub(payload)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"

    def test_no_sub_raise_error(self):
        """
        When payload does not store sub, it should raise an error.
        """
        payload = {
            "iss": "https://your-tenant.auth0.com/",
            "aud": "https://your-api.com",
            "iat": 1716230400,
            "exp": 1716234000,
        }

        with pytest.raises(HTTPException) as exc_info:
            get_user_sub(payload)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"

    def test_empty_sub_raise_error(self):
        """
        When payload stores an empty sub, it should raise an error.
        """
        payload = {
            "sub": "",
            "iss": "https://your-tenant.auth0.com/",
            "aud": "https://your-api.com",
            "iat": 1716230400,
            "exp": 1716234000,
        }

        with pytest.raises(HTTPException) as exc_info:
            get_user_sub(payload)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"


class TestSerializeStructure:
    def test_serializes_expected_structure_fields(
        self, user_factory, structure_factory
    ):
        """
        serialize_structure should convert IDs and datetimes into API-safe values.
        """
        structure_id = uuid.uuid4()
        uploaded_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        user_factory(user_sub="auth0|testuser")
        structure = structure_factory(
            structure_id=structure_id,
            user_sub="auth0|testuser",
            name="Water",
            formula="H2O",
            location="s3://test-bucket/structures/water.xyz",
            notes="stable molecule",
            uploaded_at=uploaded_at,
        )

        result = serialize_structure(structure)

        assert result == {
            "structure_id": str(structure_id),
            "name": "Water",
            "location": "s3://test-bucket/structures/water.xyz",
            "notes": "stable molecule",
            "uploaded_at": structure.uploaded_at.isoformat(),
        }


class TestSerializeJob:
    def test_serializes_job_with_relationships_and_runtime(
        self, user_factory, job_factory, structure_factory, tag_factory
    ):
        """
        serialize_job should include related structures, tag names, timestamps, and flags.
        """
        job_id = uuid.uuid4()
        submitted_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        completed_at = datetime(2026, 1, 2, 4, 5, 6, tzinfo=timezone.utc)
        user_factory(user_sub="auth0|testuser")
        structure = structure_factory(name="Methane", formula="CH4")
        first_tag = tag_factory(name="organic")
        second_tag = tag_factory(name="demo")

        job = job_factory(
            job_id=job_id,
            job_name="Methane single point",
            job_notes="baseline calculation",
            filename="methane.xyz",
            status="completed",
            calculation_type="energy",
            method="hf",
            basis_set="sto-3g",
            charge=0,
            multiplicity=1,
            submitted_at=submitted_at,
            completed_at=completed_at,
            user_sub="auth0|testuser",
            slurm_id="12345",
            runtime=timedelta(hours=1, minutes=2, seconds=3),
            is_deleted=False,
            is_public=True,
            structures=[structure],
            tags=[first_tag, second_tag],
        )

        result = serialize_job(job)

        assert result["job_id"] == str(job_id)
        assert result["job_name"] == "Methane single point"
        assert result["job_notes"] == "baseline calculation"
        assert result["filename"] == "methane.xyz"
        assert result["status"] == "completed"
        assert result["calculation_type"] == "energy"
        assert result["method"] == "hf"
        assert result["basis_set"] == "sto-3g"
        assert result["charge"] == 0
        assert result["multiplicity"] == 1
        assert result["submitted_at"] == job.submitted_at.isoformat()
        assert result["completed_at"] == job.completed_at.isoformat()
        assert result["user_sub"] == "auth0|testuser"
        assert result["slurm_id"] == "12345"
        assert result["runtime"] == "1:02:03"
        assert result["is_deleted"] is False
        assert result["is_public"] is True
        assert result["structures"] == [serialize_structure(structure)]
        assert sorted(result["tags"]) == ["demo", "organic"]

    def test_serializes_none_optional_job_fields(self, user_factory, job_factory):
        """
        Optional job fields should serialize as None when absent.
        """
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            completed_at=None,
            runtime=None,
            slurm_id=None,
        )

        result = serialize_job(job)

        assert result["completed_at"] is None
        assert result["runtime"] is None
        assert result["slurm_id"] is None


class TestCleanUpUploadCache:
    def test_removes_existing_directory(self, tmp_path):
        """
        clean_up_upload_cache should remove an existing job upload directory.
        """
        job_dir = tmp_path / "job-cache"
        nested_dir = job_dir / "nested"
        nested_dir.mkdir(parents=True)
        (nested_dir / "input.xyz").write_text("1\nH\n", encoding="utf-8")

        clean_up_upload_cache(str(job_dir))

        assert not job_dir.exists()

    def test_missing_directory_does_not_fail(self, tmp_path):
        """
        clean_up_upload_cache should be safe to call for paths that do not exist.
        """
        missing_dir = tmp_path / "missing-job-cache"

        clean_up_upload_cache(str(missing_dir))

        assert not missing_dir.exists()
