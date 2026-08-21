"""Parsing, validation, and normalization for scan specifications."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from ase.data import atomic_numbers


class ScanSpecValidationError(ValueError):
    """A scan specification cannot be safely submitted to the cluster."""


COORDINATE_ATOM_COUNTS = {
    "bond": 2,
    "angle": 3,
    "dihedral": 4,
}


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def parse_scan_spec(value: str, *, max_bytes: int) -> dict[str, Any]:
    """Parse a bounded JSON form field into a scan specification object."""

    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ScanSpecValidationError(
            "scan must contain a valid JSON object"
        ) from error
    if len(encoded) > max_bytes:
        raise ScanSpecValidationError("scan is too large")

    try:
        scan_spec = json.loads(value, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise ScanSpecValidationError(
            "scan must contain a valid JSON object"
        ) from error
    if not isinstance(scan_spec, dict):
        raise ScanSpecValidationError("scan must contain a valid JSON object")
    return scan_spec


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScanSpecValidationError(f"{field_name} must be a finite number")
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        finite = False
    if not finite:
        raise ScanSpecValidationError(f"{field_name} must be a finite number")
    return float(value)


def _validated_xyz_atom_count(input_xyz: str) -> int:
    lines = input_xyz.splitlines()
    try:
        atom_count = int(lines[0].strip())
    except (IndexError, TypeError, ValueError) as error:
        raise ScanSpecValidationError(
            "molecule must be valid XYZ text with an atom count"
        ) from error
    if atom_count < 1 or len(lines) < atom_count + 2:
        raise ScanSpecValidationError(
            "molecule must be valid XYZ text with an atom count"
        )

    for atom_number, line in enumerate(lines[2 : atom_count + 2], start=1):
        fields = line.split()
        if len(fields) < 4 or atomic_numbers.get(fields[0], 0) < 1:
            raise ScanSpecValidationError(
                f"molecule has an invalid XYZ atom row at atom {atom_number}"
            )
        try:
            coordinates = [float(value) for value in fields[1:4]]
        except ValueError as error:
            raise ScanSpecValidationError(
                f"molecule has an invalid XYZ atom row at atom {atom_number}"
            ) from error
        if not all(math.isfinite(value) for value in coordinates):
            raise ScanSpecValidationError(
                f"molecule has an invalid XYZ atom row at atom {atom_number}"
            )
    return atom_count


def _validate_point_count(point_count: int, max_points: int) -> None:
    if point_count > max_points:
        raise ScanSpecValidationError(
            f"scan must not contain more than {max_points} points"
        )


def _step_values(minimum: float, maximum: float, steps: int) -> list[float]:
    span = maximum - minimum
    if not math.isfinite(span):
        raise ScanSpecValidationError("scan range is too large")
    intervals = steps - 1
    values = [minimum + span * (index / intervals) for index in range(steps)]
    values[0] = minimum
    values[-1] = maximum
    return values


def _spacing_values(
    minimum: float,
    maximum: float,
    spacing: float,
    *,
    max_points: int,
) -> list[float]:
    span = maximum - minimum
    if not math.isfinite(span):
        raise ScanSpecValidationError("scan range is too large")

    values = []
    for index in range(max_points + 1):
        offset = spacing * index
        at_endpoint = math.isclose(offset, span, rel_tol=1e-12, abs_tol=0.0)
        if offset > span and not at_endpoint:
            break
        values.append(maximum if at_endpoint else minimum + offset)

    _validate_point_count(len(values), max_points)
    if len(values) < 2:
        raise ScanSpecValidationError("spacing must produce at least two scan points")
    return values


def _normalized_spec(
    scan_spec: Mapping[str, Any],
    *,
    coordinate: str,
    atoms: list[int],
    relax: bool,
    values: list[float],
) -> dict[str, Any]:
    normalized = dict(scan_spec)
    for field in ("min", "max", "steps", "spacing"):
        normalized.pop(field, None)
    normalized.update(
        coordinate=coordinate,
        atoms=list(atoms),
        relax=relax,
        values=values,
    )
    return normalized


def validate_scan_spec(
    scan_spec: Mapping[str, Any],
    *,
    input_xyz: str,
    max_points: int,
) -> dict[str, Any]:
    """Validate and normalize the cluster scan contract."""

    coordinate = scan_spec.get("coordinate")
    if not isinstance(coordinate, str) or coordinate not in COORDINATE_ATOM_COUNTS:
        allowed = ", ".join(COORDINATE_ATOM_COUNTS)
        raise ScanSpecValidationError(f"coordinate must be one of: {allowed}")

    atoms = scan_spec.get("atoms")
    expected_atoms = COORDINATE_ATOM_COUNTS[coordinate]
    if not isinstance(atoms, list) or len(atoms) != expected_atoms:
        raise ScanSpecValidationError(
            f"{coordinate} scan requires exactly {expected_atoms} atoms"
        )
    if any(isinstance(atom, bool) or not isinstance(atom, int) for atom in atoms):
        raise ScanSpecValidationError("atom indices must be integers")
    if len(set(atoms)) != expected_atoms:
        raise ScanSpecValidationError("atom indices must be distinct")

    atom_count = _validated_xyz_atom_count(input_xyz)
    if any(atom < 1 or atom > atom_count for atom in atoms):
        raise ScanSpecValidationError(
            f"atom indices must be between 1 and {atom_count}"
        )

    relax = scan_spec.get("relax", False)
    if not isinstance(relax, bool):
        raise ScanSpecValidationError("relax must be a boolean")

    has_values = "values" in scan_spec
    has_steps = "steps" in scan_spec
    has_spacing = "spacing" in scan_spec
    if sum((has_values, has_steps, has_spacing)) != 1:
        raise ScanSpecValidationError(
            "provide exactly one scan range: values, steps, or spacing"
        )

    if has_values:
        if "min" in scan_spec or "max" in scan_spec:
            raise ScanSpecValidationError("values cannot be combined with min or max")
        values = scan_spec["values"]
        if not isinstance(values, list) or len(values) < 2:
            raise ScanSpecValidationError("values must contain at least two points")
        _validate_point_count(len(values), max_points)
        normalized_values = [_finite_number(value, "scan value") for value in values]
        return _normalized_spec(
            scan_spec,
            coordinate=coordinate,
            atoms=atoms,
            relax=relax,
            values=normalized_values,
        )

    if "min" not in scan_spec or "max" not in scan_spec:
        raise ScanSpecValidationError("min and max are required for this range")
    minimum = _finite_number(scan_spec["min"], "min")
    maximum = _finite_number(scan_spec["max"], "max")
    if minimum == maximum:
        raise ScanSpecValidationError("min and max must be different")

    if has_steps:
        steps = scan_spec["steps"]
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 2:
            raise ScanSpecValidationError("steps must be an integer of at least 2")
        _validate_point_count(steps, max_points)
        return _normalized_spec(
            scan_spec,
            coordinate=coordinate,
            atoms=atoms,
            relax=relax,
            values=_step_values(minimum, maximum, steps),
        )

    spacing = _finite_number(scan_spec["spacing"], "spacing")
    if spacing <= 0:
        raise ScanSpecValidationError("spacing must be greater than zero")
    if maximum < minimum:
        raise ScanSpecValidationError(
            "max must be greater than min when spacing is used"
        )
    span = maximum - minimum
    if not math.isfinite(span):
        raise ScanSpecValidationError("scan range is too large")
    if spacing > span:
        raise ScanSpecValidationError("spacing must produce at least two scan points")
    return _normalized_spec(
        scan_spec,
        coordinate=coordinate,
        atoms=atoms,
        relax=relax,
        values=_spacing_values(
            minimum,
            maximum,
            spacing,
            max_points=max_points,
        ),
    )
