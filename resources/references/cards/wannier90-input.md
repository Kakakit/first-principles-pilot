# Wannier90 input tags

Source IDs: `wannier90-user-guide`.

Use the official user-guide structure as the vocabulary source for
`wannier90.win`: projections and `num_wann` define the target manifold;
disentanglement and frozen windows constrain the selected bands; `mp_grid`,
`kpoint_path`, and interpolation/post-processing settings describe the mesh
and requested analysis. Treat tags as a coupled input: a window or path is
not meaningful without checking the band count, projections, and generated
VASP hand-off blocks.

Scope: route here for tag meaning, minimal win edits, localization review,
and VASP-to-Wannier continuation. Read only the tag families needed by the
current workflow.

Caveat: this card does not reproduce the Wannier90 manual or promise that a
tag is supported by every release. The pinned `develop` guide and stable
v4.0.1 commit are the version boundary.
