# VASPKIT input and inspection recipes

Source IDs: `vaspkit-official-docs`, `vaspkit-nature-protocols-examples`.

VASPKIT is the preferred user-facing route for standard structure conversion,
POTCAR/KPOINTS preparation, and routine inspection where its documented
workflow applies. Its official command-line interface supports task selection
and batch/stdin use, but it is not a versioned Python or machine-readable API.
The pilot therefore records the exact binary banner, command and input, preserves
the generated files, and checks required output files and hashes afterwards.
A zero return code without the expected file is a failed adapter operation.

POTCAR-related tasks depend on the user's local `~/.vaspkit` configuration and
licensed potential roots. A doctor may report their presence and readability,
but must not create the configuration, install VASPKIT, choose a potential
variant, or download/redistribute POTCAR data. Missing VASPKIT capability is a
visible result; an internal renderer is used only when the caller explicitly
selects it, never as an unreported fallback.

For VASP-to-Wannier, retain the VASP-generated `wannier90.win` and make only the
requested tag additions.

Scope: route here for a concise recipe or a reproducible VASPKIT menu path,
not for undocumented physical settings.

Caveat: VASPKIT prompts and configuration can vary by installed version, and the
official tutorials state that examples are guidance rather than a guarantee of
scientific accuracy. The official documentation has
academic/scientific/educational noncommercial terms, while the Nature Protocols
examples are MIT. The plugin links to both and includes only this original
summary; it does not bundle the VASPKIT program or large example trees.
