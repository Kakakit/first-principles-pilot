#!/usr/bin/env python3
"""Local LCALCPOL parser with a conservative explicit noSOC/SOC comparison mode."""

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from pymatgen.core import Structure


EANG_TO_C_M2 = 16.021766
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
ION_PATTERN = re.compile(
    r"Ionic dipole moment:\s*p\[ion\]\s*=\s*\(\s*(%s)\s*,?\s*(%s)\s*,?\s*(%s)" % (NUMBER, NUMBER, NUMBER),
    re.I,
)
ELC_PATTERN = re.compile(
    r"Total electronic dipole moment:\s*p\[elc\]\s*=\s*\(\s*(%s)\s*,?\s*(%s)\s*,?\s*(%s)" % (NUMBER, NUMBER, NUMBER),
    re.I,
)


def _float(value):
    return float(value.replace("D", "E").replace("d", "e"))


def read_poscar(filename="POSCAR"):
    struct = Structure.from_file(str(filename))
    return np.array(struct.lattice.matrix), float(struct.volume)


def read_lcalcpol(filename="OUTCAR", details=False):
    text = Path(filename).read_text(encoding="utf-8")
    ion_matches = list(ION_PATTERN.finditer(text))
    elc_matches = list(ELC_PATTERN.finditer(text))
    if not ion_matches:
        raise RuntimeError("Cannot find p[ion]")
    if not elc_matches:
        raise RuntimeError("Cannot find p[elc]")
    records = sorted(
        [(match.start(), "ion", match) for match in ion_matches]
        + [(match.start(), "elc", match) for match in elc_matches]
    )
    pending_ion = None
    last_complete = None
    for _, kind, match in records:
        if kind == "ion":
            pending_ion = match
        elif pending_ion is not None:
            last_complete = (pending_ion, match)
            pending_ion = None
    if last_complete is None:
        raise RuntimeError("Cannot find complete p[ion]/p[elc] pair")
    ion = np.array([_float(value) for value in last_complete[0].groups()])
    elc = np.array([_float(value) for value in last_complete[1].groups()])
    if not details:return ion, elc
    def field(match):
        line=text.count('\n',0,match.start())+1
        raw=text[text.rfind('\n',0,match.start())+1:text.find('\n',match.end()) if text.find('\n',match.end())>=0 else len(text)]
        return {'line':line,'text':raw,'numeric_tokens':list(match.groups()),'unit':'electrons Angst (e Å)'}
    before=text[:last_complete[0].start()]
    cells=list(re.finditer(r'direct lattice vectors.*\n((?:[^\n]*\n){3})',before,re.I))
    cell=None
    if cells:
        lines=cells[-1].group(1).splitlines()
        tokens=[line.split()[:3] for line in lines]
        if all(len(row)==3 and all(re.fullmatch(NUMBER,v) for v in row) for row in tokens):
            cell={'matrix':[[_float(v) for v in row] for row in tokens],'text':cells[-1].group(0),'line':text.count('\n',0,cells[-1].start())+1,
                  'rounding_angstrom':max(_printed_error(v) for row in tokens for v in row)}
    volumes=list(re.finditer(r'volume of cell\s*:\s*('+NUMBER+')',before,re.I))
    vol=None
    if volumes:
        m=volumes[-1];vol={'value':_float(m.group(1)),'text':m.group(0),'line':text.count('\n',0,m.start())+1,'rounding_angstrom3':_printed_error(m.group(1))}
    return ion,elc,{'ion':field(last_complete[0]),'electronic':field(last_complete[1]),'lattice':cell,'volume':vol}


def _printed_error(token):
    token=token.lower().replace('d','e');parts=token.split('e');power=int(parts[1]) if len(parts)>1 else 0
    decimals=len(parts[0].split('.')[1]) if '.' in parts[0] else 0
    return 0.5*10**(power-decimals)


def dipole_to_p(p, volume):
    return np.asarray(p, dtype=float) / volume * EANG_TO_C_M2


def polarization_magnitude(p):
    return float(np.linalg.norm(p))


def lattice_coordinates(p, lattice):
    return np.linalg.solve(np.asarray(lattice).T, np.asarray(p))


def reduce_branch(p, lattice):
    coeff = lattice_coordinates(p, lattice)
    integer = np.round(coeff)
    coeff_red = coeff - integer
    p_red = coeff_red[0] * lattice[0] + coeff_red[1] * lattice[1] + coeff_red[2] * lattice[2]
    return p_red, coeff, coeff_red, integer


def _vector(value):
    return [float(item) for item in np.asarray(value, dtype=float)]


