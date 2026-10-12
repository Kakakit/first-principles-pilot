"""Small POSCAR syntax/scale adapter for the installed pymatgen parser.

Only the parser copy is normalized. The user's execution text stays verbatim.
"""
import math
import numpy as np
from pymatgen.core import Element


def _numbers(line, count, label):
    tokens = line.split()
    if len(tokens) < count:
        raise ValueError(f'{label}需要 {count} 个数值。')
    try:
        values = [float(v.replace('D', 'E').replace('d', 'e')) for v in tokens[:count]]
    except ValueError as exc:
        raise ValueError(f'{label}含无效数值。') from exc
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f'{label}需要有限数值。')
    return values


def _vector(line, label):
    if len(line.split()) != 3:
        raise ValueError(f'{label}需要恰好三个数值。')
    return ' '.join(format(v, '.17g') for v in _numbers(line, 3, label))


def parser_text(text):
    lines = text.lstrip('\ufeff').splitlines()
    if len(lines) < 8:
        raise ValueError('POSCAR 不完整。')
    scales = lines[1].split()
    if len(scales) not in (1, 3):
        raise ValueError('scale 行需要一个非零数，或三个正数。')
    scale = _numbers(lines[1], len(scales), 'scale 行')
    lattice = np.array([_numbers(line, 3, '晶格向量') for line in lines[2:5]])
    determinant = float(np.linalg.det(lattice))
    if not math.isfinite(determinant) or determinant <= 0:
        raise ValueError('晶胞必须是有限、非退化的右手晶胞（行列式 > 0）。')
    if len(scale) == 3:
        if any(v <= 0 for v in scale):
            raise ValueError('三分量 scale 必须全部为正数。')
        factors = np.array(scale)
    else:
        if scale[0] == 0:
            raise ValueError('scale 不能为零。')
        factor = scale[0] if scale[0] > 0 else (-scale[0] / determinant) ** (1 / 3)
        factors = np.array([factor] * 3)
    species = lines[5].split()
    if not species or any(not Element.is_valid_symbol(s) for s in species):
        raise ValueError('需要明确的元素名称行；请使用 VASP 5/6 POSCAR。')
    counts = lines[6].split()
    if len(counts) != len(species) or any(not s.isdigit() for s in counts):
        raise ValueError('元素与原子数量行必须对应，数量须为非负整数。')
    count = sum(map(int, counts))
    if not 0 < count <= 1500:
        raise ValueError('当前支持 1–1500 个原子的结构。')
    cursor = 7
    selective = lines[cursor].strip().lower().startswith('s')
    if selective:
        cursor += 1
    if cursor >= len(lines) or not lines[cursor].strip():
        raise ValueError('缺少 Direct/Cartesian 坐标类型。')
    mode = lines[cursor].strip()[0].lower()
    if mode not in ('d', 'c', 'k'):
        raise ValueError('坐标类型需要 Direct 或 Cartesian。')
    normalized = [lines[0] or 'POSCAR', '1.0']
    with np.errstate(over='ignore', invalid='ignore'):
        actual = lattice * factors
    if not np.isfinite(actual).all():
        raise ValueError('scale 后的晶格超出有效数值范围。')
    normalized += [' '.join(format(v, '.17g') for v in row) for row in actual]
    normalized += [lines[5], lines[6]]
    if selective:
        normalized.append('Selective dynamics')
    normalized.append('Direct' if mode == 'd' else 'Cartesian')
    cursor += 1
    if len(lines) < cursor + count:
        raise ValueError('原子坐标行数少于原子数量。')
    for index in range(count):
        line = lines[cursor + index]
        coords = np.array(_numbers(line, 3, f'原子 {index + 1} 坐标'))
        if mode != 'd':
            with np.errstate(over='ignore', invalid='ignore'):
                coords = coords * factors
        if not np.isfinite(coords).all():
            raise ValueError('scale 后的原子坐标超出有效数值范围。')
        tokens = line.split()
        flags = []
        if selective:
            flags = tokens[3:6]
            if len(flags) != 3 or any(f.upper() not in ('T', 'F') for f in flags):
                raise ValueError(f'原子 {index + 1} 的约束需要三个 T/F。')
        normalized.append(' '.join([*(format(v, '.17g') for v in coords), *(f.upper() for f in flags)]))
    cursor += count
    extras = []

    def blanks():
        nonlocal cursor
        start = cursor
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        return cursor > start

    separated = blanks()
    if cursor < len(lines) and lines[cursor].strip().lower().startswith('lattice velocities'):
        if len(lines) < cursor + 8 or not lines[cursor + 1].strip().isdigit():
            raise ValueError('晶格速度段不完整，需初始化值及六行向量。')
        normalized += ['Lattice velocities and vectors', lines[cursor + 1]]
        normalized += [_vector(lines[cursor + i], '晶格速度段') for i in range(2, 8)]
        cursor += 8
        extras.append('晶格速度')
        separated = blanks()
    if cursor < len(lines):
        # Older pymatgen expects a numeric velocity chunk without its mode line.
        first = lines[cursor].strip().lower()
        if first.startswith(('cartesian', 'direct')) or first in ('c', 'd', 'k'):
            cursor += 1
        elif not separated:
            raise ValueError('原子速度段需要空行或 Direct/Cartesian 类型行；请核对原子数量和多余坐标。')
        if len(lines) < cursor + count:
            raise ValueError('原子速度段不完整或附加段无法识别。')
        normalized.append('')
        normalized += [_vector(lines[cursor + i], '原子速度段') for i in range(count)]
        cursor += count
        extras.append('原子速度')
        separated = blanks()
        if cursor < len(lines):
            if not separated:
                raise ValueError('预测校正段前需要空行。')
            if len(lines) < cursor + 3 + 3 * count:
                raise ValueError('预测校正段不完整或附加段无法识别。')
            if not lines[cursor].strip().isdigit():
                raise ValueError('预测校正段初始化值无效。')
            _numbers(lines[cursor + 1], 1, '预测校正步长')
            thermostat = lines[cursor + 2].split()
            if not thermostat:
                raise ValueError('预测校正恒温器参数为空。')
            _numbers(lines[cursor + 2], len(thermostat), '预测校正恒温器参数')
            normalized += ['', *lines[cursor:cursor + 3]]
            normalized += [_vector(lines[cursor + 3 + i], '预测校正段') for i in range(3 * count)]
            cursor += 3 + 3 * count
            extras.append('预测校正')
            blanks()
    if cursor != len(lines):
        raise ValueError('存在无法识别的附加内容；本次编辑未应用。')
    return '\n'.join(normalized) + '\n', {
        'scale': scale, 'coordinate_mode': 'fractional' if mode == 'd' else 'cartesian',
        'species': species, 'counts': list(map(int, counts)), 'extra_sections': extras,
    }
