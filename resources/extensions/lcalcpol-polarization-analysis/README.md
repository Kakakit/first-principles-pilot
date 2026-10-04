# LCALCPOL polarization analysis v0.3.0

This local-only extension migrates `/Users/kafka/Downloads/calc_lcalcpol.py` while preserving its POSCAR/OUTCAR parsing, constants, unit conversion, and lattice-coordinate branch algorithm. The migration initially added polarization magnitudes; v0.3.0 also corrects the comparison order to subtract first and reduce second. The original source file remains unmodified.

## Inputs, outputs, and dependencies

Run from a directory containing the matching files:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python \
  /Users/kafka/Documents/FeCrO论文/plugins/first-principles-pilot/extensions/lcalcpol-polarization-analysis/scripts/calc_lcalcpol.py
```

The parser uses `pymatgen` to read the POSCAR lattice and volume, `numpy` for vector, magnitude, and lattice-coordinate operations, and the fixed conversion `16.021766` from `(e Ang)/Angstrom^3` to `C/m^2`. It reads the last complete `p[ion]` and `p[elc]` records and accepts ordinary decimal and scientific notation (`E/e/D/d`).

The extension performs only `local_read`. It does not execute VASP, SSH, PBS, scheduler operations, or remote services. It does not impose an `NCORE` value on the calculation.

## Scientific boundary

Interpret the result only when the OUTCAR is from a successful, converged calculation and contains complete `p[ion]` and `p[elc]` records. A single-structure output is a **formal polarization branch**. Subtracting an integer polarization quantum in lattice coordinates only selects a representative of that equivalence class; it cannot be identified directly with spontaneous polarization or switchable ferroelectricity.

To obtain a genuine spontaneous polarization, compare two structures on the same branch along an adiabatic connection: a polar structure and its reference structure, and report the same-branch `Delta P`. The migrated branch reduction retains the source's `lattice_coordinates` and `reduce_branch` algorithm and does not determine that physical comparison. The reported `|P_red|` is only the magnitude of the selected formal representative; it is not spontaneous polarization and is not a uniquely minimal value.

## Explicit compare mode

Compare noSOC and SOC endpoints with explicit paths:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python \
  scripts/calc_lcalcpol.py compare \
  --nosoc-outcar /path/noSOC/OUTCAR --nosoc-poscar /path/noSOC/POSCAR \
  --soc-outcar /path/SOC/OUTCAR --soc-poscar /path/SOC/POSCAR
```

The JSON output includes each endpoint's raw/formal/branch-representative values, lattice polarization-quantum vectors, input SHA-256 hashes, and raw/candidate branch-reduced ΔP in `C/m^2`. A lattice mismatch rejects the comparison. Endpoint files alone cannot prove an adiabatic same-branch connection, so `same_branch_delta_p.status` is `INSUFFICIENT`; the extension does not auto-minimize branches or claim spontaneous polarization or experimental agreement.

### v0.3.0 comparison change

The comparison now subtracts raw total polarizations **SOC minus noSOC first**, then reduces that difference in the shared quantum basis. Quantum vectors are stored as rows: `reduce_branch(delta_raw, quantum_vectors)` solves `quantum_vectors.T @ coefficients = delta_raw`. The noSOC quantum basis is the reference after the existing absolute lattice-match check (default `1e-8 Angstrom`).

`comparison.delta_p_branch_reduced_candidate_c_m2` and `same_branch_delta_p.branch_reduced_candidate_c_m2` now mean this difference-first representative. Their v0.2.0 meaning (independently reduced endpoints subtracted without further reduction) is retained only as `comparison.endpoint_reduced_difference_c_m2`. Consumers can identify the change via top-level `schema_version: 0.3.0`.

`difference_reduction` records the coefficients, integer quanta removed, residual coefficients, removed polarization, quantum basis, and distance to each half-integer boundary. Both raw and reduced differences are available in `C/m^2` and `uC/m^2`, with reduced-vector magnitudes. Exact half-integers retain NumPy ties-to-even rounding and have two equivalent representatives; non-orthogonal componentwise reduction does not guarantee the shortest Cartesian vector. These arithmetic operations do not establish physical branch continuity.

Regression tests cover endpoint boundary crossing, independent integer branch shifts away from ties, non-orthogonal reconstruction, unit conversion, zero/half-quantum differences, invalid tolerances, and mismatched cells. Calculation-completion, insulating-gap, and structure-lineage checks remain external audits; this release does not claim to automate them.

## Known limitations

The parser accepts the known LCALCPOL labels and takes the last complete record. Alternative output layouts and physical convergence are not inferred. Branch reduction is a formal representative operation, not a proof of physical branch continuity.

## Tests and provenance

From this extension directory, run the tests with the existing project environment:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python tests/test_calc_lcalcpol.py
```

The tests cover POSCAR lattice/volume parsing, `p[ion]`/`p[elc]` parsing, `dipole_to_p`, `polarization_magnitude`, orthogonal and non-orthogonal lattice branch reduction, missing-output errors, and numeric/label checks for the CLI magnitude report. The original source remains untouched at `/Users/kafka/Downloads/calc_lcalcpol.py`.
