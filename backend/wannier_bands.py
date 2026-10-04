"""PyW90 path proposal and checked Wannier90 band input assembly."""
from __future__ import annotations

import hashlib
import re
import shlex

from . import execution, pyw90_remote, remote_paths, wannier_execution


def path_proposal(project, node_id, parent_id, alias, band_attempt_id):
    parent, _ = wannier_execution._parent(project, node_id, parent_id, alias)
    band = execution.get(band_attempt_id)
    scf_id = (parent.get('dependency_snapshot') or {}).get('upstream_attempt_id')
    if not scf_id or (band.get('dependency_snapshot') or {}).get('upstream_attempt_id') != scf_id:
        raise ValueError('DFT 能带与 VASP-Wannier 不属于同一 SCF 来源')
    if band.get('project_id') != project['id'] or band.get('config', {}).get('alias') != alias or band.get('state') != 'COMPLETED':
        raise ValueError('请选择同项目、同服务器的已完成 DFT 能带 attempt')
    manifest, _ = execution.bundle(band['bundle_id'], allow_dependencies=True)
    if manifest.get('stage') != 'bands':
        raise ValueError('所选 attempt 不是 DFT 能带计算')
    path = execution.attempt_path(band)['shell']
    hashes = pyw90_remote._file_hashes(alias, path, ('KPOINTS', 'POSCAR'))
    # Both attempts must bind the same final SCF structure. The actual POSCAR
    # hashes may differ in formatting, so compare the frozen source identity.
    source = (band.get('dependency_snapshot') or {}).get('files', {}).get('CONTCAR', {})
    other = (parent.get('dependency_snapshot') or {}).get('files', {}).get('CONTCAR', {})
    if not source.get('sha256') or source['sha256'] != other.get('sha256'):
        raise ValueError('两个来源绑定的 SCF 最终结构不同，不能自动复用能带路径')
    output = pyw90_remote._run(alias, path, ['pre', 'kpath', '--path', '.'], ('KPOINTS',))
    match = re.search(r'(?ims)^\s*begin\s+kpoint_path\s*\n(.*?)^\s*end\s+kpoint_path\s*$', output)
    candidates = match.group(1).splitlines() if match else output.splitlines()
    lines = [line.strip() for line in candidates if len(line.split()) == 8 and
             not line.lstrip().startswith(('!', '#'))]
    if not lines or len(lines) > 200:
        raise ValueError('PyW90 能带路径为空或过长')
    for line in lines:
        fields = line.split()
        if len(fields) != 8 or not re.fullmatch(r'[A-Za-zΓ][A-Za-z0-9_Γ+-]*', fields[0]) or not re.fullmatch(r'[A-Za-zΓ][A-Za-z0-9_Γ+-]*', fields[4]):
            raise ValueError('PyW90 路径标签或坐标行格式无效')
        try:
            if not all(abs(float(fields[index])) <= 2 for index in (1, 2, 3, 5, 6, 7)):
                raise ValueError('PyW90 路径坐标超出可检查范围')
        except ValueError as exc:
            raise ValueError('PyW90 路径坐标无效') from exc
    receipt = execution.ssh(alias, 'set -e\ncd ' + remote_paths.shell_path(path) + "\nsed -n '2p' KPOINTS\n", timeout=30, stdout_limit=100)
    if receipt['exit_code'] != 0 or not re.fullmatch(r'\d+', receipt['stdout'].strip()):
        raise ValueError('DFT line-mode KPOINTS 采样数不可识别')
    points = int(receipt['stdout'].strip())
    if not 2 <= points <= 10000:
        raise ValueError('DFT 能带每段采样数无效')
    return {'project_id':project['id'], 'band_attempt_id': band_attempt_id, 'parent_attempt_id': parent_id,
            'scf_attempt_id': scf_id, 'alias': alias, 'kpoints_sha256': hashes['KPOINTS'],
            'poscar_sha256': hashes['POSCAR'], 'structure_sha256': source['sha256'],
            'path_lines': lines, 'bands_num_points': points,
            'path_sha256': hashlib.sha256(('\n'.join(lines)+'\n').encode()).hexdigest(),
            'source_path': path, 'generator': 'pyw90 pre kpath'}


