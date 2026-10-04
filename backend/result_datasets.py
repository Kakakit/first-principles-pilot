"""Read-only registration of compact electronic-structure result datasets."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

from .storage import ROOT
from . import servers

DATASET_SCHEMA = "fpilot.electronic-structure-dataset.v1"
CHARACTERIZATION_SCHEMA = "fpilot.electronic-structure-characterization.v1"
RESEARCH_ROOT = Path(os.environ.get('FPILOT_RESEARCH_ROOT', ROOT.parent / 'research-data')).expanduser().resolve()
MAX_TABLE_BYTES = 32 * 1024 * 1024
HEX64 = re.compile(r"^[a-f0-9]{64}$")


class ResultDatasetError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResultDatasetError(f"结果 JSON 不可读：{path.name}") from exc
    if not isinstance(value, dict):
        raise ResultDatasetError(f"结果 JSON 必须是对象：{path.name}")
    return value


def _inside(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ResultDatasetError("结果路径必须位于当前科研工作区内") from exc
    return resolved


def _table(dataset_dir: Path, name: object) -> Path:
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".csv"):
        raise ResultDatasetError("结果表必须是数据集目录内的 CSV 文件名")
    path = _inside(dataset_dir / name, dataset_dir)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ResultDatasetError(f"结果表缺失或为空：{name}")
    if path.stat().st_size > MAX_TABLE_BYTES:
        raise ResultDatasetError(f"结果表超过 32 MiB，只接受已有紧凑解析结果：{name}")
    return path


def _number(row: dict, key: str, source: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ResultDatasetError(f"{source} 的 {key} 不是数值") from exc
    if not math.isfinite(value):
        raise ResultDatasetError(f"{source} 的 {key} 不是有限数值")
    return value


def _band_curves(path: Path, expected_nk: int, expected_nb: int):
    bands: dict[int, list[list[float]]] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"k_index", "path_x_A^-1", "band", "energy_eV"}
        if not required.issubset(reader.fieldnames or []):
            raise ResultDatasetError(f"{path.name} 缺少能带坐标列")
        for row in reader:
            band_value = _number(row, "band", path.name)
            if not band_value.is_integer() or band_value < 1:
                raise ResultDatasetError(f"{path.name} 的 band 编号无效")
            band = int(band_value)
            point = [_number(row, "path_x_A^-1", path.name), _number(row, "energy_eV", path.name)]
            bands.setdefault(band, []).append(point)
    if len(bands) != expected_nb or set(bands) != set(range(1, expected_nb + 1)):
        raise ResultDatasetError(f"{path.name} 的能带数与 manifest 不一致")
    if any(len(points) != expected_nk for points in bands.values()):
        raise ResultDatasetError(f"{path.name} 的 k 点数与 manifest 不一致")
    axis = [point[0] for point in bands[1]]
    for points in bands.values():
        if any(abs(point[0] - axis[index]) > 1e-9 for index, point in enumerate(points)):
            raise ResultDatasetError(f"{path.name} 的能带路径坐标不一致")
    energies = [point[1] for points in bands.values() for point in points]
    visible = [points for points in bands.values() if min(point[1] for point in points) <= 6 and max(point[1] for point in points) >= -6]
    return visible, energies


def _dos_curve(path: Path):
    points = []
    all_points = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not {"energy_eV", "total"}.issubset(reader.fieldnames or []):
            raise ResultDatasetError(f"{path.name} 缺少 DOS 坐标列")
        for row in reader:
            point = [_number(row, "energy_eV", path.name), _number(row, "total", path.name)]
            all_points.append(point)
            if -6 <= point[0] <= 6:
                points.append(point)
    if len(all_points) < 2 or any(all_points[index][0] <= all_points[index - 1][0] for index in range(1, len(all_points))):
        raise ResultDatasetError(f"{path.name} 的能量网格必须严格递增")
    return points, all_points


def _interpolate_zero(points):
    for energy, value in points:
        if abs(energy) <= 1e-12:
            return value
    for left, right in zip(points, points[1:]):
        if left[0] < 0 < right[0]:
            ratio = -left[0] / (right[0] - left[0])
            return left[1] + ratio * (right[1] - left[1])
    raise ResultDatasetError("DOS 能量网格未覆盖能量零点")


def _close(actual, expected, label, tolerance=1e-7):
    if isinstance(expected, bool) or not isinstance(expected, (int, float)) or not math.isfinite(float(expected)):
        raise ResultDatasetError(f"characterization 缺少有限数值：{label}")
    if abs(actual - float(expected)) > tolerance:
        raise ResultDatasetError(f"曲线与 characterization 数值不一致：{label}")


def _validate_reference(manifest: dict, characterization: dict):
    reference = manifest.get("energy_reference")
    if not isinstance(reference, dict) or reference.get("kind") not in ("reference-outcar", "explicit"):
        raise ResultDatasetError("能量参考必须显式记录为 reference-outcar 或 explicit")
    value = reference.get("value_eV")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ResultDatasetError("能量参考必须包含有限的 value_eV")
    if characterization.get("energy_reference") != reference:
        raise ResultDatasetError("manifest 与 characterization 的能量参考不一致")
    if reference.get("kind") == "reference-outcar" and not HEX64.fullmatch(str(reference.get("sha256", ""))):
        raise ResultDatasetError("reference-outcar 能量参考缺少 SHA-256")
    return reference


def import_dataset(manifest_path: str):
    if not isinstance(manifest_path, str) or not manifest_path.strip() or len(manifest_path) > 4096:
        raise ResultDatasetError("请提供 manifest.json 的明确本地路径")
    path = _inside(Path(manifest_path), RESEARCH_ROOT)
    if path.name != "manifest.json" or not path.is_file():
        raise ResultDatasetError("请选择已有结果集的 manifest.json")
    dataset_dir = path.parent
    manifest = _json(path)
    characterization_path = dataset_dir / "characterization.json"
    characterization = _json(characterization_path)
    if manifest.get("schema") != DATASET_SCHEMA or characterization.get("schema") != CHARACTERIZATION_SCHEMA:
        raise ResultDatasetError("结果集 schema 不受支持")
    if Path(str(characterization.get("dataset_manifest", ""))).resolve() != path:
        raise ResultDatasetError("characterization 未绑定当前 manifest")
    reference = _validate_reference(manifest, characterization)
    band_path = manifest.get("band_path") or {}
    try:
        nk, nb = int(band_path["nkpoints"]), int(band_path["nbands"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ResultDatasetError("manifest 缺少能带维度") from exc
    if nk <= 1 or nb <= 0:
        raise ResultDatasetError("manifest 能带维度无效")
    ticks, labels = band_path.get("ticks_A^-1"), band_path.get("labels")
    if not isinstance(ticks, list) or not isinstance(labels, list) or len(ticks) != len(labels) or len(ticks) < 2:
        raise ResultDatasetError("高对称路径刻度与标签不一致")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in ticks):
        raise ResultDatasetError("高对称路径刻度必须是有限数值")
    spin_order = manifest.get("spin_order")
    spins = manifest.get("spins")
    if not isinstance(spin_order, list) or not spin_order or not isinstance(spins, dict):
        raise ResultDatasetError("manifest 缺少自旋通道")
    compact_spins, source_files = {}, []
    report_spins = characterization.get("spins")
    if not isinstance(report_spins, dict):
        raise ResultDatasetError("characterization 缺少自旋摘要")
    for key in spin_order:
        if key not in spins or key not in report_spins:
            raise ResultDatasetError("manifest 与 characterization 自旋通道不一致")
        meta = spins[key]
        band_file = _table(dataset_dir, meta.get("band_table"))
        band_curves, energies = _band_curves(band_file, nk, nb)
        nonpositive = [value for value in energies if value <= 0]
        positive = [value for value in energies if value > 0]
        edges = report_spins[key].get("sampled_path_band_edges") or {}
        if edges.get("classification") == "gapped_on_sampled_path":
            if not nonpositive or not positive:
                raise ResultDatasetError(f"{key} 无法复核采样路径带边")
            _close(max(nonpositive), edges.get("vbm_relative_eV"), f"{key}.vbm_relative_eV")
            _close(min(positive), edges.get("cbm_relative_eV"), f"{key}.cbm_relative_eV")
            _close(min(positive) - max(nonpositive), edges.get("sampled_path_indirect_gap_eV"), f"{key}.sampled_path_indirect_gap_eV")
        dos_file = _table(dataset_dir, meta.get("pdos_table")) if meta.get("pdos_table") else None
        dos_points, all_dos = _dos_curve(dos_file) if dos_file else ([], [])
        if dos_file:
            reported_dos=(report_spins[key].get("density_of_states") or {}).get("total_dos_at_reference_states_per_eV")
            _close(_interpolate_zero(all_dos), reported_dos, f"{key}.total_dos_at_reference_states_per_eV", tolerance=1e-6)
        source_files.append({"role":f"{key}.bands","name":band_file.name,"sha256":_sha256(band_file),"size_bytes":band_file.stat().st_size})
        if dos_file:
            source_files.append({"role":f"{key}.pdos","name":dos_file.name,"sha256":_sha256(dos_file),"size_bytes":dos_file.stat().st_size})
        compact_spins[key]={"label":meta.get("label",key),"band_curves":band_curves,"dos_curve":dos_points,
                            "sampled_path_band_edges":edges,"density_of_states":report_spins[key].get("density_of_states")}
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ResultDatasetError("manifest 缺少输入来源记录")
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not Path(item["path"]).is_absolute() or not HEX64.fullmatch(str(item.get("sha256", ""))):
            raise ResultDatasetError("manifest 输入来源记录不完整")
    manifest_hash, characterization_hash = _sha256(path), _sha256(characterization_path)
    identity = hashlib.sha256(json.dumps({"manifest":manifest_hash,"characterization":characterization_hash,"tables":source_files},sort_keys=True,separators=(",",":")).encode()).hexdigest()
    record={"id":identity,"schema":"fpilot.web-result-dataset.v1","read_only":True,"submission_authorized":False,
            "source":{"manifest_path":str(path),"manifest_sha256":manifest_hash,"characterization_sha256":characterization_hash,
                      "table_files":source_files,"producer_input_hashes":inputs,"producer_input_hash_verification":"RECORDED_NOT_REHASHED"},
            "units":{"energy":"eV","path_x":"Angstrom^-1","dos":"states/eV"},
            "energy_reference":reference,"band_path":{"ticks_A^-1":[float(value) for value in ticks],"labels":labels,"nkpoints":nk,"nbands":nb},
            "spins":compact_spins,"magnetization":characterization.get("magnetization"),
            "scientific_boundaries":characterization.get("scientific_boundaries",[]),
            "curve_window_eV":[-6.0,6.0],"note":"曲线来自已有紧凑解析表；输入大文件 hash 采用生产者记录，未在导入时重新扫描。"}
    target=ROOT/"results"/"records"/(identity+".json")
    target.parent.mkdir(parents=True,exist_ok=True)
    packed=json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    if target.exists():
        if target.read_bytes()!=packed:
            raise ResultDatasetError("结果记录内容寻址冲突；未覆盖")
        return {**record,"duplicate":True}
    temporary=target.with_name(target.name+"."+uuid.uuid4().hex+".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(packed);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:
        if temporary.exists():temporary.unlink()
    return {**record,"duplicate":False}


def get(key: str):
    if not HEX64.fullmatch(str(key)):
        raise ResultDatasetError("结果记录不存在")
    path=ROOT/"results"/"records"/(key+".json")
    if not path.is_file():raise ResultDatasetError("结果记录不存在")
    value=_json(path)
    source=value.get("source") or {}
    identity=hashlib.sha256(json.dumps({"manifest":source.get("manifest_sha256"),"characterization":source.get("characterization_sha256"),"tables":source.get("table_files")},sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if value.get("id")!=key or identity!=key:raise ResultDatasetError("结果记录身份校验失败")
    return value


def list_records():
    rows=[]
    for path in sorted((ROOT/"results"/"records").glob("*.json"),key=lambda item:item.stat().st_mtime,reverse=True):
        try:
            value=get(path.stem)
            rows.append({key:value[key] for key in ("id","schema","read_only","source","units","energy_reference","band_path","magnetization","scientific_boundaries","curve_window_eV","note")})
        except (OSError,ResultDatasetError,KeyError):
            continue
    return rows