def polarization_quantum(lattice, volume):
    """Return the three lattice polarization-quantum vectors in C/m^2."""
    return [_vector(dipole_to_p(vector, volume)) for vector in np.asarray(lattice, dtype=float)]


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _single_result(outcar, poscar, lattice_tolerance=1e-8):
    lattice, volume = read_poscar(poscar)
    p_ion, p_elc, evidence = read_lcalcpol(outcar,details=True)
    cell=evidence['lattice'];vol=evidence['volume']
    if cell and not np.allclose(lattice,cell['matrix'],rtol=0,atol=lattice_tolerance+cell['rounding_angstrom']+1e-12):
        raise ValueError('OUTCAR 晶格与所选结构不一致，拒绝量子计算')
    if vol and abs(volume-vol['value'])>vol['rounding_angstrom3']+1e-8+3*np.linalg.norm(lattice)**2*lattice_tolerance:
        raise ValueError('OUTCAR 体积与所选结构不一致，拒绝量子计算')
    # Retain full-precision frozen structure after checking rounded OUTCAR evidence.

    total = p_ion + p_elc
    branch_reduced, coeff, coeff_reduced, integer = reduce_branch(total, lattice)
    raw = dipole_to_p(total, volume)
    reduced = dipole_to_p(branch_reduced, volume)
    return {
        "detail_schema": "fpilot.polarization-detail.v1",
        "outcar_evidence": evidence,
        "cell_source": "冻结结构（已核对 OUTCAR 打印值）" if cell else "冻结结构（OUTCAR 未提供晶格字段）",
        "conversion_factor": EANG_TO_C_M2,
        "component_polarizations_c_m2": {"ion":_vector(dipole_to_p(p_ion,volume)),"electronic":_vector(dipole_to_p(p_elc,volume))},
        "inputs": {
            "OUTCAR": {"path": str(Path(outcar).resolve()), "sha256": _sha256(outcar)},
            "POSCAR": {"path": str(Path(poscar).resolve()), "sha256": _sha256(poscar)},
        },
        "lattice_matrix_angstrom": [[float(item) for item in row] for row in lattice],
        "volume_angstrom3": float(volume),
        "p_ion_e_ang": _vector(p_ion),
        "p_elc_e_ang": _vector(p_elc),
        "total_dipole_e_ang": _vector(total),
        "raw": {"dipole_e_ang": _vector(total), "polarization_c_m2": _vector(raw),
                "magnitude_c_m2": polarization_magnitude(raw)},
        "formal": {
            "polarization_c_m2": _vector(raw),
            "lattice_coefficients": _vector(coeff),
            "equivalence": "formal polarization is defined modulo the lattice polarization quantum",
        },
        "branch_reduced": {
            "dipole_e_ang": _vector(branch_reduced),
            "polarization_c_m2": _vector(reduced),
            "magnitude_c_m2": polarization_magnitude(reduced),
            "integer_quanta_removed": _vector(integer),
            "algorithm": "componentwise nearest-integer lattice representative; not a physical minimization",
        },
        "polarization_quantum": {
            "vectors_c_m2": polarization_quantum(lattice, volume),
            "definition": "e times each POSCAR lattice vector divided by cell volume",
        },
        "precision": {
            "parser": "last complete p[ion]/p[elc] record",
            "numeric_format": "decimal and scientific notation (E/e/D/d) accepted",
            "lattice_tolerance_angstrom": lattice_tolerance,
        },
    }


