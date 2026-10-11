"""On-demand, bounded PyW90 CLI advice on the source server.

Numerical advice and complete bounded logs cross SSH; inputs stay in place.
"""
from __future__ import annotations

from . import wannier_lineage

import math
import re
import shlex
import json
from pathlib import Path

from . import execution, remote_paths, wannier_execution

PYTHON = {'jutai-1': '/home/user/anaconda3/envs/eDMFT/bin/python'}
PACKAGE = '$HOME/230269265lht/tools/pyw90/1.0.7'
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?'
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def _file_hashes(alias, path, names):
    command = 'set -e\ncd ' + remote_paths.shell_path(path) + '\n'
    command += '\n'.join('test -s ' + shlex.quote(name) + ' && test ! -L ' + shlex.quote(name)
                         for name in names) + '\n'
    command += 'sha256sum -- ' + ' '.join(map(shlex.quote, names))
    receipt = execution.ssh(alias, command, timeout=90, stdout_limit=4000)
    if receipt['exit_code'] != 0:
        raise ValueError('远端分析输入缺失或不可读：' + receipt.get('stderr', '')[-250:])
    rows = receipt['stdout'].splitlines()
    if len(rows) != len(names):
        raise ValueError('远端分析文件哈希回执不完整')
    hashes = {}
    for name, row in zip(names, rows):
        match = re.fullmatch(r'([a-f0-9]{64})\s+\*?' + re.escape(name), row)
        if not match:
            raise ValueError('远端分析文件哈希回执格式异常')
        hashes[name] = match.group(1)
    return hashes


def _run(alias, path, arguments, input_names):
    if alias not in PYTHON:
        raise ValueError('该服务器尚未登记 PyW90 解释器和独立安装目录')
    invocation = ' '.join(map(shlex.quote, arguments))
    adapter = Path(__file__).with_name('pyw90_adapter.py').read_text(encoding='utf-8')
    checker = Path(__file__).with_name('pyw90_window_check.py').read_text(encoding='utf-8')
    links = '\n'.join('ln -s "$source_dir"/' + shlex.quote(name) +
                      ' "$scratch"/' + shlex.quote(name) for name in input_names)
    command = ('set -e\ncd ' + remote_paths.shell_path(path) + '\n'
               'source_dir=$(pwd -P)\n'
               'scratch_root="$HOME/230269265lht/.fpilot/pyw90-advice"\n'
               'mkdir -p "$scratch_root"\n'
               'scratch=$(mktemp -d "$scratch_root/run.XXXXXX")\n'
               "trap 'rm -rf -- \"$scratch\"' EXIT\n"
               + links + '\ncd "$scratch"\n'
               'PYTHONPATH="' + PACKAGE + '" MPLBACKEND=Agg timeout 180 '
               + shlex.quote(PYTHON[alias])
               + ' -c ' + shlex.quote(checker + '\n' + adapter + '\nmain()\n') + ' '
               + invocation + '\n')
    receipt = execution.ssh(alias, command, timeout=210, stdout_limit=524288)
    if receipt['exit_code'] != 0 or receipt.get('stdout_truncated') or receipt.get('stderr_truncated'):
        diagnostic = receipt.get('stderr', '')[-700:]
        if 'KeyError: PeriodicSite:' in diagnostic:
            raise ValueError('所选 vasprun.xml 缺少 PyW90 pre dos 所需的站点轨道投影；请选择包含投影 DOS 的已完成 DOS attempt（如设置 LORBIT=11）')
        if 'FileNotFoundError:' in diagnostic:
            raise ValueError('PyW90 所需的远端输入文件缺失：' + diagnostic.split('FileNotFoundError:')[-1].strip())
        raise ValueError('PyW90 远端分析未完成：' + diagnostic +
                         ('；输出超过上限' if receipt.get('stdout_truncated') else ''))
    return {'arguments': arguments, 'stdout': ANSI.sub('', receipt['stdout']),
            'stderr': ANSI.sub('', receipt.get('stderr', '')), 'exit_code': receipt['exit_code']}


def _remote_num_wann(alias, path, seed):
    wannier_execution.source_names(seed)
    command = ('set -e\ncd ' + remote_paths.shell_path(path) + '\n'
               'win=' + shlex.quote(seed + '.win') + '\n'
               'if ! test -s "$win"; then win=wannier90.win; fi\n'
               'test -f "$win" && test ! -L "$win" && test -s "$win"\n'
               'printf "FPILOT_WIN=%s\\n" "$win"\n'
               'sed -n -E "s/^[[:space:]]*[Nn][Uu][Mm]_[Ww][Aa][Nn][Nn][[:space:]]*[:=][[:space:]]*([0-9]+).*/FPILOT_NUM_WANN=\\1/p" "$win" | tail -1\n')
    receipt = execution.ssh(alias, command, timeout=30, stdout_limit=1000)
    if receipt['exit_code'] != 0:
        raise ValueError('远端 .win 缺失或不可读：' + receipt.get('stderr', '')[-250:])
    win = re.search(r'^FPILOT_WIN=(wannier90(?:\.[12])?\.win)$', receipt['stdout'], re.M)
    number = re.search(r'^FPILOT_NUM_WANN=([0-9]+)$', receipt['stdout'], re.M)
    if not win or not number or int(number.group(1)) < 1:
        raise ValueError('.win 缺少有效的 num_wann，无法生成能窗建议')
    return win.group(1), int(number.group(1))


