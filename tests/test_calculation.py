import subprocess
import uuid

import pytest
from conftest import make_auth0_payload

import storage
from models import Job, JobInput, Tags


def _custom_data(**overrides):
    values = {
        "calculation_type": "energy",
        "method": "b3lyp",
        "basis_set": "6-31g",
        "charge": "0",
        "multiplicity": "1",
        "job_name": "Water energy",
    }
    values.update(overrides)
    return values


def _standard_data(**overrides):
    values = {
        "charge": "0",
        "multiplicity": "1",
        "job_name": "Standard water analysis",
    }
    values.update(overrides)
    return values


def _forbid_cluster_and_upload_url_calls(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "Calculation submission must not call Slurm or create upload URLs"
        )

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(storage, "generate_archive_upload_url", forbidden)


def test_openapi_documents_durable_calculation_submission_contract(client):
    """Swagger should describe both submission forms and safe job responses."""
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]

    for path in (
        "/calculation/custom",
        "/calculation/workflow/standard_analysis",
    ):
        operation = schema["paths"][path]["post"]
        response_schema = operation["responses"]["201"]["content"]["application/json"][
            "schema"
        ]
        assert response_schema["$ref"].endswith("/JobResponse")
        assert "processed asynchronously" in operation["description"]
        assert "Slurm" not in operation["description"]

        request_schema = operation["requestBody"]["content"]["multipart/form-data"][
            "schema"
        ]
        request_schema_name = request_schema["$ref"].rsplit("/", 1)[-1]
        request_properties = components[request_schema_name]["properties"]
        assert {"file", "structure_id", "job_name"}.issubset(request_properties)


@pytest.mark.parametrize(
    "path,data",
    [
        ("/calculation/custom", _custom_data(multiplicity="7")),
        (
            "/calculation/workflow/standard_analysis",
            _standard_data(multiplicity="7"),
        ),
    ],
)
def test_calculation_endpoints_reject_unsupported_multiplicity(
    client,
    path,
    data,
):
    response = client.post(
        path,
        data=data,
        files={"file": ("input.xyz", b"xyz", "chemical/x-xyz")},
    )

    assert response.status_code == 422


def test_custom_submission_persists_inputs_without_external_orchestration(
    client,
    db,
    monkeypatch,
    group_factory,
    user_factory,
    tag_factory,
):
    """A custom request should durably create one submitting job."""
    _forbid_cluster_and_upload_url_calls(monkeypatch)
    group = group_factory()
    user = user_factory(group=group, user_sub="auth0|testuser")
    existing_tag = tag_factory(user_sub=user.user_sub, name="existing")

    response = client.post(
        "/calculation/custom",
        data=_custom_data(
            calculation_type="optimization",
            method="  b3lyp  ",
            basis_set="  6-31g  ",
            charge="-1",
            multiplicity="2",
            optimization_type="ts",
            job_name="  Optimise water  ",
            job_notes="  transition search  ",
            tags=["EXISTING", "New", "new"],
        ),
        files={
            "file": (
                "../../unsafe-name.xyz",
                b"custom xyz input",
                "chemical/x-xyz",
            ),
            "keywords": (
                "../../unsafe-keywords.json",
                b'{"scf": "tight"}',
                "application/json",
            ),
        },
    )

    assert response.status_code == 201
    result = response.json()
    job_id = uuid.UUID(result["job_id"])
    assert result["status"] == "submitting"
    assert result["filename"] == "input.xyz"
    assert result["job_name"] == "Optimise water"
    assert result["job_notes"] == "transition search"
    assert result["calculation_type"] == "optimization"
    assert result["method"] == "b3lyp"
    assert result["basis_set"] == "6-31g"
    assert result["charge"] == -1
    assert result["multiplicity"] == 2
    assert result["optimization_type"] == "ts"
    assert result["user_sub"] == user.user_sub
    assert result["group_id"] == str(group.group_id)
    assert sorted(result["tags"]) == ["existing", "new"]
    assert result["structures"] == []
    assert {
        "slurm_id",
        "attempt_count",
        "terminal_status",
        "is_uploaded",
        "is_deleted",
    }.isdisjoint(result)

    job = db.query(Job).filter_by(job_id=job_id).one()
    assert job.status == "submitting"
    assert job.slurm_id is None
    assert job.attempt_count == 0
    assert job.cancel_requested is False
    assert job.is_uploaded is False
    assert job.is_deleted is False
    assert job.is_public is False
    assert job.user_sub == user.user_sub
    assert job.group_id == group.group_id
    assert sorted(tag.name for tag in job.tags) == ["existing", "new"]
    assert existing_tag in job.tags
    assert job.job_input.input_xyz == "custom xyz input"
    assert job.job_input.keywords == {"scf": "tight"}
    assert db.query(Tags).filter_by(user_sub=user.user_sub, name="new").count() == 1


