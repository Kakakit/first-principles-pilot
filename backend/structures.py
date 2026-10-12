"""Local, non-executing structure import. Original bytes are retained verbatim."""
import base64
import hashlib
import importlib.metadata
import math
import warnings
import numpy as np
from pymatgen.io.cif import CifParser
from pymatgen.io.vasp import Poscar
from .poscar_text import parser_text

MAX_BYTES = 256 * 1024
MAX_ATOMS = 1500


def parse_structure(filename, file_format, encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError('文件必须非空且不超过 256 KiB。')
        text = raw.decode('utf-8-sig')
    except (UnicodeError, ValueError) as exc:
        raise ValueError('请导入有效的 UTF-8 文本文件，大小不超过 256 KiB。') from exc
    digest = hashlib.sha256(raw).hexdigest()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        if file_format == 'poscar':
            normalized, info = parser_text(text)
            poscar = Poscar.from_str(normalized, read_velocities=True)
            structure = poscar.structure
            if not poscar.true_names:
                raise ValueError('无法确定元素名称，请检查 POSCAR 元素行。')
            extra = bool(info['extra_sections'])
        elif file_format == 'cif':
            parser = CifParser.from_str(text, occupancy_tolerance=1.0, frac_tolerance=0)
            structures = parser.parse_structures(primitive=False, on_error='raise')
            if len(structures) != 1:
                raise ValueError('文件含多个结构；请拆成单结构 CIF 后分别导入。')
            structure = structures[0]
            extra = False
        else:
            raise ValueError('只支持 CIF 和 POSCAR。')
    if not 0 < len(structure) <= MAX_ATOMS:
        raise ValueError('当前支持 1–1500 个原子的结构。')
    if not structure.is_ordered:
        raise ValueError('存在混合或部分占位；请先构建明确的有序结构，本工作台不会自动取整。')
    if np.linalg.det(structure.lattice.matrix) < 0:
        raise ValueError('当前导出器会变换左手晶胞；请先提供右手晶胞（行列式 > 0），原文件未修改。')
    values = [structure.volume] + [v for row in structure.lattice.matrix for v in row] + [v for row in structure.frac_coords for v in row] + [v for row in structure.cart_coords for v in row]
    if not all(math.isfinite(float(v)) for v in values) or structure.volume < 1e-8:
        raise ValueError('晶胞或坐标非有限值，或晶胞体积为零。')
    notes = list(dict.fromkeys(str(w.message) for w in caught))
    if file_format == 'cif':
        notes.insert(0, 'CIF 按对称操作展开至文件给定晶胞，不做原胞约化；原子编号是解析后顺序，不是原始 CIF 行号。')
    if extra:
        notes.append('原文件包含速度或预测校正信息；原文保留，规范化 POSCAR 仅导出几何与约束。')
    sd = structure.site_properties.get('selective_dynamics')
    atoms = [{
        'id': f'{digest[:16]}:{i+1}', 'element': site.specie.symbol,
        'fractional': [float(v) for v in site.frac_coords],
        'cartesian': [float(v) for v in site.coords],
        'selective': [bool(v) for v in sd[i]] if sd is not None else None,
    } for i, site in enumerate(structure)]
    return {
        'version': 1, 'formula': structure.composition.formula,
        'lattice': structure.lattice.matrix.tolist(),
        'lengths': list(structure.lattice.abc), 'angles': list(structure.lattice.angles),
        'volume': float(structure.volume), 'atoms': atoms, 'warnings': notes,
        'source': {'filename': filename, 'format': file_format, 'sha256': digest, 'base64': encoded},
        'parser': {'name': 'pymatgen', 'version': importlib.metadata.version('pymatgen')},
        'poscar': Poscar(structure, sort_structure=False).get_str(significant_figures=16),
    }
