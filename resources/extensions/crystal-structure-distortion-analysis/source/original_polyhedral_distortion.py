#!/usr/bin/env python3
"""Calculate local FeO4 and CrO6 distortion metrics from a VASP POSCAR."""

import argparse
import csv
import itertools
import json
import math
from pathlib import Path

import numpy as np
from pymatgen.core import Structure
from scipy.spatial import ConvexHull


COORDINATION = {"Cr": 6, "Fe": 4}
IDEAL_ANGLE = {4: math.degrees(math.acos(-1.0 / 3.0)), 6: 90.0}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("poscar", type=Path, help="Input POSCAR path")
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("polyhedral_distortion"),
        help="Output path without extension (default: polyhedral_distortion)",
    )
    parser.add_argument(
        "--search-radius",
        type=float,
        default=3.0,
        help="O-neighbor search radius in angstrom (default: 3.0)",
    )
    return parser.parse_args()


def angle_degrees(vector_a, vector_b):
    cosine = np.dot(vector_a, vector_b) / (
        np.linalg.norm(vector_a) * np.linalg.norm(vector_b)
    )
    return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))


def pairings(items):
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


def best_opposite_pairs(vectors):
    """Pair six bonds by maximizing the sum of the three O-M-O angles."""
    candidates = list(pairings(tuple(range(6))))
    return max(
        candidates,
        key=lambda pairs: sum(angle_degrees(vectors[i], vectors[j]) for i, j in pairs),
    )


def ideal_bond_length(coordination, volume):
    if coordination == 6:
        # A regular octahedron with center-to-vertex distance r has V = 4 r^3 / 3.
        return (3.0 * volume / 4.0) ** (1.0 / 3.0)
    if coordination == 4:
        # A regular tetrahedron with circumradius r has V = 8 r^3 / (9 sqrt(3)).
        return (9.0 * math.sqrt(3.0) * volume / 8.0) ** (1.0 / 3.0)
    raise ValueError("Only tetrahedral and octahedral sites are supported")


def analyze_site(structure, site_index, search_radius):
    site = structure[site_index]
    element = site.specie.symbol
    coordination = COORDINATION[element]
    oxygen_neighbors = sorted(
        (
            neighbor
            for neighbor in structure.get_neighbors(site, search_radius)
            if neighbor.specie.symbol == "O"
        ),
        key=lambda neighbor: neighbor.nn_distance,
    )
    if len(oxygen_neighbors) < coordination:
        raise ValueError(
            f"Site {site_index + 1} ({element}) has only {len(oxygen_neighbors)} O "
            f"neighbors within {search_radius:.3f} angstrom"
        )
    oxygen_neighbors = oxygen_neighbors[:coordination]

    vectors = np.array([neighbor.coords - site.coords for neighbor in oxygen_neighbors])
    lengths = np.linalg.norm(vectors, axis=1)
    mean_length = float(np.mean(lengths))
    delta_d = float(np.mean(((lengths - mean_length) / mean_length) ** 2))
    baur_d = float(np.mean(np.abs(lengths - mean_length) / mean_length))

    hull_volume = float(ConvexHull(vectors).volume)
    d0 = ideal_bond_length(coordination, hull_volume)
    quadratic_elongation = float(np.mean((lengths / d0) ** 2))

    opposite_pairs = ()
    trans_angles = []
    if coordination == 6:
        opposite_pairs = best_opposite_pairs(vectors)
        trans_pair_sets = {frozenset(pair) for pair in opposite_pairs}
        angle_values = [
            angle_degrees(vectors[i], vectors[j])
            for i, j in itertools.combinations(range(coordination), 2)
            if frozenset((i, j)) not in trans_pair_sets
        ]
        trans_angles = [angle_degrees(vectors[i], vectors[j]) for i, j in opposite_pairs]
    else:
        angle_values = [
            angle_degrees(vectors[i], vectors[j])
            for i, j in itertools.combinations(range(coordination), 2)
        ]

    ideal_angle = IDEAL_ANGLE[coordination]
    angle_variance = float(
        sum((angle - ideal_angle) ** 2 for angle in angle_values)
        / (len(angle_values) - 1)
    )

    result = {
        "site_number": site_index + 1,
        "element": element,
        "coordination": coordination,
        "polyhedron": f"{element}O{coordination}",
        "fractional_coordinates": [float(value) for value in site.frac_coords],
        "oxygen_site_numbers": [int(neighbor.index) + 1 for neighbor in oxygen_neighbors],
        "bond_lengths_angstrom": [float(value) for value in lengths],
        "mean_bond_length_angstrom": mean_length,
        "bond_length_range_angstrom": float(np.max(lengths) - np.min(lengths)),
        "bond_length_distortion_delta": delta_d,
        "baur_distortion_index": baur_d,
        "polyhedron_volume_angstrom3": hull_volume,
        "equal_volume_ideal_bond_length_angstrom": d0,
        "quadratic_elongation": quadratic_elongation,
        "angle_count": len(angle_values),
        "ideal_angle_degree": ideal_angle,
        "mean_angle_degree": float(np.mean(angle_values)),
        "angle_variance_degree2": angle_variance,
    }

    if coordination == 6:
        pair_records = []
        for pair in opposite_pairs:
            i, j = pair
            pair_vector = vectors[i] - vectors[j]
            pair_vector /= np.linalg.norm(pair_vector)
            pair_records.append(
                {
                    "oxygen_site_numbers": [
                        int(oxygen_neighbors[i].index) + 1,
                        int(oxygen_neighbors[j].index) + 1,
                    ],
                    "mean_bond_length_angstrom": float((lengths[i] + lengths[j]) / 2.0),
                    "trans_angle_degree": angle_degrees(vectors[i], vectors[j]),
                    "axis_unit_vector_cartesian": [float(value) for value in pair_vector],
                }
            )
        pair_records.sort(key=lambda record: record["mean_bond_length_angstrom"])
        lx, ly, lz = [record["mean_bond_length_angstrom"] for record in pair_records]
        q2 = math.sqrt(2.0) * (lx - ly)
        q3 = math.sqrt(2.0 / 3.0) * (2.0 * lz - lx - ly)
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
                    sum((angle - 180.0) ** 2 for angle in trans_angles)
                    / (len(trans_angles) - 1)
                ),
            }
        )
    return result


