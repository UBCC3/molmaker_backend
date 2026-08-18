"""Client for the restricted JSON cluster dispatch interface."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Literal
from uuid import UUID

from enum_types import CalculationType, JobStatus
from settings import BackendSettings

SSH_HOST = "cluster"
PROTOCOL_VERSION = 1
MAX_CLUSTER_RESPONSE_BYTES = 64 * 1024 * 1024
COMMAND_REJECTION = "Command rejected by allowed_commands.sh"
INVALID_STATUS_RESPONSE = "Invalid status lookup response from cluster"
INVALID_FINALISATION_RESPONSE = "Invalid artifact finalisation response from cluster"
JOB_ERROR_EXIT_CODE = 10
SUBMISSION_OUTCOME_UNKNOWN_EXIT_CODE = 11
SERVICE_ERROR_EXIT_CODE = 12
UNKNOWN_SUBMISSION_MESSAGE = (
    "Submission may have succeeded; look up the job before retrying"
)

ERROR_CATEGORIES = {
    "job_error",
    "submission_outcome_unknown",
    "service_error",
}
RESULT_FIELDS_BY_COMMAND = {
    "submit": {"slurm_id", "recovered"},
    "find-submission": {"slurm_id"},
    "status-batch": {"jobs"},
    "cancel": {"slurm_id", "cancel_requested"},
    "upload-artifacts": {
        "job_id",
        "archive_uploaded",
        "calculation_result",
        "calculation_error",
        "artifacts",
    },
    "ack-finalisation": {"job_id", "cleaned"},
}
JOB_SPECIFIC_COMMANDS = {
    "submit",
    "cancel",
    "upload-artifacts",
    "ack-finalisation",
}


class ClusterClientError(RuntimeError):
    """A cluster client operation failed."""


class JobDispatchError(ClusterClientError):
    """This job's cluster operation failed and may be retried."""


class SubmissionOutcomeUnknownError(ClusterClientError):
    """The job may have been submitted; recover it before resubmitting."""


class ClusterServiceError(ClusterClientError):
    """A shared SSH or cluster service failed; retry after backoff."""


class _ClusterResponseTooLargeError(ClusterServiceError):
    """The cluster response exceeded the bounded transport contract."""


@dataclass(frozen=True)
class SlurmJobStatus:
    slurm_id: str
    state: str
    exit_code: str | None
    elapsed_seconds: int | None
    has_result_error: bool = False


@dataclass(frozen=True)
class FinalisationResult:
    """Validated database-bound data returned after the archive upload."""

    calculation_result: dict[str, Any] | None
    calculation_error: dict[str, Any] | None
    artifacts: dict[str, str]


