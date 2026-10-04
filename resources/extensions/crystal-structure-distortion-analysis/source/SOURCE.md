# Migration source record

- Source artifact: user-supplied Python script copied as `source/original_polyhedral_distortion.py`.
- Original SHA-256: `8156f1f8921ed7f9f2184b5ed9f35e012d2a79aadc383c26974961ce6b0c4845`.
- Original purpose: calculate local FeO4 and CrO6 distortion metrics from a VASP POSCAR.
- Migration policy: the copied source is kept unchanged; the reusable implementation lives at `src/crystal_distortion.py`.

The source copy is provenance only and is not the extension entrypoint.

The source copy is provenance only and is not the extension entrypoint. The
bundle's `source/guidance/` files were intentionally not copied into the host
runtime tree; they are neither runtime dependencies nor host registry data.
