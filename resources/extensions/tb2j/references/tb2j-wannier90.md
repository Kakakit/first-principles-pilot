# TB2J Wannier90 interface card

Use the public `wann2J.py` command supplied by TB2J. The plugin does not bundle
TB2J or reimplement its numerical methods.

- Spinor input selects one Wannier prefix and passes `--spinor`; `groupby` is
  `spin` or `orbital`.
- Collinear input selects separate up/down prefixes.
- Both modes need a structure, a finite Fermi energy, a three-value k mesh, and
  magnetic elements or 1-based magnetic atom indices.
- A prefix is usable through `<prefix>_tb.dat`, or through
  `<prefix>_hr.dat` together with `<prefix>_centres.xyz`.
- `.amn`, `.mmn`, and `.eig` are not inputs to `wann2J.py`.
- `exchange.out` is the stable human-readable result checked here. Other files
  vary with TB2J version and selected options.

Sources:

- Official project: <https://github.com/mailhexu/TB2J>
- Local inspected command definition: `TB2J-main/TB2J/scripts/wann2J.py`
- Local inspected output guide: `TB2J-main/docs/src/output.rst`
