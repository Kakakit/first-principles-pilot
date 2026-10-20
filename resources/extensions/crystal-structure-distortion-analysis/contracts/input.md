# Input contract

## Required file

- One VASP5/6 `POSCAR`/`CONTCAR`-style structure file.
- Element symbols must be present on the species line.
- `Direct` and `Cartesian` coordinates are supported; `Selective dynamics` is accepted and ignored for geometry analysis.
- A single positive scale factor and VASP's single negative target-volume scale are supported.
- VASP4 files without element symbols and three-component/per-species scale factors are rejected.

## Configuration

JSON object:

```json
{
  "centers": {
    "Cr": {"coordination": 6, "ligand": "O", "geometry": "octahedron"},
    "Fe": {"coordination": 4, "ligand": "O", "geometry": "tetrahedron"}
  },
  "search_radius_angstrom": 3.0,
  "shell_gap_warning_angstrom": 0.15
}
```

Only coordination 4/tetrahedron and 6/octahedron are implemented. Distances are in Å and angles in degrees.

## Source assumptions

1. The configured coordination number is a scientific assumption supplied by the caller. The tool does not infer oxidation state, bond valence, or coordination chemistry.
2. Neighbors are periodic ligand images within `search_radius_angstrom`, sorted by center-ligand distance; the nearest `coordination` entries are selected.
3. If an `(n+1)`th ligand is also inside the search radius, the `n -> n+1` distance gap is recorded. Gaps below `shell_gap_warning_angstrom` produce a warning, or an error with `--fail-on-ambiguous-shell`.
4. Site numbering is 1-based and follows POSCAR atom order.
5. The structure must contain every configured center species to be analyzed; absent configured species are simply reported as zero sites in dry-run, while a file containing no configured centers fails full analysis.

## v2 2.1 shorthand

options.polyhedra accepts CrO6, FeO4, separated by ASCII/Chinese commas, semicolons or spaces. Each label has one center, one ligand and coordination 4 or 6; conflicting coordination for one center fails. search_radius_angstrom defaults to 3. No oxidation state or chemical coordination is inferred. Legacy center_element/ligand_element/coordination remains compatible; conflicting old/new declarations fail. Preferred execution is local with actual NumPy/SciPy imports; no automatic environment changes.
