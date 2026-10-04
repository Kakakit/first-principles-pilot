# Output contract

All outputs are local files under caller-selected paths.

## Processed electronic-structure bundle

`prepare --output-dir DIR` writes:

- `manifest.json`, schema `fpilot.electronic-structure-dataset.v1`;
- `bands_<spin>.csv`, with one row per `(k point, band)` and columns for path
  position, energy, k-vector, every species/orbital weight, grouped `s/p/d/f`
  weights, and species totals;
- `pdos_<spin>.csv` when DOSCAR is supplied, with energy, total DOS, every
  species/orbital contribution, grouped contributions, and species totals;
- `characterization.json`, schema
  `fpilot.electronic-structure-characterization.v1`;
- `characterization.md`, a compact human-readable view of the same report.

All energies use the one explicit reference recorded in the manifest. PROCAR
and PDOS columns are absolute sums and are never normalized per selector.

The characterization report includes:

- reference-crossing and sampled-path VBM/CBM/gap metrics for each spin;
- ranked species/group and orbital-component weights inside a caller-selected
  near-reference window;
- total and projected DOS at the reference plus integrated DOS in that window;
- final cell magnetization and the last PAW-projected ion/species/total table,
  when available.

The report states that path gaps are not global-gap proofs, DOS metrics depend
on mesh/broadening/NEDOS, and cell and projected magnetic moments are distinct.

## Selective processed-data figures

`plot --mode band-dos` writes two spin rows when the dataset is collinear. Each
row contains a wide band axis and a narrow DOS axis sharing the energy axis.
Selected projection colors are reused between fatband markers and PDOS curves.

`plot --mode pdos` writes a standalone PDOS figure with spin down mirrored by
default. Color encodes projection and line style encodes spin.

`plot --mode fatband` overlays selected processed weight columns using one
absolute marker-area scale. It does not reopen or reparse PROCAR.

## Figures

Commands accept `--formats` with any comma-separated subset of:

- PNG;
- PDF;
- SVG.
- LZW-compressed TIFF.

The output stem is supplied with `--output`; the extension adds the requested suffixes.

## VASP parsed band data

When `vasp-band --data-prefix PREFIX` is used:

- `PREFIX.csv` for one channel, or `PREFIX_1.csv`, `PREFIX_2.csv` for two channels;
- `PREFIX.json` summary/provenance.

CSV columns:

- `k_index`;
- `path_x`;
- `band` (1-based);
- `energy_eV` relative to the selected reference;
- `occupation` when available.

JSON schema version `1.0` contains:

- energy reference;
- absolute input paths and SHA-256 hashes;
- EIGENVAL selection metadata;
- channel point/band counts;
- segment indices, ticks and labels;
- occupation-based VBM/CBM, indirect gap and minimum direct gap when meaningful.

## Audited fatband-panel bundle

`fatband-panel` writes:

- requested PNG/TIFF/PDF/SVG figures under `--output`;
- `PREFIX.csv` containing every selected-window point for every projection and spin, including raw weight and a `marker_visible` flag;
- `PREFIX.json` with SHA-256 input hashes, band OUTCAR audit, Fermi provenance, PROCAR spin-detection mode, species-to-ion mapping, path summary, absolute marker scale, per-window weight summaries, output paths and scientific boundaries;
- a backend-measured alignment-layout JSON for an independent rendered geometry gate.

All fatband-panel marker areas equal `--scale * raw_weight` in pt². A single scale is shared across panels; `--weight-threshold` affects display only and never removes source CSV rows.

With `--overlay-projections`, expanded projections share one axis per spin and
use color to identify the orbital component. The JSON summary records
`projection_layout: "overlay"`; otherwise it records `"separate-panels"`.
Marker areas remain directly comparable across colors and spin channels because
no per-component normalization is applied.

## Plot conventions

- energy zero is the explicit reference selected by each command;
- a dashed horizontal zero-energy line is drawn for band/fatband and energy-on-y DOS plots;
- spin-down DOS is mirrored by default when values are positive in the source;
- DFT/Wannier overlay uses DFT lines and Wannier point markers;
- legacy `fatband` marker area is normalized by its selected maximum;
- `fatband-panel` marker area uses an absolute shared PROCAR-weight scale across every panel.

## Failure behavior

Expected parse/contract errors print:

```text
ERROR: <message>
```

and exit with status `2`.

No remote writes, scheduler submissions, or external services are used.
