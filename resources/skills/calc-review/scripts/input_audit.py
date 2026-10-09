#!/usr/bin/env python3
"""Input-first VASP audit for a single local ``calc`` directory.

The audit intentionally stops at inputs.  It never requires OUTCAR and never
executes VASP.  Its JSON is small enough to pass through a scheduler wrapper
and contains deterministic per-input hashes plus one canonical manifest hash.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path


INPUT_NAMES = ("INCAR", "POSCAR", "KPOINTS", "POTCAR")
PROFILES = {"generic", "relax", "static", "soc", "lcalcpol", "wannier"}
TRUE_VALUES = {".TRUE.", "TRUE", "T", "1", "YES", "Y", "ON"}
FALSE_VALUES = {".FALSE.", "FALSE", "F", "0", "NO", "N", "OFF"}
ELEMENT_RE = re.compile(r"^[A-Z][a-z]?$")
INT_RE = re.compile(r"^[+-]?\d+$")


class AuditError(Exception):
    """A user-facing audit invocation error."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_canonical(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def parse_incar(text):
    """Return last-value tags, duplicate records, and malformed line numbers."""
    tags = {}
    occurrences = {}
    malformed = []
    lines = text.splitlines()
    position = 0
    while position < len(lines):
        line_number = position + 1
        original = lines[position]
        position += 1
        if re.fullmatch(r'\s*WANNIER90_WIN\s*=\s*"\s*', original, re.I):
            content = []
            while position < len(lines) and lines[position].strip() != '"':
                content.append(lines[position])
                position += 1
            if position == len(lines):
                malformed.append(line_number)
                continue
            position += 1
            value = '"\n' + '\n'.join(content) + '\n"'
            occurrences.setdefault('WANNIER90_WIN', []).append({'line': line_number, 'value': value})
            tags['WANNIER90_WIN'] = value
            continue
        line = re.split(r"[!#]", original, maxsplit=1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            malformed.append(line_number)
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not value:
            malformed.append(line_number)
            continue
        occurrences.setdefault(key, []).append({"line": line_number, "value": value})
        tags[key] = value
    duplicates = []
    for key, values in sorted(occurrences.items()):
        if len(values) <= 1:
            continue
        normalized = {re.sub(r"\s+", " ", value["value"].upper()) for value in values}
        duplicates.append({
            "tag": key,
            "lines": [value["line"] for value in values],
            "conflicting": len(normalized) > 1,
        })
    return tags, duplicates, malformed


def _tag_bool(tags, name):
    value = str(tags.get(name, "")).split()
    if not value:
        return None
    token = value[0].upper()
    if token in TRUE_VALUES:
        return True
    if token in FALSE_VALUES:
        return False
    return None


def _tag_int(tags, name):
    value = str(tags.get(name, "")).strip()
    return int(value) if INT_RE.fullmatch(value) else None


def _file_info(calc_dir, name):
    path = calc_dir / name
    info = {"name": name, "path": str(path), "exists": path.exists(), "size_bytes": None, "sha256": None}
    if not path.exists():
        return info, None
    if not path.is_file():
        info["kind"] = "not-a-file"
        return info, None
    try:
        data = path.read_bytes()
    except OSError as exc:
        info["read_error"] = str(exc)
        return info, None
    info["size_bytes"] = len(data)
    info["sha256"] = _sha256(data)
    return info, data


def _parse_poscar(data):
    errors = []
    warnings = []
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return {}, ["POSCAR is not UTF-8 text"], []
    if len(lines) < 7:
        return {}, ["POSCAR has too few lines"], []
    try:
        scale = float(lines[1].split()[0])
        if scale == 0:
            errors.append("POSCAR scale factor is zero")
    except (IndexError, ValueError):
        errors.append("POSCAR scale factor is not numeric")
    lattice = []
    for index in range(2, 5):
        values = lines[index].split()
        if len(values) < 3:
            errors.append("POSCAR lattice vector {} is incomplete".format(index - 1))
            continue
        try:
            vector = [float(item) for item in values[:3]]
            if not all(math.isfinite(item) for item in vector):
                raise ValueError
            lattice.append(vector)
        except ValueError:
            errors.append("POSCAR lattice vector {} is not numeric".format(index - 1))
    if len(lattice) == 3:
        determinant = (
            lattice[0][0] * (lattice[1][1] * lattice[2][2] - lattice[1][2] * lattice[2][1])
            - lattice[0][1] * (lattice[1][0] * lattice[2][2] - lattice[1][2] * lattice[2][0])
            + lattice[0][2] * (lattice[1][0] * lattice[2][1] - lattice[1][1] * lattice[2][0])
        )
        if not math.isfinite(determinant) or abs(determinant) < 1e-12:
            errors.append("POSCAR lattice vectors are singular")
    element_index = 5
    element_tokens = lines[element_index].split()
    if element_tokens and all(INT_RE.fullmatch(item) for item in element_tokens):
        errors.append("POSCAR is VASP4-style and has no element symbols")
        return {"elements": [], "counts": []}, errors, warnings
    elements = element_tokens
    if not elements or any(not ELEMENT_RE.fullmatch(item) for item in elements):
        errors.append("POSCAR element symbols are invalid")
    count_index = 6
    counts = lines[count_index].split() if len(lines) > count_index else []
    if len(counts) != len(elements) or any(not INT_RE.fullmatch(item) for item in counts):
        errors.append("POSCAR element counts do not match element symbols")
        parsed_counts = []
    else:
        parsed_counts = [int(item) for item in counts]
        if any(item <= 0 for item in parsed_counts):
            errors.append("POSCAR element counts must be positive")
    coordinate_index = count_index + 1
    if len(lines) > coordinate_index and lines[coordinate_index].strip().lower().startswith("s"):
        coordinate_index += 1
    if len(lines) <= coordinate_index:
        errors.append("POSCAR has no coordinate section")
    else:
        mode = lines[coordinate_index].strip().lower()
        if not (mode.startswith("d") or mode.startswith("c") or mode.startswith("k")):
            errors.append("POSCAR coordinate mode is not recognizable")
        coordinate_lines = [line for line in lines[coordinate_index + 1:] if line.strip()]
        if parsed_counts and len(coordinate_lines) < sum(parsed_counts):
            errors.append("POSCAR has fewer coordinate rows than the declared atom count")
        for atom_index, line in enumerate(coordinate_lines[:sum(parsed_counts)], 1):
            fields = line.split()
            if len(fields) < 3:
                errors.append("POSCAR coordinate row {} is incomplete".format(atom_index))
                continue
            try:
                coordinates = [float(item) for item in fields[:3]]
                if not all(math.isfinite(item) for item in coordinates):
                    raise ValueError
            except ValueError:
                errors.append("POSCAR coordinate row {} is not numeric".format(atom_index))
    return {"elements": elements, "counts": parsed_counts, "total_atoms": sum(parsed_counts)}, errors, warnings


def _potcar_titles(data):
    try:
        text = data.decode("utf-8", errors="ignore")
    except AttributeError:
        return []
    titles = []
    for line in text.splitlines():
        match = re.search(r"TITEL\s*=\s*(.*)", line, re.I)
        if not match:
            continue
        tokens = re.sub(r"[;]", " ", match.group(1)).split()
        symbol = None
        for token in tokens:
            candidate = token.split("_")[0].split(".")[0]
            if ELEMENT_RE.fullmatch(candidate):
                symbol = candidate
                break
        titles.append({"titel": match.group(1).strip(), "element": symbol})
    return titles


def _audit_kpoints(data):
    errors = []
    warnings = []
    try:
        lines = [line.strip() for line in data.decode("utf-8").splitlines() if line.strip()]
    except UnicodeDecodeError:
        return ["KPOINTS is not UTF-8 text"], []
    if len(lines) < 4:
        return ["KPOINTS has fewer than four non-empty lines"], []
    try:
        subdivisions = int(lines[1].split()[0])
        if subdivisions < 0:
            errors.append("KPOINTS subdivisions must be non-negative")
    except (IndexError, ValueError):
        errors.append("KPOINTS second line is not an integer")
        subdivisions = None
    mode = lines[2].lower()
    if mode.startswith("l"):
        if subdivisions is None or subdivisions <= 0:
            errors.append("line-mode KPOINTS requires a positive subdivisions count")
        if len(lines) < 6:
            errors.append("line-mode KPOINTS has too few path points")
        elif not lines[3].lower().startswith(("r", "c", "k")):
            errors.append("line-mode KPOINTS coordinate system is invalid")
        else:
            points = [line for line in lines[4:] if line]
            if len(points) < 2:
                errors.append("line-mode KPOINTS requires at least two path points")
            for index, line in enumerate(points, 1):
                fields = line.split()
                try:
                    values = [float(item) for item in fields[:3]]
                    if len(fields) < 3 or not all(math.isfinite(item) for item in values):
                        raise ValueError
                except ValueError:
                    errors.append("line-mode KPOINTS point {} is invalid".format(index))
        return errors, warnings
    if subdivisions == 0:
        if not mode.startswith(("g", "m", "a")):
            errors.append("automatic KPOINTS mode must be Gamma, Monkhorst-Pack, or Auto")
            return errors, warnings
        mesh = lines[3].split()
        if len(mesh) < 3 or any(not INT_RE.fullmatch(item) for item in mesh[:3]):
            errors.append("KPOINTS mesh must contain three integers")
        elif any(int(item) <= 0 for item in mesh[:3]):
            errors.append("KPOINTS mesh integers must be positive")
        if len(lines) > 4:
            fields = lines[4].split()
            try:
                values = [float(item) for item in fields[:3]]
                if len(fields) < 3 or not all(math.isfinite(item) for item in values):
                    raise ValueError
            except ValueError:
                errors.append("KPOINTS shift line is not numeric")
        return errors, warnings
    if subdivisions is not None and subdivisions > 0:
        if not mode.startswith(("r", "c", "k")):
            errors.append("explicit KPOINTS coordinate system is invalid")
            return errors, warnings
        if len(lines) < 3 + subdivisions:
            errors.append("explicit KPOINTS has fewer points than declared")
        for index, line in enumerate(lines[3:3 + subdivisions], 1):
            fields = line.split()
            try:
                values = [float(item) for item in fields[:4]]
                if len(fields) < 4 or not all(math.isfinite(item) for item in values):
                    raise ValueError
            except ValueError:
                errors.append("explicit KPOINTS point {} is invalid".format(index))
        return errors, warnings
    errors.append("KPOINTS mode is unsupported")
    return errors, warnings


def _audit_incar(tags, duplicates, malformed, profile, executable):
    errors = []
    warnings = []
    for duplicate in duplicates:
        message = "duplicate INCAR tag {} on lines {}".format(
            duplicate["tag"], ",".join(str(item) for item in duplicate["lines"])
        )
        (errors if duplicate["conflicting"] else warnings).append(message)
    if malformed:
        errors.append("INCAR has unparsed lines: {}".format(",".join(map(str, malformed))))
    profile = profile.lower()
    lsorbit = _tag_bool(tags, "LSORBIT") is True
    lnoncollinear = _tag_bool(tags, "LNONCOLLINEAR") is True
    lspiral = _tag_bool(tags, "LSPIRAL") is True
    lc = _tag_bool(tags, "LCALCPOL") is True
    if lc and (_tag_int(tags, "NCORE") or 1) != 1:
        errors.append("LCALCPOL PEAD requires NCORE = 1; edit the input and freeze a new version")
    if lc and "NPAR" in tags and "NCORE" in tags:
        errors.append("LCALCPOL cannot specify both NPAR and NCORE")
    if profile == "relax" and (_tag_int(tags, "NSW") or 0) <= 0:
        errors.append("relax profile requires NSW > 0")
    if profile == "static" and (_tag_int(tags, "NSW") or 0) > 0:
        errors.append("static profile conflicts with NSW > 0")
    if profile == "soc" and not lsorbit and not lnoncollinear:
        errors.append("soc profile requires LSORBIT or LNONCOLLINEAR")
    if profile == "lcalcpol":
        if not lc:
            errors.append("lcalcpol profile requires LCALCPOL = .TRUE.")
    if profile == "wannier" and _tag_bool(tags, "LWANNIER90") is not True:
        errors.append("wannier profile requires LWANNIER90 = .TRUE.")
    if lspiral and lsorbit:
        errors.append("LSPIRAL + LSORBIT is invalid")
    if lsorbit or lnoncollinear:
        executable_name = Path(executable).name.lower() if executable else ""
        if executable_name != "vasp_ncl":
            errors.append("SOC/non-collinear inputs require vasp_ncl")
    if _tag_bool(tags, "LSORBIT") is None and "LSORBIT" in tags:
        errors.append("INCAR LSORBIT value is not boolean")
    if _tag_bool(tags, "LNONCOLLINEAR") is None and "LNONCOLLINEAR" in tags:
        errors.append("INCAR LNONCOLLINEAR value is not boolean")
    istart = _tag_int(tags, "ISTART")
    icharg = _tag_int(tags, "ICHARG")
    if "ISTART" in tags and istart not in {0, 1, 2, 3}:
        errors.append("INCAR ISTART must be one of 0, 1, 2, or 3")
    if "ICHARG" in tags and icharg not in {0, 1, 2, 4, 5, 10, 11, 12, 14}:
        errors.append("INCAR ICHARG has an unsupported value")
    restart = {"ISTART": istart, "ICHARG": icharg, "required_files": []}
    if istart is not None and istart >= 1:
        restart["required_files"].append("WAVECAR")
    if icharg in {1, 11, 12}:
        restart["required_files"].append("CHGCAR")
    return errors, warnings, restart


def audit_inputs(calc_dir, profile="generic", executable="vasp_std"):
    calc_dir = Path(calc_dir).expanduser().resolve(strict=False)
    if not calc_dir.is_dir():
        raise AuditError("calc directory does not exist: {}".format(calc_dir))
    profile = str(profile).lower()
    if profile not in PROFILES:
        raise AuditError("unknown profile: {}".format(profile))
    errors = []
    warnings = []
    inputs = {}
    raw = {}
    for name in INPUT_NAMES:
        info, data = _file_info(calc_dir, name)
        inputs[name] = info
        raw[name] = data
        if not info["exists"]:
            errors.append("missing {}".format(name))
        elif info.get("kind") == "not-a-file":
            errors.append("{} is not a regular file".format(name))
        elif info.get("size_bytes") == 0:
            errors.append("{} is empty".format(name))
        elif info.get("read_error"):
            errors.append("{} cannot be read".format(name))

    poscar = {"elements": [], "counts": []}
    if raw["POSCAR"]:
        poscar, poscar_errors, poscar_warnings = _parse_poscar(raw["POSCAR"])
        errors.extend(poscar_errors)
        warnings.extend(poscar_warnings)
    if raw["KPOINTS"]:
        k_errors, k_warnings = _audit_kpoints(raw["KPOINTS"])
        errors.extend(k_errors)
        warnings.extend(k_warnings)
    titles = _potcar_titles(raw["POTCAR"]) if raw["POTCAR"] else []
    if raw["POTCAR"] and not titles:
        errors.append("POTCAR contains no TITEL records")
    potcar_order_matches = False
    if titles and poscar.get("elements"):
        potcar_elements = [item.get("element") for item in titles]
        expected = poscar["elements"]
        potcar_order_matches = potcar_elements == expected
        if not potcar_order_matches:
            errors.append("POTCAR TITEL order does not match POSCAR elements")
    incar_tags = {}
    duplicates = []
    malformed = []
    if raw["INCAR"]:
        try:
            text = raw["INCAR"].decode("utf-8")
            incar_tags, duplicates, malformed = parse_incar(text)
        except UnicodeDecodeError:
            errors.append("INCAR is not UTF-8 text")
    if raw["INCAR"] and not incar_tags:
        errors.append("INCAR has no parseable assignments")
    incar_errors, incar_warnings, restart = _audit_incar(
        incar_tags, duplicates, malformed, profile, executable
    )
    errors.extend(incar_errors)
    warnings.extend(incar_warnings)
    for required in restart["required_files"]:
        info, _ = _file_info(calc_dir, required)
        inputs[required] = info
        if (not info["exists"] or info.get("kind") == "not-a-file"
                or not isinstance(info.get("size_bytes"), int)
                or info.get("size_bytes", 0) <= 0 or info.get("read_error")):
            errors.append("{} required by INCAR restart settings".format(required))

    manifest_items = []
    for name in INPUT_NAMES:
        info = inputs[name]
        manifest_items.append({
            "name": name,
            "exists": bool(info.get("exists")),
            "size_bytes": info.get("size_bytes"),
            "sha256": info.get("sha256"),
        })
    for name in sorted(set(restart["required_files"])):
        info = inputs[name]
        manifest_items.append({
            "name": name,
            "exists": bool(info.get("exists")),
            "size_bytes": info.get("size_bytes"),
            "sha256": info.get("sha256"),
        })
    manifest_hash = hashlib.sha256(_canonical(manifest_items).encode("utf-8")).hexdigest()
    return {
        "schema": "fpilot.input-audit.v1",
        "status": "PASS" if not errors else "FAIL",
        "profile": profile,
        "calc_dir": str(calc_dir),
        "vasp_executable": executable,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "inputs": inputs,
        "input_hashes": {name: inputs[name].get("sha256") for name in sorted(inputs)},
        "input_manifest_sha256": manifest_hash,
        "canonical_input_manifest_sha256": manifest_hash,
        "poscar": poscar,
        "potcar_titel": titles,
        "incar": {
            "tags": incar_tags,
            "duplicate_tags": duplicates,
            "malformed_lines": malformed,
            "restart": restart,
        },
        "checks": {
            "required_inputs_nonempty": all(
                inputs[name].get("exists") and isinstance(inputs[name].get("size_bytes"), int)
                and inputs[name].get("size_bytes", 0) > 0 for name in INPUT_NAMES
            ),
            "poscar_species_counts": bool(poscar.get("elements")) and len(poscar.get("elements", [])) == len(poscar.get("counts", [])),
            "potcar_titel_order": potcar_order_matches,
        },
    }


def build_parser():
    parser = argparse.ArgumentParser(description="Audit VASP inputs without reading OUTCAR.")
    parser.add_argument("calc_dir", nargs="?", help="local calc directory")
    parser.add_argument("--calc-dir", dest="calc_dir_opt")
    parser.add_argument("--profile", default="generic", type=str.lower, choices=sorted(PROFILES))
    parser.add_argument("--vasp-executable", "--vasp-bin", "--executable", default="vasp_std")
    parser.add_argument("--output", help="write the same compact JSON to this path")
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        if args.calc_dir and args.calc_dir_opt:
            raise AuditError("provide calc directory once")
        calc_dir = args.calc_dir_opt or args.calc_dir
        if not calc_dir:
            raise AuditError("calc directory is required")
        result = audit_inputs(calc_dir, args.profile, args.vasp_executable)
        if args.output:
            _atomic_write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    except AuditError as exc:
        print("input_audit: {}".format(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("input_audit: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