def csv_row(result):
    fields = [
        "site_number",
        "element",
        "coordination",
        "polyhedron",
        "mean_bond_length_angstrom",
        "bond_length_range_angstrom",
        "bond_length_distortion_delta",
        "baur_distortion_index",
        "polyhedron_volume_angstrom3",
        "equal_volume_ideal_bond_length_angstrom",
        "quadratic_elongation",
        "mean_angle_degree",
        "angle_variance_degree2",
        "lx_angstrom",
        "ly_angstrom",
        "lz_angstrom",
        "q2_angstrom",
        "q3_angstrom",
        "q_jt_angstrom",
        "mean_trans_angle_degree",
        "trans_angle_variance_degree2",
    ]
    return fields, {field: result.get(field, "") for field in fields}


def mean_and_std(values):
    array = np.asarray(values, dtype=float)
    return {"mean": float(np.mean(array)), "std_population": float(np.std(array))}


def build_summary(results):
    summary = {}
    common_metrics = [
        "mean_bond_length_angstrom",
        "bond_length_range_angstrom",
        "bond_length_distortion_delta",
        "baur_distortion_index",
        "polyhedron_volume_angstrom3",
        "quadratic_elongation",
        "angle_variance_degree2",
    ]
    octahedral_metrics = [
        "q2_angstrom",
        "q3_angstrom",
        "q_jt_angstrom",
        "mean_trans_angle_degree",
        "trans_angle_variance_degree2",
    ]
    for element in COORDINATION:
        selected = [result for result in results if result["element"] == element]
        metrics = common_metrics + (octahedral_metrics if COORDINATION[element] == 6 else [])
        summary[element] = {
            "site_count": len(selected),
            "coordination": COORDINATION[element],
            "metrics": {
                metric: mean_and_std([result[metric] for result in selected])
                for metric in metrics
            },
        }
    return summary


