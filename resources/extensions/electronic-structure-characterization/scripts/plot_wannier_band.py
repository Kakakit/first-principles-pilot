#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from banddos_core.cli import main
if __name__ == "__main__":
    raise SystemExit(main(["wannier-band", *sys.argv[1:]]))
