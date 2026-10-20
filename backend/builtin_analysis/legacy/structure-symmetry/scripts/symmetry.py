#!/usr/bin/env python3
"""Read-only spatial symmetry report for one VASP5/6 structure."""
import argparse
import hashlib
import json
import math
import sys
from importlib.metadata import version
from pathlib import Path

from pymatgen.io.vasp import Poscar
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
import spglib

TOLERANCES = (0.001, 0.01, 0.1)
SCHEMA = 'fpilot.structure-symmetry.v1'


def load_request(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) != {'source_file', 'source_name', 'options', 'output_file'}:
        raise ValueError('对称性请求字段无效')
    if value['source_name'] not in ('POSCAR', 'CONTCAR'):
        raise ValueError('结构来源仅支持 POSCAR/CONTCAR')
    options = value['options']
    if not isinstance(options, dict) or set(options) != {'structure_source', 'angle_tolerance_degree'} or options['structure_source'] != value['source_name']:
        raise ValueError('对称性选项无效')
    angle = options['angle_tolerance_degree']
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle) or not 0 < angle <= 30:
        raise ValueError('angle_tolerance 必须位于 0–30°')
    source = Path(value['source_file'])
    if source.name != value['source_name'] or not source.is_file() or source.is_symlink() or source.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('结构来源文件无效或超过 10 MiB')
    return value, source


def parse_structure(source):
    raw = source.read_bytes()
    lines = raw.decode('utf-8-sig').splitlines()
    if len(lines) < 8 or all(token.isdigit() for token in lines[5].split()):
        raise ValueError('需要带明确元素名称的完整 VASP5/6 POSCAR')
    structure = Poscar.from_str(raw.decode('utf-8-sig')).structure
    if not structure.is_ordered or not 0 < len(structure) <= 1500 or structure.volume <= 1e-8:
        raise ValueError('只支持非空、完整占位且晶胞体积为正的结构')
    return raw, structure


def analyze(source, source_name, angle):
    raw, structure = parse_structure(source)
    rows = []
    for symprec in TOLERANCES:
        dataset = SpacegroupAnalyzer(structure, symprec=symprec, angle_tolerance=angle).get_symmetry_dataset()
        if dataset is None:
            raise ValueError(f'symprec={symprec} Å 未能识别空间对称性')
        equivalent = [int(value) for value in dataset.equivalent_atoms]
        wyckoffs = [str(value) for value in dataset.wyckoffs]
        groups = {}
        for index, representative in enumerate(equivalent):
            groups.setdefault(representative, []).append(index + 1)
        rows.append({'symprec_angstrom': symprec, 'angle_tolerance_degree': angle,
                     'space_group_symbol': str(dataset.international), 'space_group_number': int(dataset.number),
                     'hall_number': int(dataset.hall_number), 'wyckoff_letters_by_site': wyckoffs,
                     'equivalent_representative_1based': [index + 1 for index in equivalent],
                     'equivalent_groups': [{'representative_1based': key + 1, 'site_indices_1based': members,
                                            'element': structure[key].specie.symbol, 'wyckoff_letter': wyckoffs[key]}
                                           for key, members in sorted(groups.items())]})
    return {'schema': SCHEMA,
            'input': {'name': source_name, 'sha256': hashlib.sha256(raw).hexdigest(),
                      'atom_count': len(structure), 'formula': structure.composition.reduced_formula},
            'libraries': {'pymatgen': version('pymatgen'), 'spglib': spglib.__version__},
            'parameters': {'symprec_angstrom': list(TOLERANCES), 'angle_tolerance_degree': angle},
            'results': rows, 'scientific_review': 'NOT_PERFORMED',
            'scope': '仅普通空间群与等价原子；不分析磁空间群，不修正或替换结构。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'run'))
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        request, source = load_request(args.request)
        if args.action == 'check':
            raw, structure = parse_structure(source)
            print(json.dumps({'schema': 'fpilot.extension-applicability.v1', 'applicability_state': 'APPLICABLE',
                              'source_sha256': hashlib.sha256(raw).hexdigest(), 'formula': structure.composition.reduced_formula,
                              'reason': '结构可解析；运行时比较三个空间对称性容差'}, ensure_ascii=False))
            return 0
        result = analyze(source, request['source_name'], float(request['options']['angle_tolerance_degree']))
        output = Path(request['output_file'])
        if output.name != 'symmetry.json' or output.parent != args.request.parent:
            raise ValueError('输出路径必须为请求目录内的 symmetry.json')
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'schema': SCHEMA, 'output': output.name,
                          'summary': [{'symprec_angstrom': row['symprec_angstrom'],
                                       'space_group_symbol': row['space_group_symbol'],
                                       'space_group_number': row['space_group_number']}
                                      for row in result['results']]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
