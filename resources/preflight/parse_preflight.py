#!/usr/bin/env python3
"""Parse simple or multi-host cluster preflight checker output."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


KEY_VALUE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
INLINE_PAIR_RE = re.compile(r"([A-Z][A-Z0-9_]*)=([^\s]+)")
HOST_HEADER_RE = re.compile(r"^\s*=+\s+(.+?)\s+=+\s*$")
TRANSPORT_FAILURES = {
    "SSH_CONNECTION_FAILED", "SSH_TIMEOUT", "CONNECT_TIMEOUT", "CONNECTION_TIMEOUT",
    "NO_ROUTE", "DNS_FAILURE", "SSH_AUTH_FAILED", "AUTH_FAILED", "UNAVAILABLE", "OFFLINE",
    "UNREACHABLE_OR_AUTH_FAILED",
}


def read_text(path=None):
    if path and path != "-":
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def parse_fields(text):
    fields = {}
    for line in text.splitlines():
        match = KEY_VALUE_RE.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[match.group(1)] = value
    return fields


def _parse_inline_pairs(line):
    return {key: value for key, value in INLINE_PAIR_RE.findall(line)}


def _split_host_blocks(text):
    lines = text.splitlines()
    headers = [(index, match.group(1).strip()) for index, line in enumerate(lines)
               if (match := HOST_HEADER_RE.match(line))]
    if not headers:
        return [(None, text)]
    blocks = []
    for position, (start, host) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        blocks.append((host, "\n".join(lines[start + 1:end])))
    return blocks


def _host_classification(fields):
    status = fields.get("STATUS", "").strip().upper()
    scheduler = fields.get("SCHEDULER", "").strip().upper()
    if status in TRANSPORT_FAILURES or status.startswith("SSH_"):
        return "BLOCKED_NETWORK", "UNAVAILABLE", False, "transport"
    if status in {"OK", "READY", "AVAILABLE", "SUCCESS"}:
        return "READY", "AVAILABLE", False, None
    if re.search(r"(?:SCHEDULER|PBS|SLURM).*(?:ERROR|FAIL|DOWN|UNAVAILABLE)", status) or re.search(
        r"(?:ERROR|FAIL|DOWN|UNAVAILABLE)", scheduler
    ):
        return "SCHEDULER_ERROR", "UNKNOWN", True, "scheduler"
    if scheduler and scheduler not in {"NOT DETECTED", "NONE", "UNKNOWN"}:
        return "READY", "AVAILABLE", False, None
    return "UNKNOWN", "UNKNOWN", False, None


def _parse_host(host_name, block_text):
    fields = parse_fields(block_text)
    nodes = []
    summaries = []
    for line in block_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("NODE="):
            nodes.append(_parse_inline_pairs(stripped))
        elif stripped.startswith("SUMMARY"):
            summaries.append({
                "text": stripped,
                "fields": _parse_inline_pairs(stripped[len("SUMMARY"):].strip()),
            })
    classification, availability, scheduler_error, error_class = _host_classification(fields)
    host = host_name or fields.get("HOST")
    result = {
        "host": host,
        "alias": host,
        "fields": fields,
        "scheduler": fields.get("SCHEDULER"),
        "status": fields.get("STATUS"),
        "nodes": nodes,
        "summaries": summaries,
        "summary": summaries[0] if len(summaries) == 1 else None,
        "availability": availability,
        "scheduler_error": scheduler_error,
        "error_class": error_class,
        "classification": classification,
    }
    return result


def _overall_classification(hosts):
    classifications = [host["classification"] for host in hosts]
    if classifications and all(item == "BLOCKED_NETWORK" for item in classifications):
        return "BLOCKED_NETWORK"
    if "SCHEDULER_ERROR" in classifications:
        return "SCHEDULER_ERROR"
    if "BLOCKED_NETWORK" in classifications and any(item == "READY" for item in classifications):
        return "PARTIAL"
    if classifications and all(item == "READY" for item in classifications):
        return "READY"
    return "UNKNOWN"


def _timestamp(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        parsed = value[:-1] + "+00:00" if value.endswith("Z") else value
        value = datetime.fromisoformat(parsed)
    if value.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_preflight(text, observed_at=None):
    blocks = _split_host_blocks(text)
    hosts = [_parse_host(name, block) for name, block in blocks]
    result = {
        "hosts": hosts,
        "classification": _overall_classification(hosts),
        "scheduler_error": any(host["scheduler_error"] for host in hosts),
        "observed_at": _timestamp(observed_at),
    }
    # Preserve the original simple HOST=... compatibility surface.
    if len(blocks) == 1 and blocks[0][0] is None:
        fields = hosts[0]["fields"]
        result.update(fields)
        result["fields"] = fields
        result["availability"] = hosts[0]["availability"]
        result["error_class"] = hosts[0]["error_class"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Parse simple or ===== host ===== preflight output.")
    parser.add_argument("input", nargs="?", help="checker output file, or -/omitted for stdin")
    parser.add_argument("--file", dest="file_option", help="checker output file")
    parser.add_argument("--observed-at", help="UTC ISO-8601 observation time; defaults to now")
    args = parser.parse_args(argv)
    if args.input and args.file_option:
        parser.error("provide input file once")
    try:
        print(json.dumps(parse_preflight(read_text(args.file_option or args.input), args.observed_at), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print("parse_preflight: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
