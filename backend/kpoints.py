"""Read-only VASPKIT-style mesh proposals using the current pymatgen runtime."""
import hashlib
import json
import math
import re
import numpy as np
from pymatgen.core import Lattice


def lattice_hash(matrix):
    return hashlib.sha256(json.dumps(matrix, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def suggest(lattice, kpr=0.04, center='Gamma', gamma_only=False):
    try:
        matrix = np.asarray(lattice, dtype=float)
        spacing = float(kpr)
    except (ValueError, TypeError):
        raise ValueError('请提供有效的三维晶格与 kpr 数值')
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or np.linalg.det(matrix) < 1e-8:
        raise ValueError('晶格须为有限、非退化的右手三维晶格，单位 Å')
    if not math.isfinite(spacing) or spacing <= 0 or spacing > 1 or type(kpr) is bool:
        raise ValueError('kpr 须大于 0 且不超过 1；Gamma-only 请使用单独选项')
    if center not in ('Gamma', 'Monkhorst-Pack') or type(gamma_only) is not bool:
        raise ValueError('网格中心或 Gamma-only 选择无效')
    reciprocal = Lattice(matrix).reciprocal_lattice_crystallographic
    grid = [1, 1, 1] if gamma_only else [max(1, math.ceil(length / spacing - 1e-12)) for length in reciprocal.abc]
    if any(n > 1000 for n in grid):
        raise ValueError('建议网格超过当前每个方向 1–1000 的支持范围，请增大 kpr')
    return {'grid': grid, 'center': 'Gamma' if gamma_only else center, 'kpr': spacing,
            'gamma_only': gamma_only, 'target_spacing_A_inv': 2 * math.pi * spacing,
            'actual_spacing_A_inv': [2 * math.pi * length / n for length, n in zip(reciprocal.abc, grid)],
            'total_points': math.prod(grid), 'lattice_sha256': lattice_hash(matrix.tolist()),
            'algorithm': 'vaspkit-reciprocal-ceil-v1', 'remote_checked': False}


def generation_info(raw, grid, center):
    if not isinstance(raw,str) or len(raw)>20000:
        raise ValueError('网格生成信息无效')
    try:
        value=json.loads(raw)
        generated=suggest(value['lattice'],value['kpr'],value['center'],value['gamma_only'])
        origin=value['source']
        if value['grid']!=grid or generated['grid']!=grid or value['center']!=center or value['lattice_sha256']!=generated['lattice_sha256'] or value['algorithm']!=generated['algorithm']:
            raise ValueError('网格与生成记录不一致；手动编辑后请移除旧生成信息')
        if origin['kind'] not in ('reference','contcar') or not isinstance(origin['label'],str) or len(origin['label'])>1000:
            raise ValueError('网格来源信息无效')
        if origin['kind']=='contcar' and not re.fullmatch(r'[a-f0-9]{32}',origin.get('attempt_id','')):
            raise ValueError('最终结构来源身份无效')
        return {**generated,'lattice':value['lattice'],'source':origin}
    except (KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('网格生成信息无效') from exc
