# PyW90 VASP/Wannier90 extension

This extension now has three deliberately separate levels:

1. `scripts/pyw90_compare.py` is the recommended two-spin comparison workflow. One `fpilot.pyw90-compare-request.v1` file binds the spin channels, seednames, `.win/.wout/checkpoint` lineage, VASP k-path, explicit Fermi source, prepared DFT/Wannier band files, display window, metric window, and output directory.
2. `scripts/pyw90_window_scan.py` is the bounded energy-window search mode. It resolves the outer energy support carrying the chosen projector weight (or accepts an explicit range), anchors the outer lower edge in a complete `.eig` band gap, scans an independent progressive frozen window with explicit disentanglement headroom, and scores a caller-selected scientific subwindow. A smaller refinement round is available only after a human marks that spin `poor`.
3. `scripts/pyw90.py` remains the compatible low-level adapter for expert use. It validates and delegates one native `eig`, `pre`, or `cmp` argv and keeps full stdout/stderr outside the JSON receipt.

No entrypoint submits jobs, runs VASP, edits production inputs, or exposes PyW90 `auto`. The scan mode may run only the caller's explicit local Wannier90 argv, with `shell=False`, finite candidates, workers, and timeout.

## Bounded window-search mode

Use one request per spin. Start with the worse channel when visual review or the comparison metrics show a clear asymmetry, for example `spin_down`. The complete example is [`examples/window_scan_request.json`](examples/window_scan_request.json).

```bash
python scripts/pyw90_window_scan.py check  --request window_scan_request.json
python scripts/pyw90_window_scan.py plan   --request window_scan_request.json
python scripts/pyw90_window_scan.py run    --request window_scan_request.json
python scripts/pyw90_window_scan.py score  --request window_scan_request.json
```

`source.eig` and `source.dft_band` have different roles. The regular-grid, per-spin seed `.eig` supplies the energy topology and exact per-k counts. Every proposed `dis_win_min` is the midpoint of a complete adjacent-band gap below the projector-support range; by default only the nearest such gap is used. The smallest possible `dis_win_max` must contain at least `num_wann` states at every sampled k point and the complete projector-support range. `search.max_outer_excess_states` prevents an unnecessarily deep outer window from silently increasing the candidate space far beyond `num_wann`; deeper gaps are considered only when `allow_deep_gap_fallback=true`.

The frozen window has a different job. It does **not** have to contain the projector-support range and is not expanded to make `frozen_most` approach `num_wann`. At each k point, frozen states consume exact dimensions of the Wannier subspace; `search.min_disentanglement_headroom` therefore enforces `frozen_most <= num_wann - headroom`. In automatic-projector mode, the default `frozen_projectability_coverages=[0.4,0.5,0.6]` generates progressively wider high-weight core intervals while the larger target coverage (typically 0.90–0.99) remains outer support only. The largest accepted core interval also becomes the default ranking window. These fractions are search candidates, not universal physical constants.

`frozen_windows_relative_eV` overrides those automatic core intervals after PROCAR/fatband review has identified chemically continuous bands worth fixing exactly. For explicit-energy mode, or as a fallback when no projector core is available, planning begins with `frozen_seed_window_relative_eV` (the template frozen window shifted by `E_F` when omitted), cautiously contracts its upper edge when necessary, and progressively extends only its lower edge. A historical `search.initial` is retained only if the entire candidate passes the new constraints.

Automatic outer-support selection uses `target.mode=auto_projector`. The lowest-cost native source is the same-spin regular-grid Wannier90 `.amn`: the script sums `|A_mn(k)|^2` over every trial orbital present in that file and finds the narrowest energy interval containing the requested cumulative fraction of their total weight. `.amn` does not provide a selector for only some configured trial orbitals; use a processed PROCAR table when a species/orbital subset is required. `target.projectability.source=csv` consumes the line-path state table produced by the independent `electronic-structure-characterization` extension; `selectors` names the orbital-weight columns to sum and `energy_eV` must already be relative to the declared Fermi reference. The PROCAR path grid need not match the regular `.eig` grid because it estimates only energy support and orbital character; `.eig` remains the sole source for gaps and exact state counts. DOSCAR is intentionally not used because it loses `(k, band)` character. The support interval may cross `E_F` and may contain more than `num_wann` states; that is legal for an outer window and is not a reason to freeze all of them.

For the Cr/Fe-d + O-p model, use the processed PROCAR fatband first to identify chemically continuous high-weight manifolds. Keep the historical narrow frozen interval as the first control, add wider occupied-manifold candidates stepwise, and add `CBM+0.5`/`CBM+1.0 eV` candidates only when the fatband shows that those conduction states retain the intended d/p character. A fixed energy window cannot distinguish a target d/p band from an intruding state of different character.

For a manually chosen scientific range, use the independent explicit mode:

```json
"target": {
  "mode": "explicit_energy",
  "energy_window_relative_eV": [-8.0, 2.0]
}
```

`plan` writes immutable per-candidate `.win` copies and `window_scan_manifest.json`; it never edits `source.template_win`. `run` executes only pending candidates by default and isolates stdout, stderr, and status. `score` ranks RMSE/MAE/maximum residual inside `comparison.ranking_window_relative_eV`, which may be any scientifically intended subwindow inside the projector-support target. Reference DFT energies select the ranking samples, so a mismatched Wannier partner that crosses a ranking boundary remains in the residual rather than disappearing. `comparison.metric_window_relative_eV` may be a broader paired-state diagnostic window. `comparison.max_excess_jump_eV` optionally rejects a candidate whose largest adjacent Wannier-band jump exceeds the matched DFT maximum by the declared amount; it is a cross-gap-spike alarm, not a replacement for inspecting the overlay.

When `dft_band_offset` is `auto`, the primary and full diagnostic metrics each record their independently optimized order-preserving offset. Candidate primary scores must nevertheless share one primary offset and the same DFT reference sample count; if those signatures differ, `score` keeps the diagnostic rows but blocks `best_candidate_id` until the caller supplies one explicit common offset. Only candidates with completed/disentanglement-converged/localization-converged `.wout` evidence, matching candidate `.win/.wout` windows, and a passed optional shape gate enter the primary ranking. Window lineage uses a `1e-5 eV` tolerance because standard Wannier90 `.wout` output rounds window values to five decimal places. Spread and raw RMSE are retained as evidence; neither may rescue visibly unphysical band connectivity.

If a spike remains, run a diagnostic pair outside the automated ranking: first stop after disentanglement (`num_iter=0`), then repeat with localization enabled. A spike already present at `num_iter=0` points to the projector/window subspace; a spike introduced only after localization points instead to localization freedom, centres, or spread behaviour. After the window/projector diagnosis, compare progressively denser regular meshes and inspect real-space Hamiltonian decay. A coarse mesh can make a delocalized model look acceptable at sampled points yet oscillate on interpolation. These are separate convergence tests and are not inferred from a low band RMSE.

For plotting, a DFT band coordinate that restarts at each high-symmetry segment is unfolded onto one continuous display axis while interpolation and error evaluation remain segment-local. This prevents disconnected path segments from being drawn on top of one another.

After inspecting the best overlay, change only the review object when another local search is justified:

```json
"review": {
  "quality": "poor",
  "spin": "spin_down",
  "note": "visible mismatch remains near the Fermi level"
}
```

Then run `refine`. It keeps the gap-anchored `dis_win_min` fixed, adds one-coordinate neighbours for `dis_win_max` and the frozen boundaries around the current eligible best candidate, and records the review note in the manifest history. Every refinement must still cover the target in the outer window, preserve the configured outer-state ceiling and frozen headroom, and pass exact per-k counts. Refinement is never triggered by the numerical score alone: the hard limits are three rounds, sixteen new candidates per round, sixty-four initial candidates, eight workers, and a 24-hour per-candidate timeout. Run and score the new pending candidates, inspect them, and repeat only while the configured round budget remains.

## Recommended dual-spin workflow

Prepare the VASP `bnd_up.dat`/`bnd_down.dat` and each seed's `*_band.dat` plus `*_band.labelinfo.dat` through an audited, plot-only workflow. Then fill one request:

```json
{
  "schema": "fpilot.pyw90-compare-request.v1",
  "dft": {
    "spin_mode": "collinear-ispin2",
    "fermi_eV": 5.0182,
    "fermi_source": "inputs/scf/OUTCAR",
    "kpoints": "inputs/band/KPOINTS",
    "expected_bands": 72,
    "bands": {"spin_up": "prepared/bnd_up.dat", "spin_down": "prepared/bnd_down.dat"}
  },
  "wannier": {
    "spin_up": {
      "label": "Spin up", "seedname": "wan.1", "expected_num_wann": 54,
      "win": "inputs/spin_up/wan.1.win", "wout": "inputs/spin_up/wan.1.wout",
      "checkpoint": "inputs/spin_up/wan.1.chk",
      "band": "prepared/spin_up/wan.1_band.dat",
      "labelinfo": "prepared/spin_up/wan.1_band.labelinfo.dat"
    },
    "spin_down": {
      "label": "Spin down", "seedname": "wan.2", "expected_num_wann": 54,
      "win": "inputs/spin_down/wan.2.win", "wout": "inputs/spin_down/wan.2.wout",
      "checkpoint": "inputs/spin_down/wan.2.chk",
      "band": "prepared/spin_down/wan.2_band.dat",
      "labelinfo": "prepared/spin_down/wan.2_band.labelinfo.dat"
    }
  },
  "comparison": {
    "display_window_relative_eV": [-8.0, 5.0],
    "metric_window_relative_eV": [-8.0, 5.0],
    "dft_band_offset": "auto",
    "allow_lineage_mismatch_for_diagnostic": false
  },
  "output_dir": "comparison"
}
```

