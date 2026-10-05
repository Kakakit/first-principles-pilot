# First-principles extensions

This directory contains reusable analysis or calculation extensions. New packages use `extension.manifest.json` and an independent doctor/check/run CLI: see the [v2 protocol](../../docs/extensions-b/PROTOCOL.md) and `extension-templates/structure-statistics`. TB2J v2 supports installation, enable/disable and removal, and defaults to direct CLI execution on the source server.

The legacy `extension.json` discovery instructions below remain for compatibility.

## Discovery and module contract

新扩展的唯一创建清单见
[`CREATE_FROM_GITHUB.md`](CREATE_FROM_GITHUB.md)。从上游 GitHub 项目复制时，先
复制 [`_template/`](_template/)，再参考完整的
[`pyw90/`](pyw90/) 外部命令适配器；这样保留可读性和人工可运行性，不增加核心
插件对第三方库的硬依赖。

The manager discovers every direct child containing `extension.json`. Ordinary
`list`, `describe`, and `validate-all` use those manifests directly; they do not
require a current registry lock. `registry.json` is a useful lock/audit record,
not a prerequisite for using a copied extension. Run `fpilot_extensions.py audit`
when you want to find stale or missing registry entries.

New command-style modules may add `module_type`, `requires`, `provides`, and
`command` as one group. `requires` and `provides` are artifact roles, not names
of other extensions. `command.path` points to a file inside the extension and
`command.usage` is a short copyable command. Legacy v1 manifests may omit the
whole group.

## Current v2 structure

Immutable tool code belongs in the versioned plugin's `extensions/<name>/`.
Give post-processing its own stage/attempt under
`flows/<flow>/stages/<stage>/attempts/<attempt>/`; inputs and outputs stay at
that attempt root and metadata stays in `_fpilot/`. The plugin extension tree
is a source package, not a run-data directory. Old `prepare-extension` helpers
are available only through the explicit workspace `legacy` entry for recovery.

## Lifecycle

1. Copy the non-runnable scaffold in `_template/` into `extensions/<name>/`.
2. Record the real source path/hash, dependencies, input/output contract, tests, side effects, and authorization route in `extension.json`.
3. Run `scripts/fpilot_extensions.py validate <path>` and inspect `metadata_valid`, `source_hash_verified`, `test_paths_present`, and `tests_executed=false`.
4. Planned metadata may validate for documentation but is never registration-eligible. `registry.json` can lock name, version, status, and directory hash for reproducibility, but adding a discovered extension does not require an immediate registry edit.
5. The manager never executes tests. Before a `ready` extension is selected, an external test receipt should be recorded in the stage/ledger; the manager reports `tests_executed=false`.
6. In `calc-pilot`, describe and validate the discovered extension, select `ready` by default (or obtain explicit approval for `experimental`), and record the dependency check before use.

The manager never executes extension entrypoints or tests. Remote/scheduler
work must use the current input-audited domain submission route after the user
requests it. Old hpc-submit/hpc-batch contracts remain historical compatibility
interfaces. No extension independently authorizes cancellation, overwrite,
deletion, or retry.

## Registered extensions

- `local-crystal-field-analysis` extracts local d-manifold metrics from Wannier90 Hamiltonians and compares the fixed Cr/Fe coarse/dense mapping documented in its README.
- `lcalcpol-polarization-analysis` reads POSCAR/OUTCAR LCALCPOL `p[ion]` and `p[elc]`, reports raw polarization, and reduces the dipole by integer lattice-coordinate quanta. A single-structure result is a formal polarization branch; branch reduction only selects a representative and is not spontaneous polarization or evidence of switchable ferroelectricity. Spontaneous polarization requires a same-branch polar/reference adiabatic comparison.
- `electronic-structure-characterization` is a local-only ready workflow that parses PROCAR/DOSCAR once into reusable spin-resolved band-weight and PDOS tables, extracts bounded band-edge/near-reference/DOS/magnetic descriptors, and selectively composes band+DOS, standalone PDOS and fatband figures. The earlier VASP/Wannier90 plotting commands remain available through `banddos.py`. Path gaps, PAW projections, DOS broadening and magnetic-moment definitions retain explicit scientific boundaries.
- `tb2j` is a local-only experimental postprocessor. It checks the Wannier90 Hamiltonian parent, runs the user's `wann2J.py`, and records the command and logs. For every spinor or collinear prefix it accepts `_tb.dat`, or `_hr.dat` plus `_centres.xyz`; it does not require a window-scan extension and does not interpret the sign of exchange parameters.
- `pyw90` is a local-only experimental postprocessor. Version 0.3.0 retains the complete two-spin comparison request and adds a separate bounded energy-window-search mode. The search uses regular-grid `.eig` state-count constraints, immutable candidate inputs, optional explicit local Wannier90 execution, convergence/lineage-gated band-error ranking, and human-triggered finite refinement for a poor spin channel. It never exposes upstream `auto`, VASP, a scheduler, or silent production-input repair.

Experimental extensions require explicit approval before selection:

- `crystal-structure-distortion-analysis` is local-only with `local_read` and `local_write`. It analyzes VASP5/6 POSCAR/CONTCAR geometry using center/ligand species and coordination numbers supplied by config; it does not infer chemical coordination. For octahedra, Q2/Q3 use reproducibly sorted pair-average lengths rather than crystallographic directions, while QJT is a scalar amplitude. Directional interpretation requires an external local-axis convention.

`pdos-orbital-centers` remains a planned candidate. `prepare-remote-dense-wannier` remains blocked until its local generation and authorized remote execution are separated.

`electronic-structure-characterization` version 0.3.0 is `ready` after 14 current-host parser/workflow/CLI tests and a real 180-k-point, 72-band, 14-ion CrFeO prepare-characterize-plot run. The processed bundle exposes 48 band-weight and 48 PDOS selectors per spin; concrete data, figures and QA receipts remain under `runs/crfeo-ferri-v1/analysis/s8_electronic_structure_characterization/`.
