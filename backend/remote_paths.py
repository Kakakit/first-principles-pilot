"""Safe, stable paths for remote fpilot attempts.

The web workbench keeps the scheduler-specific script in ``execution.py`` but
centralises the path policy here.  A path received from a user or an imported
template is never interpolated into a remote command until it has passed the
workspace and component checks in this module.

The remote workspace is deliberately represented as a shell expression.  The
usual value is ``$HOME/230269265lht``; an absolute path is also accepted when
it contains the registered workspace component.  We keep both the expression
and the absolute path returned by the read-only remote probe in an attempt
record so the GUI can show exactly where a task was staged.
"""

import posixpath
import re
import shlex
from typing import Any, Dict, Mapping, Optional


WORKSPACE_NAME = "230269265lht"
WORKSPACE_NAMES = (WORKSPACE_NAME, "fpilot")
DEFAULT_WORKSPACE_ROOT = "$HOME/" + WORKSPACE_NAME
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


# Public name used by the server configuration validator.  Keeping this
# alias short makes it convenient for imported templates to validate a root
# without depending on the internal helper name.
def validate_root(value: Optional[str] = None) -> str:
    return normalize_workspace_root(value)


def _segments(value: str):
    return [part for part in value.split("/") if part not in ("", ".")]


def _valid_component(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _COMPONENT.fullmatch(value):
        raise ValueError("{} 含有不安全的路径字符".format(label))
    if value in (".", ".."):
        raise ValueError("{} 不能是相对目录".format(label))
    return value


def normalize_workspace_root(value: Optional[str] = None) -> str:
    """Return a canonical, shell-safe workspace-root expression.

    ``$HOME/230269265lht`` and descendants are the normal form.  Explicit
    absolute paths are useful for a cluster with a mounted project area, but
    they must still contain a path component named ``230269265lht``.  Parent
    traversal is rejected before normalisation so a string such as
    ``/tmp/230269265lht/../other`` cannot escape the workspace policy.
    """

    root = DEFAULT_WORKSPACE_ROOT if value is None or value == "" else value
    if not isinstance(root, str) or not root or any(ch in root for ch in "\x00\r\n"):
        raise ValueError("remote_workspace_root 必须是安全的单行路径")

    if root == "$HOME" or root.startswith("$HOME/"):
        suffix = root[len("$HOME") :]
        if not suffix.startswith("/") or any(part == ".." for part in suffix.split("/")):
            raise ValueError("remote_workspace_root 必须位于 $HOME/230269265lht 下")
        parts = _segments(suffix)
        if not parts or parts[0] not in WORKSPACE_NAMES:
            raise ValueError("remote_workspace_root 必须位于 $HOME/fpilot 或原登记工作区下")
        for part in parts:
            _valid_component(part, "remote_workspace_root")
        return "$HOME/" + "/".join(parts)

    if not root.startswith("/"):
        raise ValueError("remote_workspace_root 只能使用 $HOME/230269265lht 或绝对路径")
    if any(part == ".." for part in root.split("/")):
        raise ValueError("remote_workspace_root 不允许 .. 路径穿越")
    normal = posixpath.normpath(root)
    parts = _segments(normal)
    if not any(name in parts for name in WORKSPACE_NAMES):
        raise ValueError("绝对 remote_workspace_root 必须包含 fpilot 或原登记工作区组件")
    for part in parts:
        _valid_component(part, "remote_workspace_root")
    return normal


def normalize_relative_path(value: str) -> str:
    """Validate a path relative to a previously validated remote root."""

    if not isinstance(value, str) or not value or any(ch in value for ch in "\x00\r\n"):
        raise ValueError("远端相对路径无效")
    if value.startswith("/") or any(part == ".." for part in value.split("/")):
        raise ValueError("远端相对路径不允许绝对路径或 ..")
    parts = _segments(value)
    if not parts:
        raise ValueError("远端相对路径不能为空")
    for part in parts:
        _valid_component(part, "远端路径组件")
    return "/".join(parts)


def readable_slug(value: Any, label: str, maximum: int = 64) -> str:
    if not isinstance(value, str) or len(value) > maximum or not _SLUG.fullmatch(value):
        raise ValueError("{} 只允许小写字母、数字和短横线".format(label))
    return value


def readable_identity(manifest: Mapping[str, Any], project: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve user-facing path fields without replacing immutable IDs."""
    if not isinstance(project, Mapping):
        raise ValueError("新路径需要冻结的 project.json")
    identity = frozen_identity(manifest, project)
    project_slug = project.get("remoteSlug")
    if not isinstance(project_slug, str) or not project_slug:
        name = str(project.get("name", "")).lower()
        project_slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:64]
    project_slug = readable_slug(project_slug, "remoteSlug")
    nodes = project.get("nodes") or []
    node = next((item for item in nodes if isinstance(item, Mapping) and item.get("id") == identity["node_id"]), None)
    if not node or not isinstance(node.get("data"), Mapping):
        raise ValueError("冻结项目缺少当前节点路径信息")
    data = node["data"]
    if data.get("executionMode") != "vasp" or data.get("kind") not in ("relax", "scf", "bands", "dos", "vasp-wannier"):
        raise ValueError("此节点不能准备或提交 VASP 任务")
    sequence = data.get("stageSequence", nodes.index(node) + 1)
    if not isinstance(sequence, int) or not 1 <= sequence <= 999:
        raise ValueError("stageSequence 必须是 1–999 的整数")
    stage_slug = readable_slug(data.get("stageSlug") or manifest.get("stage"), "stageSlug", 48)
    return {**identity, "project_slug": project_slug, "flow_slug": "main",
            "stage_sequence": sequence, "stage_slug": stage_slug,
            "stage_segment": "{:02d}-{}".format(sequence, stage_slug)}


def attempt_relative_path(project_slug: str, stage_sequence: int, stage_slug: str,
                             attempt_segment: str) -> str:
    project = readable_slug(project_slug, "remoteSlug")
    stage = "{:02d}-{}".format(stage_sequence, readable_slug(stage_slug, "stageSlug", 48))
    attempt = _valid_component(attempt_segment, "attempt_segment")
    return "material-projects/{}/flows/main/stages/{}/attempts/{}".format(project, stage, attempt)


def join_workspace(root: str, relative: str) -> str:
    """Join a validated root expression and a validated relative path."""

    normalized_root = normalize_workspace_root(root)
    relative = normalize_relative_path(relative)
    return normalized_root.rstrip("/") + "/" + relative


def absolute_attempt_path(root_absolute: str, relative: str) -> str:
    """Join an absolute probe result and a relative attempt path."""

    if not isinstance(root_absolute, str) or not root_absolute.startswith("/"):
        raise ValueError("远端工作区绝对路径无效")
    if any(part == ".." for part in root_absolute.split("/")):
        raise ValueError("远端工作区绝对路径不允许 ..")
    normalized_root = posixpath.normpath(root_absolute)
    if not any(name in _segments(normalized_root) for name in WORKSPACE_NAMES):
        raise ValueError("远端工作区绝对路径缺少 fpilot 或原登记工作区组件")
    return normalized_root.rstrip("/") + "/" + normalize_relative_path(relative)


def shell_path(path: str) -> str:
    """Quote a remote path while retaining the intended ``$HOME`` expansion."""

    normalized = normalize_workspace_root(path) if path == "$HOME" or path.startswith("$HOME/") else path
    if normalized == "$HOME":
        return '"$HOME"'
    if normalized.startswith("$HOME/"):
        return '"$HOME"/' + shlex.quote(normalized[len("$HOME/") :])
    if not normalized.startswith("/") or any(ch in normalized for ch in "\x00\r\n"):
        raise ValueError("远端路径不是安全的绝对路径")
    return shlex.quote(normalized)


def workspace_probe_command(root: str) -> str:
    """Create a read-only command that resolves and snapshots ``root``."""

    root_expr = shell_path(normalize_workspace_root(root))
    # Only directory metadata is returned.  This is bounded even when the
    # workspace contains years of calculation outputs.
    return """set -eu
root={root}
ancestor=$root
suffix=''
while ! test -d "$ancestor"; do
  test ! -e "$ancestor"
  test ! -L "$ancestor"
  suffix=/$(basename "$ancestor")$suffix
  ancestor=$(dirname "$ancestor")
done
test -w "$ancestor"
parent_abs=$(cd "$ancestor" && pwd -P)
root_abs=$parent_abs$suffix
case "/$root_abs/" in
  */{workspace}/*|*/fpilot/*) ;;
  *) exit 42 ;;
esac
printf 'FPILOT_ROOT_ABS=%s\\n' "$root_abs"
printf 'FPILOT_ROOT_PARENT=%s\\n' "$parent_abs"
meta=$(stat -c '%d:%i:%Y:%s' "$ancestor" 2>/dev/null || stat -f '%d:%i:%m:%z' "$ancestor" 2>/dev/null || printf 'unavailable')
printf 'FPILOT_ROOT_SNAPSHOT=%s\\n' "$meta"
""".format(root=root_expr, workspace=WORKSPACE_NAME)


def parse_workspace_probe(receipt: Mapping[str, Any], root: str) -> Dict[str, Any]:
    """Parse the bounded output of :func:`workspace_probe_command`."""

    if not isinstance(receipt, Mapping) or receipt.get("exit_code") != 0:
        detail = ""
        if isinstance(receipt, Mapping):
            detail = str(receipt.get("stderr", ""))[-500:]
        raise ValueError("远端工作区只读预检失败{}".format(": " + detail if detail else ""))
    stdout = str(receipt.get("stdout", ""))
    absolute_match = re.search(r"^FPILOT_ROOT_ABS=(/[^\n]*)$", stdout, re.M)
    snapshot_match = re.search(r"^FPILOT_ROOT_SNAPSHOT=([^\n]*)$", stdout, re.M)
    if not absolute_match or not snapshot_match:
        raise ValueError("远端工作区预检缺少绝对路径或 snapshot")
    root_expression = normalize_workspace_root(root)
    absolute = absolute_match.group(1).strip()
    # This also rejects symlinks resolving outside the registered workspace.
    absolute = posixpath.normpath(absolute)
    if not any(name in _segments(absolute) for name in WORKSPACE_NAMES):
        raise ValueError("远端工作区解析结果缺少 fpilot 或原登记工作区组件")
    return {
        "expression": root_expression,
        "absolute_path": absolute,
        "metadata": snapshot_match.group(1).strip(),
        "existing_parent": (re.search(r"^FPILOT_ROOT_PARENT=(/[^\n]*)$", stdout, re.M).group(1)
                            if re.search(r"^FPILOT_ROOT_PARENT=(/[^\n]*)$", stdout, re.M) else absolute),
        "probe_exit_code": receipt.get("exit_code"),
    }


def workspace_dispatch_guard(root, expected):
    """Resolve before creating an approved workspace; bind to the prepared path."""
    return (workspace_probe_command(root) + 'test "$root_abs" = ' + shlex.quote(expected) + '\n'
            + 'mkdir -p "$root"\ntest "$(cd "$root" && pwd -P)" = ' + shlex.quote(expected) + '\n')


def frozen_identity(manifest: Mapping[str, Any], project: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    """Extract stable project/flow/node IDs from a frozen input.

    Current ``inputs.freeze`` stores the complete project in ``project.json``
    and the node ID in the manifest.  Newer producers may duplicate
    ``project_id`` or ``flow_id`` in the manifest; when present these fields
    are checked against the embedded project rather than trusted blindly.
    """

    if not isinstance(manifest, Mapping):
        raise ValueError("冻结 manifest 无效")
    node_id = manifest.get("node_id")
    project_id = manifest.get("project_id")
    flow_id = manifest.get("flow_id")
    if isinstance(project, Mapping):
        embedded_project_id = project.get("id")
        if project_id is not None and project_id != embedded_project_id:
            raise ValueError("冻结 manifest 与 project.json 的 project_id 不一致")
        project_id = embedded_project_id
        embedded_flow = project.get("flow_id")
        if flow_id is not None and embedded_flow is not None and flow_id != embedded_flow:
            raise ValueError("冻结 manifest 与 project.json 的 flow_id 不一致")
        flow_id = flow_id or embedded_flow
        nodes = project.get("nodes")
        if isinstance(nodes, list) and not any(isinstance(item, Mapping) and item.get("id") == node_id for item in nodes):
            raise ValueError("冻结 manifest 的 node_id 不在 project.json 中")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("冻结输入缺少稳定 project_id")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError("冻结输入缺少稳定 node_id")
    # The editor has no separate flow identity yet.  ``single`` is explicit,
    # deterministic, and remains compatible with the current workspace layout.
    flow_id = flow_id or "single"
    return {
        "project_id": _valid_component(project_id, "project_id"),
        "flow_id": _valid_component(flow_id, "flow_id"),
        "node_id": _valid_component(node_id, "node_id"),
    }
