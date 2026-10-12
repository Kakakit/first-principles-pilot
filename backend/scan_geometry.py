"""Isotropic POSCAR scans relative to one verified baseline, without editing it."""
import hashlib
import math
from decimal import Decimal

from .poscar_text import parser_text
from .source_contracts import structure_geometry, tag

MODES = ('volume_ratio', 'volume', 'length_scale')
KEYS = {'volume_ratio': 'V/V₀', 'volume': 'V / Å³', 'length_scale': '长度因子 s'}


def transform(text, mode, value):
    if mode not in MODES:
        raise ValueError('POSCAR 扫描方式无效')
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError('POSCAR 扫描值必须大于零；目标体积使用正数填写，输出为负 scale')
    normalized, info = parser_text(text)
    if any(s in info['extra_sections'] for s in ('晶格速度', '预测校正')):
        raise ValueError('POSCAR 含晶格速度或预测校正段，不适用于独立体积扫描；请先用文本编辑核对结构')
    baseline = structure_geometry(normalized.encode())
    volume = baseline['volume']
    ratio = number ** 3 if mode == 'length_scale' else number if mode == 'volume_ratio' else number / volume
    target = volume * ratio
    if not math.isfinite(target) or target < 1e-8 or target > 1e12:
        raise ValueError('扫描后的晶胞体积超出范围')
    lines = normalized.splitlines()
    # Normalize the baseline first: replacing a negative/triple scale directly
    # would otherwise use the unscaled lattice (and wrong Cartesian positions).
    lines[1] = str(value) if mode == 'length_scale' else '-' + (
        str(value) if mode == 'volume' else format(Decimal(str(volume)) * Decimal(str(value)), 'f'))
    result = '\n'.join(lines) + '\n'
    _, parsed = parser_text(result)
    geometry = structure_geometry(result.encode())
    return result, {
        'schema': 'fpilot.poscar-scan.v1', 'mode': mode, 'value': str(value),
        'baseline_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'poscar_sha256': hashlib.sha256(result.encode()).hexdigest(),
        'volume_before_A3': volume, 'volume_A3': geometry['volume'],
        'volume_ratio': geometry['volume'] / volume, 'length_factor': ratio ** (1 / 3),
        'lengths_A': geometry['lengths'], 'scale': parsed['scale'],
    }


def validate_source(policy):
    if policy['mode'] in ('self', 'manual') or policy['purpose'] == 'continue' or 'WAVECAR' in policy['required_files']:
        raise ValueError('POSCAR 扫描是独立计算，请选择新计算或上游结构/电荷初值；不使用同节点续算或 WAVECAR')


def warnings(incar, stage):
    if stage == 'relax' and tag(incar, 'ISIF') not in ('0', '1', '2', '4', '5'):
        return ['当前 ISIF=' + str(tag(incar, 'ISIF') or '未设置') + ' 允许改变体积；负 scale 只指定初始体积。固定体积 EOS 请在基础节点设置 ISIF=4（形状与原子优化）或 ISIF=2（仅原子优化），本批次不会自动改写 ISIF。']
    return []
