"""On-demand, bounded PyW90 CLI advice on the source server.

Only parsed numerical advice crosses SSH.  The calculation files stay in place.
"""
from __future__ import annotations

from . import wannier_lineage

import math
import re
import shlex

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
               + " -c 'from pyw90.pyw90_cli import main_cli; main_cli()' "
               + invocation + '\n')
    receipt = execution.ssh(alias, command, timeout=210, stdout_limit=120000)
    if receipt['exit_code'] != 0 or receipt.get('stdout_truncated'):
        diagnostic = receipt.get('stderr', '')[-700:]
        if 'KeyError: PeriodicSite:' in diagnostic:
            raise ValueError('所选 vasprun.xml 缺少 PyW90 pre dos 所需的站点轨道投影；请选择包含投影 DOS 的已完成 DOS attempt（如设置 LORBIT=11）')
        if 'FileNotFoundError:' in diagnostic:
            raise ValueError('PyW90 所需的远端输入文件缺失：' + diagnostic.split('FileNotFoundError:')[-1].strip())
        raise ValueError('PyW90 远端分析未完成：' + diagnostic +
                         ('；输出超过上限' if receipt.get('stdout_truncated') else ''))
    return ANSI.sub('', receipt['stdout'])


def _remote_num_wann(alias, path, seed):
    wannier_execution.source_names(seed)
    command = ('set -e\ncd ' + remote_paths.shell_path(path) + '\n'
               'win=' + shlex.quote(seed + '.win') + '\n'
               'if ! test -s "$win"; then win=wannier90.win; fi\n'
               'test -f "$win" && test ! -L "$win" && test -s "$win"\n'
               'printf "FPILOT_WIN=%s\\n" "$win"\n'
               'sed -n -E "s/^[[:space:]]*[Nn][Uu][Mm]_[Ww][Aa][Nn][Nn][[:space:]]*=[[:space:]]*([0-9]+).*/FPILOT_NUM_WANN=\\1/p" "$win" | tail -1\n')
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
            if len(rows) >= 50:
                break
        return rows
    return []


def recommend(project, node_id, parent_id, alias, seed, mode, emin, emax,
              energy_reference, dos_attempt_id=None):
    if mode not in ('eig', 'pre') or not all(isinstance(v, (int, float)) and math.isfinite(v)
                                              for v in (emin, emax)) or not -100 <= emin < emax <= 100:
        raise ValueError('请选择 eig/pre 和有限的相对费米能量范围')
    parent, _ = wannier_execution._parent(project, node_id, parent_id, alias)
    reference = energy_reference or {}
    fermi = reference.get('fermi_eV')
    if not isinstance(fermi, (int, float)) or not math.isfinite(fermi):
        raise ValueError('请先读取 VASP-Wannier OUTCAR 的费米能级')
    parent_path = execution.attempt_path(parent)['shell']
    win_name, nwann = _remote_num_wann(alias, parent_path, seed)
    parent_hashes = _file_hashes(alias, parent_path, ('OUTCAR', win_name, seed + '.eig'))
    if parent_hashes['OUTCAR'] != reference.get('outcar_sha256'):
        raise ValueError('OUTCAR 与页面能量参考不一致，请重新读取后分析')
    common = ['--path', '.', '--rm-fermi', '-e', str(emin), str(emax)]
    if mode == 'eig':
        output = _run(alias, parent_path, ['eig', 'suggest', *common, '-i', seed + '.eig',
                                           '--efermi', str(fermi), '-w', str(nwann)],
                      (seed + '.eig',))
        columns = ('dis_win_min', 'dis_win_max')
        candidates = _table(output, columns)
        frozen = _table(output, ('dis_froz_min', 'dis_froz_max'))
        for row in candidates + frozen:
            for tag in row:
                row[tag] = round(row[tag] - fermi, 8)
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
        args = ['pre', 'dos', *common]
        if seed.endswith('.2'):
            args.append('--spin-down')
        first = _run(alias, dos_path, args, ('vasprun.xml', 'EIGENVAL'))
        selected = re.search(r'pyw90 --extra input:\s*\n([^\n]+)', first)
        projection = re.search(r'Wannier90 Projection:\s*\n(.*?)\n\s*pyw90 --extra input:', first, re.S)
        result = {'projections': projection.group(1).strip().splitlines() if projection else [],
                  'frozen': [], 'outer': []}
        if selected and selected.group(1).strip():
            second = _run(alias, dos_path, [*args, '--extra', selected.group(1).strip()],
                          ('vasprun.xml', 'EIGENVAL'))
            result['frozen'] = _table(second, ('dis_froz_min', 'dis_froz_max', 'percent'))
            for row in result['frozen']:
                row['dis_froz_min'] = round(row['dis_froz_min'] - fermi, 8)
                row['dis_froz_max'] = round(row['dis_froz_max'] - fermi, 8)
            match = re.search(r'Lowest `dis_win_max` for [^:]+:\s*(' + NUMBER + ')', second)
            if match:
                result['minimum_outer_max'] = round(float(match.group(1)) - fermi, 8)
    return {'schema': 'fpilot.pyw90-advice.v1', 'mode': mode, 'seed': seed,
            'source_attempt_id': source_id, 'source_hashes': source_hashes,
            'fermi_eV': fermi, 'relative_range_eV': [emin, emax],
            'pyw90_version': '1.0.7', 'advice': result,
            'note': '候选数值由 PyW90 命令行给出；未自动写入 .win，也不代表局域化质量合格。'}