def _job_id(value: UUID | str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise ValueError("job_id must be a UUID") from None


def _slurm_id(value: str | int) -> str:
    value = str(value)
    if not value.isascii() or not value.isdigit() or int(value) == 0:
        raise ValueError("slurm_id must be a positive integer")
    return value


def _parse_response(output: str, operation: str) -> dict[str, Any]:
    """Parse the common response envelope returned by dispatch.py."""

    invalid_message = f"Invalid {operation} response from cluster"
    try:
        if len(output.encode("utf-8")) > MAX_CLUSTER_RESPONSE_BYTES:
            raise _ClusterResponseTooLargeError(
                f"{operation.capitalize()} response is too large"
            )
        response = json.loads(output, parse_constant=_reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ClusterServiceError(invalid_message) from None

    if not isinstance(response, dict):
        raise ClusterServiceError(invalid_message)
    if response.get("protocol_version") != PROTOCOL_VERSION:
        raise ClusterServiceError(f"Unsupported {operation} response version")

    if response.get("ok") is True and isinstance(response.get("result"), dict):
        return response
    if response.get("ok") is False and isinstance(response.get("error"), dict):
        if response["error"].get("category") in ERROR_CATEGORIES:
            return response
    raise ClusterServiceError(invalid_message)


def _finalisation_result(
    result: dict[str, Any],
    *,
    expected_job_id: str,
) -> FinalisationResult:
    """Validate the transport-level finalisation response."""

    if result["job_id"] != expected_job_id:
        raise ClusterServiceError(INVALID_FINALISATION_RESPONSE)
    if result["archive_uploaded"] is not True:
        raise ClusterServiceError(INVALID_FINALISATION_RESPONSE)
    calculation_result = result["calculation_result"]
    calculation_error = result["calculation_error"]
    if calculation_result is not None and not isinstance(calculation_result, dict):
        raise ClusterServiceError(INVALID_FINALISATION_RESPONSE)
    if calculation_error is not None and not isinstance(calculation_error, dict):
        raise ClusterServiceError(INVALID_FINALISATION_RESPONSE)

    artifacts = result["artifacts"]
    if not isinstance(artifacts, dict):
        raise ClusterServiceError(INVALID_FINALISATION_RESPONSE)
    for kind, content in artifacts.items():
        if (
            not isinstance(kind, str)
            or not kind
            or not isinstance(content, str)
            or not content
            or "\x00" in content
        ):
            raise ClusterServiceError(INVALID_FINALISATION_RESPONSE)
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            raise ClusterServiceError(INVALID_FINALISATION_RESPONSE) from None

    return FinalisationResult(
        calculation_result=calculation_result,
        calculation_error=calculation_error,
        artifacts=dict(artifacts),
    )


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _returned_slurm_id(value: Any, operation: str) -> str:
    if not isinstance(value, str):
        raise ClusterServiceError(f"Invalid {operation} response from cluster")
    try:
        return _slurm_id(value)
    except ValueError:
        raise ClusterServiceError(
            f"Invalid {operation} response from cluster"
        ) from None


def _status_from_row(row: Any) -> SlurmJobStatus:
    """Convert one validated status row into its backend representation."""

    try:
        status = SlurmJobStatus(
            _slurm_id(row["slurm_id"]),
            row["state"],
            row["exit_code"],
            row["elapsed_seconds"],
            row["has_result_error"],
        )
    except (KeyError, TypeError, ValueError):
        raise ClusterServiceError(INVALID_STATUS_RESPONSE) from None

    if (
        not isinstance(status.state, str)
        or not status.state
        or (status.exit_code is not None and not isinstance(status.exit_code, str))
        or (
            status.elapsed_seconds is not None
            and type(status.elapsed_seconds) is not int
        )
        or type(status.has_result_error) is not bool
    ):
        raise ClusterServiceError(INVALID_STATUS_RESPONSE)
    return status


@dataclass(frozen=True)
class ClusterDispatchClient:
    """Send JSON requests to the cluster and validate their responses."""

    cluster_work_dir: PurePosixPath
    timeout_seconds: int
    storage_timeout_seconds: int

    @classmethod
    def from_settings(
        cls,
        settings: BackendSettings,
    ) -> "ClusterDispatchClient":
        orchestration = settings.orchestration
        return cls(
            settings.require_cluster_work_dir(),
            orchestration.slurm_command_timeout_seconds,
            orchestration.storage_operation_timeout_seconds,
        )

    def submit_job(
        self,
        *,
        job_id: UUID | str,
        calculation_type: CalculationType,
        method: str,
        basis_set: str,
        charge: int,
        multiplicity: int,
        optimization_type: Literal["ground", "ts"] | None,
        input_xyz: str,
        keywords: dict[str, Any] | None,
        time_limit_minutes: int,
        memory_mb: int,
        recover_existing: bool,
    ) -> str:
        """Submit one complete job request, recovering an earlier attempt first."""

        result = self._send_request(
            {
                "command": "submit",
                "job_id": _job_id(job_id),
                "calculation_type": calculation_type.value,
                "method": method,
                "basis_set": basis_set,
                "charge": charge,
                "multiplicity": multiplicity,
                "optimization_type": optimization_type,
                "input_xyz": input_xyz,
                "keywords": keywords,
                "time_limit_minutes": time_limit_minutes,
                "memory_mb": memory_mb,
                "recover_existing": recover_existing,
            },
            "job submission",
        )
        if not isinstance(result["recovered"], bool):
            raise SubmissionOutcomeUnknownError(UNKNOWN_SUBMISSION_MESSAGE)
        try:
            return _returned_slurm_id(result["slurm_id"], "job submission")
        except ClusterServiceError:
            raise SubmissionOutcomeUnknownError(UNKNOWN_SUBMISSION_MESSAGE) from None

    def find_submission(self, job_id: UUID | str) -> str | None:
        result = self._send_request(
            {
                "command": "find-submission",
                "job_id": _job_id(job_id),
            },
            "job lookup",
        )
        if result["slurm_id"] is None:
            return None
        return _returned_slurm_id(result["slurm_id"], "job lookup")

    def get_slurm_job_statuses(
        self,
        slurm_ids: Iterable[str | int],
    ) -> dict[str, SlurmJobStatus]:
        """Fetch one temporary batch of Slurm job statuses."""

        requested = [_slurm_id(value) for value in slurm_ids]
        if not requested:
            raise ValueError("at least one slurm_id is required")
        requested_ids = set(requested)

        rows = self._send_request(
            {"command": "status-batch", "slurm_ids": requested},
            "status lookup",
        )["jobs"]
        if not isinstance(rows, list):
            raise ClusterServiceError(INVALID_STATUS_RESPONSE)

        statuses: dict[str, SlurmJobStatus] = {}
        for row in rows:
            status = _status_from_row(row)
            if status.slurm_id not in requested_ids or status.slurm_id in statuses:
                raise ClusterServiceError(INVALID_STATUS_RESPONSE)
            statuses[status.slurm_id] = status
        return statuses

    def cancel_slurm_job(self, slurm_id: str | int) -> None:
        slurm_id = _slurm_id(slurm_id)
        result = self._send_request(
            {"command": "cancel", "slurm_id": slurm_id},
            "job cancellation",
        )
        if result["slurm_id"] != slurm_id or result["cancel_requested"] is not True:
            raise ClusterServiceError("Invalid job cancellation response from cluster")

    def upload_artifacts(
        self,
        *,
        job_id: UUID | str,
        calculation_type: CalculationType,
        terminal_status: JobStatus,
        archive_upload_url: str,
        allow_missing_error: bool = False,
    ) -> FinalisationResult:
        """Upload one archive and return the content destined for PostgreSQL."""

        normalized_job_id = _job_id(job_id)
        result = self._send_request(
            {
                "command": "upload-artifacts",
                "job_id": normalized_job_id,
                "calculation_type": calculation_type.value,
                "terminal_status": terminal_status.value,
                "archive_upload_url": archive_upload_url,
                "allow_missing_error": allow_missing_error,
            },
            "artifact finalisation",
            timeout_seconds=self.storage_timeout_seconds,
        )
        return _finalisation_result(result, expected_job_id=normalized_job_id)

    def acknowledge_finalisation(self, job_id: UUID | str) -> None:
        """Acknowledge a committed result so cluster cleanup can be repeated."""

        normalized_job_id = _job_id(job_id)
        result = self._send_request(
            {
                "command": "ack-finalisation",
                "job_id": normalized_job_id,
            },
            "finalisation acknowledgement",
            timeout_seconds=self.storage_timeout_seconds,
        )
        if result["job_id"] != normalized_job_id or result["cleaned"] is not True:
            raise ClusterServiceError(
                "Invalid finalisation acknowledgement from cluster"
            )

    def _send_request(
        self,
        request: dict[str, Any],
        operation: str,
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Send one request and return its validated result."""

        command = request["command"]
        submission = command == "submit"
        job_specific = command in JOB_SPECIFIC_COMMANDS
        expected_result_fields = RESULT_FIELDS_BY_COMMAND.get(command)
        if expected_result_fields is None:
            raise ValueError("Cluster command is not supported")
        payload = {"protocol_version": PROTOCOL_VERSION, **request}
        try:
            input_text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            if job_specific:
                raise JobDispatchError(
                    f"Cluster {operation} request is invalid"
                ) from None
            raise ValueError(f"Cluster {operation} request is invalid") from None

        dispatch_path = (
            self.cluster_work_dir / "Cluster-API-QC" / "runner" / "dispatch.py"
        )
        remote_command = shlex.join(["python3", str(dispatch_path)])
        try:
            completed = subprocess.run(
                ["ssh", SSH_HOST, remote_command],
                input=input_text,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=timeout_seconds or self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            if submission:
                raise SubmissionOutcomeUnknownError(
                    UNKNOWN_SUBMISSION_MESSAGE
                ) from None
            raise ClusterServiceError(f"Cluster {operation} timed out") from None
        except (OSError, UnicodeError):
            raise ClusterServiceError(f"Cluster {operation} could not start") from None

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if COMMAND_REJECTION in stdout or COMMAND_REJECTION in stderr:
            raise ClusterServiceError("SSH rejected the cluster command")

        try:
            response = _parse_response(stdout, operation)
            if response["ok"] is True and (
                set(response["result"]) != expected_result_fields
            ):
                raise ClusterServiceError(f"Invalid {operation} response from cluster")
        except _ClusterResponseTooLargeError:
            if command == "upload-artifacts":
                raise JobDispatchError(
                    f"Cluster {operation} response is too large"
                ) from None
            if completed.returncode == 0 and not submission:
                raise
        except ClusterServiceError:
            if completed.returncode == 0 and not submission:
                raise
        else:
            if response["ok"] is True and completed.returncode == 0:
                return response["result"]

        self._raise_for_failed_process(
            completed.returncode,
            operation,
            job_specific=job_specific,
            submission=submission,
        )

    @staticmethod
    def _raise_for_failed_process(
        return_code: int,
        operation: str,
        *,
        job_specific: bool,
        submission: bool,
    ) -> None:
        if return_code == JOB_ERROR_EXIT_CODE and job_specific:
            raise JobDispatchError(f"Cluster {operation} failed for this job")
        if return_code == SERVICE_ERROR_EXIT_CODE:
            raise ClusterServiceError(f"Cluster {operation} failed")
        if submission:
            raise SubmissionOutcomeUnknownError(UNKNOWN_SUBMISSION_MESSAGE)
        raise ClusterServiceError(f"Cluster {operation} failed")
