# VASP to Wannier90 interface

Source IDs: `vasp-wiki-wannier-interface`.

VASP can write the Wannier90 hand-off files (`wannier90.win`, `.mmn`, `.eig`,
and `.amn`). When the win file is absent, VASP supplies the structural,
mesh, and k-point blocks; when it exists, VASP fills missing blocks while
leaving existing choices for the user to audit. `WANNIER90_WIN` is therefore
an augmentation/check point, not a reason to rebuild the complete file.

Scope: use this card for the VASP-to-Wannier boundary, generated-file
expectations, and the `num_wann`/band-count hand-off.

Caveat: this is an authored summary. The VASP program, binary, POTCAR data,
and copied upstream page text are outside the plugin and remain link-only.
Confirm version-specific behavior against the pinned Wiki revisions before a
production run.
