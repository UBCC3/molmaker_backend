import json
from types import SimpleNamespace
import uuid

import pytest


def _xyz_file(content=b"2\n\nH 0 0 0\nH 0 0 1\n"):
    return {"file": ("input.xyz", content, "chemical/x-xyz")}


def _advanced_files(content=b"advanced xyz", keywords=b'{"extra": true}'):
    return {
        "file": ("input.xyz", content, "chemical/x-xyz"),
        "keywords": ("keywords.json", keywords, "application/json"),
    }


def _configure_cluster(monkeypatch, tmp_path, env="production"):
    import cluster.routes as cluster_routes

    backend_dir = tmp_path / "backend"
    cluster_dir = tmp_path / "cluster"
    cleanup_calls = []

    monkeypatch.setattr(cluster_routes, "BACKEND_WORK_DIR", str(backend_dir))
    monkeypatch.setattr(cluster_routes, "CLUSTER_WORK_DIR", str(cluster_dir))
    monkeypatch.setattr(cluster_routes, "ENV", env)
    monkeypatch.setattr(cluster_routes, "ANACONDA_DIR", "/conda/bin/python")
    monkeypatch.setattr(cluster_routes, "clean_up_upload_cache", cleanup_calls.append)

    return cluster_routes, backend_dir, cluster_dir, cleanup_calls


def _freeze_job_id(monkeypatch, cluster_routes, value="11111111-1111-4111-8111-111111111111"):
    job_id = uuid.UUID(value)
    monkeypatch.setattr(cluster_routes.uuid, "uuid4", lambda: job_id)
    return job_id


def _mock_upload_urls(monkeypatch, cluster_routes):
    calls = []

    def fake_construct_upload_script(job_id, calculation_type):
        calls.append((job_id, calculation_type))
        return {"zip": f"put:{job_id}:{calculation_type}"}

    monkeypatch.setattr(cluster_routes, "construct_upload_script", fake_construct_upload_script)
    return calls


def _mock_subprocess_run(monkeypatch, cluster_routes, stdout=None, side_effects=None):
    calls = []
    stdout_values = list(stdout or ["12345\n"])
    effects = list(side_effects or [])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if effects:
            effect = effects.pop(0)
            if effect:
                raise effect
        if command[0] == "scp":
            return SimpleNamespace(stdout="", returncode=0)
        output = stdout_values.pop(0) if stdout_values else "12345\n"
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(cluster_routes.subprocess, "run", fake_run)
    return calls


