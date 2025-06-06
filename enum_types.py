from enum import Enum

class CalculationType(str, Enum):
    energy = "energy"
    geometry = "geometry"
    optimization = "optimization"
    frequency = "frequency"

calculation_types = {
    'Molecular Energy': 'energy',
	'Geometric Optimization': 'optimization',
	'Vibrational Frequency': 'frequency',
	'Molecular Orbitals': 'orbitals',
}

wave_functional_theories = {
    'Hartree-Fock': 'scf',
	'MP2': 'mp2',
	'MP4': 'mp4',
	'CCSD': 'ccsd',
	'CCSD(T)': r'ccsd\(t\)'
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

basis_sets = {
    'STO-3G': 'sto-3g',
    '6-31G': '6-31g',
    '6-31G(d)': r'6-31G\(d\)',
    '6-311G(2d,p)': r'6-311G\(2d,p\)',
    'cc-pVDZ': 'cc-pvdz',
    'cc-pVTZ': 'cc-pvtz',
    'cc-pCVQZ': 'cc-pcvqz',
    'cc-pCVTZ': 'cc-pcvtz',
    'cc-pVQZ': 'cc-pvqz',
    'jun-cc-pVDZ': 'jun-cc-pvdz',
    'aug-cc-pVDZ': 'aug-cc-pvdz',
    'aug-cc-pVTZ': 'aug-cc-pvtz',
    'aug-cc-pVQZ': 'aug-cc-pvqz',
}

multiplicities = {
	'Singlet': 1,
	'Doublet': 2,
	'Triplet': 3,
	'Quartet': 4,
	'Quintet': 5,
	'Sextet': 6,
}
