from unittest.mock import Mock
from uuid import UUID

from conftest import TestingSessionLocal
from enum_types import JobStatus
from models import Job
from orchestration.cluster_client import ClusterDispatchClient, SlurmJobStatus
from orchestration.finalisation_reconciler import FinalisationReconciler
from settings import OrchestrationSettings
from orchestration.status_reconciler import StatusReconciler
from orchestration.submission_reconciler import SubmissionReconciler


def _settings():
    return OrchestrationSettings(
        submission_poll_interval_seconds=5,
        submission_query_limit=25,
        status_poll_interval_seconds=15,
        status_batch_size=100,
        finalisation_poll_interval_seconds=5,
        finalisation_query_limit=25,
        max_attempts=3,
        outage_initial_backoff_seconds=15,
        outage_max_backoff_seconds=300,
        slurm_command_timeout_seconds=120,
        storage_operation_timeout_seconds=120,
        backend_job_staging_min_space_gb=1,
    )


def _saved_job(db, job_id):
    db.expire_all()
    return db.get(Job, job_id)


def test_api_job_moves_through_all_three_reconcilers(
    client,
    db,
    tmp_path,
    monkeypatch,
    user_factory,
):
    work_directory = tmp_path / "backend-work"
    monkeypatch.setenv("BACKEND_WORK_DIR", str(work_directory))
    user_factory(user_sub="auth0|testuser")
    response = client.post(
        "/calculation/custom",
        data={
            "calculation_type": "energy",
            "method": "b3lyp",
            "basis_set": "6-31g",
            "charge": "0",
            "multiplicity": "1",
            "job_name": "Lifecycle test",
        },
        files={"file": ("input.xyz", b"1\n\nH 0 0 0\n", "chemical/x-xyz")},
    )
    assert response.status_code == 201
    job_id = UUID(response.json()["job_id"])
    assert _saved_job(db, job_id).status == JobStatus.submitting.value

    settings = _settings()
    cluster_client = Mock(spec=ClusterDispatchClient)
    cluster_client.submit_job.return_value = "7001"
    SubmissionReconciler(
        session_factory=TestingSessionLocal,
        cluster_client=cluster_client,
        settings=settings,
        backend_jobs_directory=work_directory / "jobs",
        sleep=Mock(),
        clock=Mock(return_value=0.0),
    ).run_round()
    submitted = _saved_job(db, job_id)
    assert submitted.status == JobStatus.submitted.value
    assert submitted.slurm_id == "7001"
    assert not (work_directory / "jobs" / str(job_id)).exists()

    cluster_client.get_slurm_job_statuses.side_effect = [
        {
            "7001": SlurmJobStatus(
                slurm_id="7001",
                state="RUNNING",
                exit_code=None,
                elapsed_seconds=10,
            )
        },
        {
            "7001": SlurmJobStatus(
                slurm_id="7001",
                state="COMPLETED",
                exit_code="0:0",
                elapsed_seconds=42,
            )
        },
    ]
    status_reconciler = StatusReconciler(
        session_factory=TestingSessionLocal,
        cluster_client=cluster_client,
        settings=settings,
        sleep=Mock(),
        clock=Mock(return_value=0.0),
    )
    status_reconciler.run_round()
    assert _saved_job(db, job_id).status == JobStatus.running.value

    status_reconciler.run_round()
    finalising = _saved_job(db, job_id)
    assert finalising.status == JobStatus.finalising.value
    assert finalising.terminal_status == JobStatus.completed.value

    FinalisationReconciler(
        session_factory=TestingSessionLocal,
        cluster_client=cluster_client,
        settings=settings,
        generate_upload_urls=Mock(
            return_value={
                "zip": "https://upload.test/archive",
                "result": "https://upload.test/result",
            }
        ),
        required_artifacts_exist=Mock(return_value=False),
        sleep=Mock(),
        clock=Mock(return_value=0.0),
    ).run_round()

    completed = _saved_job(db, job_id)
    assert completed.status == JobStatus.completed.value
    assert completed.is_uploaded is True
    assert completed.runtime.total_seconds() == 42
    cluster_client.stage_job_inputs.assert_called_once()
    cluster_client.submit_job.assert_called_once()
    cluster_client.stage_upload_manifest.assert_called_once()
    cluster_client.upload_artifacts.assert_called_once()
