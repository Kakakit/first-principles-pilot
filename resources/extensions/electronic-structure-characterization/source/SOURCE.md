# Source provenance

Three user-supplied scripts were copied unchanged before refactoring.

| Preserved source | SHA-256 | Role in migration |
|---|---|---|
| `original_vasp_soc_band.py` | `5d5a57537f1ac08a86c7d4878c36993352d3079a438441d55bb22ea7c01e8788` | VASP mixed-mesh EIGENVAL path extraction, reciprocal path axis, occupation-based band edges |
| `original_wannier_band.py` | `1c76043782280a5ea0931d4bbc62750fe939209a3f539228ba524a6f67ce341d` | Wannier90 band/labelinfo parsing and spin-channel plotting |
| `original_dft_wannier_compare.py` | `88cb2f112dc12d66c5de8c2e973e49d1eef9edb1f699d7ceb008c9159dd88da5` | VASPKIT DFT matrix parsing and segment-wise DFT/Wannier overlay concept |

The refactored extension is a new working tree. The preserved source copies are not entrypoints.

The two migration guidance markdown files supplied in the earlier task are copied under `source/guidance/` for provenance and local review.
