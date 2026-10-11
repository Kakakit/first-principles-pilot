"""Read-only interface-mesh diagnostics, also sent to the remote interpreter.

Counts use individual eigenvalues at each k point, never band envelopes.
Energy values in this module are absolute eV until the response boundary.
"""
import json
import math
import re

import numpy as np

WINDOW_TAGS = ('dis_win_min', 'dis_win_max', 'dis_froz_min', 'dis_froz_max')


def read_eig(path):
    data = np.loadtxt(path, ndmin=2)
    if data.shape[1] != 3 or not len(data) or not np.isfinite(data).all():
        raise ValueError('.eig 必须包含完整、有限的带号、k 点号和能量')
    indices = data[:, :2]
    if not np.equal(indices, np.floor(indices)).all() or (indices < 1).any():
        raise ValueError('.eig 的带号与 k 点号必须是从 1 开始的整数')
    nbands, nkpts = map(int, indices.max(axis=0))
    if len(data) != nbands * nkpts:
        raise ValueError('.eig 声明的 k 点／能带网格不完整')
    ordered = data[np.lexsort((data[:, 0], data[:, 1]))]
    expected = np.column_stack((np.tile(np.arange(1, nbands + 1), nkpts),
                                np.repeat(np.arange(1, nkpts + 1), nbands)))
    if not np.array_equal(ordered[:, :2], expected):
        raise ValueError('.eig 存在重复或缺失的带号／k 点号')
    energies = ordered[:, 2].reshape(nkpts, nbands)
    if (np.diff(energies, axis=1) < -1e-7).any():
        raise ValueError('.eig 的带号未按能量排序，无法可靠定位相邻带间隙')
    return energies


def read_basis(path, seed):
    with open(path, encoding='utf-8') as stream:
        lines = [re.split(r'[!#]', line, 1)[0].strip() for line in stream]
    values, projections, in_projection = {}, [], False
    for line in lines:
        if re.fullmatch(r'begin\s+projections', line, re.I):
            in_projection = True
        elif re.fullmatch(r'end\s+projections', line, re.I):
            in_projection = False
        elif in_projection and line:
            projections.append(line)
        else:
            match = re.fullmatch(r'(\w+)\s*[:=]\s*(.*?)\s*', line)
            if match:
                tag, value = match.groups()
                tag = tag.lower()
                if tag == 'num_wann' and tag in values:
                    raise ValueError('.win 的 num_wann 重复，无法确定当前模型')
                values[tag] = value
    nwann = int(values.get('num_wann', '0'))
    if nwann < 1:
        raise ValueError('.win 缺少有效的 num_wann')
    spinors = values.get('spinors', 'false').lower().strip('.') in ('true', 't')
    return {'num_wann': nwann, 'spinors': spinors,
            'channel': 'spinor' if spinors else 'down' if seed.endswith('.2') else
                       'up' if seed.endswith('.1') else 'single',
            'projections': projections, 'exclude_bands': values.get('exclude_bands', ''),
            'window_parameters': {tag: float(values[tag].replace('D', 'E').replace('d', 'e'))
                                  for tag in WINDOW_TAGS if tag in values}}


def state_counts(energies, lower, upper):
    per_k = np.sum((energies >= lower) & (energies <= upper), axis=1)
    return {'min': int(per_k.min()), 'max': int(per_k.max()),
            'min_kpoint': int(per_k.argmin()) + 1, 'max_kpoint': int(per_k.argmax()) + 1}


def global_gaps(energies, eps=0.004):
    low, high = energies.min(axis=0), energies.max(axis=0)
    return [{'lower_band': i + 1, 'upper_band': i + 2,
             'i_max': float(high[i]), 'i+1_min': float(low[i + 1]),
             'width_eV': float(low[i + 1] - high[i]),
             'midpoint_eV': float((low[i + 1] + high[i]) / 2)}
            for i in range(len(low) - 1) if low[i + 1] > high[i] + eps]


