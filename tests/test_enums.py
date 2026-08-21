from enum_types import (
    CalculationType,
    JobFailureReason,
    JobStatus,
    basis_sets,
    calculation_types,
    density_functional_theories,
    multiplicities,
    optimization_types,
    wave_functional_theories,
)


def test_job_status_values_match_the_orchestration_contract():
    assert {status.value for status in JobStatus} == {
        "submitting",
        "submitted",
        "running",
        "finalising",
        "completed",
        "failed",
        "cancelled",
    }


def test_calculation_types_include_scan():
    assert CalculationType.scan.value == "scan"
    assert calculation_types["Bond/Angle Scan"] == "scan"


def test_job_failure_reason_values_are_separate_from_statuses():
    assert {reason.value for reason in JobFailureReason} == {
        "calculation_failed",
        "out_of_memory",
        "timeout",
        "node_failure",
        "submission_failed",
        "status_check_failed",
        "result_upload_failed",
        "cluster_failed",
        "unknown",
    }


class TestEnumsAPI:
    def test_calculation_types_endpoint(self, client):
        response = client.get("/enums/calculation_types")

        assert response.status_code == 200
        assert response.json() == calculation_types

    def test_wave_functional_theories_endpoint(self, client):
        response = client.get("/enums/wave_functional_theories")

        assert response.status_code == 200
        assert response.json() == wave_functional_theories

    def test_density_functional_theories_endpoint(self, client):
        response = client.get("/enums/density_functional_theories")

        assert response.status_code == 200
        assert response.json() == density_functional_theories

    def test_basis_sets_endpoint(self, client):
        response = client.get("/enums/basis_sets")

        assert response.status_code == 200
        assert response.json() == basis_sets

    def test_multiplicities_endpoint(self, client):
        response = client.get("/enums/multiplicities")

        assert response.status_code == 200
        assert response.json() == multiplicities

    def test_optimization_types_endpoint(self, client):
        response = client.get("/enums/optimization_types")

        assert response.status_code == 200
        assert response.json() == optimization_types
