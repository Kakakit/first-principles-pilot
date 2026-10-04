# Local crystal-field analysis

This formal extension contains the local, offline part of the Wannier90 crystal-field workflow migrated from `02_Wannier计算/crystal_field_wannier/`.

It has two entrypoints:

- `scripts/calc_local_crystal_field.py` reads one spin channel's `seedname_hr.dat` and a matching POSCAR, fits a local coordination frame from metal--oxygen bond directions, and prints local d-subspace metrics to stdout.
- `scripts/compare_dense_coarse.py` compares two model directories. It reads spin-1 and spin-2 `hr.dat` files plus POSCAR from each directory and writes the requested JSON report.

## Contract and scientific boundaries

The code requires NumPy. The source does not pin a NumPy version; the migration test was run with NumPy 2.0.2. Wannier90 Hamiltonian values are treated as eV. POSCAR lattice vectors and bond lengths are handled in Angstrom, while rotations and fit RMSD are dimensionless. The expected Wannier90 projection order is:

`l=2: [dz2, dxz, dyz, dx2-y2, dxy]`; `l=1: [pz, px, py]`.

`--sites` must list species in the same order as the POSCAR species row. The script expands each atom to five d WFs for `l=2` or three p WFs for `l=1`; this projection/atom order is a contract, not an inference from the contents of `hr.dat`.

The comparison script is deliberately not a general site mapper. Its current hard-coded mapping is:

| site | atom index | WF start | coordination | frame/partition |
| --- | ---: | ---: | ---: | --- |
| Cr | 0 | 0 | 6 | octahedral `t2g/eg` |
| Fe | 4 | 20 | 4 | tetrahedral `e/t2` |

These fixed values must not be generalized or silently reused for another structure, species ordering, or Wannier projection layout without a separate implementation and validation. The compare script also assumes `wannier90.1_hr.dat` and `wannier90.2_hr.dat` in both model directories.

The local crystal-field eigenvalues and subspace centroids are not equivalent to PDOS global-axis nominal centroids. They come from the local rotated Wannier Hamiltonian and its subspace projections; a PDOS centroid labeled by a global orbital axis is a different quantity.

The extension performs only `local_read` and `local_write`: it does not SSH, read or write remote files, run Wannier90, submit or manage scheduler jobs, or modify registries.

## Run from the extension root

```bash
cd /Users/kafka/Documents/FeCrO论文/plugins/first-principles-pilot/extensions/local-crystal-field-analysis

/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python scripts/calc_local_crystal_field.py \
  /path/to/wannier90.1_hr.dat \
  --poscar /path/to/POSCAR \
  --sites 'Cr:l=2,Fe:l=2,O:l=1' \
  --coord 'Cr:6,Fe:4' \
  --mp-grid 8 7 7 --occ 60 --spin up

/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python scripts/compare_dense_coarse.py \
  --module scripts/calc_local_crystal_field.py \
  --coarse /path/to/coarse \
  --dense /path/to/dense \
  --output /path/to/dense_minus_coarse.json
```

The comparison output is indented JSON with `coarse`, `dense`, and `dense_minus_coarse` sections. The calculation entrypoint intentionally emits its report on stdout rather than JSON.

## Test and validation

From this directory, the synthetic test can be run without relying on the caller's current working directory:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python tests/test_calc_local_crystal_field.py
```

The extension metadata is checked separately by the plugin manager:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python \
  ../../scripts/fpilot_extensions.py validate .
```

The migration intentionally excludes `prepare_remote_dense_wannier.py`, `finalize_remote_dense_analysis.sh`, every `results_*` directory/file, historical `.win`/`.wout` files, and caches.