Run with the registered workspace Python:

```bash
06_工具/.venv-figure/bin/python extensions/pyw90/scripts/pyw90_compare.py check --request compare.json
06_工具/.venv-figure/bin/python extensions/pyw90/scripts/pyw90_compare.py run --request compare.json
```

`check` is read-only. It fails early when the Fermi value does not match its OUTCAR source, spin keys or seednames are ambiguous, the DFT/Wannier band counts differ from their declarations, KPOINTS and labelinfo describe different paths, or `.win/.wout/checkpoint` lineage is inconsistent.

For a deliberately bounded diagnostic, a non-local checkpoint may be declared as:

```json
{"sha256": null, "unavailable_reason": "large checkpoint retained on the cluster"}
```

Such a request is `PARTIAL`, not fully reproduced. A `.win/.wout` mismatch is blocked by default. Setting `allow_lineage_mismatch_for_diagnostic: true` permits only a visibly warned comparison; it never relabels the checkpoint as if it came from the current `.win`.

`run` writes a fixed tree:

- `preflight.json`: resolved files, sizes, hashes, spin/path identities, and lineage status.
- `metrics.json`: selected common order-preserving DFT band offset; overall and per-band MAE/RMSE/maximum difference inside the requested relative-energy interval.
- `dft_wannier_spin_comparison.{png,tiff,pdf,svg}`: two aligned spin panels; every disconnected path segment is drawn independently.
- `dft_wannier_spin_comparison.alignment-layout.json`: backend-measured panel geometry.
- `run.json`: compact output receipt and all warnings.

The metric is not PyW90's unrestricted native `Final Quality`. It compares only pairs for which both DFT and Wannier energies lie inside `metric_window_relative_eV`, after segment-local interpolation and a single order-preserving band offset. It is an interface/model-fidelity diagnostic, not a convergence proof.

## Low-level compatibility adapter

The earlier `fpilot.pyw90-request.v1` request remains supported. An executable containing a path separator is resolved relative to the request file, never relative to `input_dir`; a plain `pyw90` is resolved through `PATH`. `expected_output_base` may be `input_dir` (compatible default) or `output_dir`. Receipts include SHA-256 input/output hashes, Python/PyW90 versions, and relevant cache environment values.

```bash
python scripts/pyw90.py doctor --executable /absolute/path/to/pyw90
python scripts/pyw90.py check --request request.json
python scripts/pyw90.py run --request request.json
```

## Boundaries

- Collinear `ISPIN=2` comparison requires two explicit channels; never pass the unsplit two-spin EIGENVAL to `eig suggest` and interpret a NumPy shape error as a scientific result.
- `pre band` commonly produces `bnd_up.dat` and `bnd_down.dat`; declare both rather than expecting a generic `bnd.dat`.
- K-path labels are parsed as text. No `eval` or agent-invented replacement path is used.
- Current `.win` settings and a checkpoint's actual `.wout` history are different provenance objects. A mismatch is reported, not repaired silently.
- `auto` remains outside this extension because it can manage Wannier/scheduler loops.
- Energy-window feasibility comes from the uniform seed `.eig`, not a high-symmetry line path. The `.amn` or processed-PROCAR CSV only determines projector-weight support. A candidate satisfying these constraints is only runnable; it is not automatically a good Wannier model.
- The scan mode is per-spin and intentionally supports asymmetric effort: a satisfactory spin-up channel can remain frozen while a manually rejected spin-down channel receives bounded refinement.

Upstream PyW90 is provided by [Cloudiiink/pyw90](https://github.com/Cloudiiink/pyw90), pinned here to the external PyPI `1.0.7` compatibility anchor. The GPL-3.0 PyW90 source is not bundled or modified.

## Workbench fixed-reference measure protocol (0.5.1)

`python scripts/pyw90_window_scan.py measure --request request.json` reads an already lineage-checked `curves.csv` from the workbench. The request specifies `curves_csv`, `target_relative_eV: [lower, upper]` and a common zero-based `dft_band_offset`. The CSV has method (`DFT`/`Wannier90`), 1-based k_index and band, and energy_relative_eV columns; complete rectangular grids and equal point counts are required.

This separate action supports one scalar, collinear or spinor channel. Its JSON stdout reports MAE/RMSE/maximum absolute error in meV, using a fixed DFT reference mask and caller-selected order-preserving correspondence. Fitted values outside the target remain in the error. The caller validates the exact source, full path and channel first and holds one reference/offset across the campaign. Unsupported band correspondence must remain a diagnostic/manual overlay, not a ranking. This action does not launch a process or job; existing per-spin standalone search actions are unchanged.
