"""Prepare a separate restart=plot attempt from an immutable Wannier90 model."""
from __future__ import annotations

import hashlib
import io
import json
import re
import shlex
import tarfile
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import execution as ex, execution_control as control, pyw90_remote, remote_paths, servers
from . import wannier_bands, wannier_compare, wannier_execution as w90
from .storage import ROOT


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _source(project_id, source_id, band_id):
    source = ex.get(source_id)
    if source.get('project_id') != project_id:
        raise ValueError('Wannier90 来源不属于当前项目')
    bundle, parent = wannier_compare._w90(source)
    scf_id = (parent.get('dependency_snapshot') or {}).get('upstream_attempt_id')
    band = ex.get(band_id)
    wannier_compare._band(band, project_id, source['config']['alias'], scf_id)
    return source, bundle, parent


def path_proposal(project, source_id, band_id):
    source, bundle, parent = _source(project['id'], source_id, band_id)
    result = wannier_bands.path_proposal(project, source['node_id'], parent['id'],
                                         source['config']['alias'], band_id)
    return {**result, 'wannier_source_attempt_id': source_id}


def _checked_source(source, bundle, mode='full'):
    alias = source['config']['alias']
    path = ex.attempt_path(source)['shell']
    seed = bundle['seed']
    names = [seed + suffix for suffix in ('.win', '.chk', '.amn', '.mmn', '.eig')]
    if mode == 'lightweight':
        from .source_files import snapshot
        files = snapshot(alias, path, names, ex.ssh)
        if files[names[0]].get('sha256') != bundle['win_sha256']:
            raise ValueError('原 Wannier90 .win 已变化；不能补画')
        return path, files
    hashes = pyw90_remote._file_hashes(alias, path, names)
    if hashes[names[0]] != bundle['win_sha256']:
        raise ValueError('原 Wannier90 .win 已变化；不能补画')
    return path, hashes


def _plot_win(bundle, proposal):
    win = wannier_bands.with_path(bundle['win'], proposal, True)
    document = w90.win_contract.parse_win(win)
    request = {'schema': w90.win_contract.WIN_REQUEST_SCHEMA, 'add': {}, 'replace': {}}
    request['replace' if document.occurrences('restart') else 'add']['restart'] = 'plot'
    edited, _ = w90.win_contract.merge_win(win, request)
    w90._checked_win(bundle['win'], edited)
    wannier_bands.assert_applied(edited, proposal)
    return edited


