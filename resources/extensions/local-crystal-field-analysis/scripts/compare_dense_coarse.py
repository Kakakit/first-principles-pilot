#!/usr/bin/env python3
"""Compare local crystal-field parameters from coarse and dense Wannier models."""

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np


def load_module(path):
    spec = importlib.util.spec_from_file_location("crystal_field", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def h0(module, path):
    hr, _ = module.read_hr(path)
    return next(h / ndeg for r, ndeg, h in hr if tuple(r) == (0, 0, 0))


def site_metrics(module, h_spin1, h_spin2, rotation, start, coordination):
    matrices = []
    for hamiltonian in (h_spin1, h_spin2):
        block = hamiltonian[start : start + 5, start : start + 5]
        block = 0.5 * (block + block.conj().T).real
        matrices.append(rotation @ block @ rotation.T)
    matrices.append(0.5 * (matrices[0] + matrices[1]))

    if coordination == 6:
        lower, upper = module.T2_SET, module.E_SET
        lower_name, upper_name = "t2g", "eg"
    else:
        lower, upper = module.E_SET, module.T2_SET
        lower_name, upper_name = "e", "t2"

    result = {}
    for label, matrix in zip(("spin1", "spin2", "charge"), matrices):
        lower_values = np.linalg.eigvalsh(matrix[np.ix_(lower, lower)])
        upper_values = np.linalg.eigvalsh(matrix[np.ix_(upper, upper)])
        result[label] = {
            f"{lower_name}_eigenvalues_eV": lower_values.tolist(),
            f"{upper_name}_eigenvalues_eV": upper_values.tolist(),
            "centroid_splitting_eV": float(upper_values.mean() - lower_values.mean()),
            f"delta_{lower_name}_eV": float(np.ptp(lower_values)),
            f"delta_{upper_name}_eV": float(np.ptp(upper_values)),
            "cross_subspace_mixing_eV": float(
                np.linalg.norm(matrix[np.ix_(lower, upper)])
            ),
        }
    return result


def model_metrics(module, directory):
    h_spin1 = h0(module, directory / "wannier90.1_hr.dat")
    h_spin2 = h0(module, directory / "wannier90.2_hr.dat")
    symbols, counts, lattice, cart = module.read_poscar(directory / "POSCAR")
    species = [element for element, count in zip(symbols, counts) for _ in range(count)]
    oxygens = np.array([cart[i] for i, element in enumerate(species) if element == "O"])

    output = {}
    for element, atom_index, wf_start, coordination in (
        ("Cr", 0, 0, 6),
        ("Fe", 4, 20, 4),
    ):
        bonds, _, _ = module.bond_vectors(
            cart[atom_index], oxygens, lattice, coordination
        )
        references = module.OCT_REFS if coordination == 6 else module.TET_REFS
        frame, rmsd = module.kabsch_frame(bonds, references)
        output[element] = {
            "local_frame_rmsd": float(rmsd),
            "metrics": site_metrics(
                module,
                h_spin1,
                h_spin2,
                module.d_rotation(frame),
                wf_start,
                coordination,
            ),
        }
    return output


def flatten(prefix, value, output):
    if isinstance(value, dict):
        for key, item in value.items():
            flatten(f"{prefix}.{key}" if prefix else key, item, output)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flatten(f"{prefix}[{index}]", item, output)
    elif isinstance(value, (int, float)):
        output[prefix] = float(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--coarse", type=Path, required=True)
    parser.add_argument("--dense", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    module = load_module(args.module)
    coarse = model_metrics(module, args.coarse)
    dense = model_metrics(module, args.dense)
    coarse_flat, dense_flat = {}, {}
    flatten("", coarse, coarse_flat)
    flatten("", dense, dense_flat)
    differences = {
        key: dense_flat[key] - coarse_flat[key]
        for key in sorted(coarse_flat.keys() & dense_flat.keys())
    }
    result = {
        "coarse_directory": str(args.coarse),
        "dense_directory": str(args.dense),
        "coarse": coarse,
        "dense": dense,
        "dense_minus_coarse": differences,
    }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
