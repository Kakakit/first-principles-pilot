# Migration candidates

This file records migration disposition. A `ready` item is runnable only through its registered extension directory and locked registry hash; source-tree files remain provenance, not plugin entrypoints.

## 1. `wannier-window-scan` — retired into `pyw90` mode

Source tree:

- `archived-development-source/01_generate_scan.py`
- `archived-development-source/02_run_scan.py`
- `archived-development-source/03_score_results.py`
- `archived-development-source/04_make_report.py`
- `archived-development-source/`
- `archived-development-source/test_core.py`
- `archived-development-source/scan_config.yaml`

Current disposition: the standalone extension was intentionally removed. Its useful bounded workflow now lives in `extensions/pyw90/scripts/pyw90_window_scan.py` as `check|plan|run|score|refine`, with regular-grid `.eig` feasibility, convergence/lineage-gated band fitting, and manual `poor` review before finite refinement. The historical source paths above remain provenance only. The runner accepts a required argv list, executes with `shell=false`, and never submits to a scheduler; remote execution still routes through `hpc-job`.

## 2. `local-crystal-field-analysis` — migrated, ready, registered

Sources:

- `archived-development-source/calc_local_crystal_field.py`
- `archived-development-source/compare_dense_coarse.py`

Migration result: both local analysis entrypoints and their synthetic test now live under `extensions/local-crystal-field-analysis/`. The manifest records NumPy, units, projection ordering, fixed Cr/Fe comparison mapping, stdout/JSON outputs, and local-only side effects.

## 3. `pdos-orbital-centers` — planned

Source:

- `archived-development-source/calc_center.py`

Scientific boundary: any future extension must preserve the statement that the result is a global-axis nominal centroid, not a strict crystal-field eigenvalue. Input file assumptions, orbital labels, axis conventions, and output units require a fixture before registration.

## 4. `prepare-remote-dense-wannier` — migration blocker

Candidate source: `archived-development-source/prepare_remote_dense_wannier.py`.

Risk and boundary: hard-coded remote paths require splitting local template generation from remote authorized execution before any copy or registration. It must not be directly copied into a runnable extension, and it must not be registered while the path/authorization split is unresolved.
