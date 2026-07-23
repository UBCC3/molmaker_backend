import logging

from fastapi import HTTPException
import pytest
from sqlalchemy.exc import IntegrityError

from utils import (
    clean_up_upload_cache,
    commit_or_rollback,
    get_user_sub,
)


class TestCommitOrRollback:
    def test_commits_and_refreshes_requested_object(self, mocker):
        db = mocker.Mock()
        instance = object()

        commit_or_rollback(db, refresh=instance)

        db.commit.assert_called_once_with()
        db.refresh.assert_called_once_with(instance)
        db.rollback.assert_not_called()

    def test_runs_staging_operation_inside_protected_block(self, mocker):
        db = mocker.Mock()
        stage = mocker.Mock()

        commit_or_rollback(db, before_commit=stage)

        stage.assert_called_once_with()
        db.commit.assert_called_once_with()

    def test_rolls_back_when_add_fails_and_hides_database_error(
        self,
        mocker,
        caplog,
    ):
        db = mocker.Mock()
        instance = object()
        db.add.side_effect = RuntimeError("private add failure details")

        with caplog.at_level(logging.ERROR, logger="utils"):
            with pytest.raises(HTTPException) as error:
                commit_or_rollback(
                    db,
                    before_commit=lambda: db.add(instance),
                )

        assert error.value.status_code == 500
        assert error.value.detail == "Could not save changes"
        assert "private add failure details" not in error.value.detail
        assert "private add failure details" in caplog.text
        db.add.assert_called_once_with(instance)
        db.rollback.assert_called_once_with()
        db.commit.assert_not_called()

    def test_rolls_back_when_flush_fails(self, mocker, caplog):
        db = mocker.Mock()
        db.flush.side_effect = RuntimeError("flush failed")

        with caplog.at_level(logging.ERROR, logger="utils"):
            with pytest.raises(HTTPException) as error:
                commit_or_rollback(db, before_commit=db.flush)

        assert error.value.status_code == 500
        assert error.value.detail == "Could not save changes"
        assert "flush failed" in caplog.text
        db.flush.assert_called_once_with()
        db.rollback.assert_called_once_with()
        db.commit.assert_not_called()

    def test_rolls_back_and_maps_constraint_error(self, mocker, caplog):
        db = mocker.Mock()
        db.commit.side_effect = IntegrityError("statement", {}, RuntimeError("duplicate"))

        with caplog.at_level(logging.ERROR, logger="utils"):
            with pytest.raises(HTTPException) as error:
                commit_or_rollback(
                    db,
                    integrity_error_detail="Duplicate record",
                )

        assert error.value.status_code == 400
        assert error.value.detail == "Duplicate record"
        assert "duplicate" in caplog.text
        db.rollback.assert_called_once_with()

    def test_rolls_back_when_commit_fails_and_uses_safe_detail(
        self,
        mocker,
        caplog,
    ):
        db = mocker.Mock()
        db.commit.side_effect = RuntimeError("private commit failure details")

        with caplog.at_level(logging.ERROR, logger="utils"):
            with pytest.raises(HTTPException) as error:
                commit_or_rollback(
                    db,
                    error_detail="Could not create record",
                )

        assert error.value.status_code == 500
        assert error.value.detail == "Could not create record"
        assert "private commit failure details" not in error.value.detail
        assert "private commit failure details" in caplog.text
        db.rollback.assert_called_once_with()

    def test_refresh_failure_does_not_roll_back_or_run_cleanup(
        self,
        mocker,
        caplog,
    ):
        db = mocker.Mock()
        instance = object()
        cleanup = mocker.Mock()
        db.refresh.side_effect = RuntimeError("refresh failed")

        with caplog.at_level(logging.ERROR, logger="utils"):
            with pytest.raises(HTTPException) as error:
                commit_or_rollback(
                    db,
                    refresh=instance,
                    on_error=cleanup,
                )

        assert error.value.status_code == 500
        assert error.value.detail == (
            "Changes were saved, but the updated data could not be loaded"
        )
        assert "refresh failed" in caplog.text
        db.commit.assert_called_once_with()
        db.refresh.assert_called_once_with(instance)
        db.rollback.assert_not_called()
        cleanup.assert_not_called()

    def test_runs_cleanup_after_save_failure(self, mocker):
        db = mocker.Mock()
        db.commit.side_effect = RuntimeError("commit failed")
        cleanup = mocker.Mock()

        with pytest.raises(HTTPException):
            commit_or_rollback(db, on_error=cleanup)

        db.rollback.assert_called_once_with()
        cleanup.assert_called_once_with()

    def test_cleanup_failure_does_not_replace_save_error(self, mocker, caplog):
        db = mocker.Mock()
        db.commit.side_effect = RuntimeError("commit failed")
        cleanup = mocker.Mock(side_effect=RuntimeError("cleanup failed"))

        with caplog.at_level(logging.ERROR, logger="utils"):
            with pytest.raises(HTTPException) as error:
                commit_or_rollback(
                    db,
                    error_detail="Could not save record",
                    on_error=cleanup,
                )

        assert error.value.status_code == 500
        assert error.value.detail == "Could not save record"
        assert "commit failed" in caplog.text
        assert "cleanup failed" in caplog.text
        db.rollback.assert_called_once_with()
        cleanup.assert_called_once_with()


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
