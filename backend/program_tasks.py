"""Single foreground commands with editable text inputs on a registered direct host."""
import hashlib
import io
import json
import re
import shlex
import tarfile
import time
import uuid
from contextlib import closing
from pathlib import PurePosixPath

from . import branch_bindings, custom_commands, execution as ex, execution_control as control
from . import batch_scheduler, program_sources, program_batch, fast_dispatch, inputs, recycle, remote_paths, servers
from .execution_modes import is_direct
from .storage import validate_workspace


def filename(name):
    if (not isinstance(name, str) or not name.strip() or len(name.encode()) > 240
            or name.startswith('.') or name in ('_fpilot', 'input-manifest.sha256')
            or '/' in name or '\\' in name or any(ord(c) < 32 for c in name)):
        raise ValueError('请使用任务目录内的普通文件名：' + str(name)[:80])
    return name


def check_document(draft):
    if not isinstance(draft, dict):
        raise ValueError('自定义程序草稿格式无效')
    command = draft.get('command')
    if not isinstance(command, str) or not command.strip() or '\0' in command or len(command.encode()) > 32768:
        raise ValueError('请输入有效的启动命令（最多 32 KiB）')
    files, outputs = draft.get('files'), draft.get('outputs', [])
    if not isinstance(files, list) or len(files) > 64 or not isinstance(outputs, list) or len(outputs) > 64:
        raise ValueError('输入文件和预期输出各最多 64 个')
    saved = {}
    for item in files:
        if not isinstance(item, dict):
            raise ValueError('输入文件格式无效')
        name, text = filename(item.get('name')), item.get('text')
        if name in saved or not isinstance(text, str) or '\0' in text:
            raise ValueError('输入文件名重复或不是文本：' + name)
        saved[name] = text
    if sum(len(text.encode()) for text in saved.values()) > 2*1024*1024:
        raise ValueError('一次运行的文本输入合计最多 2 MiB')
    outputs = [filename(name) for name in outputs if name != '']
    if len(set(outputs)) != len(outputs) or set(outputs).intersection(saved):
        raise ValueError('预期输出名称重复或与输入文件同名')
    return {'command':command, 'files':saved, 'outputs':outputs}


def host(alias):
    entry = servers.load_registry()['hosts'].get(alias) if isinstance(alias, str) else None
    if not entry or not (is_direct(entry) or str(entry.get('scheduler','')).lower() in ('pbs','torque','pbs/torque','slurm')):
        raise ValueError('请选择已登记的直接运行、PBS 或 Slurm 服务器')
    return entry


def prepare(project, node_id):
    validate_workspace({'activeId':project['id'], 'projects':[project]})
    node = next((n['data'] for n in project['nodes'] if n['id']==node_id), {})
    if node.get('kind') != 'program':
        raise ValueError('请选择自定义程序节点')
    recycle.assert_available('projects', project['id'])
    draft = node.get('programDraft') or {}
    document = check_document(draft)
    alias = draft.get('alias', '')
    entry = host(alias)
    scheduler = servers.normalize_scheduler(entry['scheduler'])
    config = {'alias':alias, 'scheduler':scheduler}
    profile = {}
    if is_direct(entry):
        config['command_mode'] = 'direct'
    else:
        profile = program_batch.resources(draft)
        document['batch_script'] = program_batch.wrapper(scheduler,profile)
    refs = program_sources.resolve(project,node_id,alias,draft,document)
    if refs:
        document['sources'] = refs
    binding = branch_bindings.check(project, node_id, alias)
    root, prefix = remote_paths.project_location(project, alias, entry.get('remote_workspace_root') or '$HOME')
    from .site_registration import connection_identity
    connection = connection_identity(alias)
    probe = ex.ssh(alias, remote_paths.workspace_probe_command(root), timeout=30, mutating=False)
    absolute = remote_paths.parse_workspace_probe(probe, root)['absolute_path']
    key = uuid.uuid4().hex
    slug = project['remoteSlug']
    relative = remote_paths.attempt_relative_path(slug, node['stageSequence'], node['stageSlug'], key, prefix)
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    inputs.put_file(ex.ROOT/'program-bundles'/(digest+'.json'), raw)
    record = dict(id=key,kind='program',state='PREPARED',created_at=ex.now(),bundle_id=digest,
                  project_id=project['id'],flow_id='single',node_id=node_id,execution_scope=node_id,
                  project_slug=slug,flow_slug='main',attempt_segment=key,remote_host=alias,stage_sequence=node['stageSequence'],stage_slug=node['stageSlug'],
                  branch_scopes=binding['scopes'],path_schema='fpilot.remote-attempt-path',
                  remote_workspace_root=root,remote_workspace_root_abs=absolute,remote_relative_path=relative,
                  remote_path=remote_paths.join_workspace(root,relative), remote_path_absolute=remote_paths.absolute_attempt_path(absolute,relative),
                  remote_project_path_absolute=remote_paths.project_path(absolute,prefix), settings_snapshot={'connection':connection},
                  scheduler=scheduler,config=config, job_id=None,
                  profile=profile,script=document['command'],expected_outputs=document['outputs'])
    if refs:
        record['program_sources'] = refs
    with closing(ex.db()) as db:
        db.execute('INSERT INTO attempts VALUES(?,?,?)',(key,'PREPARED',json.dumps(record,ensure_ascii=False)))
        control.journal(db,key,'PREPARED')
        db.commit()
    return record


