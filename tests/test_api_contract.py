import json


LEGACY_OPERATIONS = {
    ("/jobs/", "post"),
    ("/jobs/advanced_analysis", "post"),
    ("/cluster/run_advanced_analysis", "post"),
    ("/cluster/run_standard_analysis", "post"),
    ("/cluster/status/{slurm_id}", "get"),
    ("/cluster/result/{job_id}", "get"),
    ("/cluster/error/{job_id}", "get"),
    ("/cluster/cancel/{slurm_id}", "post"),
    ("/storage/files/{job_id}/{calculation}/{status}", "get"),
    ("/storage/download/archive/{job_id}", "get"),
    ("/storage/jobs/{job_id}", "get"),
    ("/structures/presigned/{structure_id}", "get"),
}

REPLACEMENT_OPERATIONS = {
    ("/calculation/custom", "post"),
    ("/calculation/workflow/standard_analysis", "post"),
    ("/jobs/", "get"),
    ("/jobs/{job_id}", "get"),
    ("/jobs/{job_id}/cancel", "post"),
    ("/storage/jobs/{job_id}/archive", "get"),
}


def test_openapi_exposes_only_the_job_oriented_calculation_contract(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    for path, method in LEGACY_OPERATIONS:
        assert path not in paths or method not in paths[path]

    for path, method in REPLACEMENT_OPERATIONS:
        assert method in paths[path]

    assert not any(path.startswith("/cluster/") for path in paths)
    assert "slurm_id" not in json.dumps(schema)


def test_job_responses_do_not_expose_stored_slurm_id(
    client,
    user_factory,
    job_factory,
):
    user_factory(user_sub="auth0|testuser")
    job = job_factory(slurm_id="12345")

    detail_response = client.get(f"/jobs/{job.job_id}")
    list_response = client.get("/jobs/")

    assert detail_response.status_code == 200
    assert list_response.status_code == 200
    assert "slurm_id" not in json.dumps(detail_response.json())
    assert "slurm_id" not in json.dumps(list_response.json())
