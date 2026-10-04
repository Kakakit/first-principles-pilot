# atomate2 architecture vocabulary

Source IDs: `atomate2-architecture`.

atomate2 is used here as a naming and organization reference: a `Job` is an
execution unit, a `Flow` groups dependent jobs, and maker/input-set objects
assemble repeatable work. The pilot keeps its own immutable attempts and
ledger contracts; adopting these terms does not add atomate2 as a runtime
dependency or replace the fpilot scheduler/audit gates.

Scope: route here for readable flow/attempt organization and dependency
language when designing or explaining a workflow.

Caveat: this is an architecture-only summary pinned to atomate2 main and
stable v0.1.5 metadata. It is not authority for VASP, Wannier90, or TB2J
physics and does not copy atomate2 source or documentation wholesale.