class TestClusterRunAPI:
    def test_run_advanced_analysis_saves_files_copies_and_submits(
        self, client, monkeypatch, tmp_path
    ):
        """
        POST /cluster/run_advanced_analysis should stage files and submit expected commands.
        """
        cluster_routes, backend_dir, cluster_dir, cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        job_id = _freeze_job_id(monkeypatch, cluster_routes)
        upload_url_calls = _mock_upload_urls(monkeypatch, cluster_routes)
        subprocess_calls = _mock_subprocess_run(monkeypatch, cluster_routes, stdout=["67890\n"])

        response = client.post(
            "/cluster/run_advanced_analysis",
            data={
                "calculation_type": "energy",
                "method": "hf",
                "basis_set": "sto-3g",
                "charge": "0",
                "multiplicity": "1",
                "opt_type": "ts",
            },
            files=_advanced_files(),
        )

        assert response.status_code == 200
        assert response.json() == {"job_id": str(job_id), "slurm_id": "67890"}
        backend_job_dir = backend_dir / "jobs" / str(job_id)
        remote_job_dir = cluster_dir / "jobs" / str(job_id)
        assert (backend_job_dir / "input.xyz").read_bytes() == b"advanced xyz"
        assert json.loads((backend_job_dir / "urls.json").read_text()) == {
            "zip": f"put:{job_id}:energy"
        }
        assert (backend_job_dir / "keywords.json").read_bytes() == b'{"extra": true}'
        assert upload_url_calls == [(str(job_id), "energy")]
        assert subprocess_calls == [
            (
                ["scp", "-r", str(backend_job_dir), f"cluster:{remote_job_dir}"],
                {"check": True},
            ),
            (
                [
                    "ssh",
                    "cluster",
                    f"python3 {cluster_dir}/dispatch.py submit",
                    f"{remote_job_dir}/input.xyz",
                    str(job_id),
                    "energy",
                    "hf",
                    "sto-3g",
                    "0",
                    "1",
                    "--opt-type ts ",
                    f"--keywords-file {remote_job_dir}/keywords.json",
                ],
                {"check": True, "capture_output": True, "text": True},
            ),
        ]
        assert cleanup_calls == [str(backend_job_dir)]

    def test_run_standard_analysis_copies_and_submits_remote_job(
        self, client, monkeypatch, tmp_path
    ):
        """
        POST /cluster/run_standard_analysis should stage files and submit expected commands.
        """
        cluster_routes, backend_dir, cluster_dir, cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        job_id = _freeze_job_id(monkeypatch, cluster_routes)
        upload_url_calls = _mock_upload_urls(monkeypatch, cluster_routes)
        subprocess_calls = _mock_subprocess_run(monkeypatch, cluster_routes, stdout=["24680\n"])

        response = client.post(
            "/cluster/run_standard_analysis",
            data={"charge": "1", "multiplicity": "2", "opt_type": "ground"},
            files=_xyz_file(b"standard xyz"),
        )

        assert response.status_code == 200
        assert response.json() == {"job_id": str(job_id), "slurm_id": "24680"}
        backend_job_dir = backend_dir / "jobs" / str(job_id)
        remote_job_dir = cluster_dir / "jobs" / str(job_id)
        assert (backend_job_dir / "input.xyz").read_bytes() == b"standard xyz"
        assert json.loads((backend_job_dir / "urls.json").read_text()) == {
            "zip": f"put:{job_id}:standard"
        }
        assert upload_url_calls == [(str(job_id), "standard")]
        assert subprocess_calls == [
            (
                ["scp", "-r", str(backend_job_dir), f"cluster:{remote_job_dir}"],
                {"check": True},
            ),
            (
                [
                    "ssh",
                    "cluster",
                    f"python3 {cluster_dir}/dispatch.py submit",
                    f"{remote_job_dir}/input.xyz",
                    str(job_id),
                    "1",
                    "2",
                    "--opt-type ground ",
                ],
                {
                    "check": True,
                    "capture_output": True,
                    "text": True,
                    "cwd": None,
                },
            ),
        ]
        assert cleanup_calls == [str(backend_job_dir)]

    def test_run_standard_analysis_local_non_numeric_output_returns_null_slurm_id(
        self, client, monkeypatch, tmp_path
    ):
        """
        Local standard analysis should convert non-SLURM stdout into null slurm_id.
        """
        cluster_routes, backend_dir, cluster_dir, cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
            env="local",
        )
        job_id = _freeze_job_id(monkeypatch, cluster_routes)
        _mock_upload_urls(monkeypatch, cluster_routes)
        subprocess_calls = _mock_subprocess_run(monkeypatch, cluster_routes, stdout=["local done\n"])

        response = client.post(
            "/cluster/run_standard_analysis",
            data={"charge": "0", "multiplicity": "1"},
            files=_xyz_file(b"local xyz"),
        )

        assert response.status_code == 200
        assert response.json() == {"job_id": str(job_id), "slurm_id": None}
        backend_job_dir = backend_dir / "jobs" / str(job_id)
        remote_job_dir = cluster_dir / "jobs" / str(job_id)
        assert (remote_job_dir / "input.xyz").read_bytes() == b"local xyz"
        assert subprocess_calls == [
            (
                [
                    "/conda/bin/python",
                    f"{cluster_dir}/src/standard_analysis.py",
                    str(job_id),
                    f"{remote_job_dir}/input.xyz",
                    "0",
                    "1",
                ],
                {
                    "check": True,
                    "capture_output": True,
                    "text": True,
                    "cwd": str(cluster_dir),
                },
            )
        ]
        assert cleanup_calls == [str(backend_job_dir)]

    @pytest.mark.parametrize(
        "endpoint, data, expected_detail",
        [
            (
                "/cluster/run_advanced_analysis",
                {
                    "calculation_type": "energy",
                    "method": "hf",
                    "basis_set": "sto-3g",
                    "charge": "0",
                    "multiplicity": "1",
                },
                "Cluster job submission failed",
            ),
            (
                "/cluster/run_standard_analysis",
                {"charge": "0", "multiplicity": "1"},
                "Cluster job submission failed",
            ),
        ],
    )
    def test_run_analysis_subprocess_failure_returns_500(
        self, client, monkeypatch, tmp_path, endpoint, data, expected_detail
    ):
        """
        Cluster subprocess failures should return 500 and clean up staged files.
        """
        cluster_routes, backend_dir, _cluster_dir, cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        job_id = _freeze_job_id(monkeypatch, cluster_routes)
        _mock_upload_urls(monkeypatch, cluster_routes)
        _mock_subprocess_run(
            monkeypatch,
            cluster_routes,
            side_effects=[
                cluster_routes.subprocess.CalledProcessError(returncode=1, cmd=["scp"]),
            ],
        )

        response = client.post(endpoint, data=data, files=_xyz_file())

        assert response.status_code == 500
        assert response.json()["detail"] == expected_detail
        assert cleanup_calls == [str(backend_dir / "jobs" / str(job_id))]


