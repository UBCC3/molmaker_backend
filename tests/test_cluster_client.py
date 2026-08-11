import json
import subprocess
import uuid
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

import orchestration.cluster_client as cluster_client
from enum_types import CalculationType, JobStatus
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
    SlurmJobStatus,
    SubmissionOutcomeUnknownError,
)
from settings import BackendSettings


JOB_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
WORK_DIR = PurePosixPath("/home/test/molmaker")
REMOTE_COMMAND = "python3 /home/test/molmaker/dispatch.py"


def success(result):
    return json.dumps(
        {
            "protocol_version": 1,
            "ok": True,
            "result": result,
        }
    )


def failure(category, message="safe failure"):
    return json.dumps(
        {
            "protocol_version": 1,
            "ok": False,
            "error": {"category": category, "message": message},
        }
    )


@pytest.fixture
def client():
    return ClusterDispatchClient(
        WORK_DIR,
        timeout_seconds=37,
        storage_timeout_seconds=41,
    )


@pytest.fixture
def run_dispatch(monkeypatch):
    def configure(*, stdout="", stderr="", returncode=0, error=None):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if error is not None:
                raise error
            return SimpleNamespace(
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            )

        monkeypatch.setattr(cluster_client.subprocess, "run", fake_run)
        return calls

    return configure


def submit(client, **overrides):
    arguments = {
        "job_id": JOB_ID,
        "calculation_type": CalculationType.energy,
        "method": "hf",
        "basis_set": "sto-3g",
        "charge": 0,
        "multiplicity": 1,
        "optimization_type": None,
        "input_xyz": "1\n\nH 0 0 0\n",
        "keywords": None,
        "recover_existing": False,
    }
    arguments.update(overrides)
    return client.submit_job(**arguments)


def sent_request(calls, *, timeout=37):
    assert len(calls) == 1
    command, options = calls[0]
    assert command == ["ssh", "cluster", REMOTE_COMMAND]
    assert options["check"] is False
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["timeout"] == timeout
    return json.loads(options["input"])


def test_client_reads_cluster_settings(monkeypatch):
    monkeypatch.setenv("CLUSTER_WORK_DIR", "/remote/molmaker")
    monkeypatch.setenv("SLURM_COMMAND_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("STORAGE_OPERATION_TIMEOUT_SECONDS", "46")

    client = ClusterDispatchClient.from_settings(BackendSettings.from_env())

    assert client.cluster_work_dir == PurePosixPath("/remote/molmaker")
    assert client.timeout_seconds == 45
    assert client.storage_timeout_seconds == 46


def test_submit_sends_inputs_and_settings_in_one_json_request(client, run_dispatch):
    calls = run_dispatch(
        stdout=success({"slurm_id": "12345", "recovered": False})
    )

    result = submit(
        client,
        calculation_type=CalculationType.geometry,
        method="ccsd(t)",
        basis_set="basis with spaces",
        charge=-1,
        multiplicity=2,
        optimization_type="ts",
        keywords={"scf_type": "df"},
        recover_existing=True,
    )

    assert result == "12345"
    assert sent_request(calls) == {
        "protocol_version": 1,
        "command": "submit",
        "job_id": str(JOB_ID),
        "calculation_type": "optimization",
        "method": "ccsd(t)",
        "basis_set": "basis with spaces",
        "charge": -1,
        "multiplicity": 2,
        "optimization_type": "ts",
        "input_xyz": "1\n\nH 0 0 0\n",
        "keywords": {"scf_type": "df"},
        "recover_existing": True,
    }


def test_submit_accepts_a_recovered_job(client, run_dispatch):
    run_dispatch(stdout=success({"slurm_id": "24680", "recovered": True}))

    assert submit(client, recover_existing=True) == "24680"


def test_find_submission_returns_a_match_or_none(client, run_dispatch):
    calls = run_dispatch(stdout=success({"slurm_id": "12345"}))

    assert client.find_submission(JOB_ID) == "12345"
    assert sent_request(calls) == {
        "protocol_version": 1,
        "command": "find-submission",
        "job_id": str(JOB_ID),
    }

    run_dispatch(stdout=success({"slurm_id": None}))
    assert client.find_submission(JOB_ID) is None


def test_status_batch_returns_typed_rows_and_allows_missing_jobs(
    client,
    run_dispatch,
):
    calls = run_dispatch(
        stdout=success(
            {
                "jobs": [
                    {
                        "slurm_id": "12345",
                        "state": "CANCELLED+",
                        "exit_code": "0:15",
                        "elapsed_seconds": 62,
                    }
                ]
            }
        )
    )

    result = client.get_slurm_job_statuses(["12345", "67890"])

    assert result == {
        "12345": SlurmJobStatus("12345", "CANCELLED+", "0:15", 62)
    }
    assert sent_request(calls) == {
        "protocol_version": 1,
        "command": "status-batch",
        "slurm_ids": ["12345", "67890"],
    }


def test_cancel_slurm_job_checks_the_acknowledgement(client, run_dispatch):
    calls = run_dispatch(
        stdout=success({"slurm_id": "12345", "cancel_requested": True})
    )

    client.cancel_slurm_job("12345")

    assert sent_request(calls)["command"] == "cancel"


def test_upload_artifacts_sends_fresh_urls_only_in_json_stdin(
    client,
    run_dispatch,
):
    calls = run_dispatch(
        stdout=success({"job_id": str(JOB_ID), "uploaded": True})
    )
    secret_url = "https://storage.example/upload?signature=secret"

    client.upload_artifacts(
        job_id=JOB_ID,
        calculation_type=CalculationType.frequency,
        terminal_status=JobStatus.failed,
        upload_urls={"zip": secret_url, "error": secret_url},
        allow_missing_error=True,
    )

    request = sent_request(calls, timeout=41)
    assert secret_url not in " ".join(calls[0][0])
    assert request == {
        "protocol_version": 1,
        "command": "upload-artifacts",
        "job_id": str(JOB_ID),
        "calculation_type": "frequency",
        "terminal_status": "failed",
        "upload_urls": {"zip": secret_url, "error": secret_url},
        "allow_missing_error": True,
    }


@pytest.mark.parametrize(
    ("category", "returncode", "error_type"),
    [
        ("job_error", cluster_client.JOB_ERROR_EXIT_CODE, JobDispatchError),
        (
            "submission_outcome_unknown",
            cluster_client.SUBMISSION_OUTCOME_UNKNOWN_EXIT_CODE,
            SubmissionOutcomeUnknownError,
        ),
        (
            "service_error",
            cluster_client.SERVICE_ERROR_EXIT_CODE,
            ClusterServiceError,
        ),
    ],
)
def test_submission_uses_the_dispatch_error_category(
    client,
    run_dispatch,
    category,
    returncode,
    error_type,
):
    run_dispatch(stdout=failure(category), returncode=returncode)

    with pytest.raises(error_type):
        submit(client)


def test_submission_transport_timeout_has_an_unknown_outcome(client, run_dispatch):
    run_dispatch(error=subprocess.TimeoutExpired(["ssh"], timeout=37))

    with pytest.raises(SubmissionOutcomeUnknownError):
        submit(client)


def test_read_only_timeout_is_a_shared_service_failure(client, run_dispatch):
    run_dispatch(error=subprocess.TimeoutExpired(["ssh"], timeout=37))

    with pytest.raises(ClusterServiceError, match="timed out"):
        client.find_submission(JOB_ID)


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        success({"slurm_id": "12345"}),
        success({"slurm_id": "12345", "recovered": False, "extra": True}),
    ],
)
def test_submission_with_a_malformed_response_has_an_unknown_outcome(
    client,
    run_dispatch,
    response,
):
    run_dispatch(stdout=response)

    with pytest.raises(SubmissionOutcomeUnknownError):
        submit(client)


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        json.dumps({"protocol_version": 2, "ok": True, "result": {}}),
        json.dumps({"protocol_version": 1, "ok": True, "result": []}),
        json.dumps(
            {
                "protocol_version": 1,
                "ok": False,
                "error": {"category": "unknown", "message": "bad"},
            }
        ),
    ],
)
def test_lookup_rejects_a_malformed_response(client, run_dispatch, response):
    run_dispatch(stdout=response)

    with pytest.raises(ClusterServiceError):
        client.find_submission(JOB_ID)


