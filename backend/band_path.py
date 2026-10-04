"""Editable SeeK-path line mode draft in the input cell's reciprocal basis."""
from pymatgen.core import Element
import seekpath
import copy
import json
from functools import lru_cache


def generate(structure):
    return copy.deepcopy(_generate(json.dumps(structure, sort_keys=True, ensure_ascii=False, allow_nan=False)))


@lru_cache(maxsize=32)
def _generate(serialized):
    structure = json.loads(serialized)
    cell = (structure['lattice'], [atom['fractional'] for atom in structure['atoms']],
            [Element(atom['element']).Z for atom in structure['atoms']])
    found = seekpath.get_path_orig_cell(cell, symprec=0.01)
    points = found['point_coords']
    lines = []
    for start, end in found['path']:
        for label in (start, end):
            coords = points[label]
            lines.append(' '.join(f'{float(value):.10f}' for value in coords) + ' ' + label)
    if not lines:
        raise ValueError('未能生成当前晶胞的高对称路径')
    return {'path': '\n'.join(lines), 'structure_hash': structure['source']['sha256'],
            'algorithm': 'seekpath.get_path_orig_cell', 'symprec': 0.01,
            'reference': 'https://seekpath.readthedocs.io/en/latest/maindoc.html'}