def lower_diagnostic(energies, lower, gaps):
    low, high = energies.min(axis=0), energies.max(axis=0)
    crossing = [{'band': i + 1, 'min_eV': float(low[i]), 'max_eV': float(high[i])}
                for i in range(len(low)) if low[i] <= lower <= high[i]]
    containing = next((gap for gap in gaps if gap['i_max'] < lower < gap['i+1_min']), None)
    below = [g for g in gaps if g['i+1_min'] <= lower]
    above = [g for g in gaps if g['i_max'] >= lower]
    return {'value_eV': lower, 'classification': 'crossing' if crossing else
            'gap' if containing else 'below_all' if lower < float(low.min()) else
            'above_all' if lower > float(high.max()) else 'no_resolved_gap',
            'crossing_bands': crossing, 'containing_gap': containing,
            'nearest_below': max(below, key=lambda g: g['i+1_min']) if below else None,
            'nearest_above': min(above, key=lambda g: g['i_max']) if above else None}


def evaluate_windows(energies, nwann, values, target_range):
    outer_min = values.get('dis_win_min', float(energies.min()))
    outer_max = values.get('dis_win_max', float(energies.max()))
    froz_max = values.get('dis_froz_max')
    froz_min = values.get('dis_froz_min', outer_min) if froz_max is not None else None
    resolved = dict(zip(WINDOW_TAGS, (outer_min, outer_max, froz_min, froz_max)))
    errors = []
    if outer_min >= outer_max:
        errors.append('外窗下界必须小于上界')
    outer = state_counts(energies, outer_min, outer_max)
    if outer['min'] < nwann:
        errors.append(f"完整外窗最少 {outer['min']} 个态，小于 num_wann={nwann}")
    frozen = None
    if froz_max is not None:
        frozen = state_counts(energies, froz_min, froz_max)
        if not outer_min <= froz_min <= froz_max <= outer_max:
            errors.append('冻结窗必须位于外窗内且下界不大于上界')
        if frozen['max'] > nwann:
            errors.append(f"完整冻结窗最多 {frozen['max']} 个态，超过 num_wann={nwann}")
    coverage = outer_min <= target_range[0] and target_range[1] <= outer_max
    return {'absolute_windows_eV': resolved, 'outer': outer, 'frozen': frozen,
            'valid': not errors, 'errors': errors, 'analysis_range_covered': coverage,
            'defaulted_tags': [tag for tag in WINDOW_TAGS if tag not in values]}


def window_check(request):
    energies = read_eig(request['eig_name'])
    basis = read_basis(request['win_name'], request['seed'])
    fermi = float(request['fermi_eV'])
    values = request.get('absolute_windows')
    if values is None:
        values = basis['window_parameters']
    if set(values) - set(WINDOW_TAGS) or not all(isinstance(v, (float, int)) and
                                               math.isfinite(v) for v in values.values()):
        raise ValueError('窗口检查只接受四项有限的能量边界')
    target = request['absolute_range']
    gaps = global_gaps(energies)
    current = evaluate_windows(energies, basis['num_wann'], values, target)
    candidates = {}
    for group, rows in request.get('candidates', {}).items():
        candidates[group] = []
        for row in rows:
            merged = {**values, **{tag: row[tag] for tag in WINDOW_TAGS if tag in row}}
            checked = evaluate_windows(energies, basis['num_wann'], merged, target)
            if group == 'outer':
                gap = next((g for g in gaps if abs(g['midpoint_eV'] - row['dis_win_min']) < 1e-7), None)
                checked['gap'] = gap
            candidates[group].append(checked)
    report = {'schema': 'fpilot.pyw90-window-check.v1', 'basis': basis,
              'nkpts': len(energies), 'nbands': energies.shape[1],
              'fermi_eV': fermi, 'mesh_scope': 'interface_eig_sampled_kpoints',
              'analysis_counts': state_counts(energies, *target), 'gaps': gaps,
              'analysis_lower': lower_diagnostic(energies, target[0], gaps),
              'outer_lower': lower_diagnostic(energies, current['absolute_windows_eV']['dis_win_min'], gaps),
              'current': current, 'candidates': candidates}
    # Retain absolute values; UI values consistently use E-E_F in eV.
    def relative_check(check):
        check['relative_windows_eV'] = {tag: None if value is None else value - fermi
                                       for tag, value in check['absolute_windows_eV'].items()}
    relative_check(current)
    for checks in candidates.values():
        for check in checks:
            relative_check(check)
    return report