def test_standard_submission_uses_workflow_defaults(
    client,
    db,
    monkeypatch,
    user_factory,
):
    """The standard workflow should persist its fixed method and basis set."""
    _forbid_cluster_and_upload_url_calls(monkeypatch)
    user_factory(user_sub="auth0|testuser")

    response = client.post(
        "/calculation/workflow/standard_analysis",
        data=_standard_data(charge="1", multiplicity="2"),
        files={
            "file": (
                "molecule.xyz",
                b"standard xyz input",
                "chemical/x-xyz",
            )
        },
    )

    assert response.status_code == 201
    result = response.json()
    job_id = uuid.UUID(result["job_id"])
    assert result["calculation_type"] == "standard"
    assert result["method"] == "mp2"
    assert result["basis_set"] == "6-311+G(2d,p)"
    assert result["charge"] == 1
    assert result["multiplicity"] == 2
    assert result["optimization_type"] == "ground"
    assert result["status"] == "submitting"

    job = db.query(Job).filter_by(job_id=job_id).one()
    assert job.optimization_type == "ground"
    assert job.job_input.input_xyz == "standard xyz input"
    assert job.job_input.keywords is None


def test_submission_saves_a_snapshot_of_a_readable_stored_structure(
    client,
    db,
    monkeypatch,
    group_factory,
    user_factory,
    structure_factory,
):
    """A readable database structure should be copied and linked to the job."""
    _forbid_cluster_and_upload_url_calls(monkeypatch)
    group = group_factory()
    owner = user_factory(group=group, user_sub="auth0|owner")
    submitter = user_factory(group=group, user_sub="auth0|testuser")
    structure = structure_factory(
        user_sub=owner.user_sub,
        group_id=group.group_id,
        is_public=True,
        content="stored structure content",
    )

    response = client.post(
        "/calculation/workflow/standard_analysis",
        data=_standard_data(
            structure_id=str(structure.structure_id),
            optimization_type="ts",
            tags=["Shared"],
        ),
    )

    assert response.status_code == 201
    result = response.json()
    job_id = uuid.UUID(result["job_id"])
    assert result["optimization_type"] == "ts"
    assert result["user_sub"] == submitter.user_sub
    assert result["group_id"] == str(group.group_id)
    assert result["tags"] == ["shared"]
    assert result["structures"][0]["structure_id"] == str(structure.structure_id)
    job = db.query(Job).filter_by(job_id=job_id).one()
    assert job.job_input.input_xyz == "stored structure content"
    assert [linked.structure_id for linked in job.structures] == [
        structure.structure_id
    ]