def _table(text, columns):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        header = line.split()
        if not all(column in header for column in columns):
            continue
        rows = []
        for next_line in lines[index + 1:]:
            cells = next_line.split()
            if not cells or not cells[0].isdigit():
                if rows:
                    break
                continue
            try:
                values = {column: float(cells[1 + header.index(column)]) for column in columns}
            except (IndexError, ValueError):
                break
            if all(math.isfinite(value) for value in values.values()):
                rows.append(values)
        return rows
    return []


def _marker(output, name):
    match = re.search(r'^' + name + r'=(.+)$', output, re.M)
    if not match:
        raise ValueError('PyW90 结构化核验回执缺失：' + name)
    return json.loads(match.group(1))


def _absolute_windows(relative_windows, fermi):
    if relative_windows is None:
        return None
    tags = ('dis_win_min', 'dis_win_max', 'dis_froz_min', 'dis_froz_max')
    if set(relative_windows) - set(tags) or not all(isinstance(v, (int, float)) and
                                                  math.isfinite(v) for v in relative_windows.values()):
        raise ValueError('请填写有限的相对费米能量边界')
    return {tag: value + fermi for tag, value in relative_windows.items()}


def _interface_check(alias, path, seed, win_name, fermi, emin, emax, relative_windows, candidates=None):
    request = {'seed': seed, 'eig_name': seed + '.eig', 'win_name': win_name,
               'fermi_eV': fermi, 'absolute_range': [emin + fermi, emax + fermi],
               'absolute_windows': _absolute_windows(relative_windows, fermi),
               'candidates': candidates or {}}
    run = _run(alias, path, ['window-check', json.dumps(request, allow_nan=False)], (seed + '.eig', win_name))
    return _marker(run['stdout'], 'FPILOT_WINDOW_CHECK')


def _source(project, node_id, parent_id, alias, seed, emin, emax, energy_reference):
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (emin, emax)) or not -100 <= emin < emax <= 100:
        raise ValueError('请填写有限且递增的相对费米能量范围')
    parent, _ = wannier_execution._parent(project, node_id, parent_id, alias)
    reference = energy_reference or {}
    fermi = reference.get('fermi_eV')
    if not isinstance(fermi, (int, float)) or not math.isfinite(fermi):
        raise ValueError('请先读取 VASP-Wannier OUTCAR 的费米能级')
    path = execution.attempt_path(parent)['shell']
    win_name, nwann = _remote_num_wann(alias, path, seed)
    hashes = _file_hashes(alias, path, ('OUTCAR', win_name, seed + '.eig'))
    if hashes['OUTCAR'] != reference.get('outcar_sha256'):
        raise ValueError('OUTCAR 与页面能量参考不一致，请重新读取后分析')
    return parent, path, win_name, nwann, fermi, hashes


def check(project, node_id, parent_id, alias, seed, emin, emax, energy_reference,
          relative_windows=None, expected_source_hashes=None):
    _, path, win_name, _, fermi, hashes = _source(project, node_id, parent_id, alias, seed, emin, emax, energy_reference)
    if expected_source_hashes and any(hashes.get(name) != value for name, value in expected_source_hashes.items()):
        raise ValueError('候选的接口来源已变化，请重新分析')
    report = _interface_check(alias, path, seed, win_name, fermi, emin, emax, relative_windows)
    if _file_hashes(alias, path, tuple(hashes)) != hashes:
        raise ValueError('窗口检查期间接口输入发生变化，请重新读取')
    return {**report, 'source_hashes': hashes, 'parent_attempt_id': parent_id}


