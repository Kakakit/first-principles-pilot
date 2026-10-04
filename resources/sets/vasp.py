"""Dependency-free, strict VASP static input-set generation.

This module is intentionally a small contract layer rather than a second
workflow engine.  It understands enough of VASP's four-file input surface to
make a reviewable static calculation, while requiring the caller to provide
all physical choices.  In particular, pseudopotential bytes are never
embedded in the returned document.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path


INPUT_SET_SCHEMA = "fpilot.vasp-input-set.v1"
VALIDATION_SCHEMA = "fpilot.vasp-input-validation.v1"
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_TAG_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(.*?)\s*$")
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_CHEMICAL_SYMBOL_RE = re.compile(r"^[A-Z][a-z]?$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
_SHORTHAND_RE = re.compile(
    r"^(\d+)\*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)$"
)
_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si",
    "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni",
    "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo",
    "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba",
    "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po",
    "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf",
    "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
}


class InputSetError(ValueError):
    """Raised when an input-set request cannot be made physically explicit."""

    def __init__(self, message, *, errors=None, warnings=None):
        super().__init__(message)
        self.errors = list(errors or [])
        self.warnings = list(warnings or [])


def canonical_json(value):
    """Return a stable JSON representation suitable for hashing or receipts."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value):
    """Return the SHA-256 of :func:`canonical_json` encoded as UTF-8."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _file_record(data, *, source_path=None):
    record = {
        "sha256": _sha256_bytes(data),
        "size_bytes": len(data),
    }
    if source_path is not None:
        record["source_path"] = str(Path(source_path).resolve())
    return record


def _error(code, message, **details):
    item = {"code": code, "message": message}
    if details:
        item["details"] = details
    return item


def _warning(code, message, **details):
    item = {"code": code, "message": message}
    if details:
        item["details"] = details
    return item


def _format_value(value):
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, (list, tuple)):
        return " ".join(_format_value(item) for item in value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numeric value")
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if value is None:
        raise ValueError("None is not a VASP tag value")
    return str(value).strip()


def _read_text(path, label, errors):
    if path is None:
        errors.append(_error("MISSING_{}".format(label.upper()), "{} path is required".format(label)))
        return None
    value = Path(path)
    if not value.is_file():
        errors.append(_error("MISSING_{}".format(label.upper()), "{} file does not exist: {}".format(label, value)))
        return None
    try:
        return value.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(_error("UNREADABLE_{}".format(label.upper()), "cannot read {}: {}".format(label, exc)))
        return None


def _parse_poscar(text):
    """Parse the strict VASP5 subset needed by the input-set contract."""

    errors = []
    if not isinstance(text, str):
        return None, [_error("POSCAR_TYPE", "POSCAR content must be text")]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 8:
        return None, [_error("POSCAR_SHORT", "POSCAR has fewer than eight non-empty lines")]
    try:
        scale = float(lines[1].split()[0])
    except (ValueError, IndexError):
        scale = None
    if scale is None or not math.isfinite(scale) or scale == 0:
        errors.append(_error("POSCAR_SCALE", "POSCAR scale must be a nonzero finite number"))
    lattice = []
    for index in range(2, 5):
        tokens = lines[index].split()
        try:
            vector = [float(token) for token in tokens]
        except ValueError:
            vector = []
        if len(vector) != 3 or not all(math.isfinite(value) for value in vector):
            errors.append(_error("POSCAR_LATTICE", "POSCAR lattice line {} must contain three finite numbers".format(index + 1)))
        else:
            lattice.append(vector)
    species = lines[5].split()
    if not species or any(not _CHEMICAL_SYMBOL_RE.fullmatch(item) or item not in _ELEMENTS for item in species):
        errors.append(_error("POSCAR_SPECIES", "POSCAR must use a valid VASP5 species line"))
    if len(set(species)) != len(species):
        errors.append(_error("POSCAR_SPECIES_DUPLICATE", "POSCAR species must be unique and ordered"))
    counts_tokens = lines[6].split()
    counts = []
    for token in counts_tokens:
        if not re.fullmatch(r"\d+", token) or int(token) <= 0:
            errors.append(_error("POSCAR_COUNTS", "POSCAR atom counts must be positive integers"))
            break
        counts.append(int(token))
    if len(counts) != len(species) or not counts:
        errors.append(_error("POSCAR_COUNTS_LENGTH", "POSCAR species and count lengths do not match"))
    natoms = sum(counts) if counts else 0
    cursor = 7
    selective = False
    if cursor < len(lines) and lines[cursor].lower().startswith("selective"):
        selective = True
        cursor += 1
    if cursor >= len(lines) or lines[cursor][:1].lower() not in {"d", "c", "k"}:
        errors.append(_error("POSCAR_COORDINATES", "POSCAR must declare Direct or Cartesian coordinates"))
        mode = None
        cursor += 1
    else:
        mode = "Direct" if lines[cursor][:1].lower() == "d" else "Cartesian"
        cursor += 1
    coordinates = lines[cursor:cursor + natoms]
    velocity_lines = lines[cursor + natoms:]
    if len(coordinates) != natoms:
        errors.append(_error("POSCAR_ATOM_COUNT", "POSCAR coordinate count does not match atom counts", expected=natoms, actual=len(coordinates)))
    for line in coordinates:
        tokens = line.split()
        if len(tokens) < 3:
            errors.append(_error("POSCAR_COORDINATE_VALUE", "each POSCAR coordinate needs three numbers"))
            break
        try:
            values = [float(item) for item in tokens[:3]]
        except ValueError:
            errors.append(_error("POSCAR_COORDINATE_VALUE", "POSCAR coordinates must be numeric"))
            break
        if not all(math.isfinite(value) for value in values):
            errors.append(_error("POSCAR_COORDINATE_VALUE", "POSCAR coordinates must be finite"))
            break
    if velocity_lines:
        if len(velocity_lines) != natoms:
            errors.append(_error(
                "POSCAR_VELOCITY_COUNT",
                "optional POSCAR velocity block must contain one line per ion",
                expected=natoms,
                actual=len(velocity_lines),
            ))
        for line in velocity_lines:
            tokens = line.split()
            if len(tokens) < 3:
                errors.append(_error("POSCAR_VELOCITY_VALUE", "POSCAR velocity rows need three numbers"))
                break
            try:
                values = [float(token) for token in tokens[:3]]
            except ValueError:
                errors.append(_error("POSCAR_VELOCITY_VALUE", "POSCAR velocity rows must be numeric"))
                break
            if not all(math.isfinite(value) for value in values):
                errors.append(_error("POSCAR_VELOCITY_VALUE", "POSCAR velocity rows must be finite"))
                break
    if errors:
        return None, errors
    return {
        "species": species,
        "counts": counts,
        "natoms": natoms,
        "scale": scale,
        "lattice": lattice,
        "coordinate_mode": mode,
        "selective_dynamics": selective,
    }, []


def _potcar_element(title):
    # VASP TITEL lines normally look like ``PAW_PBE Fe 06Sep2000``.  Looking
    # only for known symbols avoids mistaking PBE, date, or version tokens for
    # an element.
    for token in re.split(r"\s+", title.replace("=", " ")):
        token = token.strip(";,()[]")
        candidate = token.split("_", 1)[0].split(".", 1)[0]
        if candidate in _ELEMENTS:
            return candidate
    return None


def _parse_potcar(path, species):
    errors = []
    if not Path(path).is_file():
        return None, [_error("POTCAR_UNREADABLE", "POTCAR source must be an existing regular file")]
    try:
        data = Path(path).read_bytes()
    except (OSError, TypeError) as exc:
        return None, [_error("POTCAR_UNREADABLE", "cannot read POTCAR: {}".format(exc))]
    text = data.decode("utf-8", errors="replace")
    titles = []
    lexch = []
    enmax = []
    for line in text.splitlines():
        title_match = re.match(r"^\s*TITEL\s*=\s*(.*?)\s*$", line, re.IGNORECASE)
        if title_match:
            titles.append(title_match.group(1))
        lexch_match = re.match(r"^\s*LEXCH\s*=\s*(.*?)\s*$", line, re.IGNORECASE)
        if lexch_match:
            lexch.append(lexch_match.group(1).split()[0])
        enmax_match = re.match(r"^\s*ENMAX\s*=\s*([-+]?\d+(?:\.\d*)?(?:[Ee][-+]?\d+)?)", line, re.IGNORECASE)
        if enmax_match:
            enmax.append(float(enmax_match.group(1)))
    if not titles:
        errors.append(_error("POTCAR_TITEL_MISSING", "POTCAR has no detectable TITEL records"))
    potcar_species = [_potcar_element(title) for title in titles]
    if any(item is None for item in potcar_species):
        errors.append(_error("POTCAR_TITEL_ELEMENT", "one or more POTCAR TITEL records have no detectable element"))
    if potcar_species != list(species):
        errors.append(_error("POTCAR_SPECIES_ORDER", "POTCAR TITEL element order must exactly match POSCAR species", expected=list(species), actual=potcar_species))
    title_families = []
    for title in titles:
        family_match = re.search(r"PAW[_-]([A-Za-z0-9]+)", title, re.IGNORECASE)
        if family_match:
            title_families.append(family_match.group(1).upper())
    title_xc_families = {item for item in title_families if item}
    lexch_families = {str(item).upper() for item in lexch if item}
    # TITEL commonly spells the same family as ``PBE`` while LEXCH uses ``PE``.
    # Those vocabularies are recorded separately; unioning them would falsely
    # reject an ordinary PAW_PBE POTCAR.
    mixed_xc = len(title_xc_families) > 1 or len(lexch_families) > 1
    if mixed_xc:
        errors.append(_error(
            "POTCAR_MIXED_XC",
            "POTCAR contains mixed detectable XC metadata",
            title_families=sorted(title_xc_families),
            lexch_families=sorted(lexch_families),
        ))
    records = []
    for index, title in enumerate(titles):
        record = {"titel": title, "element": potcar_species[index] if index < len(potcar_species) else None}
        if index < len(lexch):
            record["lexch"] = lexch[index]
        if index < len(enmax):
            record["enmax"] = enmax[index]
        records.append(record)
    return {
        "source_path": str(Path(path).resolve()),
        "sha256": _sha256_bytes(data),
        "size_bytes": len(data),
        "titel": titles,
        "elements": potcar_species,
        "records": records,
        "xc_families": {
            "titel": sorted(title_xc_families),
            "lexch": sorted(lexch_families),
        },
        "mixed_xc": mixed_xc,
        "content_packaged": False,
    }, errors


def _parse_incar(content):
    tags = {}
    errors = []
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.split("!", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        match = _TAG_RE.fullmatch(line)
        if not match:
            errors.append(_error("INCAR_SYNTAX", "invalid INCAR line {}".format(line_number)))
            continue
        tag, value = match.groups()
        tag = tag.upper()
        if tag in tags:
            errors.append(_error("INCAR_DUPLICATE_TAG", "duplicate INCAR tag {}".format(tag)))
        else:
            tags[tag] = value.strip()
    return tags, errors


def _render_template(path, context):
    data = Path(path).read_bytes()
    text = data.decode("utf-8")
    unknown = sorted({item for item in _PLACEHOLDER_RE.findall(text) if item not in context})
    if unknown:
        raise InputSetError("unknown template placeholders: {}".format(", ".join(unknown)), errors=[_error("UNKNOWN_PLACEHOLDER", "template contains unknown placeholder", placeholders=unknown)])
    try:
        rendered = _PLACEHOLDER_RE.sub(lambda match: _format_value(context[match.group(1)]), text)
    except ValueError as exc:
        raise InputSetError(
            "template placeholder has no valid value",
            errors=[_error("PLACEHOLDER_VALUE", str(exc))],
        ) from exc
    if _PLACEHOLDER_RE.search(rendered):
        raise InputSetError("unresolved template placeholder", errors=[_error("UNRESOLVED_PLACEHOLDER", "template placeholder remained after rendering")])
    return rendered, _sha256_bytes(data)


def _flatten_moments(value, *, vector):
    if value is None:
        return None
    if isinstance(value, str):
        tokens = value.replace(",", " ").split()
        result = []
        for token in tokens:
            match = _SHORTHAND_RE.fullmatch(token)
            if match:
                result.extend([float(match.group(2))] * int(match.group(1)))
            elif _NUMBER_RE.fullmatch(token):
                result.append(float(token))
            else:
                raise ValueError("invalid MAGMOM token {}".format(token))
        return result
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("MAGMOM values must be finite")
        return [result]
    if not isinstance(value, (list, tuple)):
        raise ValueError("MAGMOM must be a scalar/list or vector/list")
    if vector and value and all(isinstance(item, (list, tuple)) for item in value):
        result = []
        for item in value:
            if len(item) != 3:
                raise ValueError("each SOC MAGMOM vector must have three components")
            for component in item:
                if isinstance(component, bool):
                    raise ValueError("MAGMOM values must be numeric and not boolean")
                number = float(component)
                if not math.isfinite(number):
                    raise ValueError("MAGMOM values must be finite")
                result.append(number)
        return result
    if any(isinstance(item, (list, tuple)) for item in value):
        raise ValueError("collinear MAGMOM must contain scalar values")
    try:
        if any(isinstance(item, bool) for item in value):
            raise ValueError("MAGMOM values must be numeric and not boolean")
        result = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError("MAGMOM values must be numeric") from exc
    if not all(math.isfinite(item) for item in result):
        raise ValueError("MAGMOM values must be finite")
    return result


def _numeric_vector(value, length, label):
    if isinstance(value, str):
        tokens = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        tokens = list(value)
    else:
        raise ValueError("{} must contain {} numeric values".format(label, length))
    if len(tokens) != length:
        raise ValueError("{} must contain {} numeric values".format(label, length))
    result = []
    for token in tokens:
        if isinstance(token, bool):
            raise ValueError("{} values must be numeric and not boolean".format(label))
        try:
            number = float(token)
        except (TypeError, ValueError) as exc:
            raise ValueError("{} values must be numeric".format(label)) from exc
        if not math.isfinite(number):
            raise ValueError("{} values must be finite".format(label))
        result.append(number)
    return result


def _parse_bool(value):
    return str(value).strip().upper() in {".TRUE.", "TRUE", ".T.", "T", "1", "YES"}


def _parse_int(value):
    token = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", token):
        return None
    return int(token)


def _parse_finite_float(value):
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _pick(cfg, explicit, *names):
    if explicit is not None:
        return explicit
    for name in names:
        if name in cfg:
            return cfg[name]
    return None


def _profile_values(poscar_path, potcar_path, profile, kwargs):
    if isinstance(poscar_path, Mapping):
        if profile is not None:
            raise InputSetError("profile was provided twice", errors=[_error("PROFILE_DUPLICATE", "profile was provided as both positional and keyword argument")])
        profile = poscar_path
        poscar_path = None
    cfg = dict(profile or {})
    for section_name in ("physics", "electronic"):
        section = cfg.get(section_name)
        if isinstance(section, Mapping):
            merged = dict(section)
            merged.update(cfg)
            cfg = merged
    structure = cfg.get("structure")
    if isinstance(structure, Mapping):
        if poscar_path is None:
            poscar_path = structure.get("path") or structure.get("poscar") or structure.get("poscar_path")
    potcar = cfg.get("potcar")
    if isinstance(potcar, Mapping):
        if potcar_path is None:
            potcar_path = potcar.get("path") or potcar.get("source_path")
    values = {
        "poscar_path": _pick(cfg, poscar_path, "poscar", "poscar_path", "structure_path"),
        "potcar_path": _pick(cfg, potcar_path, "potcar_path", "potcar_source"),
        "system": _pick(cfg, kwargs.get("system"), "system", "SYSTEM"),
        "encut": _pick(cfg, kwargs.get("encut"), "encut", "ENCUT"),
        "ediff": _pick(cfg, kwargs.get("ediff"), "ediff", "EDIFF"),
        "ismeared": _pick(cfg, kwargs.get("ismeared"), "ismeared", "ismear", "ISMEAR"),
        "sigma": _pick(cfg, kwargs.get("sigma"), "sigma", "SIGMA"),
        "spin_mode": _pick(cfg, kwargs.get("spin_mode"), "spin_mode"),
        "magmom": _pick(cfg, kwargs.get("magmom"), "magmom", "MAGMOM"),
        "saxis": _pick(cfg, kwargs.get("saxis"), "saxis", "SAXIS"),
        "kpoints": _pick(cfg, kwargs.get("kpoints"), "kpoints"),
        "kpoints_mode": _pick(cfg, kwargs.get("kpoints_mode"), "kpoints_mode"),
        "kpoints_grid": _pick(cfg, kwargs.get("kpoints_grid"), "kpoints_grid", "grid"),
        "kpoints_shift": _pick(cfg, kwargs.get("kpoints_shift"), "kpoints_shift", "shift"),
        "kspacing": _pick(cfg, kwargs.get("kspacing"), "kspacing", "KSPACING"),
        "overrides": _pick(cfg, kwargs.get("overrides"), "overrides"),
        "strict": _pick(cfg, kwargs.get("strict"), "strict"),
        "source": _pick(cfg, kwargs.get("source"), "source", "generation_source"),
        "template_dir": _pick(cfg, kwargs.get("template_dir"), "template_dir"),
    }
    spin = cfg.get("spin")
    if isinstance(spin, str) and values["spin_mode"] is None:
        values["spin_mode"] = spin
    elif isinstance(spin, Mapping):
        if values["spin_mode"] is None:
            values["spin_mode"] = spin.get("mode")
        if values["magmom"] is None:
            values["magmom"] = spin.get("magmom")
        if values["saxis"] is None:
            values["saxis"] = spin.get("saxis")
    if isinstance(values["kpoints"], Mapping):
        kp = values["kpoints"]
        if values["kpoints_mode"] is None:
            values["kpoints_mode"] = kp.get("mode")
        if values["kpoints_grid"] is None:
            values["kpoints_grid"] = kp.get("grid") or kp.get("mesh")
        if values["kpoints_shift"] is None:
            values["kpoints_shift"] = kp.get("shift")
    return values


def _generation_source(value):
    """Return a compact source record for an input-set source."""

    if value is None:
        value = {"kind": "internal"}
    if not isinstance(value, Mapping):
        raise InputSetError(
            "generation source must be an object",
            errors=[_error("GENERATION_SOURCE_MISSING", "source must be {kind: internal} or omitted")],
        )
    kind = str(value.get("kind", "")).strip().lower()
    if kind == "imported":
        source_dir = value.get("source_dir")
        if source_dir is not None and not isinstance(source_dir, str):
            raise InputSetError(
                "imported source directory must be text",
                errors=[_error("GENERATION_SOURCE_DIR", "source.source_dir must be text when provided")],
            )
        result = {"kind": "imported", "status": "selected"}
        if source_dir:
            result["source_dir"] = str(Path(source_dir).resolve())
        return result
    if kind != "internal":
        raise InputSetError(
            "this deterministic builder only accepts the explicit internal renderer",
            errors=[_error(
                "GENERATION_SOURCE_UNSUPPORTED",
                "VASPKIT output must first be validated by the adapter before a future external-source input-set path can use it",
                kind=kind or None,
            )],
        )
    reason = value.get("reason", "")
    if not isinstance(reason, str):
        raise InputSetError(
            "internal renderer reason must be text",
            errors=[_error(
                "GENERATION_SOURCE_REASON",
                "source.reason must be text when provided",
            )],
        )
    return {
        "kind": "internal",
        "status": "selected",
        "reason": reason.strip(),
        "fallback": bool(value.get("fallback", False)),
    }


def import_vasp_input_set(input_dir):
    """Import one existing static VASP directory without rewriting its files.

    The imported contract keeps the original INCAR, POSCAR, and KPOINTS text
    and records hashes for all four required files.  POTCAR bytes stay outside
    the JSON contract, just as they do for internally rendered input sets.
    """

    root = Path(input_dir).expanduser()
    if not root.is_dir():
        raise InputSetError(
            "input directory does not exist: {}".format(root),
            errors=[_error("INPUT_DIR", "input_dir must be an existing directory")],
        )
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise InputSetError(
            "input directory cannot be resolved: {}".format(root),
            errors=[_error("INPUT_DIR", "input_dir cannot be resolved")],
        ) from exc

    errors = []
    raw = {}
    texts = {}
    paths = {}
    for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR"):
        path = root / name
        paths[name] = path
        if not path.is_file():
            errors.append(_error("MISSING_" + name, "{} is required in input_dir".format(name)))
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append(_error("UNREADABLE_" + name, "cannot read {}: {}".format(name, exc)))
            continue
        if not data:
            errors.append(_error("EMPTY_" + name, "{} must not be empty".format(name)))
            continue
        raw[name] = data
        if name != "POTCAR":
            try:
                texts[name] = data.decode("utf-8")
            except UnicodeDecodeError:
                errors.append(_error(name + "_UTF8", "{} must be UTF-8 text".format(name)))

    structure = None
    if "POSCAR" in texts:
        structure, poscar_errors = _parse_poscar(texts["POSCAR"])
        errors.extend(poscar_errors)

    tags = {}
    if "INCAR" in texts:
        tags, incar_errors = _parse_incar(texts["INCAR"])
        errors.extend(incar_errors)

    potcar = None
    if structure is not None and "POTCAR" in raw:
        potcar, potcar_errors = _parse_potcar(paths["POTCAR"], structure["species"])
        errors.extend(potcar_errors)

    kpoints_mode = None
    kpoints_grid = None
    kpoints_shift = None
    if "KPOINTS" in texts:
        lines = [line.strip() for line in texts["KPOINTS"].splitlines() if line.strip()]
        if len(lines) != 5:
            errors.append(_error("KPOINTS_REGULAR", "imported KPOINTS must be a five-line regular mesh"))
        else:
            if not re.fullmatch(r"[+]?[0-9]+", lines[1]) or int(lines[1]) != 0:
                errors.append(_error("KPOINTS_MODE", "imported KPOINTS must use an automatic mesh"))
            mode = lines[2].lower()
            if mode not in {"gamma", "monkhorst-pack"}:
                errors.append(_error("KPOINTS_MODE", "imported KPOINTS mode must be Gamma or Monkhorst-Pack"))
            else:
                kpoints_mode = "Gamma" if mode == "gamma" else "Monkhorst-Pack"
            mesh = lines[3].split()
            if len(mesh) != 3 or any(not re.fullmatch(r"[+]?[0-9]+", item) for item in mesh):
                errors.append(_error("KPOINTS_GRID", "imported KPOINTS mesh must contain three positive integers"))
            else:
                kpoints_grid = [int(item) for item in mesh]
                if any(item <= 0 for item in kpoints_grid):
                    errors.append(_error("KPOINTS_GRID", "imported KPOINTS mesh must contain three positive integers"))
            try:
                kpoints_shift = _numeric_vector(lines[4], 3, "KPOINTS shift")
            except ValueError as exc:
                errors.append(_error("KPOINTS_SHIFT", str(exc)))

    def require_tag(name, predicate, code, message):
        value = tags.get(name)
        if value is None or not predicate(value):
            errors.append(_error(code, message))

    require_tag("NSW", lambda value: _parse_int(value) == 0, "STATIC_NSW", "imported static input must set NSW = 0")
    require_tag("IBRION", lambda value: _parse_int(value) == -1, "STATIC_IBRION", "imported static input must set IBRION = -1")
    require_tag("ENCUT", lambda value: (_parse_finite_float(value) or 0) > 0, "ENCUT_VALUE", "ENCUT must be a positive finite number")
    require_tag("EDIFF", lambda value: (_parse_finite_float(value) or 0) > 0, "EDIFF_VALUE", "EDIFF must be a positive finite number")
    require_tag("ISMEAR", lambda value: _parse_int(value) is not None, "ISMEAR_VALUE", "ISMEAR must be an integer")
    if "SIGMA" in tags:
        require_tag("SIGMA", lambda value: (_parse_finite_float(value) is not None and _parse_finite_float(value) >= 0), "SIGMA_VALUE", "SIGMA must be a non-negative finite number")

    ispin = _parse_int(tags.get("ISPIN"))
    lsorbit = _parse_bool(tags.get("LSORBIT", ".FALSE."))
    lnoncollinear = _parse_bool(tags.get("LNONCOLLINEAR", ".FALSE."))
    if lsorbit or lnoncollinear:
        spin_mode = "soc"
        if ispin not in {None, 1}:
            errors.append(_error("SOC_ISPIN", "SOC input must use ISPIN = 1"))
        if not lsorbit or not lnoncollinear:
            errors.append(_error("SOC_TAGS", "SOC input must set LSORBIT and LNONCOLLINEAR true"))
    elif ispin in {None, 1}:
        spin_mode = "nonmagnetic"
    elif ispin == 2:
        spin_mode = "collinear"
    else:
        spin_mode = None
        errors.append(_error("SPIN_ISPIN", "ISPIN must be 1 or 2 for an imported input"))

    magmom_values = None
    natoms = structure["natoms"] if structure is not None else None
    if spin_mode == "nonmagnetic":
        if "MAGMOM" in tags:
            errors.append(_error("NONMAGNETIC_MAGMOM", "nonmagnetic input must not contain MAGMOM"))
    elif spin_mode == "collinear":
        try:
            magmom_values = _flatten_moments(tags.get("MAGMOM"), vector=False)
        except ValueError as exc:
            errors.append(_error("MAGMOM_SYNTAX", str(exc)))
        if magmom_values is None or natoms is None or len(magmom_values) != natoms:
            errors.append(_error("MAGMOM_COUNT", "collinear MAGMOM count must match the ion count", expected=natoms, actual=None if magmom_values is None else len(magmom_values)))
    elif spin_mode == "soc":
        try:
            magmom_values = _flatten_moments(tags.get("MAGMOM"), vector=True)
        except ValueError as exc:
            errors.append(_error("MAGMOM_SYNTAX", str(exc)))
        expected = 3 * natoms if natoms is not None else None
        if magmom_values is None or expected is None or len(magmom_values) != expected:
            errors.append(_error("MAGMOM_COUNT", "SOC MAGMOM count must be three components per ion", expected=expected, actual=None if magmom_values is None else len(magmom_values)))

    saxis_values = None
    if spin_mode == "soc":
        try:
            saxis_values = _numeric_vector(tags.get("SAXIS"), 3, "SAXIS")
            if all(abs(item) == 0 for item in saxis_values):
                raise ValueError("SAXIS must not be the zero vector")
        except ValueError as exc:
            errors.append(_error("SAXIS", str(exc)))
    elif "SAXIS" in tags:
        errors.append(_error("SAXIS_UNEXPECTED", "SAXIS is only valid for SOC input"))
    if lsorbit and (_parse_bool(tags.get("LSPIRAL", ".FALSE.")) or "QSPIRAL" in tags):
        errors.append(_error("SOC_SPIRAL_CONFLICT", "LSPIRAL/QSPIRAL cannot be combined with LSORBIT"))

    kspacing = None
    if "KSPACING" in tags:
        kspacing = _parse_finite_float(tags["KSPACING"])
        if kspacing is None or kspacing <= 0:
            errors.append(_error("KSPACING", "KSPACING must be a positive finite number"))

    if errors:
        raise InputSetError("invalid imported VASP input set", errors=errors)
    if kspacing is not None:
        warnings = [_warning("KPOINTS_KSPACING_REDUNDANT", "KPOINTS is present and effective; KSPACING is retained as redundant metadata")]
    else:
        warnings = []
    files = {
        name: _file_record(raw[name], source_path=paths[name])
        for name in ("INCAR", "POSCAR", "KPOINTS")
    }
    files["POTCAR"] = {
        "source_path": potcar["source_path"], "sha256": potcar["sha256"],
        "size_bytes": potcar["size_bytes"], "titel": potcar["titel"],
        "elements": potcar["elements"], "records": potcar["records"],
        "xc_families": potcar["xc_families"], "mixed_xc": potcar["mixed_xc"],
        "content_packaged": False,
    }
    input_manifest = [
        {"name": name, "sha256": files[name]["sha256"], "size_bytes": files[name]["size_bytes"]}
        for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR")
    ]
    return {
        "schema": INPUT_SET_SCHEMA,
        "status": "READY",
        "stage": "static",
        "source": _generation_source({"kind": "imported", "source_dir": str(root)}),
        "contents": {name: texts[name] for name in ("INCAR", "POSCAR", "KPOINTS")},
        "files": files,
        "structure": structure,
        "potcar": files["POTCAR"],
        "spin": {"mode": spin_mode, "magmom_count": len(magmom_values) if magmom_values is not None else 0, "saxis": saxis_values},
        "kpoints": {"mode": kpoints_mode, "grid": kpoints_grid, "shift": kpoints_shift, "kspacing": kspacing},
        "template_layers": [],
        "override_receipt": {"records": [], "review_required": False},
        "input_manifest": input_manifest,
        "input_manifest_sha256": canonical_hash(input_manifest),
        "warnings": warnings,
        "errors": [],
        "conflicts": [],
        "content_packaged": False,
        "review_required": False,
    }


def build_vasp_input_set(poscar_path=None, potcar_path=None, *, profile=None, system=None, encut=None,
                         ediff=None, ismear=None, ismeared=None, ismeer=None, sigma=None, spin_mode=None,
                         magmom=None, saxis=None, kpoints=None, kpoints_mode=None, kpoints_grid=None,
                         kpoints_shift=None, kspacing=None, overrides=None, strict=None,
                         source=None, template_dir=None):
    """Build a deterministic static VASP input-set contract.

    The first positional argument may also be a profile mapping.  Named
    arguments are intentionally verbose so a caller cannot accidentally rely
    on a hidden physics default.
    """

    supplied_ismear = [item for item in (ismear, ismeared, ismeer) if item is not None]
    if len({_format_value(item) for item in supplied_ismear}) > 1:
        raise InputSetError(
            "ISMEAR was provided more than once with different values",
            errors=[_error("ISMEAR_DUPLICATE", "ismear aliases disagree")],
        )
    ismear_value = supplied_ismear[0] if supplied_ismear else None
    values = _profile_values(poscar_path, potcar_path, profile, {
        "system": system, "encut": encut, "ediff": ediff, "ismeared": ismear_value,
        "sigma": sigma, "spin_mode": spin_mode, "magmom": magmom, "saxis": saxis,
        "kpoints": kpoints, "kpoints_mode": kpoints_mode, "kpoints_grid": kpoints_grid,
        "kpoints_shift": kpoints_shift, "kspacing": kspacing, "overrides": overrides,
        "strict": strict, "source": source, "template_dir": template_dir,
    })
    errors = []
    warnings = []
    generation_source = _generation_source(values["source"])
    if values["strict"] is None:
        values["strict"] = False
    if not isinstance(values["strict"], bool):
        raise InputSetError(
            "strict must be boolean",
            errors=[_error("STRICT_TYPE", "strict must be true or false")],
        )
    mandatory = (("system", "SYSTEM"), ("encut", "ENCUT"), ("ediff", "EDIFF"),
                 ("ismeared", "ISMEAR"), ("sigma", "SIGMA"), ("spin_mode", "spin_mode"),
                 ("kpoints_mode", "KPOINTS mode"), ("kpoints_grid", "KPOINTS grid"),
                 ("kpoints_shift", "KPOINTS shift"))
    for key, label in mandatory:
        if values[key] is None:
            errors.append(_error("MISSING_" + key.upper(), "{} must be explicitly provided".format(label)))
    if not isinstance(values["system"], str) or not values["system"].strip() or "\n" in values["system"] or "\r" in values["system"]:
        errors.append(_error("SYSTEM_VALUE", "SYSTEM must be a non-empty single-line string"))
    spin_mode_value = str(values["spin_mode"]).lower() if values["spin_mode"] is not None else None
    if spin_mode_value not in {"nonmagnetic", "collinear", "soc"}:
        errors.append(_error("SPIN_MODE", "spin_mode must be nonmagnetic, collinear, or soc"))
    poscar_text = _read_text(values["poscar_path"], "POSCAR", errors)
    structure, poscar_errors = _parse_poscar(poscar_text) if poscar_text is not None else (None, [])
    errors.extend(poscar_errors)
    potcar, potcar_errors = (None, [])
    if structure is not None and values["potcar_path"] is not None:
        potcar, potcar_errors = _parse_potcar(values["potcar_path"], structure["species"])
    elif values["potcar_path"] is None:
        errors.append(_error("MISSING_POTCAR", "POTCAR path is required"))
    errors.extend(potcar_errors)
    if structure is None:
        natoms = None
    else:
        natoms = structure["natoms"]
    try:
        raw_grid = list(values["kpoints_grid"]) if values["kpoints_grid"] is not None else None
        if raw_grid is None or len(raw_grid) != 3:
            raise ValueError
        grid = []
        for item in raw_grid:
            if isinstance(item, bool):
                raise ValueError
            if isinstance(item, int):
                normalized = item
            elif isinstance(item, float) and math.isfinite(item) and item.is_integer():
                normalized = int(item)
            elif isinstance(item, str) and re.fullmatch(r"[+]?[0-9]+", item.strip()):
                normalized = int(item)
            else:
                raise ValueError
            if normalized < 1:
                raise ValueError
            grid.append(normalized)
    except (TypeError, ValueError):
        errors.append(_error("KPOINTS_GRID", "KPOINTS grid must contain three positive integers"))
        grid = None
    mode = str(values["kpoints_mode"]).strip() if values["kpoints_mode"] is not None else None
    if mode and mode.lower() in {"line", "line-mode", "linemode"}:
        errors.append(_error("KPOINTS_MODE", "only explicit regular-mesh KPOINTS are supported"))
    elif mode and mode.lower() not in {"gamma", "monkhorst-pack", "monkhorst", "mp"}:
        errors.append(_error("KPOINTS_MODE", "KPOINTS mode must be Gamma or Monkhorst-Pack"))
    elif mode:
        mode = "Gamma" if mode.lower() == "gamma" else "Monkhorst-Pack"
    try:
        shift = _numeric_vector(values["kpoints_shift"], 3, "KPOINTS shift")
    except ValueError as exc:
        errors.append(_error("KPOINTS_SHIFT", str(exc)))
        shift = None
    if values["kspacing"] is not None:
        try:
            if isinstance(values["kspacing"], bool):
                raise ValueError
            spacing = float(values["kspacing"])
            if not math.isfinite(spacing) or spacing <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(_error("KSPACING", "KSPACING must be a positive finite number"))
            spacing = None
    else:
        spacing = None
    if errors:
        raise InputSetError("invalid VASP input-set request", errors=errors, warnings=warnings)
    magmom_values = None
    if spin_mode_value == "nonmagnetic":
        if values["magmom"] is not None:
            errors.append(_error("NONMAGNETIC_MAGMOM", "nonmagnetic input must not provide MAGMOM"))
    else:
        try:
            magmom_values = _flatten_moments(values["magmom"], vector=spin_mode_value == "soc")
        except ValueError as exc:
            errors.append(_error("MAGMOM_SYNTAX", str(exc)))
        expected_moments = natoms if spin_mode_value == "collinear" else (3 * natoms if natoms is not None else None)
        if magmom_values is None:
            errors.append(_error("MAGMOM_MISSING", "{} spin mode requires explicit MAGMOM".format(spin_mode_value)))
        elif expected_moments is not None and len(magmom_values) != expected_moments:
            errors.append(_error("MAGMOM_COUNT", "MAGMOM count does not match the spin contract", expected=expected_moments, actual=len(magmom_values)))
    saxis_values = None
    if spin_mode_value == "soc":
        try:
            saxis_values = _numeric_vector(values["saxis"], 3, "SAXIS")
            if all(abs(item) == 0 for item in saxis_values):
                raise ValueError("SAXIS must not be the zero vector")
        except ValueError as exc:
            errors.append(_error("SAXIS", str(exc)))
    elif values["saxis"] is not None:
        errors.append(_error("SAXIS_UNEXPECTED", "SAXIS is only valid for SOC input"))
    if errors:
        raise InputSetError("invalid VASP spin or k-point request", errors=errors, warnings=warnings)

    template_root = Path(values["template_dir"]).resolve() if values["template_dir"] else _TEMPLATE_DIR
    layer_names = ["common", "static"]
    if spin_mode_value == "nonmagnetic":
        layer_names.append("nonmagnetic")
    elif spin_mode_value == "collinear":
        layer_names.append("magnetic")
    elif spin_mode_value == "soc":
        layer_names.append("soc")
    context = {
        "SYSTEM": values["system"], "ENCUT": values["encut"], "EDIFF": values["ediff"],
        "ISMEAR": values["ismeared"], "SIGMA": values["sigma"],
        "ISPIN": 2 if spin_mode_value == "collinear" else 1,
        "MAGMOM": magmom_values if magmom_values is not None else None,
        "SAXIS": saxis_values,
        "KPOINTS_MODE": mode,
        "KPOINTS_GRID": grid,
        "KPOINTS_SHIFT": shift,
    }
    layer_records = []
    incar_parts = []
    for name in layer_names:
        path = template_root / (name + ".incar")
        if not path.is_file():
            errors.append(_error("TEMPLATE_MISSING", "missing template layer {}".format(path)))
            continue
        try:
            rendered, digest = _render_template(path, context)
        except InputSetError as exc:
            errors.extend(exc.errors or [_error("TEMPLATE_RENDER", str(exc))])
            continue
        layer_records.append({"name": name, "path": path.relative_to(template_root).as_posix(), "sha256": digest})
        incar_parts.append(rendered.rstrip("\n"))
    kpoints_path = template_root / "regular.kpoints"
    if not kpoints_path.is_file():
        errors.append(_error("TEMPLATE_MISSING", "missing regular KPOINTS template {}".format(kpoints_path)))
        kpoints_content = ""
    else:
        try:
            kpoints_content, kpoints_digest = _render_template(kpoints_path, context)
            layer_records.append({"name": "regular-kpoints", "path": kpoints_path.relative_to(template_root).as_posix(), "sha256": kpoints_digest})
        except InputSetError as exc:
            errors.extend(exc.errors or [_error("TEMPLATE_RENDER", str(exc))])
            kpoints_content = ""
    if errors:
        raise InputSetError("invalid VASP templates", errors=errors, warnings=warnings)
    incar_content = "\n".join(incar_parts).rstrip() + "\n"
    tags, incar_errors = _parse_incar(incar_content)
    errors.extend(incar_errors)
    if spacing is not None:
        if "KSPACING" not in tags:
            tags["KSPACING"] = _format_value(spacing)
            incar_content = _render_tags(tags)

    explicit_override_records = values["overrides"] or []
    if not isinstance(explicit_override_records, (list, tuple)):
        errors.append(_error("OVERRIDES_TYPE", "overrides must be a list of structured records"))
        explicit_override_records = []
    seen_overrides = set()
    receipt = []
    physical_review_required = False
    for record in explicit_override_records:
        if not isinstance(record, Mapping):
            errors.append(_error("OVERRIDE_RECORD", "each override must be an object"))
            continue
        required = [field for field in ("tag", "new_value", "reason", "physical_choice") if field not in record]
        if required:
            errors.append(_error("OVERRIDE_FIELDS", "override is missing required fields", fields=required))
            continue
        tag = str(record["tag"]).strip().upper()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", tag):
            errors.append(_error("OVERRIDE_TAG", "override tag is invalid: {}".format(tag)))
            continue
        if tag in seen_overrides:
            errors.append(_error("OVERRIDE_DUPLICATE", "duplicate override tag {}".format(tag)))
            continue
        seen_overrides.add(tag)
        old_value = tags.get(tag)
        try:
            new_value = _format_value(record["new_value"])
        except ValueError as exc:
            errors.append(_error("OVERRIDE_VALUE", "invalid value for {}: {}".format(tag, exc)))
            continue
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            errors.append(_error("OVERRIDE_REASON", "override reason must be a non-empty string", tag=tag))
            continue
        if not isinstance(record["physical_choice"], bool):
            errors.append(_error("OVERRIDE_PHYSICAL_CHOICE", "physical_choice must be boolean", tag=tag))
            continue
        physical_choice = record["physical_choice"]
        if tag != "SYSTEM" and physical_choice is not True:
            errors.append(_error(
                "OVERRIDE_PHYSICAL_CHOICE_REQUIRED",
                "INCAR override {} must be marked as a physical choice".format(tag),
            ))
            continue
        review_required = physical_choice
        physical_review_required = physical_review_required or review_required
        item = {
            "tag": tag,
            "old_value": old_value,
            "new_value": new_value,
            "reason": record["reason"].strip(),
            "physical_choice": physical_choice,
            "review_required": review_required,
        }
        item["source"] = str(record.get("source", "user"))
        receipt.append(item)
        if tag in {"IBRION", "NSW"}:
            errors.append(_error("STATIC_SEMANTICS_OVERRIDE", "static semantic tag {} cannot be overridden".format(tag)))
        else:
            tags[tag] = new_value
    override_receipt = {
        "records": receipt,
        "review_required": physical_review_required,
    }
    incar_content = _render_tags(tags)
    # The checks below operate on the final override-resolved tags, so a
    # malformed override cannot hide behind a valid template layer.
    if _parse_int(tags.get("NSW")) is None or _parse_int(tags.get("NSW")) != 0:
        errors.append(_error("STATIC_NSW", "static input must set NSW = 0"))
    if _parse_int(tags.get("IBRION")) is None or _parse_int(tags.get("IBRION")) != -1:
        errors.append(_error("STATIC_IBRION", "static input must set IBRION = -1"))
    if spin_mode_value == "nonmagnetic" and _parse_int(tags.get("ISPIN")) != 1:
        errors.append(_error("SPIN_ISPIN", "nonmagnetic input must set ISPIN = 1"))
    if spin_mode_value == "collinear" and _parse_int(tags.get("ISPIN")) != 2:
        errors.append(_error("SPIN_ISPIN", "collinear magnetic input must set ISPIN = 2"))
    if spin_mode_value == "soc" and _parse_int(tags.get("ISPIN")) != 1:
        errors.append(_error(
            "SOC_ISPIN",
            "noncollinear/SOC input must not set ISPIN = 2; this contract writes ISPIN = 1 for VASP-version compatibility",
        ))
    try:
        final_moments = _flatten_moments(tags.get("MAGMOM"), vector=False)
    except ValueError as exc:
        final_moments = None
        errors.append(_error("MAGMOM_SYNTAX", str(exc)))
    if spin_mode_value == "nonmagnetic":
        if "MAGMOM" in tags:
            errors.append(_error("NONMAGNETIC_MAGMOM", "nonmagnetic input must not contain MAGMOM"))
    else:
        expected_final_moments = natoms if spin_mode_value == "collinear" else 3 * natoms
        if final_moments is None or len(final_moments) != expected_final_moments:
            errors.append(_error(
                "MAGMOM_COUNT",
                "final MAGMOM count does not match the spin contract",
                expected=expected_final_moments,
                actual=None if final_moments is None else len(final_moments),
            ))
    lsorbit = _parse_bool(tags.get("LSORBIT", ".FALSE."))
    lnoncollinear = _parse_bool(tags.get("LNONCOLLINEAR", ".FALSE."))
    if spin_mode_value == "soc":
        if not lsorbit or not lnoncollinear:
            errors.append(_error("SOC_TAGS", "SOC input must set LSORBIT and LNONCOLLINEAR true"))
    elif lsorbit or lnoncollinear:
        errors.append(_error("SOC_TAGS", "LSORBIT/LNONCOLLINEAR requires spin_mode=soc"))
    if spin_mode_value == "soc":
        try:
            final_saxis = _numeric_vector(tags.get("SAXIS"), 3, "SAXIS")
            if all(abs(item) == 0 for item in final_saxis):
                raise ValueError("SAXIS must not be the zero vector")
        except ValueError as exc:
            errors.append(_error("SAXIS", str(exc)))
    if lsorbit and (_parse_bool(tags.get("LSPIRAL", ".FALSE.")) or "QSPIRAL" in tags):
        errors.append(_error("SOC_SPIRAL_CONFLICT", "LSPIRAL/QSPIRAL cannot be combined with LSORBIT"))
    if "KSPACING" in tags:
        if values["strict"]:
            errors.append(_error("KPOINTS_KSPACING_REDUNDANT", "KPOINTS is present and effective; KSPACING is redundant in strict mode"))
        elif not any(item["code"] == "KPOINTS_KSPACING_REDUNDANT" for item in warnings):
            warnings.append(_warning("KPOINTS_KSPACING_REDUNDANT", "KPOINTS is present and effective; KSPACING is retained as redundant metadata"))
    encut_value = _parse_finite_float(tags.get("ENCUT"))
    if encut_value is None or encut_value <= 0:
        errors.append(_error("ENCUT_VALUE", "ENCUT must be a positive finite number"))
    ediff_value = _parse_finite_float(tags.get("EDIFF"))
    if ediff_value is None or ediff_value <= 0:
        errors.append(_error("EDIFF_VALUE", "EDIFF must be a positive finite number"))
    if _parse_int(tags.get("ISMEAR")) is None:
        errors.append(_error("ISMEAR_VALUE", "ISMEAR must be an integer"))
    sigma_value = _parse_finite_float(tags.get("SIGMA"))
    if sigma_value is None or sigma_value < 0:
        errors.append(_error("SIGMA_VALUE", "SIGMA must be a non-negative finite number"))
    detected_enmax = [
        record["enmax"] for record in potcar.get("records", [])
        if isinstance(record.get("enmax"), (int, float)) and math.isfinite(record["enmax"])
    ]
    if encut_value is not None and detected_enmax and encut_value < max(detected_enmax):
        warnings.append(_warning(
            "ENCUT_BELOW_POTCAR_ENMAX",
            "ENCUT is below the maximum detected POTCAR ENMAX; retain only with explicit convergence evidence",
            encut=encut_value,
            max_enmax=max(detected_enmax),
        ))
    if errors:
        raise InputSetError("invalid final VASP input set", errors=errors, warnings=warnings)

    poscar_bytes = poscar_text.encode("utf-8")
    kpoints_bytes = kpoints_content.encode("utf-8")
    files = {
        "INCAR": _file_record(incar_content.encode("utf-8")),
        "POSCAR": _file_record(poscar_bytes, source_path=values["poscar_path"]),
        "KPOINTS": _file_record(kpoints_bytes),
        "POTCAR": {
            "source_path": potcar["source_path"], "sha256": potcar["sha256"],
            "size_bytes": potcar["size_bytes"], "titel": potcar["titel"],
            "elements": potcar["elements"], "records": potcar["records"],
            "xc_families": potcar["xc_families"], "mixed_xc": potcar["mixed_xc"],
            "content_packaged": False,
        },
    }
    input_manifest = [
        {
            "name": name,
            "sha256": files[name]["sha256"],
            "size_bytes": files[name]["size_bytes"],
        }
        for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR")
    ]
    result = {
        "schema": INPUT_SET_SCHEMA,
        "status": "READY",
        "stage": "static",
        "source": generation_source,
        "contents": {"INCAR": incar_content, "POSCAR": poscar_text, "KPOINTS": kpoints_content},
        "files": files,
        "structure": structure,
        "potcar": files["POTCAR"],
        "spin": {"mode": spin_mode_value, "magmom_count": len(magmom_values) if magmom_values is not None else 0, "saxis": saxis_values},
        "kpoints": {"mode": mode, "grid": grid, "shift": shift, "kspacing": spacing},
        "template_layers": layer_records,
        "override_receipt": override_receipt,
        "input_manifest": input_manifest,
        "input_manifest_sha256": canonical_hash(input_manifest),
        "warnings": warnings,
        "errors": [],
        "conflicts": [],
        "content_packaged": False,
        "review_required": physical_review_required,
    }
    return result


def _render_tags(tags):
    return "".join("{} = {}\n".format(tag, tags[tag]) for tag in tags)


def _validate_file_record(name, content, declared, errors):
    if not isinstance(declared, Mapping):
        errors.append(_error("FILE_RECORD", "missing file record for {}".format(name)))
        return
    if not isinstance(content, str):
        errors.append(_error("FILE_CONTENT", "{} content is not text".format(name)))
        return
    actual = _file_record(content.encode("utf-8"))
    if declared.get("sha256") != actual["sha256"] or declared.get("size_bytes") != actual["size_bytes"]:
        errors.append(_error("FILE_HASH", "{} content hash or size does not match its manifest".format(name)))


def validate_input_set(input_set):
    """Validate a previously returned contract without reading source files."""

    errors = []
    warnings = []
    if not isinstance(input_set, Mapping):
        errors.append(_error("CONTRACT_TYPE", "input set must be an object"))
        return {"schema": VALIDATION_SCHEMA, "status": "INVALID", "valid": False, "errors": errors, "warnings": warnings}
    if input_set.get("schema") != INPUT_SET_SCHEMA:
        errors.append(_error("CONTRACT_SCHEMA", "input set schema is not {}".format(INPUT_SET_SCHEMA)))
    try:
        generation_source = _generation_source(input_set.get("source"))
    except InputSetError as exc:
        generation_source = {"kind": None}
        errors.extend(exc.errors or [_error("GENERATION_SOURCE", str(exc))])
    source_kind = generation_source.get("kind")
    contents = input_set.get("contents")
    files = input_set.get("files")
    if not isinstance(contents, Mapping) or not isinstance(files, Mapping):
        errors.append(_error("CONTRACT_FILES", "input set must contain contents and files objects"))
    else:
        for name in ("INCAR", "POSCAR", "KPOINTS"):
            if name not in contents:
                errors.append(_error("CONTENT_MISSING", "missing {} content".format(name)))
            else:
                _validate_file_record(name, contents[name], files.get(name), errors)
    if isinstance(contents, Mapping) and isinstance(contents.get("POSCAR"), str):
        structure, poscar_errors = _parse_poscar(contents["POSCAR"])
        errors.extend(poscar_errors)
    else:
        structure = None
    if isinstance(contents, Mapping) and isinstance(contents.get("INCAR"), str):
        tags, incar_errors = _parse_incar(contents["INCAR"])
        errors.extend(incar_errors)
    else:
        tags = {}
    if isinstance(contents, Mapping) and isinstance(contents.get("KPOINTS"), str):
        lines = [line.strip() for line in contents["KPOINTS"].splitlines() if line.strip()]
        parsed_kpoints = None
        if len(lines) != 5:
            errors.append(_error("KPOINTS_SYNTAX", "regular KPOINTS must have five non-empty lines"))
        else:
            try:
                if not re.fullmatch(r"[+]?0", lines[1]):
                    raise ValueError
                normalized_mode = lines[2].lower()
                if normalized_mode not in {"gamma", "monkhorst-pack"}:
                    raise ValueError
                mesh_tokens = lines[3].split()
                if len(mesh_tokens) != 3 or any(
                    not re.fullmatch(r"[+]?[0-9]+", item) for item in mesh_tokens
                ):
                    raise ValueError
                mesh = [int(item) for item in mesh_tokens]
                shift = [float(item) for item in lines[4].split()]
                if len(mesh) != 3 or any(item <= 0 for item in mesh) or len(shift) != 3:
                    raise ValueError
                if not all(math.isfinite(item) for item in shift):
                    raise ValueError
                parsed_kpoints = {
                    "mode": "Gamma" if normalized_mode == "gamma" else "Monkhorst-Pack",
                    "grid": mesh,
                    "shift": shift,
                }
            except (TypeError, ValueError):
                errors.append(_error("KPOINTS_SYNTAX", "regular KPOINTS mesh or shift is invalid"))
        declared_kpoints = input_set.get("kpoints")
        if not isinstance(declared_kpoints, Mapping):
            errors.append(_error("KPOINTS_CONTRACT", "input set has no structured KPOINTS contract"))
        elif parsed_kpoints is not None:
            for key, value in parsed_kpoints.items():
                if declared_kpoints.get(key) != value:
                    errors.append(_error(
                        "KPOINTS_CONTRACT",
                        "structured KPOINTS {} does not match KPOINTS content".format(key),
                    ))
            declared_spacing = declared_kpoints.get("kspacing")
            incar_spacing = _parse_finite_float(tags.get("KSPACING")) if "KSPACING" in tags else None
            if declared_spacing != incar_spacing:
                errors.append(_error(
                    "KPOINTS_CONTRACT",
                    "structured kspacing does not match final INCAR",
                ))
    potcar = input_set.get("potcar", files.get("POTCAR") if isinstance(files, Mapping) else None)
    if not isinstance(potcar, Mapping) or potcar.get("content_packaged") is not False:
        errors.append(_error("POTCAR_PROVENANCE", "POTCAR provenance must explicitly set content_packaged=false"))
    elif "content" in potcar or (isinstance(contents, Mapping) and "POTCAR" in contents):
        errors.append(_error("POTCAR_CONTENT_PACKAGED", "POTCAR bytes or text must not be embedded in the contract"))
    if structure is not None and isinstance(potcar, Mapping) and potcar.get("elements") != structure.get("species"):
        errors.append(_error("POTCAR_SPECIES_ORDER", "POTCAR element order does not match POSCAR"))
    spin = input_set.get("spin", {})
    mode = spin.get("mode") if isinstance(spin, Mapping) else None
    natoms = structure.get("natoms") if isinstance(structure, Mapping) else None
    effective_ispin = _parse_int(tags.get("ISPIN"))
    if source_kind == "imported" and effective_ispin is None:
        # VASP defaults ISPIN to 1.  Imported text is kept verbatim, so this
        # effective value must not be written back into INCAR.
        effective_ispin = 1
    try:
        final_moments = _flatten_moments(tags.get("MAGMOM"), vector=False)
    except ValueError as exc:
        final_moments = None
        errors.append(_error("MAGMOM_SYNTAX", str(exc)))
    if mode == "nonmagnetic":
        if effective_ispin != 1 or "MAGMOM" in tags:
            errors.append(_error("SPIN_CONTRACT", "nonmagnetic contract and final INCAR disagree"))
    elif mode == "collinear":
        if effective_ispin != 2 or natoms is None or final_moments is None or len(final_moments) != natoms:
            errors.append(_error("MAGMOM_COUNT", "collinear contract requires one MAGMOM value per ion"))
    if mode == "soc" and (effective_ispin != 1 or not _parse_bool(tags.get("LSORBIT", ".FALSE.")) or not _parse_bool(tags.get("LNONCOLLINEAR", ".FALSE."))):
        errors.append(_error("SOC_TAGS", "SOC contract and final INCAR disagree"))
    if mode == "soc" and (natoms is None or final_moments is None or len(final_moments) != 3 * natoms):
        errors.append(_error("MAGMOM_COUNT", "SOC contract requires three MAGMOM components per ion"))
    if mode not in {"nonmagnetic", "collinear", "soc"}:
        errors.append(_error("SPIN_MODE", "unknown spin mode in input-set contract"))
    if _parse_bool(tags.get("LSORBIT", ".FALSE.")) and (
            _parse_bool(tags.get("LSPIRAL", ".FALSE.")) or "QSPIRAL" in tags):
        errors.append(_error("SOC_SPIRAL_CONFLICT", "LSPIRAL/QSPIRAL cannot be combined with LSORBIT"))
    if _parse_int(tags.get("NSW")) != 0 or _parse_int(tags.get("IBRION")) != -1:
        errors.append(_error("STATIC_SEMANTICS", "static INCAR semantics are not present"))
    system_value = tags.get("SYSTEM")
    if source_kind != "imported" and (
        not isinstance(system_value, str) or not system_value.strip()
        or "\n" in system_value or "\r" in system_value
    ):
        errors.append(_error("SYSTEM_VALUE", "SYSTEM must be a non-empty single-line string"))
    encut_value = _parse_finite_float(tags.get("ENCUT"))
    if encut_value is None or encut_value <= 0:
        errors.append(_error("ENCUT_VALUE", "ENCUT must be a positive finite number"))
    ediff_value = _parse_finite_float(tags.get("EDIFF"))
    if ediff_value is None or ediff_value <= 0:
        errors.append(_error("EDIFF_VALUE", "EDIFF must be a positive finite number"))
    if _parse_int(tags.get("ISMEAR")) is None:
        errors.append(_error("ISMEAR_VALUE", "ISMEAR must be an integer"))
    sigma_value = _parse_finite_float(tags.get("SIGMA"))
    if source_kind != "imported" and (sigma_value is None or sigma_value < 0):
        errors.append(_error("SIGMA_VALUE", "SIGMA must be a non-negative finite number"))
    elif source_kind == "imported" and "SIGMA" in tags and (sigma_value is None or sigma_value < 0):
        errors.append(_error("SIGMA_VALUE", "SIGMA must be a non-negative finite number"))
    if "KSPACING" in tags:
        warnings.append(_warning("KPOINTS_KSPACING_REDUNDANT", "KPOINTS is present and effective; KSPACING is redundant"))
    override_receipt = input_set.get("override_receipt")
    if not isinstance(override_receipt, Mapping):
        errors.append(_error("OVERRIDE_RECEIPT", "input set has no structured override receipt"))
    else:
        records = override_receipt.get("records")
        if not isinstance(records, list) or any(not isinstance(item, Mapping) for item in records):
            errors.append(_error("OVERRIDE_RECEIPT", "override receipt records must be a list of objects"))
        else:
            seen_tags = set()
            expected_review = False
            for item in records:
                tag = item.get("tag")
                physical_choice = item.get("physical_choice")
                review_required = item.get("review_required")
                new_value = item.get("new_value")
                if (
                    not isinstance(tag, str)
                    or tag != tag.upper()
                    or not re.fullmatch(r"[A-Z][A-Z0-9_-]*", tag)
                    or tag in seen_tags
                ):
                    errors.append(_error("OVERRIDE_RECORD", "override record tag is invalid or duplicated"))
                    continue
                seen_tags.add(tag)
                if not isinstance(physical_choice, bool):
                    errors.append(_error("OVERRIDE_RECORD", "override physical_choice must be boolean", tag=tag))
                    continue
                if tag != "SYSTEM" and physical_choice is not True:
                    errors.append(_error("REVIEW_REQUIRED", "non-SYSTEM overrides are physical choices", tag=tag))
                if review_required is not physical_choice:
                    errors.append(_error("REVIEW_REQUIRED", "override review flag must equal physical_choice", tag=tag))
                if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                    errors.append(_error("OVERRIDE_RECORD", "override reason must be non-empty", tag=tag))
                if not isinstance(item.get("source"), str) or not item["source"].strip():
                    errors.append(_error("OVERRIDE_RECORD", "override source must be non-empty", tag=tag))
                if not isinstance(new_value, str):
                    errors.append(_error("OVERRIDE_RECORD", "override new value must be normalized text", tag=tag))
                expected_review = expected_review or physical_choice
            if (
                override_receipt.get("review_required") is not expected_review
                or input_set.get("review_required") is not expected_review
            ):
                errors.append(_error("REVIEW_REQUIRED", "review-required flags do not match override records"))
    if isinstance(files, Mapping):
        try:
            manifest = [
                {
                    "name": name,
                    "sha256": files[name]["sha256"],
                    "size_bytes": files[name]["size_bytes"],
                }
                for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR")
            ]
        except (KeyError, TypeError):
            errors.append(_error("INPUT_MANIFEST", "input file records are incomplete"))
        else:
            if input_set.get("input_manifest") != manifest or input_set.get("input_manifest_sha256") != canonical_hash(manifest):
                errors.append(_error("INPUT_MANIFEST", "input manifest or hash does not match file records"))
    valid = not errors
    return {
        "schema": VALIDATION_SCHEMA,
        "status": "VALID" if valid else "INVALID",
        "audit_status": "PASS" if valid else "FAIL",
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
    }


__all__ = [
    "INPUT_SET_SCHEMA", "InputSetError", "build_vasp_input_set", "import_vasp_input_set", "canonical_hash",
    "canonical_json", "validate_input_set",
]
