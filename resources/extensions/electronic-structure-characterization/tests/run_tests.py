#!/usr/bin/env python3
"""Dependency-free runner for the extension's function-style regression tests."""

from __future__ import annotations

import importlib.util
import inspect
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    passed = 0
    for test_file in sorted(root.glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(test_file.stem, test_file)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot import {test_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            parameters = list(inspect.signature(function).parameters)
            if not parameters:
                function()
            elif parameters == ["tmp_path"]:
                with tempfile.TemporaryDirectory(prefix="banddos-test-") as directory:
                    function(Path(directory))
            else:
                raise RuntimeError(
                    f"Unsupported fixture signature: {test_file.name}::{name}"
                )
            passed += 1
            print(f"PASS {test_file.name}::{name}")
    print(f"PASS: {passed} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
