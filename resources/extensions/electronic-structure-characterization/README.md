# electronic-structure-characterization

Reusable local VASP electronic-structure preparation, characterization and plotting workflow, with the earlier VASP/Wannier90 plotting commands retained for compatibility.

The primary workflow parses `PROCAR` and optional `DOSCAR` once, writes compact
spin-resolved wide tables plus a manifest, extracts evidence-bounded electronic
descriptors, and lets later plotting calls choose only a mode, dataset and
projection selectors. Raw VASP files are not reparsed for every figure.

## Capabilities

| Command | Main inputs | Purpose |
|---|---|---|
| `prepare` | `PROCAR`, `POSCAR`, line-mode `KPOINTS`, completed band `OUTCAR`, Fermi reference; optional `DOSCAR` | Produce reusable band/weight/PDOS tables, manifest and characterization report |
| `characterize` | processed dataset directory or `manifest.json` | Recompute sampled-path gaps, near-reference character, DOS metrics and magnetic moments |
| `plot` | processed dataset + `band-dos`, `pdos`, or `fatband` mode | Selectively compose figures without reparsing VASP outputs |
| `vasp-band` | `EIGENVAL`, optional `OUTCAR`, line-mode `KPOINTS`, `POSCAR` | VASP bands; spin-resolved EIGENVAL; mixed uniform+zero-weight path selection; optional gap summary |
| `vaspkit-band` | `REFORMATTED_BAND_*.dat` or equivalent numeric matrices | VASPKIT-style band matrices; one or multiple channels |
| `wannier-band` | `*_band.dat`, `*_band.labelinfo.dat` | Wannier90 interpolated bands, including `.1/.2` spin-channel convention |
| `compare` | DFT band source + Wannier90 band source | DFT/Wannier90 overlay after segment-wise path remapping |
| `fatband` | `PROCAR` or generic `x E weight` table | Ion/orbital projected fatband |
| `fatband-panel` | completed band `OUTCAR`, `PROCAR`, `POSCAR`, line-mode `KPOINTS`, explicit/reference Fermi source | Audited species/orbital-by-spin fatband workflow with figures, source table, provenance and layout receipt |
| `dos` | `DOSCAR` or generic numeric DOS table | TDOS and common collinear PDOS layouts |
| `panel` | `EIGENVAL` + `DOSCAR` | Publication-style band + DOS side panel on a common energy axis |

Convenience wrappers in `scripts/` expose the same subcommands separately.

## Primary processed-data workflow

Prepare the dataset once:

```bash
python src/eschar.py prepare \
  --procar band/PROCAR \
  --doscar dos/DOSCAR \
  --poscar band/POSCAR \
  --kpoints band/KPOINTS \
  --band-outcar band/OUTCAR \
  --reference-outcar dos/OUTCAR \
  --output-dir analysis/electronic_structure_dataset
```

The output directory contains:

```text
manifest.json
bands_spin_up.csv
bands_spin_down.csv
pdos_spin_up.csv
pdos_spin_down.csv
characterization.json
characterization.md
```

Each band row is one `(k point, band)` state and stores the shifted energy,
k-vector, and every available species/orbital PROCAR weight. Each PDOS row
stores the shared shifted energy, total DOS, and the corresponding
species/orbital contributions. Values are absolute sums; no column is
independently normalized.

Compose a paper-style band + PDOS figure. Spin up and spin down form two rows;
within each row the band and DOS axes share the energy coordinate:

```bash
python src/eschar.py plot \
  --mode band-dos \
  --dataset analysis/electronic_structure_dataset \
  --select Fe:d --select Cr:d --select O:p \
  --scale 22 --emin -4 --emax 4 \
  --output figures/band_pdos
```

Make a standalone mirrored-spin PDOS figure:

```bash
python src/eschar.py plot \
  --mode pdos \
  --dataset analysis/electronic_structure_dataset \
  --select Fe:d --select Cr:d --select O:p \
  --output figures/pdos
```

Reuse the same processed files for a five-component fatband:

```bash
python src/eschar.py plot \
  --mode fatband \
  --dataset analysis/electronic_structure_dataset \
  --select Fe:d-components \
  --scale 30 --weight-threshold 0.01 \
  --output figures/Fe_d_fatband
```

`characterization.json` separates sampled-path band-edge metrics, near-reference
PROCAR character, DOS-at-reference metrics, and magnetic quantities. A band
gap inferred from PROCAR is explicitly bounded to the sampled line path and
uses the chosen energy reference because PROCAR has no occupation records.
The OUTCAR cell magnetization and PAW-sphere projected magnetization are
reported separately.

## Main improvements over the source scripts

The original scripts contained useful parsing ideas but also system-specific assumptions. This migration:

- removes hard-coded FeCr2O4 titles and fixed Fermi energies;
- removes the fixed `UNIFORM_KPOINTS = 150` assumption: `--path-selector auto` detects a zero-weight path when a positive-weight mesh and zero-weight path coexist;
- removes the absolute dependency on `/Users/kafka/.agents/skills/...` and runs with only NumPy + Matplotlib;
- supports both one-channel SOC/non-spin EIGENVAL and ordinary two-channel collinear EIGENVAL records;
- derives reciprocal path distance from line-mode KPOINTS + POSCAR when supplied;
- turns VASPKIT segment-local x coordinates into a continuous plotting coordinate;
- keeps Wannier90 path discontinuities/duplicated high-symmetry boundaries explicit;
- maps Wannier bands to DFT segment widths for comparison instead of assuming identical raw x coordinates;
- adds total/projected DOSCAR parsing and spin-down mirroring;
- adds PROCAR ion/orbital weight summation for fatbands;
- adds a complete `fatband-panel` workflow: terminal/LORBIT audit, VASP5/6 species mapping, headerless collinear-spin PROCAR support, explicit SCF Fermi provenance, absolute shared marker scale, discontinuous-path labels, CSV/JSON output, and backend-measured panel alignment;
- emits optional parsed VASP-band CSV/JSON with input hashes and band-edge metrics;
- provides deterministic fixtures and parser/CLI tests.

## Installation

```bash
python -m pip install -r requirements.txt
```

Only local reads/writes are performed. No scheduler or remote access is used.

## Quick examples

### 1. VASP band from EIGENVAL

For a normal line-mode run:

```bash
python src/banddos.py vasp-band \
  --eigenval EIGENVAL \
  --outcar OUTCAR \
  --kpoints KPOINTS \
  --poscar POSCAR \
  --emin -3 --emax 3 \
  --output figures/vasp_band
```

For a hybrid/mixed mesh where the line path was appended with zero weights, the default `--path-selector auto` uses the zero-weight block. You can force behavior with `all`, `zero-weight`, or `positive-weight`.

Add machine-readable parsed output:

```bash
python src/banddos.py vasp-band ... \
  --data-prefix analysis/vasp_band
```

This writes one CSV per spin channel plus `analysis/vasp_band.json`.

### 2. VASPKIT formatted bands

```bash
python src/banddos.py vaspkit-band \
  --band-file REFORMATTED_BAND_UP.dat \
  --band-file REFORMATTED_BAND_DW.dat \
  --labels GAMMA,X,M,GAMMA \
  --channel-names "Spin up,Spin down" \
  --output figures/dft_band
```

If the matrix energies are absolute rather than already referenced, pass `--fermi VALUE`.

### 3. Wannier90 bands

The directory auto-discovery recognizes either:

```text
wannier90_band.dat
wannier90_band.labelinfo.dat
```

or the two-channel convention:

```text
wannier90.1_band.dat
wannier90.1_band.labelinfo.dat
wannier90.2_band.dat
wannier90.2_band.labelinfo.dat
```

Run:

```bash
python src/banddos.py wannier-band \
  --directory ./wannier \
  --fermi 5.6635 \
  --output figures/wannier_band
```

### 4. DFT vs Wannier90

Using VASPKIT DFT matrices:

```bash
python src/banddos.py compare \
  --dft-format vaspkit \
  --dft-band dft/REFORMATTED_BAND_UP.dat \
  --dft-band dft/REFORMATTED_BAND_DW.dat \
  --wannier-dir wannier \
  --wannier-fermi 5.6635 \
  --output figures/dft_wannier
```

Or use raw VASP EIGENVAL:

```bash
python src/banddos.py compare \
  --dft-format eigenval \
  --eigenval EIGENVAL --outcar OUTCAR \
  --kpoints KPOINTS --poscar POSCAR \
  --wannier-dir wannier --wannier-fermi 5.6635 \
  --output figures/dft_wannier
```

The comparison requires the same number and ordering of path segments. It rescales Wannier x coordinates independently inside each segment; it does **not** claim two different crystallographic paths are equivalent.

### 5. Fatband from PROCAR

For a complete, species-aware analysis (recommended):

```bash
python src/banddos.py fatband-panel \
  --procar PROCAR --poscar POSCAR --kpoints KPOINTS \
  --band-outcar OUTCAR \
  --reference-outcar ../scf/OUTCAR \
  --projection Cr:d --projection Fe:d --projection O:p \
  --emin -4 --emax 4 \
  --output analysis/fatband \
  --data-prefix analysis/fatband_source \
  --alignment-layout analysis/fatband_alignment-layout.json
```

This command refuses an unfinished/fatal band `OUTCAR`, requires `LORBIT >= 10`,
checks POSCAR/PROCAR atom counts and `ISPIN`, and requires an explicit Fermi value
or reference OUTCAR. Marker area uses one absolute PROCAR-weight scale across all
species and spin panels. The display threshold does not remove rows from the
source CSV. Default exports are PNG, LZW-compressed TIFF, PDF and SVG.

Expand the five d or three p components into separate spin-resolved panels with
one selector:

```bash
python src/banddos.py fatband-panel ... \
  --projection Cr:d-components

python src/banddos.py fatband-panel ... \
  --projection O:p-components
```