def with_path(win, proposal, enabled=True):
    if not enabled:
        document = wannier_execution.win_contract.parse_win(win)
        if not document.occurrences('bands_plot'):
            return win
        request = {'schema': wannier_execution.win_contract.WIN_REQUEST_SCHEMA,
                   'add': {}, 'replace': {'bands_plot': '.false.'}}
        disabled, _ = wannier_execution.win_contract.merge_win(win, request)
        wannier_execution._checked_win(win, disabled)
        return disabled
    lines = proposal.get('path_lines')
    points = proposal.get('bands_num_points')
    if not isinstance(lines, list) or not lines or not isinstance(points, int):
        raise ValueError('请先获取 PyW90 能带路径')
    if not 2 <= points <= 10000 or len(lines) > 200:
        raise ValueError('能带路径段数或采样数无效')
    for line in lines:
        fields = line.split() if isinstance(line, str) else []
        if len(fields) != 8 or not all(re.fullmatch(r'[A-Za-zΓ][A-Za-z0-9_Γ+-]*', fields[i]) for i in (0, 4)):
            raise ValueError('能带路径标签或坐标行格式无效')
        try:
            coordinates = [float(fields[i]) for i in (1, 2, 3, 5, 6, 7)]
        except ValueError as exc:
            raise ValueError('能带路径坐标无效') from exc
        if not all(abs(value) <= 2 for value in coordinates):
            raise ValueError('能带路径坐标超出可检查范围')
    document = wannier_execution.win_contract.parse_win(win)
    values = {'bands_plot': '.true.', 'bands_num_points': points,
              'bands_plot_format': 'gnuplot', 'kpoint_path': lines}
    request = {'schema': wannier_execution.win_contract.WIN_REQUEST_SCHEMA, 'add': {}, 'replace': {}}
    for name, value in values.items():
        request['replace' if document.occurrences(name) else 'add'][name] = value
    merged, _ = wannier_execution.win_contract.merge_win(win, request)
    wannier_execution._checked_win(win, merged)
    return merged


def assert_applied(win, proposal):
    document = wannier_execution.win_contract.parse_win(win)
    enabled = (wannier_execution.win_contract._first_value(document, 'bands_plot') or '').lower()
    if enabled not in ('.true.', 'true', 't'):
        raise ValueError('最终 .win 未启用 bands_plot')
    blocks = document.blocks.get('kpoint_path', [])
    actual = [line.strip() for line in blocks[0].body if line.strip()] if len(blocks) == 1 else []
    if actual != proposal.get('path_lines'):
        raise ValueError('最终 .win 的 kpoint_path 与已确认路径不一致')
    points = wannier_execution.win_contract._first_value(document, 'bands_num_points')
    if points != str(proposal.get('bands_num_points')):
        raise ValueError('最终 .win 的采样数与已确认路径不一致')


def recheck(proposal, force=True, max_cache_age=None):
    from . import recycle,remote_cache
    recycle.assert_available('attempts',proposal['band_attempt_id'])
    band = execution.get(proposal['band_attempt_id'])
    if band.get('state') != 'COMPLETED' or band.get('project_id') != proposal['project_id'] or band.get('config', {}).get('alias') != proposal['alias']:
        raise ValueError('DFT 能带来源状态或身份已变化')
    source = (band.get('dependency_snapshot') or {})
    if source.get('upstream_attempt_id') != proposal['scf_attempt_id'] or source.get('files', {}).get('CONTCAR', {}).get('sha256') != proposal['structure_sha256']:
        raise ValueError('DFT 能带 SCF 结构来源已变化')
    path = execution.attempt_path(band)['shell']
    if path != proposal['source_path']:
        raise ValueError('DFT 能带路径来源已变化')
    cached=remote_cache.read('band-path',proposal,max_age=max_cache_age)
    if cached and not force:return cached
    hashes = pyw90_remote._file_hashes(proposal['alias'], path, ('KPOINTS', 'POSCAR'))
    if hashes != {'KPOINTS': proposal['kpoints_sha256'], 'POSCAR': proposal['poscar_sha256']}:
        raise ValueError('DFT 能带结构或路径文件已变化，请重新生成路径')

    return remote_cache.write('band-path',proposal,{'validated':True})
