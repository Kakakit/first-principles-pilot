#!/usr/bin/env python3
import pathlib
import sys


if "--version" in sys.argv:
    print("pyw90 1.0.7")
    raise SystemExit(0)
if "--help" in sys.argv and len(sys.argv) == 2:
    print("pyw90 eig|pre|cmp")
    raise SystemExit(0)

command = next((item for item in sys.argv[1:] if item in {"eig", "pre", "cmp"}), None)
if command is None:
    print("missing command", file=sys.stderr)
    raise SystemExit(2)
pathlib.Path("eig-result.txt").write_text(command + " ok\n", encoding="utf-8")
print("fake pyw90 {} complete".format(command))
