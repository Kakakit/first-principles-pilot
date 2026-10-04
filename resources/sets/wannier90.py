"""Small, file-level helpers for VASP-generated Wannier90 input files.

VASP is the source of the structural part of a ``seedname.win`` file.  This
module only checks that those fields are present and applies explicit
Wannier90 tag requests.  It deliberately does not run Wannier90 or attempt
to choose projections, windows, or other scientific settings.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


WIN_REQUEST_SCHEMA = "fpilot.wannier90-tag-request.v1"
WIN_RESULT_SCHEMA = "fpilot.wannier90-win-result.v1"
REGISTRY_PATH = Path(__file__).resolve().parent.parent / "references" / "wannier90-tags.json"

_NAME = r"[A-Za-z][A-Za-z0-9_]*"
_BEGIN_RE = re.compile(r"^\s*begin\s+(%s)\s*$" % _NAME, re.IGNORECASE)
_END_RE = re.compile(r"^\s*end\s+(%s)\s*$" % _NAME, re.IGNORECASE)
_ASSIGN_RE = re.compile(
    r"^\s*(%s)\s*(?:(?:=|:)\s*|\s+)(.*?)\s*$" % _NAME,
    re.IGNORECASE,
)

PROTECTED_SCALARS = frozenset({"num_wann", "num_bands", "mp_grid", "spinors"})
PROTECTED_BLOCKS = frozenset({"unit_cell_cart", "atoms_cart", "atoms_frac", "kpoints"})

_FROZEN_WINDOW_TAGS = frozenset({"dis_froz_min", "dis_froz_max"})


class WannierWinError(ValueError):
    """A source file or explicit tag request is not usable."""

    def __init__(self, message: str, *, errors: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.errors = list(errors or [])


class _Scalar:
    def __init__(self, name: str, value: str, line: int):
        self.name = name
        self.value = value
        self.line = line


class _Block:
    def __init__(self, name: str, start: int, end: int | None, body: list[str]):
        self.name = name
        self.start = start
        self.end = end
        self.body = body


class _Document:
    def __init__(self, lines: list[str], scalars: dict[str, list[_Scalar]], blocks: dict[str, list[_Block]]):
        self.lines = lines
        self.scalars = scalars
        self.blocks = blocks

    def occurrences(self, name: str) -> list[tuple[str, Any]]:
        key = _normalise_name(name)
        return [("scalar", entry) for entry in self.scalars.get(key, [])] + [
            ("block", entry) for entry in self.blocks.get(key, [])
        ]


def _normalise_name(value: str) -> str:
    return str(value).strip().casefold()


def _without_comment(line: str) -> str:
    """Return the parseable part of a line.

    ``!`` and ``#`` are both accepted by Wannier90 as comment markers.  The
    input language has no quoted values that need to contain either marker,
    so the first marker is sufficient here.
    """

    positions = [position for position in (line.find("!"), line.find("#")) if position >= 0]
    return line[: min(positions)] if positions else line


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith(("\n", "\r")):
        return line[-1]
    return ""


def _data_lines(block: _Block, lines: list[str]) -> list[str]:
    result = []
    for line in lines[block.start + 1 : block.end if block.end is not None else len(lines)]:
        clean = _without_comment(line).strip()
        if clean:
            result.append(clean)
    return result


def parse_win(text: str) -> _Document:
    """Parse top-level scalar assignments and named blocks while preserving text."""

    if not isinstance(text, str):
        raise TypeError("win content must be text")
    lines = text.splitlines(keepends=True)
    # splitlines() returns no line for the empty string; preserving that is
    # useful when callers only want to inspect a malformed file.
    scalars: dict[str, list[_Scalar]] = {}
    blocks: dict[str, list[_Block]] = {}
    index = 0
    while index < len(lines):
        clean = _without_comment(lines[index]).strip()
        begin = _BEGIN_RE.match(clean)
        if begin:
            name = _normalise_name(begin.group(1))
            end_index = None
            cursor = index + 1
            while cursor < len(lines):
                end = _END_RE.match(_without_comment(lines[cursor]).strip())
                if end and _normalise_name(end.group(1)) == name:
                    end_index = cursor
                    break
                cursor += 1
            block = _Block(name=name, start=index, end=end_index, body=[])
            block.body = _data_lines(block, lines)
            blocks.setdefault(name, []).append(block)
            index = (end_index + 1) if end_index is not None else len(lines)
            continue
        if clean and not _END_RE.match(clean):
            match = _ASSIGN_RE.match(clean)
            if match:
                name = _normalise_name(match.group(1))
                scalars.setdefault(name, []).append(
                    _Scalar(name=name, value=match.group(2).strip(), line=index)
                )
        index += 1
    return _Document(lines=lines, scalars=scalars, blocks=blocks)


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if details:
        result["details"] = details
    return result


def _first_value(document: _Document, name: str) -> str | None:
    entries = document.scalars.get(_normalise_name(name), [])
    return entries[0].value if entries else None


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().casefold().strip(".,") in {"true", ".true", "t", "yes", "1"}


def _positive_integer(value: str | None) -> int | None:
    if value is None or len(value.split()) != 1:
        return None
    try:
        parsed = int(value, 10)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _mp_grid(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    fields = value.replace(",", " ").split()
    if len(fields) != 3:
        return None
    try:
        grid = tuple(int(field, 10) for field in fields)
    except ValueError:
        return None
    return grid if all(item > 0 for item in grid) else None


def _official_registry() -> dict[str, Any]:
    try:
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WannierWinError("cannot read Wannier90 tag registry") from exc
    if not isinstance(value, dict):
        raise WannierWinError("Wannier90 tag registry must be an object")
    return value


def official_tags() -> tuple[set[str], set[str]]:
    """Return the registered scalar and block names in canonical form."""

    registry = _official_registry()
    scalars = {_normalise_name(item) for item in registry.get("scalar_tags", [])}
    blocks = {_normalise_name(item) for item in registry.get("block_tags", [])}
    return scalars, blocks


def official_source_revision() -> dict[str, Any]:
    registry = _official_registry()
    value = registry.get("source_revision", {})
    return dict(value) if isinstance(value, Mapping) else {}


def validate_win(text: str) -> list[dict[str, Any]]:
    """Check only the VASP-generated structural contract and small hard rules."""

    document = parse_win(text)
    errors: list[dict[str, Any]] = []

    # A duplicate known entry is ambiguous.  Unknown source entries remain
    # untouched and are intentionally not interpreted here.
    scalar_names, block_names = official_tags()
    for name in sorted(scalar_names):
        if document.blocks.get(name):
            errors.append(_error("TAG_KIND", "official scalar appears as a block", tag=name))
    for name in sorted(block_names):
        if document.scalars.get(name):
            errors.append(_error("TAG_KIND", "official block appears as a scalar", tag=name))
    for name in sorted(scalar_names):
        entries = document.scalars.get(name, [])
        if len(entries) > 1:
            errors.append(_error("DUPLICATE_SCALAR", "known scalar appears more than once", tag=name))
    for name in sorted(block_names):
        entries = document.blocks.get(name, [])
        if len(entries) > 1:
            errors.append(_error("DUPLICATE_BLOCK", "known block appears more than once", tag=name))
        if entries and entries[0].end is None:
            errors.append(_error("UNCLOSED_BLOCK", "known block has no matching end", tag=name))

    num_wann = _positive_integer(_first_value(document, "num_wann"))
    if len(document.scalars.get("num_wann", [])) != 1:
        errors.append(_error("MISSING_NUM_WANN", "source must contain exactly one positive num_wann"))
    elif num_wann is None:
        errors.append(_error("INVALID_NUM_WANN", "num_wann must be a positive integer"))

    num_bands = _positive_integer(_first_value(document, "num_bands"))
    if len(document.scalars.get("num_bands", [])) != 1:
        errors.append(_error("MISSING_NUM_BANDS", "source must contain exactly one positive num_bands"))
    elif num_bands is None:
        errors.append(_error("INVALID_NUM_BANDS", "num_bands must be a positive integer"))

    grid = _mp_grid(_first_value(document, "mp_grid"))
    if len(document.scalars.get("mp_grid", [])) != 1:
        errors.append(_error("MISSING_MP_GRID", "source must contain exactly one positive three-integer mp_grid"))
    elif grid is None:
        errors.append(_error("INVALID_MP_GRID", "mp_grid must contain three positive integers"))
    if num_wann is not None and num_bands is not None and num_wann > num_bands:
        errors.append(_error("NUM_WANN_EXCEEDS_BANDS", "num_wann cannot exceed num_bands"))

    unit_cells = document.blocks.get("unit_cell_cart", [])
    if len(unit_cells) != 1 or not _data_lines(unit_cells[0], document.lines):
        errors.append(_error("INVALID_UNIT_CELL_CART", "unit_cell_cart must be one closed, non-empty block"))

    cart = document.blocks.get("atoms_cart", [])
    frac = document.blocks.get("atoms_frac", [])
    if len(cart) + len(frac) != 1:
        errors.append(_error("ATOMS_BLOCK", "exactly one non-empty atoms_cart or atoms_frac block is required"))
    else:
        atoms = cart[0] if cart else frac[0]
        if not _data_lines(atoms, document.lines):
            errors.append(_error("EMPTY_ATOMS_BLOCK", "the atoms block must not be empty"))

    kpoints = document.blocks.get("kpoints", [])
    if len(kpoints) != 1 or not _data_lines(kpoints[0], document.lines):
        errors.append(_error("INVALID_KPOINTS", "kpoints must be one closed, non-empty block"))
    elif grid is not None:
        actual = len(_data_lines(kpoints[0], document.lines))
        expected = grid[0] * grid[1] * grid[2]
        if actual != expected:
            errors.append(_error("KPOINT_COUNT", "kpoints count must equal the mp_grid product", expected=expected, actual=actual))

    if "fixed_step" in document.scalars and "trial_step" in document.scalars:
        errors.append(_error("STEP_CONFLICT", "fixed_step and trial_step cannot coexist"))
    # Generalised-Bloch phases are incompatible with a disentangled run.  A
    # larger band manifold is the simple, direct indication of that mode;
    # merely seeing an optional ``dis_*`` setting is not enough.
    if _truthy(_first_value(document, "use_bloch_phases")) and num_wann is not None and num_bands is not None and num_bands > num_wann:
        errors.append(_error("BLOCH_DISENTANGLEMENT_CONFLICT", "use_bloch_phases cannot be used with disentanglement"))
    if (_truthy(_first_value(document, "site_symmetry")) or "symmetrize_eps" in document.scalars) and _FROZEN_WINDOW_TAGS.intersection(document.scalars):
        errors.append(_error("SYMMETRY_FROZEN_CONFLICT", "site_symmetry/symmetrize_eps cannot be used with an inner frozen window"))
    if _truthy(_first_value(document, "bands_plot")):
        def has_nonempty_block(name: str) -> bool:
            entries = document.blocks.get(name, [])
            return bool(entries) and entries[0].end is not None and bool(_data_lines(entries[0], document.lines))

        has_path = has_nonempty_block("kpoint_path")
        has_explicit = has_nonempty_block("explicit_kpath") and has_nonempty_block("explicit_kpath_labels")
        if not has_path and not has_explicit:
            errors.append(_error("BANDS_PATH_MISSING", "bands_plot=true requires kpoint_path or explicit_kpath with explicit_kpath_labels"))
    return errors


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return ".true." if value else ".false."
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("scalar numeric value must be finite")
        return repr(value)
    if isinstance(value, str):
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("scalar string value must be a non-empty single line")
        return value
    raise ValueError("scalar value must be a boolean, number, or single-line string")


def _parse_request(request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(request, Mapping) or request.get("schema") != WIN_REQUEST_SCHEMA:
        raise WannierWinError("request schema must be {}".format(WIN_REQUEST_SCHEMA))
    unknown = sorted(set(request) - {"schema", "add", "replace"})
    if unknown:
        raise WannierWinError("request has unknown fields: {}".format(", ".join(unknown)))
    scalars, blocks = official_tags()
    add = request.get("add", {})
    replace = request.get("replace", {})
    if not isinstance(add, Mapping) or not isinstance(replace, Mapping):
        raise WannierWinError("add and replace must be objects")
    parsed: list[tuple[str, str, Any]] = []
    for action, values in (("add", add), ("replace", replace)):
        for raw_name, value in values.items():
            if not isinstance(raw_name, str) or not re.fullmatch(_NAME, raw_name):
                raise WannierWinError("requested tag name is invalid")
            name = _normalise_name(raw_name)
            if name in PROTECTED_SCALARS or name in PROTECTED_BLOCKS:
                raise WannierWinError("VASP-owned tag cannot be changed: {}".format(raw_name))
            if name in scalars:
                try:
                    _format_scalar(value)
                except ValueError as exc:
                    raise WannierWinError("invalid scalar value for {}: {}".format(raw_name, exc)) from exc
                kind = "scalar"
            elif name in blocks:
                if not isinstance(value, list) or not value or any(
                    not isinstance(item, str)
                    or not item.strip()
                    or "\n" in item
                    or "\r" in item
                    for item in value
                ):
                    raise WannierWinError("block value must be a list of single-line strings: {}".format(raw_name))
                kind = "block"
            else:
                raise WannierWinError("unknown Wannier90 tag: {}".format(raw_name))
            parsed.append((name, action, value))
    names = [item[0] for item in parsed]
    if len(names) != len(set(names)):
        raise WannierWinError("a tag cannot appear in both add and replace")
    return dict((name, value) for name, action, value in parsed if action == "add"), dict(
        (name, value) for name, action, value in parsed if action == "replace"
    )


def _request_kinds(request: Mapping[str, Any]) -> dict[str, str]:
    scalars, blocks = official_tags()
    kinds = {}
    for action in ("add", "replace"):
        for name in request.get(action, {}):
            key = _normalise_name(name)
            kinds[key] = "scalar" if key in scalars else "block"
    return kinds


def merge_win(text: str, request: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return a new text value and a small list describing explicit changes."""

    source_errors = validate_win(text)
    if source_errors:
        raise WannierWinError("source win validation failed", errors=source_errors)
    add, replace = _parse_request(request)
    kinds = _request_kinds(request)
    document = parse_win(text)
    errors: list[dict[str, Any]] = []
    for name in list(add) + list(replace):
        occurrences = document.occurrences(name)
        if len(occurrences) > 1:
            errors.append(_error("DUPLICATE_TARGET", "requested tag is ambiguous in source", tag=name))
        elif name in add and occurrences:
            errors.append(_error("ADD_EXISTS", "add requires a missing tag", tag=name))
        elif name in replace and not occurrences:
            errors.append(_error("REPLACE_MISSING", "replace requires an existing tag", tag=name))
    if errors:
        raise WannierWinError("tag merge validation failed", errors=errors)

    lines = list(document.lines)
    changes: list[dict[str, Any]] = []
    newline = "\n"
    for line in lines:
        if line.endswith("\r\n"):
            newline = "\r\n"
            break
    # Replace from the bottom upwards so a block whose new body has a
    # different number of lines cannot invalidate the original positions of
    # later replacements.
    replace_ops = []
    for name, value in replace.items():
        kind = kinds[name]
        entry = document.scalars[name][0] if kind == "scalar" else document.blocks[name][0]
        replace_ops.append((entry.line if kind == "scalar" else entry.start, name, kind, entry, value))
    for _position, name, kind, entry, value in sorted(replace_ops, key=lambda item: item[0], reverse=True):
        if kind == "scalar":
            raw = lines[entry.line]
            ending = _line_ending(raw)
            body = raw[:-len(ending)] if ending else raw
            comment_positions = [position for position in (body.find("!"), body.find("#")) if position >= 0]
            if comment_positions:
                comment_start = min(comment_positions)
                comment = body[comment_start:]
                if comment_start == 0 or not body[comment_start - 1].isspace():
                    comment = " " + comment
            else:
                comment = ""
            indent = re.match(r"^\s*", body).group(0)
            lines[entry.line] = indent + name + " = " + _format_scalar(value) + comment + ending
        else:
            lines[entry.start : entry.end + 1] = _block_lines(name, value, newline)
    for name in replace:
        changes.append({"action": "replace", "name": name, "kind": kinds[name]})

    additions: list[str] = []
    if add:
        if lines and not _line_ending(lines[-1]):
            lines[-1] += newline
        for name, value in add.items():
            kind = kinds[name]
            additions.extend(_block_lines(name, value, newline) if kind == "block" else [name + " = " + _format_scalar(value) + newline])
            changes.append({"action": "add", "name": name, "kind": kind})
        lines.extend(additions)
    return "".join(lines), changes


