#!/usr/bin/env python3
"""Minimal command skeleton. Copy this extension and replace this adapter."""

import argparse
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replace with the external-project adapter")
    parser.add_argument("command", choices=("doctor", "check", "run"))
    parser.add_argument("--request")
    args = parser.parse_args(argv)
    result = {
        "status": "SCAFFOLD",
        "message": "Copy _template and replace scripts/extension.py before use",
        "command": args.command,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1


if __name__ == "__main__":
    sys.exit(main())