class TestClusterStatusAPI:
    def test_status_returns_cluster_state(self, client, monkeypatch, tmp_path):
        cluster_routes, _backend_dir, cluster_dir, _cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        subprocess_calls = _mock_subprocess_run(monkeypatch, cluster_routes, stdout=["RUNNING\n"])

        response = client.get("/cluster/status/12345")

        assert response.status_code == 200
        assert response.json() == {"slurm_id": "12345", "state": "RUNNING"}
        assert subprocess_calls == [
            (
                ["ssh", "cluster", f"python3 {cluster_dir}/dispatch.py status 12345"],
                {"check": True, "capture_output": True, "text": True},
            )
        ]

    def test_status_failure_returns_500(self, client, monkeypatch, tmp_path):
        cluster_routes, _backend_dir, _cluster_dir, _cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        _mock_subprocess_run(
            monkeypatch,
            cluster_routes,
            side_effects=[
                cluster_routes.subprocess.CalledProcessError(returncode=1, cmd=["ssh"]),
            ],
        )

        response = client.get("/cluster/status/12345")

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to fetch status"

    @pytest.mark.parametrize(
        "endpoint, command_name",
        [
            ("/cluster/result/job-1", "result"),
            ("/cluster/error/job-1", "error"),
        ],
    )
    def test_result_endpoints_return_output(
        self, client, monkeypatch, tmp_path, endpoint, command_name
    ):
        cluster_routes, _backend_dir, cluster_dir, _cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        subprocess_calls = _mock_subprocess_run(monkeypatch, cluster_routes, stdout=["payload\n"])

        response = client.get(endpoint)

        assert response.status_code == 200
        assert response.json() == {"job_id": "job-1", "output": "payload\n"}
        assert subprocess_calls == [
            (
                ["ssh", "cluster", f"python3 {cluster_dir}/dispatch.py {command_name} job-1"],
                {"check": True, "capture_output": True, "text": True},
            )
        ]

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/cluster/result/job-1",
            "/cluster/error/job-1",
        ],
    )
    def test_result_endpoints_return_404_when_missing(
        self, client, monkeypatch, tmp_path, endpoint
    ):
        cluster_routes, _backend_dir, _cluster_dir, _cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        _mock_subprocess_run(
            monkeypatch,
            cluster_routes,
            side_effects=[
                cluster_routes.subprocess.CalledProcessError(returncode=1, cmd=["ssh"]),
            ],
        )

        response = client.get(endpoint)

        assert response.status_code == 404
        assert response.json()["detail"] == "Result not found yet"

    def test_cancel_returns_success_flag(self, client, monkeypatch, tmp_path):
        cluster_routes, _backend_dir, cluster_dir, _cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        subprocess_calls = _mock_subprocess_run(monkeypatch, cluster_routes, stdout=["true\n"])

        response = client.post("/cluster/cancel/12345")

        assert response.status_code == 200
        assert response.json() == {"slurm_id": "12345", "success": "true"}
        assert subprocess_calls == [
            (
                ["ssh", "cluster", f"python3 {cluster_dir}/dispatch.py cancel 12345"],
                {"check": True, "capture_output": True, "text": True},
            )
        ]

    def test_cancel_failure_returns_500(self, client, monkeypatch, tmp_path):
        cluster_routes, _backend_dir, _cluster_dir, _cleanup_calls = _configure_cluster(
            monkeypatch,
            tmp_path,
        )
        _mock_subprocess_run(
            monkeypatch,
            cluster_routes,
            side_effects=[
                cluster_routes.subprocess.CalledProcessError(returncode=1, cmd=["ssh"]),
            ],
        )

        response = client.post("/cluster/cancel/12345")

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to cancel the job"
