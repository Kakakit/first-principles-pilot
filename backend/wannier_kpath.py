"""Convert the selected VASP line-mode path locally; never regenerate symmetry paths."""
from pymatgen.io.vasp.inputs import Kpoints


def read(raw):
    try:
        points = Kpoints.from_str(raw.decode('utf-8-sig'))
    except (ValueError, IndexError, UnicodeError) as exc:
        raise ValueError('DFT KPOINTS 无法解析，请核对所选能带来源') from exc
    if points.style != Kpoints.supported_modes.Line_mode:
        raise ValueError('所选 DFT KPOINTS 不是 line-mode 路径；可手动编辑 kpoint_path')
    if not str(points.coord_type).lower().startswith('r'):
        raise ValueError('DFT KPOINTS 使用笛卡尔坐标，请先转换为倒格矢分数坐标或手动编辑 kpoint_path；不会直接误用坐标')
    if not 2 <= points.num_kpts <= 10000 or not len(points.kpts) or len(points.kpts) % 2 or len(points.kpts) > 400:
        raise ValueError('DFT line-mode KPOINTS 的采样数或端点数量无效')
    labels = points.labels or [None] * len(points.kpts)
    named = {}; warnings = []
    endpoints = []
    for index, (point, label) in enumerate(zip(points.kpts, labels)):
        key = tuple(float(value) for value in point)
        if not label:
            label = named.setdefault(key, 'K' + str(index + 1))
            if not warnings:
                warnings.append('部分端点没有标签，已使用 K 编号；路径坐标与分段保持原样')
        endpoints.append(str(label) + ' ' + ' '.join(format(value, '.12g') for value in key))
    return {'path_lines': [' '.join(endpoints[i:i+2]) for i in range(0, len(endpoints), 2)],
            'bands_num_points': points.num_kpts, 'warnings': warnings}
