#!/usr/bin/env python3
"""Analyze local tetrahedral/octahedral distortions in a VASP POSCAR.

The default configuration reproduces the original FeO4/CrO6 use case, while
configuration files can define other center/ligand combinations with
coordination 4 (tetrahedron) or 6 (octahedron).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy.spatial import ConvexHull
from scipy.spatial import QhullError

VERSION = "0.2.0"
DEFAULT_CONFIG = {
    "centers": {
        "Cr": {"coordination": 6, "ligand": "O", "geometry": "octahedron"},
        "Fe": {"coordination": 4, "ligand": "O", "geometry": "tetrahedron"},
    },
    "search_radius_angstrom": 3.0,
    "shell_gap_warning_angstrom": 0.15,
}
IDEAL_ANGLE = {4: math.degrees(math.acos(-1.0 / 3.0)), 6: 90.0}
SUPPORTED_COORDINATION = {4, 6}


class AnalysisError(RuntimeError):
    """Expected input/geometry error with a user-facing message."""


@dataclass(frozen=True)
class PoscarStructure:
    comment: str
    lattice: np.ndarray  # row vectors, angstrom
    species: list[str]
    frac_coords: np.ndarray

    @property
    def cart_coords(self) -> np.ndarray:
        return self.frac_coords @ self.lattice

    @property
    def volume(self) -> float:
        return float(abs(np.linalg.det(self.lattice)))

    @property
    def formula(self) -> str:
        counts: dict[str, int] = {}
        order: list[str] = []
        for symbol in self.species:
            if symbol not in counts:
                counts[symbol] = 0
                order.append(symbol)
            counts[symbol] += 1
        return "".join(symbol + (str(counts[symbol]) if counts[symbol] != 1 else "") for symbol in order)


@dataclass(frozen=True)
class Neighbor:
    site_index: int
    species: str
    image: tuple[int, int, int]
    vector: np.ndarray
    distance: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_poscar(path: Path) -> PoscarStructure:
    """Parse a VASP5/6 POSCAR with Direct or Cartesian coordinates.

    Supported: ordinary scale factors (positive) and VASP negative-volume scale.
    Unsupported by contract: VASP4 files without species names and per-species
    scale factors.
    """
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    if len(raw_lines) < 8:
        raise AnalysisError(f"POSCAR is too short: {path}")

    comment = raw_lines[0].strip()
    try:
        scale_tokens = raw_lines[1].split()
        if len(scale_tokens) != 1:
            raise AnalysisError("Only a single POSCAR scale factor is supported")
        scale_raw = float(scale_tokens[0])
        raw_lattice = np.array(
            [[float(x) for x in raw_lines[i].split()[:3]] for i in range(2, 5)],
            dtype=float,
        )
    except (ValueError, IndexError) as exc:
        raise AnalysisError("Failed to parse POSCAR scale/lattice") from exc

    if abs(np.linalg.det(raw_lattice)) < 1e-12:
        raise AnalysisError("POSCAR lattice is singular")
    if scale_raw == 0:
        raise AnalysisError("POSCAR scale factor cannot be zero")
    if scale_raw > 0:
        scale = scale_raw
    else:
        target_volume = abs(scale_raw)
        scale = (target_volume / abs(np.linalg.det(raw_lattice))) ** (1.0 / 3.0)
    lattice = raw_lattice * scale

    species_names = raw_lines[5].split()
    if not species_names or all(_is_int(token) for token in species_names):
        raise AnalysisError(
            "VASP4 POSCAR without element symbols is unsupported; use a VASP5/6 POSCAR"
        )
    try:
        counts = [int(token) for token in raw_lines[6].split()]
    except ValueError as exc:
        raise AnalysisError("Failed to parse POSCAR atom counts") from exc
    if len(species_names) != len(counts) or any(count <= 0 for count in counts):
        raise AnalysisError("POSCAR species/count lines are inconsistent")

    cursor = 7
    if cursor >= len(raw_lines):
        raise AnalysisError("POSCAR is missing coordinate mode")
    if raw_lines[cursor].strip().lower().startswith("s"):
        cursor += 1
    if cursor >= len(raw_lines):
        raise AnalysisError("POSCAR is missing coordinate mode")
    mode = raw_lines[cursor].strip().lower()
    cursor += 1
    is_direct = mode.startswith("d")
    is_cartesian = mode.startswith("c") or mode.startswith("k")
    if not (is_direct or is_cartesian):
        raise AnalysisError(f"Unknown POSCAR coordinate mode: {raw_lines[cursor - 1]!r}")

    total_atoms = sum(counts)
    if len(raw_lines) < cursor + total_atoms:
        raise AnalysisError("POSCAR contains fewer coordinate rows than atom count")
    coords = []
    for line_no, line in enumerate(raw_lines[cursor : cursor + total_atoms], start=cursor + 1):
        tokens = line.split()
        if len(tokens) < 3:
            raise AnalysisError(f"Coordinate row {line_no} has fewer than 3 values")
        try:
            coords.append([float(tokens[0]), float(tokens[1]), float(tokens[2])])
        except ValueError as exc:
            raise AnalysisError(f"Invalid coordinate row {line_no}") from exc
    coords_array = np.asarray(coords, dtype=float)

    if is_direct:
        frac_coords = coords_array
    else:
        cart_coords = coords_array * scale
        frac_coords = cart_coords @ np.linalg.inv(lattice)

    species: list[str] = []
    for symbol, count in zip(species_names, counts):
        species.extend([symbol] * count)
    return PoscarStructure(comment, lattice, species, frac_coords)


def _is_int(text: str) -> bool:
    try:
        int(text)
        return True
    except ValueError:
        return False


def load_config(path: Path | None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is not None:
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(f"Failed to read config: {path}") from exc
        if not isinstance(user, dict):
            raise AnalysisError("Config root must be a JSON object")
        config.update({k: v for k, v in user.items() if k != "centers"})
        if "centers" in user:
            config["centers"] = user["centers"]

    centers = config.get("centers")
    if not isinstance(centers, dict) or not centers:
        raise AnalysisError("Config 'centers' must be a non-empty object")
    for center, spec in centers.items():
        if not isinstance(spec, dict):
            raise AnalysisError(f"Config for center {center!r} must be an object")
        coordination = spec.get("coordination")
        ligand = spec.get("ligand")
        if coordination not in SUPPORTED_COORDINATION:
            raise AnalysisError(
                f"Center {center}: coordination must be 4 or 6, got {coordination!r}"
            )
        if not isinstance(ligand, str) or not ligand:
            raise AnalysisError(f"Center {center}: ligand must be a non-empty symbol")
        expected_geometry = "tetrahedron" if coordination == 4 else "octahedron"
        geometry = spec.get("geometry", expected_geometry)
        if geometry != expected_geometry:
            raise AnalysisError(
                f"Center {center}: coordination {coordination} requires geometry {expected_geometry!r}"
            )
        spec["geometry"] = expected_geometry

    for key in ("search_radius_angstrom", "shell_gap_warning_angstrom"):
        try:
            config[key] = float(config[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalysisError(f"Config {key!r} must be numeric") from exc
        if config[key] <= 0:
            raise AnalysisError(f"Config {key!r} must be > 0")
    return config


def angle_degrees(vector_a: np.ndarray, vector_b: np.ndarray) -> float:
    norm = np.linalg.norm(vector_a) * np.linalg.norm(vector_b)
    if norm <= 1e-15:
        raise AnalysisError("Encountered zero-length bond vector")
    cosine = np.dot(vector_a, vector_b) / norm
    return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))


def pairings(items: tuple[int, ...]) -> Iterable[tuple[tuple[int, int], ...]]:
    """Yield all unique complete pairings of an even-length tuple."""
    if not items:
        yield ()
        return
    first = items[0]
    for position in range(1, len(items)):
        second = items[position]
        remainder = items[1:position] + items[position + 1 :]
        for rest in pairings(remainder):
            yield ((first, second),) + rest


def best_opposite_pairs(vectors: np.ndarray) -> tuple[tuple[int, int], ...]:
    """Pair six octahedral bonds by maximizing total trans-angle sum."""
    if len(vectors) != 6:
        raise AnalysisError("Opposite-pair assignment requires six vectors")
    return max(
        pairings(tuple(range(6))),
        key=lambda pairs_: sum(angle_degrees(vectors[i], vectors[j]) for i, j in pairs_),
    )


def ideal_bond_length(coordination: int, volume: float) -> float:
    if volume <= 0:
        raise AnalysisError("Polyhedron volume must be positive")
    if coordination == 6:
        return (3.0 * volume / 4.0) ** (1.0 / 3.0)
    if coordination == 4:
        return (9.0 * math.sqrt(3.0) * volume / 8.0) ** (1.0 / 3.0)
    raise AnalysisError("Only tetrahedral and octahedral sites are supported")


def canonical_axis(vector: np.ndarray) -> np.ndarray:
    """Fix the arbitrary sign of an axis for deterministic output."""
    out = np.array(vector, dtype=float)
    out /= np.linalg.norm(out)
    for value in out:
        if abs(value) > 1e-12:
            if value < 0:
                out = -out
            break
    return out


def analyze_vectors(vectors: np.ndarray, coordination: int) -> dict[str, Any]:
    """Calculate geometry-only metrics from center-to-ligand vectors."""
    vectors = np.asarray(vectors, dtype=float)
    if vectors.shape != (coordination, 3):
        raise AnalysisError(
            f"Expected vector array shape ({coordination}, 3), got {vectors.shape}"
        )
    if coordination not in SUPPORTED_COORDINATION:
        raise AnalysisError("Only coordination 4 and 6 are supported")

    lengths = np.linalg.norm(vectors, axis=1)
    if np.any(lengths <= 1e-12):
        raise AnalysisError("Zero-length center-ligand bond detected")
    mean_length = float(np.mean(lengths))
    bond_std = float(np.std(lengths))
    delta_d = float(np.mean(((lengths - mean_length) / mean_length) ** 2))
    baur_d = float(np.mean(np.abs(lengths - mean_length) / mean_length))

    try:
        hull_volume = float(ConvexHull(vectors).volume)
    except QhullError as exc:
        raise AnalysisError("Ligand vertices are degenerate; polyhedron volume is undefined") from exc
    d0 = ideal_bond_length(coordination, hull_volume)
    quadratic_elongation = float(np.mean((lengths / d0) ** 2))

    all_angles = [
        angle_degrees(vectors[i], vectors[j])
        for i, j in itertools.combinations(range(coordination), 2)
    ]
    opposite_pairs: tuple[tuple[int, int], ...] = ()
    trans_angles: list[float] = []
    if coordination == 6:
        opposite_pairs = best_opposite_pairs(vectors)
        trans_sets = {frozenset(pair) for pair in opposite_pairs}
        angle_values = [
            angle_degrees(vectors[i], vectors[j])
            for i, j in itertools.combinations(range(coordination), 2)
            if frozenset((i, j)) not in trans_sets
        ]
        trans_angles = [angle_degrees(vectors[i], vectors[j]) for i, j in opposite_pairs]
    else:
        angle_values = all_angles

    ideal_angle = IDEAL_ANGLE[coordination]
    deviations = np.asarray(angle_values, dtype=float) - ideal_angle
    angle_variance = float(np.sum(deviations**2) / (len(angle_values) - 1))
    angle_rms = float(np.sqrt(np.mean(deviations**2)))
    angle_mad = float(np.mean(np.abs(deviations)))

    result: dict[str, Any] = {
        "bond_lengths_angstrom": [float(x) for x in lengths],
        "mean_bond_length_angstrom": mean_length,
        "bond_length_std_angstrom": bond_std,
        "bond_length_cv": float(bond_std / mean_length),
        "bond_length_range_angstrom": float(np.ptp(lengths)),
        "bond_length_distortion_delta": delta_d,
        "baur_distortion_index": baur_d,
        "polyhedron_volume_angstrom3": hull_volume,
        "equal_volume_ideal_bond_length_angstrom": d0,
        "quadratic_elongation": quadratic_elongation,
        "angle_count": len(angle_values),
        "ideal_angle_degree": ideal_angle,
        "mean_angle_degree": float(np.mean(angle_values)),
        "angle_variance_degree2": angle_variance,
        "angle_rms_deviation_degree": angle_rms,
        "angle_mean_abs_deviation_degree": angle_mad,
    }

    if coordination == 6:
        pair_records = []
        for i, j in opposite_pairs:
            pair_axis = canonical_axis(vectors[i] - vectors[j])
            pair_records.append(
                {
                    "ligand_local_indices": [i, j],
                    "mean_bond_length_angstrom": float((lengths[i] + lengths[j]) / 2.0),
                    "trans_angle_degree": angle_degrees(vectors[i], vectors[j]),
                    "axis_unit_vector_cartesian": [float(x) for x in pair_axis],
                }
            )
        pair_records.sort(key=lambda record: record["mean_bond_length_angstrom"])
        lx, ly, lz = [record["mean_bond_length_angstrom"] for record in pair_records]
        q2 = math.sqrt(2.0) * (lx - ly)
        q3 = math.sqrt(2.0 / 3.0) * (2.0 * lz - lx - ly)
        trans_dev = np.asarray(trans_angles, dtype=float) - 180.0
        result.update(
            {
                "axis_assignment": "lx <= ly <= lz (canonical bond-length ordering)",
                "opposite_bond_pairs": pair_records,
                "lx_angstrom": lx,
                "ly_angstrom": ly,
                "lz_angstrom": lz,
                "q2_angstrom": q2,
                "q3_angstrom": q3,
                "q_jt_angstrom": math.hypot(q2, q3),
                "mean_trans_angle_degree": float(np.mean(trans_angles)),
                "trans_angle_variance_degree2": float(
                    np.sum(trans_dev**2) / (len(trans_angles) - 1)
                ),
                "trans_angle_rms_deviation_degree": float(
                    np.sqrt(np.mean(trans_dev**2))
                ),
            }
        )
    return result


def _translation_bound(lattice: np.ndarray, radius: float) -> int:
    smallest_singular = float(np.min(np.linalg.svd(lattice, compute_uv=False)))
    if smallest_singular <= 1e-12:
        raise AnalysisError("Lattice is singular")
    return max(1, int(math.ceil(radius / smallest_singular)) + 2)


def ligand_neighbors(
    structure: PoscarStructure,
    center_index: int,
    ligand: str,
    radius: float,
) -> list[Neighbor]:
    """Enumerate ligand images within radius using periodic translations."""
    center_frac = structure.frac_coords[center_index]
    bound = _translation_bound(structure.lattice, radius)
    translations = list(itertools.product(range(-bound, bound + 1), repeat=3))
    neighbors: list[Neighbor] = []
    for ligand_index, symbol in enumerate(structure.species):
        if symbol != ligand:
            continue
        base_delta = structure.frac_coords[ligand_index] - center_frac
        for image in translations:
            frac_delta = base_delta + np.asarray(image, dtype=float)
            vector = frac_delta @ structure.lattice
            distance = float(np.linalg.norm(vector))
            if 1e-10 < distance <= radius + 1e-12:
                neighbors.append(
                    Neighbor(ligand_index, symbol, tuple(int(x) for x in image), vector, distance)
                )
    neighbors.sort(key=lambda n: (n.distance, n.site_index, n.image))
    return neighbors


def analyze_site(
    structure: PoscarStructure,
    site_index: int,
    center_spec: dict[str, Any],
    radius: float,
    shell_gap_warning: float,
    fail_on_ambiguous_shell: bool,
) -> tuple[dict[str, Any], list[str]]:
    center = structure.species[site_index]
    coordination = int(center_spec["coordination"])
    ligand = str(center_spec["ligand"])
    geometry = str(center_spec["geometry"])
    candidates = ligand_neighbors(structure, site_index, ligand, radius)
    if len(candidates) < coordination:
        raise AnalysisError(
            f"Site {site_index + 1} ({center}) has only {len(candidates)} {ligand} neighbors "
            f"within {radius:.3f} Å; expected {coordination}"
        )
    selected = candidates[:coordination]
    warnings: list[str] = []
    shell_gap = None
    next_neighbor_distance = None
    if len(candidates) > coordination:
        next_neighbor_distance = candidates[coordination].distance
        shell_gap = next_neighbor_distance - selected[-1].distance
        if shell_gap < shell_gap_warning:
            message = (
                f"Site {site_index + 1} ({center}): coordination-shell gap is only "
                f"{shell_gap:.4f} Å ({selected[-1].distance:.4f} -> {next_neighbor_distance:.4f} Å)"
            )
            if fail_on_ambiguous_shell:
                raise AnalysisError(message)
            warnings.append(message)

    vectors = np.array([neighbor.vector for neighbor in selected], dtype=float)
    geometry_result = analyze_vectors(vectors, coordination)
    result: dict[str, Any] = {
        "site_number": site_index + 1,
        "element": center,
        "ligand": ligand,
        "coordination": coordination,
        "geometry": geometry,
        "polyhedron": f"{center}{ligand}{coordination}",
        "fractional_coordinates": [float(x) for x in structure.frac_coords[site_index]],
        "ligand_site_numbers": [n.site_index + 1 for n in selected],
        "ligand_images": [list(n.image) for n in selected],
        "selected_max_distance_angstrom": float(selected[-1].distance),
        "next_neighbor_distance_angstrom": next_neighbor_distance,
        "coordination_shell_gap_angstrom": shell_gap,
        "neighbor_candidate_count": len(candidates),
        **geometry_result,
    }

    if coordination == 6:
        for pair in result["opposite_bond_pairs"]:
            i, j = pair.pop("ligand_local_indices")
            pair["ligand_site_numbers"] = [selected[i].site_index + 1, selected[j].site_index + 1]
            pair["ligand_images"] = [list(selected[i].image), list(selected[j].image)]
    return result, warnings


def mean_and_std(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    return {
        "mean": float(np.mean(array)),
        "std_population": float(np.std(array)),
        "min": float(np.min(array)),
        "max": float(np.max(array)),
    }


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    common_metrics = [
        "mean_bond_length_angstrom",
        "bond_length_std_angstrom",
        "bond_length_cv",
        "bond_length_range_angstrom",
        "bond_length_distortion_delta",
        "baur_distortion_index",
        "polyhedron_volume_angstrom3",
        "quadratic_elongation",
        "angle_variance_degree2",
        "angle_rms_deviation_degree",
    ]
    oct_metrics = [
        "q2_angstrom",
        "q3_angstrom",
        "q_jt_angstrom",
        "mean_trans_angle_degree",
        "trans_angle_variance_degree2",
        "trans_angle_rms_deviation_degree",
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault(result["polyhedron"], []).append(result)
    for polyhedron, selected in groups.items():
        coordination = int(selected[0]["coordination"])
        metrics = common_metrics + (oct_metrics if coordination == 6 else [])
        summary[polyhedron] = {
            "site_count": len(selected),
            "center": selected[0]["element"],
            "ligand": selected[0]["ligand"],
            "coordination": coordination,
            "metrics": {
                metric: mean_and_std([float(item[metric]) for item in selected])
                for metric in metrics
            },
        }
    return summary


CSV_FIELDS = [
    "site_number",
    "element",
    "ligand",
    "coordination",
    "geometry",
    "polyhedron",
    "mean_bond_length_angstrom",
    "bond_length_std_angstrom",
    "bond_length_cv",
    "bond_length_range_angstrom",
    "bond_length_distortion_delta",
    "baur_distortion_index",
    "polyhedron_volume_angstrom3",
    "equal_volume_ideal_bond_length_angstrom",
    "quadratic_elongation",
    "mean_angle_degree",
    "angle_variance_degree2",
    "angle_rms_deviation_degree",
    "angle_mean_abs_deviation_degree",
    "coordination_shell_gap_angstrom",
    "lx_angstrom",
    "ly_angstrom",
    "lz_angstrom",
    "q2_angstrom",
    "q3_angstrom",
    "q_jt_angstrom",
    "mean_trans_angle_degree",
    "trans_angle_variance_degree2",
    "trans_angle_rms_deviation_degree",
]


def write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result.get(field, "") for field in CSV_FIELDS})


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return f"{float(value):.{digits}g}"


def write_report(
    path: Path,
    input_path: Path,
    structure: PoscarStructure,
    config: dict[str, Any],
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    warnings: list[str],
) -> None:
    lines = [
        "# 局域配位多面体畸变分析",
        "",
        f"- 输入：`{input_path.name}`",
        f"- 组成（按 POSCAR 顺序）：`{structure.formula}`",
        f"- 晶胞体积：{structure.volume:.8f} Å³",
        f"- 分析位点数：{len(results)}",
        f"- 搜索半径：{config['search_radius_angstrom']:.4f} Å",
        "",
        "## 汇总",
        "",
        "| 多面体 | 数量 | d̄ (Å) | Δd | D(Baur) | λ | σ²θ (deg²) | RMS角偏差 (deg) | QJT (Å) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for polyhedron, group in summary.items():
        m = group["metrics"]
        qjt = m.get("q_jt_angstrom", {}).get("mean")
        lines.append(
            f"| {polyhedron} | {group['site_count']} | "
            f"{_fmt(m['mean_bond_length_angstrom']['mean'])} | "
            f"{_fmt(m['bond_length_distortion_delta']['mean'])} | "
            f"{_fmt(m['baur_distortion_index']['mean'])} | "
            f"{_fmt(m['quadratic_elongation']['mean'])} | "
            f"{_fmt(m['angle_variance_degree2']['mean'])} | "
            f"{_fmt(m['angle_rms_deviation_degree']['mean'])} | {_fmt(qjt)} |"
        )

    lines.extend(
        [
            "",
            "## 逐位点",
            "",
            "| 位点 | 多面体 | d̄ (Å) | std(d) (Å) | Δd | D | λ | σ²θ (deg²) | shell gap (Å) | Q2 (Å) | Q3 (Å) | QJT (Å) |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for result in results:
        lines.append(
            f"| {result['site_number']} | {result['polyhedron']} | "
            f"{_fmt(result['mean_bond_length_angstrom'])} | "
            f"{_fmt(result['bond_length_std_angstrom'])} | "
            f"{_fmt(result['bond_length_distortion_delta'])} | "
            f"{_fmt(result['baur_distortion_index'])} | "
            f"{_fmt(result['quadratic_elongation'])} | "
            f"{_fmt(result['angle_variance_degree2'])} | "
            f"{_fmt(result['coordination_shell_gap_angstrom'])} | "
            f"{_fmt(result.get('q2_angstrom'))} | {_fmt(result.get('q3_angstrom'))} | "
            f"{_fmt(result.get('q_jt_angstrom'))} |"
        )

    if warnings:
        lines.extend(["", "## 警告", ""])
        lines.extend(f"- {warning}" for warning in warnings)

    lines.extend(
        [
            "",
            "## 定义与解释边界",
            "",
            "- Δd = (1/n) Σ[(dᵢ-d̄)/d̄]²；D(Baur) = (1/n) Σ|dᵢ-d̄|/d̄。",
            "- λ = (1/n) Σ(dᵢ/d₀)²，其中 d₀ 是与实际多面体等体积的规则多面体中心-顶点距离。",
            "- 角方差 σ²θ = Σ(θᵢ-θ₀)²/(m-1)：四面体使用 6 个角与 109.4712206°；八面体仅使用 12 个 cis 角与 90°。",
            "- 八面体的三组 trans 键由总 trans 角最大化确定。Q2/Q3 使用三组平均对向键长，并按 lx≤ly≤lz 排序以保证数值可复现。",
            "- 因此 QJT=√(Q2²+Q3²) 可作为轴置换不敏感的 JT 幅度指标；Q2/Q3 的符号与单独数值不是晶体学方向标签。讨论具体轨道方向时应另行定义局域轴。",
            "- 邻居选择仍是几何配位模型：脚本不会根据键价、化学键级或 Voronoi 自动改变配位数。若 shell gap 很小，应检查 config、搜索半径和结构本身。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def analyze_structure(
    poscar: Path,
    config: dict[str, Any],
    fail_on_ambiguous_shell: bool = False,
) -> tuple[PoscarStructure, list[dict[str, Any]], dict[str, Any], list[str]]:
    structure = parse_poscar(poscar)
    centers: dict[str, Any] = config["centers"]
    radius = float(config["search_radius_angstrom"])
    shell_warning = float(config["shell_gap_warning_angstrom"])
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, symbol in enumerate(structure.species):
        if symbol not in centers:
            continue
        result, site_warnings = analyze_site(
            structure,
            index,
            centers[symbol],
            radius,
            shell_warning,
            fail_on_ambiguous_shell,
        )
        results.append(result)
        warnings.extend(site_warnings)
    if not results:
        raise AnalysisError(
            "No configured center species were found in POSCAR; configured centers: "
            + ", ".join(centers)
        )
    return structure, results, build_summary(results), warnings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("poscar", type=Path, help="Input VASP5/6 POSCAR")
    parser.add_argument("--config", type=Path, help="JSON center/ligand configuration")
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("polyhedral_distortion"),
        help="Output prefix (default: polyhedral_distortion)",
    )
    parser.add_argument(
        "--search-radius",
        type=float,
        help="Override config search radius in angstrom",
    )
    parser.add_argument(
        "--shell-gap-warning",
        type=float,
        help="Override shell-gap warning threshold in angstrom",
    )
    parser.add_argument(
        "--fail-on-ambiguous-shell",
        action="store_true",
        help="Fail if nth/(n+1)th ligand distance gap is below threshold",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate input/config, print planned center counts, write nothing",
    )
    parser.add_argument("--check-applicability", action="store_true", help="Validate configured coordination without writing outputs")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args(argv)


def dry_run_payload(structure: PoscarStructure, config: dict[str, Any], poscar: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for symbol in structure.species:
        counts[symbol] = counts.get(symbol, 0) + 1
    planned = {
        center: {
            "sites_found": counts.get(center, 0),
            **spec,
        }
        for center, spec in config["centers"].items()
    }
    return {
        "status": "dry-run",
        "version": VERSION,
        "input_file": str(poscar),
        "input_sha256": sha256_file(poscar),
        "formula": structure.formula,
        "site_count": len(structure.species),
        "planned_centers": planned,
        "search_radius_angstrom": config["search_radius_angstrom"],
        "shell_gap_warning_angstrom": config["shell_gap_warning_angstrom"],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        if args.search_radius is not None:
            if args.search_radius <= 0:
                raise AnalysisError("--search-radius must be > 0")
            config["search_radius_angstrom"] = float(args.search_radius)
        if args.shell_gap_warning is not None:
            if args.shell_gap_warning <= 0:
                raise AnalysisError("--shell-gap-warning must be > 0")
            config["shell_gap_warning_angstrom"] = float(args.shell_gap_warning)

        if args.dry_run:
            structure = parse_poscar(args.poscar)
            print(json.dumps(dry_run_payload(structure, config, args.poscar), indent=2, ensure_ascii=False))
            return 0

        if args.check_applicability:
            structure, results, _summary, warnings = analyze_structure(args.poscar, config)
            print(json.dumps({"status": "applicable", "input_sha256": sha256_file(args.poscar),
                              "formula": structure.formula, "sites": len(results), "warnings": warnings}, ensure_ascii=False))
            return 0

        structure, results, summary, warnings = analyze_structure(
            args.poscar, config, args.fail_on_ambiguous_shell
        )
        prefix: Path = args.output_prefix
        prefix.parent.mkdir(parents=True, exist_ok=True)
        csv_path = prefix.with_suffix(".csv")
        json_path = prefix.with_suffix(".json")
        report_path = prefix.with_suffix(".md")

        write_csv(csv_path, results)
        payload = {
            "schema_version": "1.0",
            "analysis": "local-polyhedral-distortion",
            "tool_version": VERSION,
            "provenance": {
                "input_file": str(args.poscar.resolve()),
                "input_sha256": sha256_file(args.poscar),
                "config_file": str(args.config.resolve()) if args.config else None,
                "config": config,
            },
            "structure": {
                "comment": structure.comment,
                "formula": structure.formula,
                "site_count": len(structure.species),
                "lattice_angstrom": structure.lattice.tolist(),
                "volume_angstrom3": structure.volume,
            },
            "warnings": warnings,
            "site_results": results,
            "summary": summary,
        }
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        write_report(report_path, args.poscar, structure, config, results, summary, warnings)

        print(f"Analyzed {len(results)} center sites")
        print(f"Warnings: {len(warnings)}")
        print(f"CSV: {csv_path}")
        print(f"JSON: {json_path}")
        print(f"Report: {report_path}")
        return 0
    except (AnalysisError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