def prepare(project, source_id, band_id, proposal, probe_id, node, cores=None,
            resource_reason='', template_id=None):
    source, bundle, parent = _source(project['id'], source_id, band_id)
    if (proposal.get('wannier_source_attempt_id') != source_id or proposal.get('band_attempt_id') != band_id or
            proposal.get('parent_attempt_id') != parent['id'] or
            proposal.get('scf_attempt_id') != (parent.get('dependency_snapshot') or {}).get('upstream_attempt_id') or
            proposal.get('project_id') != project['id']):
        raise ValueError('补画路径与所选来源不一致')
    wannier_bands.recheck(proposal)
    win = _plot_win(bundle, proposal)
    source_path, source_files = _checked_source(source, bundle, 'lightweight')
    hashes = {name: item['sha256'] for name, item in source_files.items() if item.get('sha256')}
    config = source['config']
    from . import branch_bindings
    binding=branch_bindings.check(project,source['node_id'],config['alias'])
    resource = servers.preview(config, probe_id, node, cores, resource_reason, purpose='wannier90')
    if not resource['resource_ready']:
        raise ValueError('实时资源检查未通过：' + '; '.join(resource['blockers']))
    script, script_source = w90._render_script(config, resource['profile'], template_id)
    script = ex.with_exit_trap(script)
    root = remote_paths.normalize_workspace_root(config.get('remote_workspace_root'))
    snapshot = remote_paths.parse_workspace_probe(ex.ssh(config['alias'], remote_paths.workspace_probe_command(root), timeout=30), root)
    if snapshot['absolute_path'] != source['remote_workspace_root_abs']:
        raise ValueError('远端工作区根目录与原模型不一致')
    key = uuid.uuid4().hex
    segment = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S') + '-' + key[:4]
    relative = remote_paths.attempt_relative_path(source['project_slug'], source['stage_sequence'],
                                                   source['stage_slug'], segment)
    absolute = remote_paths.absolute_attempt_path(snapshot['absolute_path'], relative)
    frozen = {'schema': 'fpilot.wannier90-plot-bundle.v1', 'source_attempt_id': source_id,
              'parent_attempt_id': parent['id'], 'project_id': project['id'],'branch_scopes':binding['scopes'],'execution_scope':source['node_id'], 'band_attempt_id': band_id,
              'source_path': source_path, 'source_hashes': hashes, 'seed': bundle['seed'],
              'source_files': source_files, 'verification_mode': 'lightweight',
              'win': win, 'win_sha256': _sha(win.encode()), 'band_path': proposal,
              'created_at': ex.now()}
    raw = json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode()
    bundle_id = _sha(raw)
    from .inputs import put_file
    put_file(ROOT / 'wannier-bundles' / (bundle_id + '.json'), raw)
    from .workbench_settings import snapshot as settings_snapshot
    record = {'settings_snapshot':settings_snapshot(config['alias'],'wannier90'),'id': key, 'kind': 'wannier90-plot', 'state': 'PREPARED', 'created_at': ex.now(),
              'project_id': project['id'],'branch_scopes':binding['scopes'],'execution_scope':source['node_id'], 'flow_id': 'single', 'node_id': source['node_id'],
              'project_slug': source['project_slug'], 'stage_sequence': source['stage_sequence'],
              'stage_slug': source['stage_slug'], 'attempt_segment': segment,
              'bundle_id': bundle_id, 'config': config, 'probe_id': probe_id, 'node': node,
              'cores': cores, 'reason': resource_reason, 'profile': resource['profile'],
              'scheduler': servers.normalize_scheduler(config['scheduler']),
              'remote_workspace_root': root, 'remote_workspace_root_abs': snapshot['absolute_path'],
              'remote_relative_path': relative, 'relative_path': relative,
              'remote_path': remote_paths.join_workspace(root, relative), 'remote_path_absolute': absolute,
              'remote_project_path_absolute': source['remote_project_path_absolute'],
              'path_schema': 'fpilot.remote-attempt-path', 'remote_host': config['alias'],
              'script': script, 'script_sha256': _sha(script.encode()), 'script_source': script_source,
              'parent_attempt_id': source_id, 'plot_source_attempt_id': source_id,
              'band_attempt_id': band_id, 'seed': bundle['seed'], 'source_hashes': hashes,
              'source_snapshot': {'source_path': source_path, 'source_attempt_id': source_id,
                                  'files': source_files, 'verification_mode': 'lightweight'},
              'input_audit': {'status': 'PASS', 'restart': 'plot', 'source_hashes': hashes,
                              'win_sha256': frozen['win_sha256']}, 'job_id': None,
              'note': '独立 restart=plot attempt；.chk 为独立副本，矩阵为同服务器硬链接；原局域化结果不变。'}
    with closing(ex.db()) as connection:
        connection.execute('INSERT INTO attempts VALUES(?,?,?)', (key, 'PREPARED', json.dumps(record, ensure_ascii=False)))
        control.journal(connection, key, 'PREPARED', {'plot_source_attempt_id': source_id})
        connection.commit()
    return record


