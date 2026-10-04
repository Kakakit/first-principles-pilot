# Extension scaffold

This directory is a deliberately small, copyable scaffold. Copy it to a
lowercase extension name, inspect the upstream project, replace the metadata
and adapter, add the smallest fixture/test, then run the extension validator.
It is intentionally absent from `registry.json` and is not a usable scientific
extension until those replacements are complete.

For the workbench runtime, replace `extension.sdk.json` as well: set the copied
directory name as `id`, declare actual selectors, dependencies, JSON output and
fixture, then call `backend.extension_sdk.validate(id)`. Install and enable are
separate actions. The scaffold ID and placeholder fixture are intentionally
invalid as a scientific extension. See [`../../../docs/EXTENSION_SDK.md`](../../../docs/EXTENSION_SDK.md)
for the command and result contract.

The seven files/areas to keep are: `extension.json`, `README.md`,
`references/`, `examples/`, `scripts/`, `tests/`, and `tests/fixtures/`.
`scripts/extension.py` is only a visible placeholder so a future AI can see
the expected `doctor|check|run` shape; replace it before registration.

When the copy is a modular command, add these four fields together. They are
optional for legacy metadata-only extensions:

```json
{
  "module_type": "postprocessor",
  "requires": ["wannier90.hr"],
  "provides": ["analysis.result"],
  "command": {
    "path": "scripts/run.py",
    "usage": "python scripts/run.py --request request.json"
  }
}
```

`requires` and `provides` name artifact roles, so a module does not depend on a
particular upstream extension. The command path must be an existing file below
the copied extension directory. A relative `source_provenance.source_path` is
resolved from that directory, which keeps copied extensions portable.

For the complete, single-source checklist, read
[`../CREATE_FROM_GITHUB.md`](../CREATE_FROM_GITHUB.md). The working PyW90
example in [`../pyw90/`](../pyw90/) shows the smallest useful external-command
adapter.
