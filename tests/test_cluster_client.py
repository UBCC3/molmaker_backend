import json
import subprocess
import uuid
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

import orchestration.cluster_client as cluster_client
from enum_types import CalculationType, JobStatus
from orchestration.cluster_client import (
    AllocationStatus,
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
    SubmissionOutcomeUnknownError,
)


JOB_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
WORK_DIR = PurePosixPath("/home/test/molmaker")
RUN_OPTIONS = {
    "check": False,
    "capture_output": True,
    "text": True,
    "timeout": 37,
}


@pytest.fixture
def client():
    return ClusterDispatchClient(WORK_DIR, timeout_seconds=37)


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
    }
    arguments.update(overrides)
    return client.submit_job(**arguments)


def ssh_command(remote_command):
    return ["ssh", "cluster", remote_command]


def test_client_reads_cluster_settings(monkeypatch):
    monkeypatch.setenv("CLUSTER_WORK_DIR", "/remote/molmaker")
    monkeypatch.setenv("SLURM_COMMAND_TIMEOUT_SECONDS", "45")

    client = ClusterDispatchClient.from_env()

    assert client.cluster_work_dir == PurePosixPath("/remote/molmaker")
    assert client.timeout_seconds == 45


def test_submit_custom_job_builds_one_safely_quoted_command(client, run_dispatch):
    calls = run_dispatch(stdout="12345\n")

    result = submit(
        client,
        calculation_type=CalculationType.geometry,
        method="ccsd(t)",
        basis_set="basis with spaces",
        charge=-1,
        multiplicity=2,
        optimization_type="ts",
        has_keywords=True,
    )

    assert result == "12345"
    assert calls == [
        (
            ssh_command(
                "python3 /home/test/molmaker/dispatch.py submit "
                "/home/test/molmaker/jobs/"
                "11111111-1111-4111-8111-111111111111/input.xyz "
                "11111111-1111-4111-8111-111111111111 optimization "
                "'ccsd(t)' 'basis with spaces' -1 2 --opt-type ts "
                "--keywords-file /home/test/molmaker/jobs/"
                "11111111-1111-4111-8111-111111111111/keywords.json"
            ),
            RUN_OPTIONS,
        )
    ]


def test_submit_standard_job_omits_method_and_basis_set(client, run_dispatch):
    calls = run_dispatch(stdout="24680")

    result = submit(
        client,
        calculation_type=CalculationType.standard,
        method="unused",
        basis_set="unused",
        charge=1,
        optimization_type="ground",
    )

    assert result == "24680"
    assert calls == [
        (
            ssh_command(
                "python3 /home/test/molmaker/dispatch.py submit "
                "/home/test/molmaker/jobs/"
                "11111111-1111-4111-8111-111111111111/input.xyz "
                "11111111-1111-4111-8111-111111111111 1 1 --opt-type ground"
            ),
            RUN_OPTIONS,
        )
    ]


@pytest.mark.parametrize(
    ("method_name", "subcommand"),
    [
        ("find_active_allocation", "find-active"),
        ("find_accounting_allocation", "find-accounting"),
    ],
)
def test_job_lookups_use_the_exact_job_id(
    client, run_dispatch, method_name, subcommand
):
    calls = run_dispatch(
        stdout=json.dumps(
            {
                "jobs": [
                    {
                        "slurm_id": "12345",
                        "job_name": str(JOB_ID),
                        "state": "PENDING",
                    }
                ]
            }
        )
    )

    result = getattr(client, method_name)(JOB_ID)

    assert result == "12345"
    assert calls == [
        (
            ssh_command(
                f"python3 /home/test/molmaker/dispatch.py {subcommand} {JOB_ID}"
            ),
            RUN_OPTIONS,
        )
    ]


def test_job_lookup_returns_none_when_no_job_matches(client, run_dispatch):
    run_dispatch(stdout='{"jobs":[]}')

    assert client.find_active_allocation(JOB_ID) is None


