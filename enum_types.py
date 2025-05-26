from enum import Enum

class CalculationType(str, Enum):
    energy = "energy"
    geometry = "geometry"
    optimization = "optimization"
    frequency = "frequency"

calculation_types = [
    'Molecular Energy',
	'Geometric Optimization',
	'Vibrational Frequency',
	'Molecular Orbitals',
]

wave_functional_theories = {
    'Hartree-Fock': 'scf',
	'MP2': 'mp2',
	'MP4': 'mp4',
	'CCSD': 'ccsd',
	'CCSD(T)': 'ccsd(t)'
}

density_functional_theories = [
    'BLYP',
	'B3LYP',
	'B3LYP-D',
	'B97-D',
	'BP86',
	'M05',
	'M05-2X',
	'PBE',
	'PBE-D',
]

basis_sets = [
    'STO-3G',
    '6-31G',
    '6-31G(d)',
    '6-311G(2d,p)',
    'cc-pVDZ',
    'cc-pVTZ',
    'cc-pVDZ',
    'cc-pCVQZ',
    'cc-pCVTZ',
    'cc-pVQZ',
    'jun-cc-pVDZ',
    'aug-cc-pVDZ',
    'aug-cc-pVTZ',
    'aug-cc-pVQZ',
]

multiplicities = {
	'Singlet': 1,
	'Doublet': 2,
	'Triplet': 3,
	'Quartet': 4,
	'Quintet': 5,
	'Sextet': 6,
}
