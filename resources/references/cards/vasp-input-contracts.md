# VASP input contracts

Source ID: `vasp-wiki-input-contracts`.

A VASP calculation is defined by `INCAR`, `POSCAR`, `KPOINTS`, and `POTCAR`.
Treat these as one matched input set: preserve exact files and hashes, validate
the structure and species order, and audit the final set before submission.

For a regular mesh, an explicit `KPOINTS` file is the effective source. VASP uses
`KSPACING` to generate a mesh only when `KPOINTS` is absent. Keeping both is
therefore redundant and can conceal which choice was intended; report the
effective source, and let a strict workflow reject the redundant configuration.
Do not describe coexistence itself as an unconditional VASP error.

`POTCAR` sections must follow the element order in `POSCAR`. Record the local
source, hash, `TITEL` sequence, XC metadata, and available `ENMAX` values. Do not
mix exchange-correlation families, invent a pseudopotential variant, download a
private potential automatically, or redistribute POTCAR contents.

Magnetism is an explicit model choice. A collinear `ISPIN=2` setup uses one
scalar `MAGMOM` value per ion. A noncollinear or SOC setup uses three components
per ion and the `vasp_ncl` executable. Noncollinearity is selected by
`LNONCOLLINEAR` or `LSORBIT`; do not combine that setup with explicit `ISPIN=2`.
VASP 6.5.0 can stop on `ISPIN=2` plus noncollinear `MAGMOM`, while earlier
versions internally treated noncollinear calculations as `ISPIN=1`. Vector
components are interpreted with the chosen `SAXIS`. Validate counts and the
executable boundary; never invent initial moments.

Scope: core file consistency and high-impact magnetic/SOC input contracts. This
card does not choose material-specific `ENCUT`, U/J, magnetic order, smearing,
k-point density, or convergence thresholds.

Caveat: these are concise plugin-authored rules linked to pinned VASP Wiki page
revisions. VASP code, binaries, and POTCAR data are not included.
