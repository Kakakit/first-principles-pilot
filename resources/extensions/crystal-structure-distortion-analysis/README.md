# crystal-structure-distortion-analysis

Local, deterministic analysis of tetrahedral and octahedral coordination-polyhedron distortion from a VASP POSCAR/CONTCAR.

## What changed from the original script

The original script was specifically written for FeO4/CrO6, with `Cr:6` and `Fe:4` hard-coded and a fixed Fe/Cr report. This migration keeps the same core definitions (bond-length distortion Δd, Baur D, equal-volume quadratic elongation λ, angle variance, octahedral trans-pair assignment and Q2/Q3/QJT), but:

- moves center/ligand/coordination definitions into JSON config;
- removes the runtime `pymatgen` dependency with an explicit VASP5/6 POSCAR parser;
- isolates geometry-only functions for deterministic unit tests;
- records periodic ligand image identities and a coordination-shell gap diagnostic;
- adds bond-length standard deviation/CV and RMS/mean-absolute angular deviations;
- emits provenance (input SHA-256 + resolved config) in JSON;
- uses dynamic reporting for arbitrary configured tetrahedral/octahedral center species;
- defines explicit failure behavior and a dry-run parser/config check.

## Quick use

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python src/crystal_distortion.py POSCAR \
  --config config/default_fecr2o4.json \
  --output-prefix analysis/polyhedral_distortion
```

`CONTCAR` can be supplied in the same position as `POSCAR`. The extension is
`experimental` and must be explicitly approved before use by a workflow that
selects extensions.

Compatibility with the original FeCr2O4 use case is the default even without `--config`:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python src/crystal_distortion.py POSCAR
```

Validate parsing/config without writing outputs:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python src/crystal_distortion.py POSCAR --config config/default_fecr2o4.json --dry-run
```

Dry-run validates the structure and resolved configuration and writes no
files. Ordinary runs write `<output-prefix>.csv`, `<output-prefix>.json`, and
`<output-prefix>.md`; an existing same-name local output may be overwritten.
No remote file is touched.

If the nth and (n+1)th ligand distances are too close for a trustworthy nearest-neighbor shell, make that diagnostic fatal:

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python src/crystal_distortion.py POSCAR --fail-on-ambiguous-shell
```

## Scientific interpretation

This is a **geometric local-polyhedron analyzer**, not an automatic bonding model. The configured coordination number must be physically justified for the material. A small shell gap is deliberately surfaced because blindly truncating the nearest-neighbor list can otherwise produce a plausible but chemically wrong polyhedron.

The tool does not infer chemical coordination, oxidation state, bond valence,
or a bonding model. Coordination numbers and center/ligand species come from
the JSON config. Only tetrahedral coordination 4 and octahedral coordination 6
are implemented.

For octahedra, the tool finds three opposite bond pairs by maximizing the sum of trans angles. It then orders the three pair-average lengths as `lx <= ly <= lz` before evaluating Q2/Q3. This makes scalar output reproducible, but the sorted axes are not crystallographic x/y/z directions. `QJT` is the safer scalar amplitude for comparing sites/structures; orbital-direction arguments require an externally defined local-axis convention.

## Tests

```bash
/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python -m pytest -q tests
```

The deterministic fixture contains one ideal CrO6 octahedron and one ideal FeO4 tetrahedron in a large periodic cell. Unit tests check ideal limits and a tetragonally elongated octahedron; CLI tests check dry-run parsing and all three outputs.

The current source test environment does not have `pytest` installed. This
historical command is therefore not currently executable there; no new Python
environment is created as part of this migration.

## Extension integration

`extension.json` records source provenance, contracts, dependencies, local-only
side effects, entrypoint and test paths. The host manager's `validate` command
must be run before the directory hash is locked into the host registry.