def test_successful_response_is_trusted_even_when_ssh_warns(client, run_dispatch):
    run_dispatch(
        stdout=success({"slurm_id": None}),
        stderr="non-fatal SSH warning",
    )

    assert client.find_submission(JOB_ID) is None


def test_allow_list_rejection_is_a_shared_cluster_failure(client, run_dispatch):
    run_dispatch(
        stdout="Command rejected by allowed_commands.sh",
        returncode=0,
    )

    with pytest.raises(ClusterServiceError, match="rejected"):
        client.find_submission(JOB_ID)


def test_result_shapes_are_checked(client, run_dispatch):
    run_dispatch(stdout=success({"slurm_id": "12345", "extra": True}))
    with pytest.raises(ClusterServiceError):
        client.find_submission(JOB_ID)

    run_dispatch(stdout=success({"slurm_id": "12345", "recovered": 1}))
    with pytest.raises(SubmissionOutcomeUnknownError):
        submit(client)

    run_dispatch(
        stdout=success({"slurm_id": "12345", "cancel_requested": 1})
    )
    with pytest.raises(ClusterServiceError):
        client.cancel_slurm_job("12345")


def test_status_batch_rejects_unrequested_or_duplicate_rows(client, run_dispatch):
    row = {
        "slurm_id": "99999",
        "state": "RUNNING",
        "exit_code": None,
        "elapsed_seconds": 2,
    }
    run_dispatch(stdout=success({"jobs": [row]}))
    with pytest.raises(ClusterServiceError):
        client.get_slurm_job_statuses(["12345"])

    row["slurm_id"] = "12345"
    run_dispatch(stdout=success({"jobs": [row, row]}))
    with pytest.raises(ClusterServiceError):
        client.get_slurm_job_statuses(["12345"])


def test_invalid_ids_do_not_contact_ssh(client, run_dispatch):
    calls = run_dispatch()

    with pytest.raises(ValueError, match="job_id"):
        client.find_submission("not-a-uuid")
    with pytest.raises(ValueError, match="slurm_id"):
        client.cancel_slurm_job("123; touch /tmp/example")

    assert calls == []


def test_cluster_errors_do_not_expose_urls(client, run_dispatch):
    secret_url = "https://storage.example/file?signature=secret"
    run_dispatch(
        stdout=failure("job_error", secret_url),
        stderr=secret_url,
        returncode=cluster_client.JOB_ERROR_EXIT_CODE,
    )

    with pytest.raises(JobDispatchError) as caught:
        client.upload_artifacts(
            job_id=JOB_ID,
            calculation_type=CalculationType.energy,
            terminal_status=JobStatus.failed,
            upload_urls={"zip": secret_url, "error": secret_url},
        )

    assert secret_url not in str(caught.value)


def test_client_has_no_file_staging_methods(client):
    assert not hasattr(client, "stage_job_inputs")
    assert not hasattr(client, "stage_upload_manifest")