`d-components` expands to `dxy`, `dyz`, `dz2`, `dxz`, `dx2-y2`; the common
`dx2-y2` spelling is mapped to VASP's `x2-y2`/`dx2-y2` column as available.
`p-components` expands to `px`, `py`, `pz`. Ordinary comma-separated selectors
retain their previous sum semantics, so `Cr:dxy,dxz` remains one combined panel.

To compare all expanded components on the same axes, add
`--overlay-projections`. This produces one shared axis per spin channel, uses
color for orbital identity, and keeps marker area on the same absolute raw
PROCAR-weight scale across every color and spin:

```bash
python src/banddos.py fatband-panel ... \
  --projection Cr:d-components \
  --overlay-projections \
  --scale 30 --weight-threshold 0.01
```

The overlay flag is opt-in: without it the original five-row or three-row
component layout is unchanged. `--scale 30` means a raw weight of 1 is drawn at
30 pt²; it is useful when several colored projections occupy the same band.

For quick ion-index plots without the audit/provenance workflow, the legacy
`fatband` command remains available:

```bash
python src/banddos.py fatband \
  --procar PROCAR \
  --kpoints KPOINTS --poscar POSCAR \
  --ions 1,3-6 \
  --orbitals dxy,dxz,dyz \
  --fermi 5.50 \
  --output figures/fatband_d
```

Group selectors `p`, `d`, `f`, and `all` are accepted for orbitals. Ion indices are 1-based and follow PROCAR.

A generic three-column table can also be plotted:

```bash
python src/banddos.py fatband \
  --table PBAND.dat --x-col 1 --energy-col 2 --weight-col 3
```

### 6. DOS / PDOS

Total DOS:

```bash
python src/banddos.py dos --doscar DOSCAR --output figures/tdos
```

Sum selected ion/orbital PDOS:

```bash
python src/banddos.py dos \
  --doscar DOSCAR \
  --ions 1-2 \
  --orbitals d \
  --output figures/dos_d
```

For a VASPKIT or other whitespace numeric table whose first column is energy:

```bash
python src/banddos.py dos \
  --table TDOS.dat \
  --labels "Total up,Total down" \
  --fermi 0.0 \
  --output figures/tdos
```

By default spin-down curves are mirrored to the negative DOS axis. Use `--no-mirror-down` to disable this.

### 7. Band + DOS panel

```bash
python src/banddos.py panel \
  --eigenval EIGENVAL --outcar OUTCAR \
  --kpoints KPOINTS --poscar POSCAR \
  --doscar DOSCAR \
  --emin -3 --emax 3 \
  --output figures/band_dos
```

## Scientific / format boundaries

1. **Energy reference is explicit.** Raw EIGENVAL energies are shifted by `--fermi` or the last `E-fermi` in `OUTCAR`. Wannier90 energies are shifted by `--fermi`/`--wannier-fermi`. VASPKIT matrices are assumed already referenced unless a Fermi value is supplied.
2. **Band gaps from EIGENVAL use occupations.** They are numerical occupied/unoccupied edges for the selected k-path, not automatically a rigorous global gap if the selected path does not contain the true extrema.
3. **PROCAR fatband uses the first scalar projection table after each band.** Headerless concatenated collinear spin blocks are detected. For noncollinear PROCAR files, vector magnetization projection tables are deliberately ignored in this version.
4. **DOSCAR PDOS inference is intentionally conservative.** Common collinear grouped (`s,p,d`) and orbital-resolved (`s,py,pz,px,d...`, optional f) layouts are supported. Ambiguous/unsupported column counts fail rather than guessing.
5. **Orbital weights depend on VASP projection settings.** Fatband/PDOS values are PAW projection weights and should not be interpreted as unique orbital populations independent of `LORBIT`, PAW spheres, and calculation setup.
6. **Comparison path mapping is geometric plotting alignment.** It checks segment count/order; it does not solve crystallographic coordinate-system equivalence for unrelated KPOINTS paths.

## Tests

```bash
python tests/run_tests.py
```

The fixture checks:

- one/two-spin EIGENVAL parsing;
- high-symmetry path segmentation;
- VASPKIT path-coordinate restart handling;
- Wannier90 spin-channel discovery;
- DOSCAR TDOS + grouped spin-PDOS;
- PROCAR orbital projections;
- VASP 6 fixed-width k-point coordinates and headerless collinear spin blocks;
- species-aware, audited multi-panel fatband workflow with CSV/JSON/layout output;
- five-component d and three-component p preset expansion, including the
  `dx2-y2` PROCAR spelling alias;
- opt-in same-axis component overlays with the original separate-panel layout
  retained by default;
- CLI figure generation for VASP band, Wannier band, DFT/Wannier comparison, DOS, fatband and band+DOS panel.

## Extension integration

`extension.json` records the three preserved original sources, entrypoints, dependencies, contracts, fixtures, tests, and local-only side effects. The current host regression suite and a real CrFeO forward run provide the readiness receipt; the repository validator remains the authority for the registry directory hash.