def frozen(record):
    if record.get('kind') != 'program':
        raise ValueError('不是自定义程序任务')
    key = record['bundle_id']
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):
        raise ValueError('冻结输入标识无效')
    raw = (ex.ROOT/'program-bundles'/(key+'.json')).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=key:
        raise ValueError('冻结输入内容已变化')
    return json.loads(raw)


def submit(record):
    if record['state'] != 'PREPARED':
        return record
    key, alias = record['id'], record['config']['alias']
    entry = host(alias)
    if servers.normalize_scheduler(entry['scheduler']) != ex.scheduler_name(record) or is_direct(entry) != is_direct(record['config']):
        raise ValueError('服务器运行方式已变化，请重新准备')
    ex.assert_recovery_target(record)
    doc = frozen(record)
    refs = doc.get('sources',[])
    for ref in refs:
        source = ex.get(ref['source_attempt_id'])
        recycle.assert_available('attempts',source['id'],source)
        ex.assert_recovery_target(source)
    dest = record['remote_path_absolute']
    command = (fast_dispatch.stage_receipt_prefix() + remote_paths.workspace_dispatch_guard(record['remote_workspace_root'], record['remote_workspace_root_abs'])
               + 'mkdir -p -- '+shlex.quote(str(PurePosixPath(dest).parent))+'\nmkdir -- '+shlex.quote(dest)
               +'\nfpilot_created=1\ncd '+shlex.quote(dest)+'\ntar -xf -\n' + program_sources.copy_commands(refs) + 'sha256sum -c input-manifest.sha256\n'
               +'fpilot_submitting=1\nprintf "FPILOT_SUBMIT_PHASE=SCHEDULER\\n"\n'
               )
    direct = is_direct(record['config'])
    if direct:
        command += (custom_commands.start_command(record,key,doc['command'])+' > _fpilot/command-start.json\n'
                    +'printf "FPILOT_DIRECT_START="\ncat _fpilot/command-start.json')
    else:
        command += (batch_scheduler.submit_command(ex.scheduler_name(record),'_fpilot/batch.sh')+
                    ' > .job-id\nprintf "FPILOT_JOB_ID="\ncat .job-id')
    marker = {k:record[k] for k in ('id','project_id','node_id','flow_id','bundle_id','remote_path_absolute','remote_workspace_root')}
    marker.update(schema='fpilot.web-attempt.v1', stage='program')
    staged = {**doc['files'],'_fpilot/attempt.json':json.dumps(marker)}
    if not direct:
        staged.update({'_fpilot/batch.sh':doc['batch_script'],'_fpilot/program.sh':doc['command']})
    staged['input-manifest.sha256'] = ''.join(hashlib.sha256(text.encode()).hexdigest()+'  ./'+name+'\n' for name,text in staged.items())
    staged['input-manifest.sha256'] += ''.join(ref['sha256']+'  ./'+ref['name']+'\n' for ref in refs)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name,text in staged.items():
            raw = text.encode()
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(raw), 0o600, int(time.time())
            archive.addfile(member,io.BytesIO(raw))
    with closing(ex.db()) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT state FROM attempts WHERE id=?',(key,)).fetchone()!=('PREPARED',):
            db.rollback()
            return ex.get(key)
        recycle.assert_available('attempts', key, record)
        control.enforce_limits(db,record)
        branch_bindings.reserve(db,record)
        fast_dispatch.clear_previous_dispatch(record)
        if direct: record['command_run_id'] = key
        record.update(state='DISPATCHING', dispatch_started_at=ex.now(), dispatch_timeout_seconds=180)
        db.execute('UPDATE attempts SET state=?,body=? WHERE id=?',('DISPATCHING',json.dumps(record),key))
        control.journal(db,key,'DISPATCHING')
        db.commit()
    try:
        receipt = ex.ssh(alias,command,stream.getvalue(),timeout=180,mutating=True)
        if fast_dispatch.known_not_submitted(record, receipt):
            return ex.get(key)
        if direct:
            return custom_commands.dispatch_result(record,receipt)
        identity = batch_scheduler.job_identity(ex.scheduler_name(record),receipt.get('stdout',''))
        record.update(**identity, state='SUBMITTED' if identity and receipt.get('exit_code')==0 else 'UNKNOWN',
                      dispatch_receipt=receipt,updated_at=ex.now())
        return ex.save(record)
    except (OSError,ValueError) as exc:
        record.update(state='UNKNOWN',tracking_error=str(exc),updated_at=ex.now())
        ex.save(record)
        raise


def output_evidence(record):
    names = record.get('expected_outputs', [])
    if not names:
        return {'file_evidence_checked':True,'outputs':{}}
    command = 'cd '+shlex.quote(record['remote_path_absolute'])+' || exit 1\n'
    for i,name in enumerate(names):
        command += f'if test -f {shlex.quote(filename(name))}; then echo {i}; fi\n'
    response = ex.ssh(record['config']['alias'],command,mutating=False,timeout=30)
    if response.get('exit_code') != 0:
        return {}
    found = response['stdout'].splitlines()
    return {'file_evidence_checked':True,'outputs':{name:str(i) in found for i,name in enumerate(names)}}
