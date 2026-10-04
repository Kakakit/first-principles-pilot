# Reference cards

These are short, authored routing cards for VASP inputs, VASPKIT, Wannier90,
and workflow organization. They are an index, not a replacement for the
upstream manuals.

## Human lookup

For a quick manual check, start with `cards/vasp-input-contracts.md` for the four
VASP inputs and magnetic/SOC consistency, `cards/vasp-wannier-interface.md` for
the VASP hand-off, `cards/wannier90-input.md` for `.win` tags,
`cards/vaspkit-recipes.md` for standard preparation/inspection recipes, and
`cards/atomate2-architecture.md` for Job/Flow organization. The pinned upstream
URLs and revision/license boundaries are in `registry.json`.

`wannier90-tags.json` is the small data-only name index used by the WP5 `.win`
checker. It records scalar versus block names and the exact official
Wannier90 documentation revision, without copying tag descriptions or the
manual into runtime context.

## Agent lookup

Use the structured index through the local script:

```text
python3 scripts/fpilot_references.py route --tag num-wann --workflow vasp-to-wannier --include-content
python3 scripts/fpilot_references.py validate
python3 scripts/fpilot_references.py refresh-check
```

Routing is deterministic, uses no network by default, and reads card content
only after a card is selected with `--include-content`. `--include-content`
uses the detail/full-content estimate (not the summary estimate); the selected
content and token budget are present in the JSON output. Match precedence is
exact error code, tag, workflow, topic, then artifact. A conflict or budget
truncation is reported explicitly.

## Refresh and license boundary

`refresh-check` verifies local card hashes offline and reports remote sources as
`OFFLINE_UNCHECKED`. With explicit network access it compares each
`refresh.checks` response to its pinned GitHub commit or MediaWiki oldid and
reports `NOT_MODIFIED`, `CHANGED`, or `BROKEN_LINK`; it never changes the pin.
`route` never performs network access. The registry records upstream commits or
page revisions so a later refresh is reviewable.

Only the five original summaries are bundled. VASP code/binaries/POTCAR and
large upstream manuals/examples are not bundled. VASP Wiki material is linked
under its documented GFDL terms; VASPKIT documentation is link-only under its
academic/scientific/educational noncommercial terms; MIT/LGPL/modified-BSD
sources are represented by summaries and links. Re-check upstream terms before
redistributing a plugin outside the current private server group.
