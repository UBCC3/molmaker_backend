from fastapi import APIRouter
from enum_types import calculation_types
from enum_types import wave_functional_theories
from enum_types import density_functional_theories
from enum_types import basis_sets
from enum_types import multiplicities
from enum_types import optimization_types

router = APIRouter(prefix="/enums", tags=["enums"])

@router.get("/calculation_types")
def get_calculation_types():
    """
    Returns a list of available calculation types.
    :return: List of calculation types.
    """
    return calculation_types

@router.get("/wave_functional_theories")
def get_wave_functional_theories():
    """
    Returns a list of available wave functional theories.
    :return: List of wave functional theories.
    """
    return wave_functional_theories

@router.get("/density_functional_theories")
def get_density_functional_theories():
    """
    Returns a list of available density functional theories.
    :return: List of density functional theories.
    """
    return density_functional_theories

@router.get("/basis_sets")
def get_basis_sets():
    """
    Returns a list of available basis sets.
    :return: List of basis sets.
    """
    return basis_sets

@router.get("/multiplicities")
def get_multiplicities():
    """
    Returns a dictionary of multiplicities.
    :return: Dictionary of multiplicities.
    """
    return multiplicities

@router.get("/optimization_types")
def get_optimization_types():
    """
    Returns a dictionary of optimization types.
    :return: Dictionary of optimization types.
    """
    return optimization_types