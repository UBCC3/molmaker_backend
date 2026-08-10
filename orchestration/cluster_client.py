"""Client for the restricted Alliance dispatch interface."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal
from uuid import UUID

from enum_types import CalculationType, JobStatus
from settings import BackendSettings


SSH_HOST = "cluster"
COMMAND_REJECTION = "Command rejected by allowed_commands.sh"
JOB_ERROR_EXIT_CODE = 10
SUBMISSION_OUTCOME_UNKNOWN_EXIT_CODE = 11
SERVICE_ERROR_EXIT_CODE = 12
UNKNOWN_SUBMISSION_MESSAGE = (
    "Submission may have succeeded; look up the job before retrying"
)


class ClusterClientError(RuntimeError):
    """A cluster client operation failed."""


class JobDispatchError(ClusterClientError):
    """This job's cluster command failed and may be retried."""


class SubmissionOutcomeUnknownError(ClusterClientError):
    """The job may have been submitted; look it up before retrying."""


class ClusterServiceError(ClusterClientError):
    """A shared SSH or cluster problem occurred; retry after backoff."""


@dataclass(frozen=True)
class SlurmJobStatus:
    slurm_id: str
    state: str
    exit_code: str | None
    elapsed_seconds: int | None


def _job_id(value: UUID | str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("job_id must be a UUID") from None


def _slurm_id(value: str | int) -> str:
    value = str(value)
    if not value.isascii() or not value.isdigit() or int(value) == 0:
        raise ValueError("slurm_id must be a positive integer")
    return value


def _json_object(output: str, operation: str) -> dict[str, Any]:
    try:
        response = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        raise ClusterServiceError(
            f"Invalid {operation} response from cluster"
        ) from None
    if not isinstance(response, dict):
        raise ClusterServiceError(f"Invalid {operation} response from cluster")
    return response


def _job_rows(output: str, operation: str) -> list[Any]:
    rows = _json_object(output, operation).get("jobs")
    if not isinstance(rows, list):
        raise ClusterServiceError(f"Invalid {operation} response from cluster")
    return rows


@dataclass(frozen=True)
class ClusterDispatchClient:
    """Run allow-listed cluster commands and return validated results."""

    cluster_work_dir: PurePosixPath
    timeout_seconds: int
    transfer_timeout_seconds: int

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

    def job_directory(self, job_id: UUID | str) -> PurePosixPath:
        return self.cluster_work_dir / "jobs" / _job_id(job_id)

    def upload_manifest_path(self, job_id: UUID | str) -> PurePosixPath:
        return self.job_directory(job_id) / "upload-urls.json"

    def stage_job_inputs(
        self,
        job_id: UUID | str,
        local_job_directory: Path,
    ) -> None:
        """Copy one job directory to its deterministic Alliance location."""

        job_id = _job_id(job_id)
        local_job_directory = Path(local_job_directory)
        if (
            local_job_directory.name != job_id
            or not (local_job_directory / "input.xyz").is_file()
        ):
            raise JobDispatchError("Calculation input file is missing")

        self._copy_to_cluster(
            local_job_directory,
            f"{self.cluster_work_dir}/jobs/",
            recursive=True,
            operation="Cluster input transfer",
        )

    def stage_upload_manifest(
        self,
        job_id: UUID | str,
        local_manifest: Path,
    ) -> None:
        """Copy one temporary upload manifest to its exact job path."""

        local_manifest = Path(local_manifest)
        if (
            local_manifest.name != "upload-urls.json"
            or not local_manifest.is_file()
        ):
            raise ClusterServiceError("Artifact upload manifest is unavailable")
        self._copy_to_cluster(
            local_manifest,
            str(self.upload_manifest_path(job_id)),
            recursive=False,
            operation="Artifact upload manifest transfer",
        )

    def _copy_to_cluster(
        self,
        source: Path,
        destination: str,
        *,
        recursive: bool,
        operation: str,
    ) -> None:
        arguments = ["scp"]
        if recursive:
            arguments.append("-r")
        arguments.extend([str(source), f"{SSH_HOST}:{destination}"])
        try:
            completed = subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.transfer_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise ClusterServiceError(f"{operation} timed out") from None
        except OSError:
            raise ClusterServiceError(f"{operation} could not start") from None

        if completed.returncode:
            raise ClusterServiceError(f"{operation} failed")

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
        has_keywords: bool = False,
    ) -> str:
        """Submit a job whose input files are already staged on Alliance."""

        job_id = _job_id(job_id)
        job_directory = self.cluster_work_dir / "jobs" / job_id
        arguments = ["submit", str(job_directory / "input.xyz"), job_id]

        if calculation_type != CalculationType.standard:
            arguments += [calculation_type.value, method, basis_set]
        arguments += [str(charge), str(multiplicity)]

        if optimization_type:
            arguments += ["--opt-type", optimization_type]
        if has_keywords:
            arguments += ["--keywords-file", str(job_directory / "keywords.json")]

        output = self._run(arguments, "job submission", scope="submission")
        try:
            return _slurm_id(output)
        except ValueError:
            raise SubmissionOutcomeUnknownError(UNKNOWN_SUBMISSION_MESSAGE) from None

    def find_active_slurm_id(self, job_id: UUID | str) -> str | None:
        return self._find("find-active", job_id)

    def find_accounting_slurm_id(self, job_id: UUID | str) -> str | None:
        return self._find("find-accounting", job_id)

    def get_slurm_job_statuses(
        self, slurm_ids: Iterable[str | int]
    ) -> dict[str, SlurmJobStatus]:
        """Fetch one temporary batch of Slurm job statuses."""

        requested = [_slurm_id(value) for value in slurm_ids]
        if not requested:
            raise ValueError("at least one slurm_id is required")
        requested_ids = set(requested)

        rows = _job_rows(
            self._run(["status-batch", *requested], "status lookup"),
            "status lookup",
        )
        slurm_job_status_by_id: dict[str, SlurmJobStatus] = {}
        required_fields = {"slurm_id", "state", "exit_code", "elapsed_seconds"}
        for row in rows:
            if not isinstance(row, dict) or not required_fields <= row.keys():
                raise ClusterServiceError("Invalid status lookup response from cluster")
            try:
                slurm_id = _slurm_id(row["slurm_id"])
            except ValueError:
                raise ClusterServiceError(
                    "Invalid status lookup response from cluster"
                ) from None
            if (
                slurm_id not in requested_ids
                or slurm_id in slurm_job_status_by_id
            ):
                raise ClusterServiceError("Invalid status lookup response from cluster")

            state = row["state"]
            exit_code = row["exit_code"]
            elapsed = row["elapsed_seconds"]
            if not isinstance(state, str) or not state:
                raise ClusterServiceError("Invalid status lookup response from cluster")
            if exit_code is not None and not isinstance(exit_code, str):
                raise ClusterServiceError("Invalid status lookup response from cluster")
            if elapsed is not None and (
                isinstance(elapsed, bool) or not isinstance(elapsed, int)
            ):
                raise ClusterServiceError("Invalid status lookup response from cluster")

            slurm_job_status_by_id[slurm_id] = SlurmJobStatus(
                slurm_id,
                state,
                exit_code,
                elapsed,
            )
        return slurm_job_status_by_id

    def cancel_slurm_job(self, slurm_id: str | int) -> None:
        slurm_id = _slurm_id(slurm_id)
        response = _json_object(
            self._run(
                ["cancel-slurm-job", slurm_id],
                "job cancellation",
                scope="job",
            ),
            "cancellation",
        )
        if (
            response.get("slurm_id") != slurm_id
            or response.get("cancel_requested") is not True
        ):
            raise ClusterServiceError("Invalid cancellation response from cluster")

    def upload_artifacts(
        self,
        *,
        job_id: UUID | str,
        calculation_type: CalculationType,
        terminal_status: JobStatus,
        allow_missing_error: bool = False,
    ) -> None:
        job_id = _job_id(job_id)
        arguments = [
            "upload-artifacts",
            job_id,
            calculation_type.value,
            terminal_status.value,
            str(self.upload_manifest_path(job_id)),
        ]
        if allow_missing_error:
            arguments.append("--allow-missing-error")
        response = _json_object(
            self._run(
                arguments,
                "artifact upload",
                scope="job",
            ),
            "artifact upload",
        )
        if response.get("job_id") != job_id or response.get("uploaded") is not True:
            raise ClusterServiceError("Invalid artifact upload response from cluster")

    def _find(self, command: str, job_id: UUID | str) -> str | None:
        job_id = _job_id(job_id)
        rows = _job_rows(
            self._run([command, job_id], "job lookup"),
            "job lookup",
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise ClusterServiceError("Cluster returned multiple jobs for one job ID")

        row = rows[0]
        if not isinstance(row, dict) or row.get("job_name") != job_id:
            raise ClusterServiceError("Invalid job lookup response from cluster")
        try:
            return _slurm_id(row.get("slurm_id"))
        except ValueError:
            raise ClusterServiceError(
                "Invalid job lookup response from cluster"
            ) from None

    def _run(
        self,
        arguments: list[str],
        operation: str,
        *,
        scope: Literal["service", "job", "submission"] = "service",
    ) -> str:
        dispatch_path = self.cluster_work_dir / "dispatch.py"
        remote_command = shlex.join(["python3", str(dispatch_path), *arguments])
        try:
            completed = subprocess.run(
                ["ssh", SSH_HOST, remote_command],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            if scope == "submission":
                raise SubmissionOutcomeUnknownError(
                    UNKNOWN_SUBMISSION_MESSAGE
                ) from None
            raise ClusterServiceError(f"Cluster {operation} timed out") from None
        except OSError:
            raise ClusterServiceError(f"Cluster {operation} could not start") from None

        stdout, stderr = completed.stdout or "", completed.stderr or ""
        if COMMAND_REJECTION in stdout or COMMAND_REJECTION in stderr:
            raise ClusterServiceError("SSH rejected the cluster command")

        if completed.returncode == JOB_ERROR_EXIT_CODE and scope != "service":
            raise JobDispatchError(f"Cluster {operation} failed for this job")
        if completed.returncode:
            if (
                scope == "submission"
                and completed.returncode != SERVICE_ERROR_EXIT_CODE
            ):
                raise SubmissionOutcomeUnknownError(UNKNOWN_SUBMISSION_MESSAGE)
            raise ClusterServiceError(f"Cluster {operation} failed")

        if stderr.strip():
            if scope == "submission":
                raise SubmissionOutcomeUnknownError(UNKNOWN_SUBMISSION_MESSAGE)
            raise ClusterServiceError(
                f"Cluster {operation} returned unexpected error output"
            )
        return stdout.strip()