def submit(key):
    record = ex.get(key)
    from . import recycle
    recycle.assert_available('attempts',key,record)
    if record.get('kind') != 'wannier90-plot' or record['state'] != 'PREPARED':
        return record
    from .workbench_settings import assert_current
    assert_current(record.get('settings_snapshot'))
    frozen = w90._bundle(record)
    wannier_bands.recheck(frozen['band_path'])
    source = ex.get(frozen['source_attempt_id'])
    if source.get('state') != 'COMPLETED' or source.get('project_id') != record['project_id']:
        raise ValueError('原 Wannier90 模型状态已变化')
    source_path, hashes = _checked_source(source, w90._bundle(source), frozen.get('verification_mode', 'full'))
    if source_path != frozen['source_path'] or hashes != frozen.get('source_files', frozen['source_hashes']):
        raise ValueError('原 Wannier90 文件已变化；没有提交')
    fresh = servers.capacity_probe(record['config'], purpose='wannier90')
    resource = servers.preview(record['config'], fresh['id'], record['node'], record['cores'], record['reason'], purpose='wannier90')
    if not resource['resource_ready'] or resource['profile'] != record['profile']:
        raise ValueError('实时资源已变化，请重新准备')
    script, script_source = w90._render_script(record['config'], record['profile'], record['script_source']['id'])
    if ex.with_exit_trap(script) != record['script'] or script_source != record['script_source']:
        raise ValueError('提交脚本已变化，请重新准备')
    with closing(ex.db()) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT state FROM attempts WHERE id=?', (key,)).fetchone()
        if not row or row[0] != 'PREPARED':
            connection.rollback(); return ex.get(key)
        recycle.assert_available('attempts',key,record)
        control.enforce_limits(connection, record)
        from . import branch_bindings
        branch_bindings.reserve(connection,record)
        from . import fast_dispatch
        fast_dispatch.clear_previous_dispatch(record)
        record.update(state='DISPATCHING', dispatch_started_at=ex.now(), final_probe_id=fresh['id'], dispatch_timeout_seconds=900)
        connection.execute('UPDATE attempts SET state=?,body=? WHERE id=?', ('DISPATCHING', json.dumps(record, ensure_ascii=False), key))
        control.journal(connection, key, 'DISPATCHING', {'plot_source_attempt_id': frozen['source_attempt_id']})
        connection.commit()
    seed = frozen['seed']
    script_name = ex.script_name(record['scheduler'])
    files = {seed + '.win': frozen['win'].encode(), script_name: record['script'].encode(),
             '_fpilot/attempt.json': json.dumps({'schema': 'fpilot.web-attempt.v1', 'id': key,
                                                  'stage': 'wannier90-plot', 'bundle_id': record['bundle_id'],
                                                  'source_attempt_id': frozen['source_attempt_id']}).encode()}
    files['input-manifest.sha256'] = ''.join(_sha(data) + '  ' + name + '\n' for name, data in files.items()).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in files.items():
            item = tarfile.TarInfo(name); item.size = len(data); item.mode = 0o600
            archive.addfile(item, io.BytesIO(data))
    checks = '\n'.join('test "$(sha256sum ' + shlex.quote(name) + " | awk '{print $1}')\" = " + shlex.quote(digest)
                       for name, digest in frozen['source_hashes'].items())
    from .source_files import guard
    if frozen.get('verification_mode') == 'lightweight':
        checks = '\n'.join(line for name, info in frozen['source_files'].items() for line in guard(shlex.quote(name), info))
    links = '\n'.join('ln ' + shlex.quote(name) + ' "$dest"/' + shlex.quote(name)
                      for name in (seed + '.amn', seed + '.mmn', seed + '.eig'))
    if frozen.get('verification_mode') == 'lightweight':
        links += '\n' + '\n'.join(line for name in (seed + '.amn', seed + '.mmn', seed + '.eig')
                                   for line in guard('"$dest"/'+shlex.quote(name), frozen['source_files'][name], copied=True))
        copy_checks = '\n'.join(guard('"$dest"/'+shlex.quote(seed+'.chk'), frozen['source_files'][seed+'.chk'], copied=True))
        copy_checks += '\n' + '\n'.join(line for name, info in frozen['source_files'].items() for line in guard(shlex.quote(name), info))
    else:
        copy_checks = 'test "$(sha256sum "$dest"/'+shlex.quote(seed+'.chk')+" | awk '{print $1}')\" = "+shlex.quote(frozen['source_hashes'][seed+'.chk'])
    submit_command = ('sbatch --parsable --export=ALL,W90_SEED=' + seed + ' ' + script_name if record['scheduler'] == 'slurm'
                      else 'qsub -v W90_SEED=' + seed + ' ' + script_name)
    identity = shlex.quote(json.dumps({'schema': 'fpilot.remote-project.v1',
                                        'project_id': record['project_id'],
                                        'project_slug': record['project_slug']},
                                       ensure_ascii=False, separators=(',', ':')))
    command = f'''set -e
root={remote_paths.shell_path(record['remote_workspace_root'])}
test "$(cd "$root" && pwd -P)" = {shlex.quote(record['remote_workspace_root_abs'])}
project_dir={remote_paths.shell_path(record['remote_project_path_absolute'])}
test -f "$project_dir/.fpilot/project.json"
test "$(cat "$project_dir/.fpilot/project.json")" = {identity}
source_dir={remote_paths.shell_path(source_path)}
cd "$source_dir"
{checks}
dest={remote_paths.shell_path(record['remote_path_absolute'])}
mkdir -p {shlex.quote(str(PurePosixPath(record['remote_path_absolute']).parent))}
mkdir "$dest"
tar -xf - -C "$dest"
cd "$dest"
sha256sum -c input-manifest.sha256
cd "$source_dir"
{links}
cp --reflink=auto -- {shlex.quote(seed + '.chk')} "$dest"/{shlex.quote(seed + '.chk')}
{copy_checks}
cd "$dest"
test -s {shlex.quote(seed + '.chk')}
mkdir .submit-once
{submit_command} > .job-id
printf 'FPILOT_JOB_ID='
cat .job-id
'''
    try:
        receipt = ex.ssh(record['config']['alias'], command, stream.getvalue(), timeout=900)
        if receipt.get('transport_state')=='NOT_SENT' and fast_dispatch.known_not_submitted(record,receipt):return ex.get(key)
        token = r'\d+(?:\.[A-Za-z0-9_.-]+)?' if record['scheduler'] == 'pbs' else r'\d+(?:;[A-Za-z0-9_.-]+)?'
        match = re.search(r'^FPILOT_JOB_ID=(' + token + r')\s*$', receipt['stdout'], re.M)
        record.update(state='SUBMITTED' if match and receipt['exit_code'] == 0 else 'UNKNOWN',
                      job_id=match.group(1).split(';', 1)[0] if match else None,
                      dispatch_receipt=receipt, updated_at=ex.now())
        fast_dispatch.tracking_note(record)
        ex.save(record)
        return ex.get(key)
    except BaseException:
        record.update(state='UNKNOWN', updated_at=ex.now())
        fast_dispatch.tracking_note(record)
        ex.save(record)
        raise
