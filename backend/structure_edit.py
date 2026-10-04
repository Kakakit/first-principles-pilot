"""Explicit geometry edits, preserving original bytes and atom identities."""
import base64
import copy
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
import numpy as np
from pymatgen.core import Structure
from pymatgen.io.vasp import Poscar


def source_bytes(document):
    source = document['source']
    raw = base64.b64decode(source['base64'], validate=True)
    if not raw or len(raw) > 256 * 1024 or hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('原文件字节与来源 SHA-256 不一致。')
    return raw


def geometry_hash(document):
    payload = {'lattice': [[float(v) for v in row] for row in document['lattice']], 'atoms': [
        {'id': atom['id'], 'element': atom['element'], 'fractional': [float(v) for v in atom['fractional']], 'selective': atom['selective']}
        for atom in document['atoms']]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def edit_structure(parent, lattice, atoms, coordinate_mode):
    source_bytes(parent)
    matrix = np.array(lattice, dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-8:
        raise ValueError('晶胞必须是非退化、有限的 3×3 行向量矩阵。')
    if np.linalg.det(matrix) < 0:
        raise ValueError('当前导出器会变换左手晶胞；请使用右手晶胞（行列式 > 0），本次编辑未应用。')
    if not atoms or len(atoms) > 1500 or [a['id'] for a in atoms] != [a['id'] for a in parent['atoms']]:
        raise ValueError('本阶段编辑必须保持原子数、身份和顺序。')
    if len(set(a['id'] for a in atoms)) != len(atoms):
        raise ValueError('原子身份重复。')
    if coordinate_mode not in ('fractional', 'cartesian'):
        raise ValueError('坐标类型无效。')
    coords = np.array([a['coordinates'] for a in atoms], dtype=float)
    if coords.shape != (len(atoms), 3) or not np.isfinite(coords).all():
        raise ValueError('原子坐标必须是有限的三个数值。')
    flags = [a.get('selective') for a in atoms]
    for f in flags:
        if f is not None and (not isinstance(f, list) or len(f) != 3 or any(type(v) is not bool for v in f)):
            raise ValueError('约束需要三个 T/F 值。')
    if any(f is not None for f in flags):
        props = {'selective_dynamics': [f if f is not None else [True]*3 for f in flags]}
    else:
        props = {}
    s = Structure(matrix, [a['element'] for a in atoms], coords,
                  coords_are_cartesian=coordinate_mode == 'cartesian',
                  to_unit_cell=False, site_properties=props)
    if not s.is_ordered or any(not site.specie.is_valid_symbol(site.specie.symbol) for site in s):
        raise ValueError('必须使用明确的化学元素符号。')
    if not math.isfinite(s.volume) or not np.isfinite(s.cart_coords).all() or not np.isfinite(s.frac_coords).all():
        raise ValueError('编辑后的几何超出有效数值范围。')
    result = copy.deepcopy(parent)
    result.update(lattice=s.lattice.matrix.tolist(), lengths=list(s.lattice.abc),
                  angles=list(s.lattice.angles), volume=float(s.volume), formula=s.composition.formula)
    result['atoms'] = [{'id': a['id'], 'element': site.specie.symbol,
                        'fractional': site.frac_coords.tolist(), 'cartesian': site.coords.tolist(),
                        'selective': props.get('selective_dynamics', [None]*len(atoms))[i]}
                       for i, (a, site) in enumerate(zip(atoms, s))]
    result['poscar'] = Poscar(s, sort_structure=False).get_str(significant_figures=16)
    result['edit'] = {'id': str(uuid.uuid4()), 'parent_geometry_sha256': geometry_hash(parent),
                      'geometry_sha256': geometry_hash(result), 'coordinate_mode': coordinate_mode,
                      'at': datetime.now(timezone.utc).isoformat(),
                      'operation': 'geometry-and-constraints',
                      'parent_edit_id': parent.get('edit', {}).get('id')}
    return result
