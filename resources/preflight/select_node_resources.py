#!/usr/bin/env python3
"""Select deterministic scheduler resources from one node's live preflight facts."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


class ResourceSelectionError(ValueError):
    """A preflight resource fact is missing, ambiguous, or unsafe."""


def _read_json(path):
    try:
        text = sys.stdin.read() if path in (None, "-") else Path(path).read_text(encoding="utf-8")
        value = json.loads(text)
    except (OSError, ValueError) as exc:
        raise ResourceSelectionError("preflight JSON is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise ResourceSelectionError("preflight JSON must contain an object")
    return value


def _integer(value, label):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ResourceSelectionError("{} must be an integer".format(label)) from exc
    if number < 0:
        raise ResourceSelectionError("{} must be non-negative".format(label))
    return number


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ResourceSelectionError("preflight observed_at is required")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ResourceSelectionError("preflight observed_at is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ResourceSelectionError("preflight observed_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _host(data, requested_host):
    hosts = data.get("hosts")
    if not isinstance(hosts, list):
        raise ResourceSelectionError("preflight JSON has no hosts list")
    matches = [
        item for item in hosts
        if isinstance(item, dict)
        and requested_host in {item.get("host"), item.get("alias")}
    ]
    if len(matches) != 1:
        raise ResourceSelectionError(
            "host must match exactly one parsed preflight host: {}".format(requested_host)
        )
    selected = matches[0]
    if selected.get("classification") != "READY":
        raise ResourceSelectionError("selected host is not READY")
    return selected


def _node(host, selected_node):
    nodes = host.get("nodes")
    if not isinstance(nodes, list):
        raise ResourceSelectionError("selected host has no node facts")
    matches = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_name = node.get("NODE") or node.get("node")
        if node_name == selected_node:
            matches.append(node)
    if len(matches) != 1:
        raise ResourceSelectionError(
            "selected node must match exactly one node fact: {}".format(selected_node)
        )
    selected = matches[0]
    available = selected.get("CPUS_FREE")
    if available is None:
        available = selected.get("cpus_free")
    if available is None:
        raise ResourceSelectionError("selected node has no CPUS_FREE fact")
    return selected, _integer(available, "CPUS_FREE")


def select_resources(
    preflight,
    host,
    selected_node,
    scheduler_cap=None,
    scheduler_cap_evidence=None,
    user_override=None,
    user_override_evidence=None,
    max_age_seconds=300,
    now=None,
):
    """Return a request based only on selected-node CPUS_FREE and explicit exceptions."""
    if scheduler_cap is not None and user_override is not None:
        raise ResourceSelectionError("scheduler cap and user override are mutually exclusive")
    selected_host = _host(preflight, host)
    node_facts, available = _node(selected_host, selected_node)
    observed_at = _parse_timestamp(preflight.get("observed_at"))
    current = now or datetime.now(timezone.utc)
    if isinstance(current, str):
        current = _parse_timestamp(current)
    if current.tzinfo is None:
        raise ResourceSelectionError("selector now must include a timezone")
    current = current.astimezone(timezone.utc)
    age_seconds = (current - observed_at).total_seconds()
    if age_seconds < 0 or age_seconds > max_age_seconds:
        raise ResourceSelectionError(
            "preflight JSON is stale or from the future: age_seconds={:.3f} max_age_seconds={}".format(
                age_seconds, max_age_seconds
            )
        )
    if available <= 0:
        raise ResourceSelectionError("selected node CPUS_FREE must be greater than zero")
    node_status = str(node_facts.get("STATUS") or node_facts.get("status") or "").upper()
    if node_status not in {"FULLY_IDLE", "PARTIAL"}:
        raise ResourceSelectionError("selected node STATUS must be FULLY_IDLE or PARTIAL")
    total_value = node_facts.get("CPUS_TOTAL")
    if total_value is None:
        total_value = node_facts.get("cpus_total")
    total = _integer(total_value, "CPUS_TOTAL")
    if total <= 0 or available > total:
        raise ResourceSelectionError("CPUS_FREE must satisfy 0 < CPUS_FREE <= CPUS_TOTAL")

    candidate = available
    exception_type = "none"
    exception_reason = "selected node CPUS_FREE is the requested core count"
    exception_evidence = [
        "preflight host={} node={} CPUS_FREE={}".format(host, selected_node, available)
    ]
    if scheduler_cap is not None:
        candidate = _integer(scheduler_cap, "scheduler/admin cap")
        if candidate <= 0:
            raise ResourceSelectionError("scheduler/admin cap must be greater than zero")
        if candidate > available:
            raise ResourceSelectionError("scheduler/admin cap cannot exceed CPUS_FREE")
        if candidate < available:
            if not str(scheduler_cap_evidence or "").strip():
                raise ResourceSelectionError(
                    "scheduler/admin cap below CPUS_FREE requires evidence"
                )
            exception_type = "scheduler_admin_cap"
            exception_reason = "scheduler or administrator imposed a lower core cap"
            exception_evidence = [str(scheduler_cap_evidence).strip()]
    elif user_override is not None:
        candidate = _integer(user_override, "user override")
        if candidate <= 0:
            raise ResourceSelectionError("user override must be greater than zero")
        if candidate > available:
            raise ResourceSelectionError("user override cannot exceed CPUS_FREE")
        if candidate < available:
            if not str(user_override_evidence or "").strip():
                raise ResourceSelectionError("user override below CPUS_FREE requires evidence")
            exception_type = "user_override"
            exception_reason = "user explicitly bounded the requested core count"
            exception_evidence = [str(user_override_evidence).strip()]

    scheduler = str(selected_host.get("scheduler") or "unknown").lower()
    if scheduler in {"pbs", "pbs/torque", "torque"}:
        request_spec = "nodes={}:ppn={}".format(selected_node, candidate)
    elif scheduler == "slurm":
        request_spec = "--nodelist={} --nodes=1 --ntasks={}".format(selected_node, candidate)
    else:
        request_spec = "node={} cores={}".format(selected_node, candidate)
    return {
        "host": host,
        "scheduler": scheduler,
        "selected_node": selected_node,
        "selected_node_available_cores": available,
        "selected_node_status": node_status,
        "selected_node_total_cores": total,
        "requested_cores": candidate,
        "request_spec": request_spec,
        "resource_policy": "full_available_core",
        "exception_type": exception_type,
        "exception_reason": exception_reason,
        "exception_evidence": exception_evidence,
        "node_evidence": {
            "source": "parse_preflight hosts[].nodes[]",
            "host": host,
            "selected_node": selected_node,
            "CPUS_FREE": available,
            "CPUS_TOTAL": total,
            "STATUS": node_status,
            "raw_node_facts": node_facts,
        },
        "preflight_observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "preflight_age_seconds": age_seconds,
        "preflight_max_age_seconds": max_age_seconds,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Select all currently free cores on one selected node unless an evidenced exception applies."
    )
    parser.add_argument("--preflight-json", required=True, help="parse_preflight JSON file or - for stdin")
    parser.add_argument("--host", required=True)
    parser.add_argument("--selected-node", required=True)
    parser.add_argument("--scheduler-cap", type=int)
    parser.add_argument("--scheduler-cap-evidence")
    parser.add_argument("--user-override", type=int)
    parser.add_argument("--user-override-evidence")
    parser.add_argument("--max-age-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        result = select_resources(
            _read_json(args.preflight_json),
            args.host,
            args.selected_node,
            scheduler_cap=args.scheduler_cap,
            scheduler_cap_evidence=args.scheduler_cap_evidence,
            user_override=args.user_override,
            user_override_evidence=args.user_override_evidence,
            max_age_seconds=args.max_age_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ResourceSelectionError as exc:
        print("select_node_resources: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