def compare_polarizations(nosoc_outcar, nosoc_poscar, soc_outcar, soc_poscar,
                          lattice_tolerance=1e-8):
    """Compare two inputs while refusing to assert a physical same-branch result."""
    if not np.isfinite(lattice_tolerance) or lattice_tolerance < 0:
        raise ValueError("lattice_tolerance must be finite and non-negative")
    no_soc = _single_result(nosoc_outcar, nosoc_poscar, lattice_tolerance)
    soc = _single_result(soc_outcar, soc_poscar, lattice_tolerance)
    no_lattice = np.asarray(no_soc["lattice_matrix_angstrom"])
    soc_lattice = np.asarray(soc["lattice_matrix_angstrom"])
    if no_lattice.shape != soc_lattice.shape or not np.allclose(
        no_lattice, soc_lattice, rtol=0.0, atol=lattice_tolerance
    ):
        raise ValueError("noSOC and SOC POSCAR lattices differ; direct comparison is rejected")
    delta_raw = np.asarray(soc["raw"]["polarization_c_m2"]) - np.asarray(no_soc["raw"]["polarization_c_m2"])
    # Subtract first, then reduce in the shared (noSOC reference) quantum basis.
    # Quantum vectors are rows, just like lattice vectors in reduce_branch().
    quantum = np.asarray(no_soc["polarization_quantum"]["vectors_c_m2"])
    delta_branch, coeff, coeff_red, integer = reduce_branch(delta_raw, quantum)
    endpoint_reduced_difference = np.asarray(soc["branch_reduced"]["polarization_c_m2"]) - np.asarray(no_soc["branch_reduced"]["polarization_c_m2"])
    return {
        "schema_version": "0.3.0",
        "mode": "compare",
        "noSOC": no_soc,
        "SOC": soc,
        "comparison": {
            "lattice_match": True,
            "lattice_max_abs_difference_angstrom": float(np.max(np.abs(soc_lattice - no_lattice))),
            "delta_p_raw_c_m2": _vector(delta_raw),
            "delta_p_raw_uc_m2": _vector(delta_raw * 1e6),
            "delta_p_branch_reduced_candidate_c_m2": _vector(delta_branch),
            "delta_p_branch_reduced_candidate_uc_m2": _vector(delta_branch * 1e6),
            "delta_p_branch_reduced_candidate_magnitude_c_m2": polarization_magnitude(delta_branch),
            "delta_p_branch_reduced_candidate_magnitude_uc_m2": polarization_magnitude(delta_branch) * 1e6,
            "endpoint_reduced_difference_c_m2": _vector(endpoint_reduced_difference),
            "difference_reduction": {
                "algorithm": "subtract raw SOC - noSOC polarizations, then componentwise nearest-integer quantum reduction",
                "quantum_reference": "noSOC; SOC lattice matches within the stated absolute tolerance",
                "quantum_vectors_c_m2": quantum.tolist(),
                "coefficients": _vector(coeff),
                "integer_quanta_removed": [int(value) for value in integer],
                "reduced_coefficients": _vector(coeff_red),
                "removed_polarization_c_m2": _vector(integer @ quantum),
                "distance_to_half_integer": _vector(0.5 - np.abs(coeff_red)),
                "rounding": "numpy ties-to-even; exact half-integer boundaries have two equivalent representatives",
                "limitation": "not necessarily the shortest Cartesian vector in a non-orthogonal quantum lattice; not a physical branch proof",
            },
            "same_branch_delta_p": {
                "status": "INSUFFICIENT",
                "value_c_m2": None,
                "branch_reduced_candidate_c_m2": _vector(delta_branch),
                "reason": "the supplied endpoint files do not prove an adiabatic same-branch connection",
            },
            "conclusion": "INSUFFICIENT",
            "physical_claims": {
                "spontaneous_polarization": "not established",
                "experimental_agreement": "not assessed",
                "automatic_minimization": "not performed",
            },
        },
        "assumptions": [
            "OUTCAR files are expected to be completed LCALCPOL calculations; convergence is not inferred by this parser.",
            "The branch-reduced values are formal representatives, not proofs of the physical branch.",
            "A physical spontaneous polarization requires a demonstrated same-branch path between reference and polar structures.",
        ],
    }


def _print_single():
    result = _single_result("OUTCAR", "POSCAR")
    raw = result["raw"]
    reduced = result["branch_reduced"]
    print("\n========== LCALCPOL ==========")
    print("\nIonic dipole:")
    print(result["p_ion_e_ang"])
    print("\nElectronic dipole:")
    print(result["p_elc_e_ang"])
    print("\nTotal dipole (e Ang):")
    print(result["total_dipole_e_ang"])
    print("\nCell volume:")
    print(result["volume_angstrom3"], "A^3")
    print("\nRaw polarization:")
    print(np.asarray(raw["polarization_c_m2"]), "C/m^2")
    print("\nRaw polarization magnitude |P|:")
    print(raw["magnitude_c_m2"], "C/m^2")
    print("\nLattice coefficients:")
    print(result["formal"]["lattice_coefficients"])
    print("\nInteger quantum removed:")
    print(result["branch_reduced"]["integer_quanta_removed"])
    print("\nReduced dipole:")
    print(reduced["dipole_e_ang"])
    print("\nReduced polarization:")
    print(np.asarray(reduced["polarization_c_m2"]), "C/m^2")
    print("\nBranch-reduced polarization magnitude |P_red|:")
    print(reduced["magnitude_c_m2"], "C/m^2")
    print("\nBranch-reduced polarization magnitude |P_red|*1e6:")
    print(reduced["magnitude_c_m2"] * 1e6, "uC/m^2")
    print("\nEquivalent unit:")
    print(np.asarray(reduced["polarization_c_m2"]) * 1e6, "uC/m^2")
    print("\n==============================")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read LCALCPOL output locally; no VASP or remote operations.")
    sub = parser.add_subparsers(dest="mode")
    compare = sub.add_parser("compare", help="explicitly compare noSOC and SOC endpoint files")
    compare.add_argument("--nosoc-outcar", required=True)
    compare.add_argument("--nosoc-poscar", required=True)
    compare.add_argument("--soc-outcar", required=True)
    compare.add_argument("--soc-poscar", required=True)
    compare.add_argument("--lattice-tolerance", type=float, default=1e-8)
    args = parser.parse_args(argv)
    if args.mode == "compare":
        try:
            print(json.dumps(compare_polarizations(
                args.nosoc_outcar, args.nosoc_poscar, args.soc_outcar, args.soc_poscar,
                args.lattice_tolerance,
            ), ensure_ascii=False, sort_keys=True))
            return 0
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False))
            return 2
    try:
        _print_single()
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
