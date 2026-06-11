from enum_types import (
    basis_sets,
    calculation_types,
    density_functional_theories,
    multiplicities,
    optimization_types,
    wave_functional_theories,
)


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