@pytest.mark.parametrize(
    "include_file,include_structure",
    [
        (False, False),
        (True, True),
    ],
)
def test_submission_requires_exactly_one_molecule_source(
    client,
    db,
    user_factory,
    structure_factory,
    include_file,
    include_structure,
):
    """Neither or both molecule sources should be rejected before persistence."""
    user = user_factory(user_sub="auth0|testuser")
    structure = structure_factory(user_sub=user.user_sub)
    data = _custom_data()
    files = None
    if include_structure:
        data["structure_id"] = str(structure.structure_id)
    if include_file:
        files = {"file": ("input.xyz", b"xyz", "chemical/x-xyz")}

    response = client.post(
        "/calculation/custom",
        data=data,
        files=files,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Provide exactly one molecule source: file or structure_id"
    )
    assert db.query(Job).count() == 0
    assert db.query(JobInput).count() == 0


def test_submission_hides_an_inaccessible_structure(
    client,
    db,
    set_auth_user,
    user_factory,
    structure_factory,
):
    """A private structure owned by another user should look unavailable."""
    owner = user_factory(user_sub="auth0|owner")
    submitter = user_factory(user_sub="auth0|submitter")
    structure = structure_factory(
        user_sub=owner.user_sub,
        is_public=False,
    )
    set_auth_user(make_auth0_payload(submitter.user_sub))

    response = client.post(
        "/calculation/custom",
        data=_custom_data(structure_id=str(structure.structure_id)),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == ("Structure not found or not accessible")
    assert db.query(Job).count() == 0
    assert db.query(JobInput).count() == 0


@pytest.mark.parametrize(
    "files,expected_detail",
    [
        (
            {"file": ("input.mol", b"mol", "chemical/x-mdl-molfile")},
            "Invalid molecule file format. Only .xyz files are allowed.",
        ),
        (
            {
                "file": ("input.xyz", b"xyz", "chemical/x-xyz"),
                "keywords": ("keywords.txt", b"tight", "text/plain"),
            },
            "Invalid keywords file format. Only .json files are allowed.",
        ),
    ],
)
def test_custom_submission_rejects_unsupported_input_files(
    client,
    db,
    user_factory,
    files,
    expected_detail,
):
    """Unsupported source and keyword files should fail before persistence."""
    user_factory(user_sub="auth0|testuser")

    response = client.post(
        "/calculation/custom",
        data=_custom_data(),
        files=files,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail
    assert db.query(Job).count() == 0
    assert db.query(JobInput).count() == 0


def test_custom_submission_rejects_standard_workflow_type(
    client,
    db,
    user_factory,
):
    """Standard analysis should use its dedicated workflow endpoint."""
    user_factory(user_sub="auth0|testuser")

    response = client.post(
        "/calculation/custom",
        data=_custom_data(calculation_type="standard"),
        files={"file": ("input.xyz", b"xyz", "chemical/x-xyz")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Use /calculation/workflow/standard_analysis for standard analysis"
    )
    assert db.query(Job).count() == 0
    assert db.query(JobInput).count() == 0


def test_database_failure_rolls_back_job_and_inputs(
    client,
    db,
    monkeypatch,
    user_factory,
):
    """A failed database commit must not leave an untracked input row."""
    user_factory(user_sub="auth0|testuser")

    def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db, "commit", fail_commit)

    response = client.post(
        "/calculation/custom",
        data=_custom_data(),
        files={"file": ("input.xyz", b"xyz", "chemical/x-xyz")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to create calculation job"
    assert db.query(Job).count() == 0
    assert db.query(JobInput).count() == 0


@pytest.mark.parametrize(
    "keyword_contents",
    [b"not-json", b"[]", b'{"value": NaN}', b'{"value": 1e999}'],
)
def test_custom_submission_rejects_invalid_keyword_json(
    client,
    db,
    user_factory,
    keyword_contents,
):
    """Keywords are validated before the job and input are persisted."""
    user_factory(user_sub="auth0|testuser")

    response = client.post(
        "/calculation/custom",
        data=_custom_data(),
        files={
            "file": ("input.xyz", b"xyz", "chemical/x-xyz"),
            "keywords": (
                "keywords.json",
                keyword_contents,
                "application/json",
            ),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Keywords file must contain a valid JSON object"
    )
    assert db.query(Job).count() == 0
    assert db.query(JobInput).count() == 0
