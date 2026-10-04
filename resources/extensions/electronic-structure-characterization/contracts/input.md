# Input contract

The extension is a local preparation, characterization and plotting workflow.
Each subcommand has its own required input set.

## Processed electronic-structure dataset

`prepare` requires:

- `PROCAR` containing scalar orbital projections and band energies;
- VASP5/6 `POSCAR` with species names;
- line-mode `KPOINTS` matching the selected PROCAR path;
- a completed band `OUTCAR` confirming terminal completion and `LORBIT >= 10`;
- exactly one energy reference: `--fermi` or `--reference-outcar`.

`DOSCAR` is optional, but is required to produce PDOS tables and use
`plot --mode band-dos` or `plot --mode pdos`. A separate magnetic OUTCAR may be
supplied; otherwise the reference OUTCAR is used for magnetization extraction.

`characterize` and `plot` accept either the processed dataset directory or its
`manifest.json`. The manifest must use schema
`fpilot.electronic-structure-dataset.v1`; every referenced table must retain its
original header and row ordering.

Processed selectors use `SPECIES:ORBITAL` syntax. Group columns such as `Fe:d`
and `O:p`, component presets such as `Cr:d-components`, and comma sums such as
`Fe:dxy,dxz` are supported when the corresponding columns exist in the
manifest. Band+DOS mode requires every selected column to exist in both the
band-weight and PDOS tables.

## VASP band

- `EIGENVAL`: standard VASP layout. Supported band records:
  - one energy + occupation (non-spin/SOC-style EIGENVAL);
  - two energies + two occupations (ordinary collinear ISPIN=2 EIGENVAL).
- optional `OUTCAR`: last `E-fermi` value is used when `--fermi` is absent.
- optional line-mode `KPOINTS` or `KPATH.in`: points-per-segment on line 2 and paired endpoints after the mode/header section.
- optional `POSCAR`: single scale-factor VASP lattice; used only to convert fractional reciprocal coordinates to physical path distance.

`--path-selector auto` selects a zero-weight block only when positive and zero weights coexist; otherwise all k-points are retained.

## VASPKIT band matrix

Whitespace numeric matrix with x/path coordinate in column 1 and one band energy per remaining column. A decrease/reset in the x coordinate starts a new segment. Segment-local x is converted to a continuous plotting coordinate.

## Wannier90 band

A matching pair of:

- `<seed>_band.dat` and `<seed>_band.labelinfo.dat`; or
- `<seed>.1_band.dat`/labelinfo and `<seed>.2_band.dat`/labelinfo.

`band.dat` must contain repeated two-column x/energy blocks of equal point count. `labelinfo.dat` supplies point indices, path positions, and labels.

## DFT/Wannier comparison

Both sources must describe the same number/order of path segments. The extension aligns source x coordinates segment-by-segment. It does not infer a different symmetry path or coordinate rotation.

## PROCAR fatband

Standard text PROCAR with parseable `# of k-points`, `# of bands`, `# of ions`, k-point, band-energy, and `ion ... tot` projection tables. Ion selectors are 1-based.

For noncollinear PROCAR output, only the first scalar charge-projection table is consumed; vector magnetization projection tables are not used.

### Audited species-aware fatband panel

`fatband-panel` additionally requires:

- a VASP5/6 `POSCAR` with explicit species symbols and counts;
- line-mode `KPOINTS` whose segment endpoints match the PROCAR path;
- the completed band-run `OUTCAR`, with terminal `General timing`, no fatal marker, and `LORBIT >= 10`;
- exactly one energy reference: `--fermi` or `--reference-outcar`;
- one or more repeatable `--projection SPECIES:ORBITAL[,ORBITAL...]` selectors.

Two convenience selectors expand rather than sum components:

- `SPECIES:d-components` -> `dxy`, `dyz`, `dz2`, `dxz`, `dx2-y2`;
- `SPECIES:p-components` -> `px`, `py`, `pz`.

`dx2-y2`, `d_x2-y2`, and `d_z2` are accepted aliases. Other comma-separated
selectors keep the original summed-projection behavior.

`--overlay-projections` places every expanded projection on the same axis for
each spin channel and distinguishes them by color. It changes only the layout;
the selected projections, raw weights, energy window and CSV rows are unchanged.
Without the flag, each projection retains its own row.

The POSCAR atom count must match PROCAR ions, and OUTCAR `ISPIN` must match the parsed PROCAR spin-channel count. VASP 6 fixed-width/concatenated negative k-point coordinates and collinear files with two concatenated spin blocks but no `spin component` headers are supported.

## DOSCAR

Standard DOSCAR total DOS header and NEDOS rows. Common total-DOS layouts:

- E, DOS, integrated DOS;
- E, DOS_up, DOS_down, integrated_up, integrated_down.

Projected DOS blocks are supported when the value-column count maps unambiguously to common collinear VASP layouts:

- 3 orbitals/groups: s, p, d;
- 9 orbital-resolved s/p/d components;
- 16 orbital-resolved s/p/d/f components;
- the corresponding spin-paired forms.

Unsupported/ambiguous PDOS layouts fail explicitly.

## Generic DOS/fatband tables

Whitespace numeric text. Comments beginning with `#`, `!`, or `;` are ignored. Non-numeric header lines are skipped. Rows must have a consistent numeric column count.

## Units and assumptions

- energy: eV;
- reciprocal-path distance from POSCAR+KPOINTS: Å⁻¹;
- DOS: input units, normally states/eV;
- PROCAR/PBAND weight: dimensionless projection weight.