def test_status_batch_returns_typed_rows_and_allows_missing_jobs(client, run_dispatch):
    calls = run_dispatch(
        stdout=json.dumps(
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

    result = client.get_allocation_statuses(["12345", "67890"])

    assert result == {"12345": AllocationStatus("12345", "CANCELLED+", "0:15", 62)}
    assert calls == [
        (
            ssh_command(
                "python3 /home/test/molmaker/dispatch.py status-batch 12345 67890"
            ),
            RUN_OPTIONS,
        )
    ]


def test_cancel_allocation_checks_the_acknowledgement(client, run_dispatch):
    calls = run_dispatch(stdout='{"slurm_id":"12345","cancel_requested":true}')

    client.cancel_allocation("12345")

    assert calls == [
        (
            ssh_command(
                "python3 /home/test/molmaker/dispatch.py cancel-allocation 12345"
            ),
            RUN_OPTIONS,
        )
    ]


def test_cancel_allocation_rejects_a_bad_acknowledgement(client, run_dispatch):
    run_dispatch(stdout='{"slurm_id":"12345","cancel_requested":1}')

    with pytest.raises(ClusterServiceError, match="Invalid"):
        client.cancel_allocation("12345")


def test_upload_artifacts_uses_the_job_manifest(client, run_dispatch):
    calls = run_dispatch(stdout=f'{{"job_id":"{JOB_ID}","uploaded":true}}')

    client.upload_artifacts(
        job_id=JOB_ID,
        calculation_type=CalculationType.frequency,
        terminal_status=JobStatus.completed,
    )

    assert calls == [
        (
            ssh_command(
                "python3 /home/test/molmaker/dispatch.py upload-artifacts "
                "11111111-1111-4111-8111-111111111111 frequency completed "
                "/home/test/molmaker/jobs/"
                "11111111-1111-4111-8111-111111111111/upload-urls.json"
            ),
            RUN_OPTIONS,
        )
    ]


def test_allow_list_rejection_is_a_shared_cluster_failure(client, run_dispatch):
    run_dispatch(
        stdout="Command rejected by allowed_commands.sh: sacct\n",
        returncode=0,
    )

    with pytest.raises(ClusterServiceError, match="rejected"):
        client.find_accounting_allocation(JOB_ID)


@pytest.mark.parametrize(
    ("returncode", "error_type"),
    [
        (cluster_client.JOB_ERROR_EXIT_CODE, JobDispatchError),
        (
            cluster_client.SUBMISSION_OUTCOME_UNKNOWN_EXIT_CODE,
            SubmissionOutcomeUnknownError,
        ),
        (cluster_client.SERVICE_ERROR_EXIT_CODE, ClusterServiceError),
        (255, SubmissionOutcomeUnknownError),
    ],
)
def test_submission_uses_the_dispatch_failure_category(
    client, run_dispatch, returncode, error_type
):
    run_dispatch(stderr="safe dispatch error", returncode=returncode)

    with pytest.raises(error_type):
        submit(client)


def test_submission_with_no_valid_id_has_an_unknown_outcome(client, run_dispatch):
    run_dispatch(stdout="not-a-slurm-id")

    with pytest.raises(SubmissionOutcomeUnknownError):
        submit(client)


@pytest.mark.parametrize(
    ("operation", "error_type"),
    [
        ("submission", SubmissionOutcomeUnknownError),
        ("lookup", ClusterServiceError),
    ],
)
def test_timeout_category_depends_on_the_operation(
    client, run_dispatch, operation, error_type
):
    run_dispatch(error=subprocess.TimeoutExpired(["ssh"], timeout=37))

    with pytest.raises(error_type, match="timed out|look up"):
        if operation == "submission":
            submit(client)
        else:
            client.find_active_allocation(JOB_ID)


def test_success_with_stderr_is_not_accepted(client, run_dispatch):
    run_dispatch(stdout='{"jobs":[]}', stderr="unexpected warning\n")

    with pytest.raises(ClusterServiceError, match="unexpected error output"):
        client.find_active_allocation(JOB_ID)


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        "[]",
        '{"wrong":[]}',
        '{"jobs":"not a list"}',
        '{"jobs":[{"slurm_id":"12345"}]}',
    ],
)
def test_lookup_rejects_malformed_responses(client, run_dispatch, stdout):
    run_dispatch(stdout=stdout)

    with pytest.raises(ClusterServiceError):
        client.find_active_allocation(JOB_ID)


def test_lookup_rejects_multiple_matches(client, run_dispatch):
    row = {"slurm_id": "12345", "job_name": str(JOB_ID)}
    run_dispatch(stdout=json.dumps({"jobs": [row, row]}))

    with pytest.raises(ClusterServiceError, match="multiple"):
        client.find_active_allocation(JOB_ID)


@pytest.mark.parametrize(
    "row",
    [
        {"slurm_id": "not-an-id", "job_name": str(JOB_ID)},
        {"slurm_id": "12345", "job_name": str(uuid.uuid4())},
    ],
)
def test_lookup_rejects_wrong_fields(client, run_dispatch, row):
    run_dispatch(stdout=json.dumps({"jobs": [row]}))

    with pytest.raises(ClusterServiceError):
        client.find_active_allocation(JOB_ID)


@pytest.mark.parametrize(
    "rows",
    [
        [
            {
                "slurm_id": "99999",
                "state": "RUNNING",
                "exit_code": None,
                "elapsed_seconds": 2,
            }
        ],
        [
            {
                "slurm_id": "12345",
                "state": "RUNNING",
                "exit_code": None,
                "elapsed_seconds": 2,
            }
        ]
        * 2,
    ],
)
def test_status_batch_rejects_unrequested_or_duplicate_rows(client, run_dispatch, rows):
    run_dispatch(stdout=json.dumps({"jobs": rows}))

    with pytest.raises(ClusterServiceError, match="Invalid"):
        client.get_allocation_statuses(["12345"])


def test_invalid_ids_do_not_contact_ssh(client, run_dispatch):
    calls = run_dispatch(stdout="12345")

    with pytest.raises(ValueError, match="job_id"):
        client.find_active_allocation("not-a-uuid")
    with pytest.raises(ValueError, match="slurm_id"):
        client.cancel_allocation("123; touch /tmp/example")

    assert calls == []


def test_cluster_errors_do_not_expose_stderr(client, run_dispatch):
    secret = "https://storage.example/file?X-Amz-Signature=secret"
    run_dispatch(stderr=secret, returncode=cluster_client.JOB_ERROR_EXIT_CODE)

    with pytest.raises(JobDispatchError) as caught:
        client.upload_artifacts(
            job_id=JOB_ID,
            calculation_type=CalculationType.energy,
            terminal_status=JobStatus.failed,
        )

    assert secret not in str(caught.value)