def recommend(project, node_id, parent_id, alias, seed, mode, emin, emax,
              energy_reference, dos_attempt_id=None, relative_windows=None):
    if mode not in ('eig', 'pre') or not all(isinstance(v, (int, float)) and math.isfinite(v)
                                              for v in (emin, emax)) or not -100 <= emin < emax <= 100:
        raise ValueError('请选择 eig/pre 和有限的相对费米能量范围')
    parent, parent_path, win_name, nwann, fermi, parent_hashes = _source(
        project, node_id, parent_id, alias, seed, emin, emax, energy_reference)
    # PyW90 1.0.7 mixes raw and shifted bounds in its frozen-window branch.
    # Invoke both modes with absolute values and shift only energy fields back.
    common = ['--path', '.', '-e', str(emin + fermi), str(emax + fermi)]
    runs = []
    if mode == 'eig':
        run = _run(alias, parent_path, ['eig', 'suggest', *common, '-i', seed + '.eig',
                                           '--efermi', str(fermi), '-w', str(nwann)],
                      (seed + '.eig',))
        runs.append(run)
        output = run['stdout']
        columns = ('dis_win_min', 'dis_win_max', 'i+1_min', 'i_max', 'Nleast', 'Nmost')
        candidates = _table(output, columns)
        frozen = _table(output, ('dis_froz_min', 'dis_froz_max', 'N'))
        result = {'outer': candidates, 'frozen': frozen}
        source_hashes = parent_hashes
        source_id = parent_id
    else:
        if dos_attempt_id:
            dos = execution.get(dos_attempt_id)
            manifest, _ = execution.bundle(dos['bundle_id'], allow_dependencies=True)
            if (dos.get('project_id') != project['id'] or dos.get('state') != 'COMPLETED' or
                dos.get('config', {}).get('alias') != alias or manifest.get('stage') != 'dos' or
                wannier_lineage.scf_id(dos) !=
                    wannier_lineage.scf_id(parent)):
                raise ValueError('DOS 来源须为同项目、同服务器、同 SCF 来源的已完成 DOS attempt')
            dos_path = execution.attempt_path(dos)['shell']
            source_id = dos_attempt_id
        else:
            dos_path = parent_path
            source_id = parent_id
        source_hashes = _file_hashes(alias, dos_path, ('vasprun.xml', 'EIGENVAL'))
        # pre dos has no --efermi option. Use the parent OUTCAR's absolute
        # range so a separate DOS source cannot silently shift the reference.
        args = ['pre', 'dos', '--path', '.', '-e', str(emin + fermi), str(emax + fermi)]
        if seed.endswith('.2'):
            args.append('--spin-down')
        first_run = _run(alias, dos_path, args, ('vasprun.xml', 'EIGENVAL'))
        runs.append(first_run)
        first = first_run['stdout']
        selected = re.search(r'pyw90 --extra input:\s*\n([^\n]+)', first)
        projection = re.search(r'Wannier90 Projection:\s*\n(.*?)\n\s*pyw90 --extra input:', first, re.S)
        result = {'projections': projection.group(1).strip().splitlines() if projection else [],
                  'frozen': [], 'outer': [], 'dos_selection': _marker(first, 'FPILOT_DOS_SELECTION')}
        if selected and selected.group(1).strip():
            second_run = _run(alias, dos_path, [*args, '--extra', selected.group(1).strip(),
                                             '--fpilot-num-wann', str(nwann)],
                          ('vasprun.xml', 'EIGENVAL'))
            runs.append(second_run)
            second = second_run['stdout']
            result['eigenval'] = _marker(second, 'FPILOT_EIGENVAL')
            result['frozen'] = _table(second, ('dis_froz_min', 'dis_froz_max', 'N', 'pdos', 'tdos', 'percent'))
            match = re.search(r'Lowest `dis_win_max` for [^:]+:\s*(' + NUMBER + ')', second)
            if match and 'There is no enough states' not in second:
                result['minimum_outer_max'] = float(match.group(1))
        if _file_hashes(alias, dos_path, ('vasprun.xml', 'EIGENVAL')) != source_hashes:
            raise ValueError('PyW90 分析期间 DOS 输入发生变化，请刷新来源后重新分析')
    mesh = _interface_check(alias, parent_path, seed, win_name, fermi, emin, emax, relative_windows,
                            {group: result[group] for group in ('outer', 'frozen')})
    if mode == 'pre' and result.get('eigenval') and bool(result['eigenval'].get('spinors')) != mesh['basis']['spinors']:
        raise ValueError('DOS 与当前接口的 SOC／spinors 模型不同，请更换 DOS 来源')
    if mesh['basis']['num_wann'] != nwann or _file_hashes(alias, parent_path, tuple(parent_hashes)) != parent_hashes:
        raise ValueError('PyW90 分析期间接口输入发生变化，请刷新来源后重新分析')
    mesh['source_hashes'] = parent_hashes
    energy_tags = {'dis_win_min', 'dis_win_max', 'dis_froz_min', 'dis_froz_max', 'i+1_min', 'i_max'}
    for group in ('outer', 'frozen'):
        for row, checked in zip(result[group], mesh['candidates'][group]):
            for tag in energy_tags & row.keys():
                row[tag] = round(row[tag] - fermi, 10)
            row['check'] = checked
    if 'minimum_outer_max' in result:
        result['minimum_outer_max'] = round(result['minimum_outer_max'] - fermi, 10)
    note = '候选数值由 PyW90 命令行给出；未自动写入 .win，也不代表局域化质量合格。'
    if mode == 'pre' and not result['frozen']:
        note = '分析已完成，但当前范围未生成冻结窗候选；请核对分析范围与投影选择。' + note
    return {'schema': 'fpilot.pyw90-advice.v2', 'mode': mode, 'seed': seed,
            'source_attempt_id': source_id, 'source_hashes': source_hashes,
            'interface_source_hashes': parent_hashes, 'interface': mesh, 'runs': runs,
            'fermi_eV': fermi, 'relative_range_eV': [emin, emax],
            'pyw90_version': '1.0.7', 'advice': result,
            'note': note}
