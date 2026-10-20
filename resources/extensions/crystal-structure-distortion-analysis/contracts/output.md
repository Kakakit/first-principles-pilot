# Output contract

For output prefix `polyhedral_distortion` (or any caller-selected prefix), the
extension writes three local files. Ordinary runs may overwrite existing
same-name local files; they never write to remote paths or submit jobs.

## `polyhedral_distortion.csv`

One row per analyzed center. Stable scalar columns include site/species/coordination, bond-length statistics, Δd, Baur D, polyhedron volume, quadratic elongation λ, angular metrics, shell gap, and octahedral Q2/Q3/QJT fields where applicable. Missing non-applicable octahedral fields are blank.

## `polyhedral_distortion.json`

Schema version `1.0`. Contains:

- `analysis`, `tool_version`;
- `provenance`: absolute input path, SHA-256, config path and resolved config;
- `structure`: comment, formula string, site count, lattice in Å and volume in Å³;
- `warnings`;
- `site_results`: complete neighbor identities/images and all metrics;
- `summary`: mean/population-std/min/max grouped by polyhedron type.

## `polyhedral_distortion.md`

Human-readable summary and per-site table, warnings, metric definitions, and interpretation boundaries.

## Units

- distance: Å;
- polyhedron/cell volume: Å³;
- angle: degree;
- angle variance: degree²;
- Δd, Baur D, coefficient of variation and λ: dimensionless;
- Q2/Q3/QJT: Å.

## Failure markers

The program exits with status `2` and prints `ERROR: <message>` to stderr for expected parse/configuration/geometry failures. It does not create remote files, submit jobs, or write outside the caller-selected local output prefix.

Dry-run prints the resolved parsing/configuration payload to stdout and does
not create or overwrite any output file.

## Additive v2 2.1.0 geometry

The existing schema_version=1.0 and scalar/CSV definitions stay unchanged. detail_schema=fpilot.polyhedra-geometry.v1 adds per-site Cartesian coordinates/vectors (Å), convex-hull faces, all internal angles and site warnings. Global atom numbers remain 1-based; ligand_local_indices and faces index the selected ligand arrays from 0.

bridges uses only shared selected ligand incidences. The first center is in its original cell; center_j_image and ligand_image are integer lattice translations. Different ligand paths stay distinct; reverse/translation duplicates collapse. Identical source center numbers in different images are valid endpoints. Each path records two lengths, center distance, coordinates and angle, with no magnetic inference.

New bonds.csv, angles.csv and bridges.csv have Chinese labels and units. Original distortion.csv stays compatible. Offline presentation excludes private provenance paths, shards geometry under the existing asset limit and never invokes analysis. Legacy files missing coordinates fail explicitly rather than borrowing a current draft.
