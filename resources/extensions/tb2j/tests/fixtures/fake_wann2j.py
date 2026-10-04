#!/usr/bin/env python3
import argparse
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--output_path", default="TB2J_results")
parser.add_argument("--version", action="store_true")
args, _ = parser.parse_known_args()
if args.version:
    print("TB2J 0.0-fake")
else:
    output = Path(args.output_path)
    output.mkdir(parents=True, exist_ok=True)
    (output / "exchange.out").write_text("fake exchange output\n", encoding="utf-8")
    print("fake wann2J completed")
