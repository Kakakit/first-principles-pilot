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
    result.pop('poscarText', None)
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


def edit_poscar(parent, text, rebuild_atom_ids=False):
    """Validate a proposal; saving the returned document is a separate UI action."""
    from .structures import parse_structure
    from .poscar_text import parser_text
    source_bytes(parent)
    if not isinstance(text, str) or not text or len(text.encode('utf-8')) > 256 * 1024:
        raise ValueError('POSCAR 文本必须非空且不超过 256 KiB。')
    result = parse_structure('POSCAR', 'poscar', base64.b64encode(text.encode('utf-8')).decode())
    _, info = parser_text(text)
    same_rows = [a['element'] for a in result['atoms']] == [a['element'] for a in parent['atoms']]
    preserved = same_rows and not rebuild_atom_ids
    for index, atom in enumerate(result['atoms']):
        atom['id'] = parent['atoms'][index]['id'] if preserved else f'{uuid.uuid4()}:{index + 1}'
    result['source'] = copy.deepcopy(parent['source'])
    result['poscarText'] = text
    result['edit'] = {'id': str(uuid.uuid4()), 'parent_geometry_sha256': geometry_hash(parent),
                      'geometry_sha256': geometry_hash(result), 'coordinate_mode': info['coordinate_mode'],
                      'at': datetime.now(timezone.utc).isoformat(), 'operation': 'poscar-text',
                      'parent_edit_id': parent.get('edit', {}).get('id')}
    messages = []
    if not preserved:
        messages.append('已重新建立原子身份；请重新核对 MAGMOM、Hubbard 和 POTCAR，旧记录不会按行套用。')
    if info['extra_sections']:
        messages.append('附加段随当前 POSCAR 原文保留：' + '、'.join(info['extra_sections']))
    result['warnings'] += messages
    return {'structure': result, 'summary': {
        'volume_before': parent['volume'], 'volume_after': result['volume'],
        'volume_ratio': result['volume'] / parent['volume'],
        'lengths_before': parent['lengths'], 'lengths_after': result['lengths'],
        'atoms_before': len(parent['atoms']), 'atoms_after': len(result['atoms']),
        'elements_before': [a['element'] for a in parent['atoms']],
        'species_after': info['species'], 'counts_after': info['counts'],
        'atom_ids_preserved': preserved, **info, 'messages': messages,
    }}


def input_geometry(document):
    """Old cached POSCARs are regenerated; explicit text is reparsed and checked."""
    source_bytes(document)
    if 'poscarText' not in document:
        return edit_structure(document, document['lattice'],
                              [dict(a, coordinates=a['fractional']) for a in document['atoms']], 'fractional')
    from .structures import parse_structure
    text = document['poscarText']
    if not isinstance(text, str):
        raise ValueError('POSCAR 文本无效，请重新验证。')
    parsed = parse_structure('POSCAR', 'poscar', base64.b64encode(text.encode('utf-8')).decode())
    mismatch = 'POSCAR 文本与保存的几何不一致，请重新验证并应用。'
    if [a['element'] for a in parsed['atoms']] != [a['element'] for a in document['atoms']]:
        raise ValueError(mismatch)
    for key in ('lattice', 'lengths', 'angles', 'volume'):
        if not np.allclose(parsed[key], document[key], atol=1e-10, rtol=0):
            raise ValueError(mismatch)
    for actual, saved in zip(parsed['atoms'], document['atoms']):
        if actual['selective'] != saved['selective'] or any(
                not np.allclose(actual[key], saved[key], atol=1e-10, rtol=0)
                for key in ('fractional', 'cartesian')):
            raise ValueError(mismatch)
    if len(set(a['id'] for a in document['atoms'])) != len(document['atoms']):
        raise ValueError('原子身份重复。')
    result = copy.deepcopy(document)
    result['poscar'] = text
    return result