def write_report(path, input_path, structure, results, summary):
    cr = summary["Cr"]["metrics"]
    fe = summary["Fe"]["metrics"]
    lines = [
        "# Fe–Cr–O 局域多面体畸变分析",
        "",
        f"输入结构：`{input_path.name}`；组成：{structure.composition.reduced_formula}。",
        "",
        "## 配位识别",
        "",
        "- Cr：6 配位，形成 CrO₆ 八面体。",
        "- Fe：4 配位，形成 FeO₄ 四面体。",
        "- Q₂、Q₃ 和 Q_JT 仅对 CrO₆ 计算；它们不适用于 FeO₄。",
        "",
        "## 汇总",
        "",
        "| 多面体 | 数量 | d̄ (Å) | Δ_d | D | λ | σ²_θ (deg²) | Q₂ (Å) | Q₃ (Å) | Q_JT (Å) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| CrO₆ | {summary['Cr']['site_count']} | "
            f"{cr['mean_bond_length_angstrom']['mean']:.8f} | "
            f"{cr['bond_length_distortion_delta']['mean']:.8e} | "
            f"{cr['baur_distortion_index']['mean']:.8e} | "
            f"{cr['quadratic_elongation']['mean']:.8f} | "
            f"{cr['angle_variance_degree2']['mean']:.8f} | "
            f"{cr['q2_angstrom']['mean']:.8f} | "
            f"{cr['q3_angstrom']['mean']:.8f} | "
            f"{cr['q_jt_angstrom']['mean']:.8f} |"
        ),
        (
            f"| FeO₄ | {summary['Fe']['site_count']} | "
            f"{fe['mean_bond_length_angstrom']['mean']:.8f} | "
            f"{fe['bond_length_distortion_delta']['mean']:.8e} | "
            f"{fe['baur_distortion_index']['mean']:.8e} | "
            f"{fe['quadratic_elongation']['mean']:.8f} | "
            f"{fe['angle_variance_degree2']['mean']:.8f} | — | — | — |"
        ),
        "",
        "## 逐位点数据",
        "",
        "| 位点 | 多面体 | d̄ (Å) | 键长范围 (Å) | Δ_d | λ | σ²_θ (deg²) | Q₂ (Å) | Q₃ (Å) | Q_JT (Å) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        q_values = (
            f"{result['q2_angstrom']:.8f} | {result['q3_angstrom']:.8f} | "
            f"{result['q_jt_angstrom']:.8f}"
            if result["coordination"] == 6
            else "— | — | —"
        )
        lines.append(
            f"| {result['site_number']} | {result['polyhedron']} | "
            f"{result['mean_bond_length_angstrom']:.8f} | "
            f"{result['bond_length_range_angstrom']:.8f} | "
            f"{result['bond_length_distortion_delta']:.8e} | "
            f"{result['quadratic_elongation']:.8f} | "
            f"{result['angle_variance_degree2']:.8f} | {q_values} |"
        )
    lines.extend(
        [
            "",
            "## 定义与约定",
            "",
            "- Δ_d = (1／n) Σᵢ[(dᵢ − d̄)／d̄]²。",
            "- D = (1／n) Σᵢ|dᵢ − d̄|／d̄。",
            "- λ = (1／n) Σᵢ(dᵢ／d₀)²；d₀ 是与实际多面体等体积的理想多面体键长。",
            "- σ²_θ = Σᵢ(θᵢ − θ₀)²／(m − 1)。CrO₆ 使用 12 个顺式角和 θ₀ = 90°；FeO₄ 使用全部 6 个角和 θ₀ = 109.4712206°。",
            "- CrO₆ 的三组对向键由三组 O–Cr–O 夹角总和最大确定。",
            "- 为得到可重复的标量，三组平均键长按 lₓ ≤ lᵧ ≤ l_z 排序。随后 Q₂ = √2(lₓ − lᵧ)，Q₃ = √(2／3)(2l_z − lₓ − lᵧ)，Q_JT = √(Q₂² + Q₃²)。",
            "- Q_JT 对三条局域轴的置换不变；Q₂、Q₃ 的单独数值和符号依赖轴约定。若需讨论具体轨道取向，应按晶体学方向重新指定局域 x、y、z 轴。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    args = parse_args()
    structure = Structure.from_file(args.poscar)
    results = [
        analyze_site(structure, index, args.search_radius)
        for index, site in enumerate(structure)
        if site.specie.symbol in COORDINATION
    ]
    summary = build_summary(results)

    output_prefix = args.output_prefix
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_prefix.with_suffix(".csv")
    json_path = output_prefix.with_suffix(".json")
    report_path = output_prefix.with_suffix(".md")

    fields, _ = csv_row(results[0])
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow(csv_row(result)[1])

    payload = {
        "input_file": str(args.poscar.resolve()),
        "formula": str(structure.composition.reduced_formula),
        "site_results": results,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(report_path, args.poscar, structure, results, summary)

    print(f"Analyzed {len(results)} metal sites")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