def _block_lines(name: str, value: list[str], newline: str) -> list[str]:
    return ["begin " + name + newline] + [item + newline for item in value] + ["end " + name + newline]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_source(path: str | Path) -> tuple[Path, bytes, str]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise WannierWinError("win source does not exist: {}".format(source))
    try:
        data = source.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise WannierWinError("cannot read win source: {}".format(source)) from exc
    return source, data, text


def check_file(path: str | Path, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source, data, text = _read_source(path)
    changes: list[dict[str, Any]] = []
    if request is not None:
        merged, changes = merge_win(text, request)
        errors = validate_win(merged)
    else:
        errors = validate_win(text)
    if errors:
        raise WannierWinError("win validation failed", errors=errors)
    return {
        "schema": WIN_RESULT_SCHEMA,
        "status": "VALID",
        "source": str(source),
        "output": None,
        "source_sha256": _sha256(data),
        "output_sha256": None,
        "changes": changes,
        "official_source_revision": official_source_revision(),
    }


def write_file(path: str | Path, request: Mapping[str, Any], output: str | Path) -> dict[str, Any]:
    source, source_data, text = _read_source(path)
    target = Path(output).expanduser().resolve()
    if source == target:
        raise WannierWinError("output must be different from source")
    if target.exists():
        raise WannierWinError("output already exists: {}".format(target))
    merged, changes = merge_win(text, request)
    errors = validate_win(merged)
    if errors:
        raise WannierWinError("merged win validation failed", errors=errors)
    output_data = merged.encode("utf-8")
    try:
        with target.open("xb") as handle:
            handle.write(output_data)
    except OSError as exc:
        raise WannierWinError("cannot write output: {}".format(target)) from exc
    return {
        "schema": WIN_RESULT_SCHEMA,
        "status": "WRITTEN",
        "source": str(source),
        "output": str(target),
        "source_sha256": _sha256(source_data),
        "output_sha256": _sha256(output_data),
        "changes": changes,
        "official_source_revision": official_source_revision(),
    }


# Clear aliases for callers that prefer verb-oriented names.
check_wannier_win = check_file
write_wannier_win = write_file


__all__ = [
    "WIN_REQUEST_SCHEMA",
    "WIN_RESULT_SCHEMA",
    "WannierWinError",
    "official_tags",
    "parse_win",
    "validate_win",
    "merge_win",
    "check_file",
    "write_file",
    "check_wannier_win",
    "write_wannier_win",
]
