# Extension migration guide

Use this guide when moving an existing script into a reusable extension.

For a new module sourced from GitHub, use the single detailed checklist in
[`CREATE_FROM_GITHUB.md`](CREATE_FROM_GITHUB.md) and copy
[`_template/`](_template/). The `pyw90` directory is the reference implementation
for a small external-command adapter; `tb2j` is the reference postprocessor.

An extension is a directory with an `extension.json`. The manager scans direct
children of `extensions/`, so copying or removing one directory does not
change the core workflow. `registry.json` is an optional reproducibility lock;
use `fpilot_extensions.py audit` to inspect it explicitly.

1. Keep the original script and its documentation at the original source path. Migration begins as a wrapper or copied working tree only after the source is recorded.
2. Create `extensions/<name>/extension.json` from `_template/` and record the source path, source hash, version, domain, dependencies, side effects, authorization policy, and entrypoint. A command-style module may additionally declare `module_type`, `requires`, `provides`, and `command` together; its artifact roles should describe files/data, not another extension name.
3. Write the input contract with required files, configuration keys, units, and source assumptions. Write the output contract with files, schemas, units, provenance, and failure markers.
4. Add the smallest deterministic fixture and a dry-run or parser test. The manager validates metadata and paths but never runs the extension.
5. Run `validate` or `list`, inspect the directory hash, and optionally add the validated formal extension to `registry.json`.
6. Lock name, version, directory hash, entrypoint, dependency check, and authorization route in the stage contract and ledger before using it.

Remote reads, remote writes, and scheduler submission remain explicit side effects.
Use the current v2 domain submission route after the user requests submission.
The former v1.3 hpc-submit and hpc-batch routes are historical compatibility
interfaces. Migration never grants an extension independent permission.

## Current boundaries

`local-crystal-field-analysis` and `lcalcpol-polarization-analysis` satisfy this migration gate and are registered as `ready`. The former standalone `wannier-window-scan` directory was intentionally removed; its bounded search responsibility is now a mode inside the independent `pyw90` extension. The LCALCPOL extension is local-only: it parses completed `POSCAR`/`OUTCAR` data, reports raw and branch-reduced formal polarization plus vector magnitudes, and does not establish spontaneous polarization without a same-branch polar/reference comparison. New candidates remain non-runnable until their own source, fixtures, tests, validation evidence, and registry lock exist.

`crystal-structure-distortion-analysis` is now registered as `experimental` and
requires explicit approval. It is local-only (`local_read`/`local_write`), reads
VASP5/6 `POSCAR` or `CONTCAR`, and takes center/ligand species and coordination
numbers from JSON config rather than inferring chemical coordination. Its
declared pytest command cannot currently run because the host
`.venv-figure` environment has no `pytest`; the extension therefore remains
experimental until a current test receipt is available. Its Q2/Q3/QJT
directional interpretation is also bounded by the configured/sorted local
geometry convention.

`electronic-structure-characterization` supersedes `band-dos-analysis` and is
registered as `ready`. It is local-only (`local_read`/`local_write`): `prepare`
parses PROCAR/DOSCAR once into reusable spin-resolved band-weight and PDOS tables,
`characterize` extracts bounded line-path gaps, near-reference orbital character,
DOS, and magnetic descriptors, and `plot` selectively composes band+DOS, PDOS,
or fatband figures. The previous plotting subcommands remain available through
the compatibility entrypoint. It never performs remote reads/writes, scheduler
submissions, or VASP/Wannier90 execution. Keep its scientific limits explicit:
PROCAR line-path metrics are not automatically global gaps, PAW projections are
not unique orbital populations, cell and PAW-sphere magnetic moments are distinct,
and ambiguous DOSCAR PDOS layouts fail instead of being guessed. Promotion is
backed by a current 14-test receipt and a real CrFeO forward run.

`tb2j` is the reference postprocessor module. Its parent is a local Wannier90
directory (`input_dir` plus the selected prefix); it validates Hamiltonian files
and delegates to `wann2J.py`. It does not require `wannier-window-scan`, so a
user can remove that extension and still run TB2J on a manually supplied
Wannier model.

`pyw90` follows the same independent-module rule. It accepts only its own request
schemas and local files, delegates selected `eig`/`pre`/`cmp` commands to an
externally installed PyW90, and leaves the upstream `auto` scheduler loop outside
the extension. Its separate window-search request plans finite per-spin candidates
from a regular-grid `.eig`, optionally runs an explicit local Wannier90 argv, and
requires a manual `poor` review before bounded refinement.
