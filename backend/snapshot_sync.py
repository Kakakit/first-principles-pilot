"""Snapshot transport, independent of calculation roots and execution ownership."""
import copy
import json
import posixpath
import shlex
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from . import project_sync as sync, project_snapshot as snap
from .execution_control import write_json
from .storage import WorkspaceStore

SCHEMA = 'fpilot.project-snapshot.v2'


def directory(value):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\x00\r\n'):
        raise ValueError('快照保存目录需为安全的单行 Linux 路径')
    value=value.strip()
    if '..' in value.split('/') or not (value.startswith('/') or value=='$HOME' or value.startswith('$HOME/')):
        raise ValueError('快照目录需使用 $HOME/... 或 Linux 绝对路径，不允许 ..')
    if '$' in value.replace('$HOME','',1) or not value.strip('/'):raise ValueError('快照目录不允许其他变量或服务器根目录')
    return posixpath.normpath(value)


def shell_directory(value):
    value=directory(value)
    return '"$HOME"'+('/'+shlex.quote(value[6:]) if value.startswith('$HOME/') else '') if value.startswith('$HOME') else shlex.quote(value)


def location(alias, value):
    sync.registered(alias)
    return {'alias':alias,'root':directory(value),'snapshot_directory':directory(value),'transfer_mode':'snapshot'}


def uncertain(value):
    return bool(value and (value.get('operation') or value.get('mode') in ('STOPPED','DISPATCHING','TRANSFERRING')))


def capture(pid):
    manifest,objects=snap.capture(sync.ex.ROOT,pid)
    manifest['schema']=SCHEMA
    manifest['transfer_mode']='snapshot'
    return manifest,objects


def remember(pid,alias,snapshot_directory):
    with sync.project_lock(pid):
        value=location(alias,snapshot_directory)
        if uncertain(sync.local(pid)):raise ValueError('上次传输待核验，地址未更换')
        write_json(sync.ex.ROOT/'project-sync/locations'/(snap.digest(pid.encode())+'.json'),value)
        return value


def pause(pid):
    """Called under the project dispatch lock. Preserve authorizations and jobs."""
    result={'chains':0,'scans':0,'analyses':0}
    with closing(sync.ex.db()) as db:
        db.execute('BEGIN IMMEDIATE')
        for table,key in (('chain_runs','chains'),('scan_batches','scans')):
            try:rows=db.execute('SELECT id,body FROM '+table+' WHERE project_id=?',(pid,)).fetchall()
            except __import__('sqlite3').OperationalError:continue
            for rid,raw in rows:
                record=json.loads(raw)
                if record.get('state')!='ACTIVE':continue
                record.update(state='PAUSED',revision=record.get('revision',0)+1,snapshot_pause_reason='跨设备快照传输',snapshot_paused_at=sync.now())
                db.execute('UPDATE '+table+' SET state=?,body=? WHERE id=?',('PAUSED',json.dumps(record,ensure_ascii=False),rid));result[key]+=1
        db.commit()
    from . import task_recovery
    for path in (sync.ex.ROOT/'task-recovery').glob('*.json'):
        operation=json.loads(path.read_bytes())
        if operation.get('project_id')==pid and operation.get('state')=='CONFIRMED':
            operation.update(state='PAUSED',paused_at=sync.now(),snapshot_pause_reason='跨设备快照传输')
            task_recovery.save(operation)
    for path in (sync.ex.ROOT/'analyses-v2').glob('*/analysis.json'):
        record=json.loads(path.read_bytes())
        if record.get('project_id')==pid and record.get('state') in ('QUEUED','WAIT_RESOURCE'):
            record.update(state='PAUSED',snapshot_origin_state=record['state'],snapshot_pause_reason='跨设备快照传输')
            write_json(path,record);result['analyses']+=1
    return result


def convert(pid):
    with sync.project_lock(pid):
        value=sync.local(pid)
        if not value:raise ValueError('项目没有旧交接记录，无需切换')
        if value.get('transfer_mode')=='snapshot':return sync.status(pid)
        if uncertain(value):raise ValueError('旧交接回执未完成，请先核查，不直接解锁')
        fact=sync.remote(value,'status',pid);sync.verify_server(value,fact['server'])
        if fact.get('operation'):raise ValueError('旧服务器仍有未完成操作，请先核查回执')
        paused=pause(pid)
        updated={**value,'legacy_handoff':copy.deepcopy(value),'transfer_mode':'snapshot','mode':'SNAPSHOT',
                 'root':directory(value['root'].rstrip('/')+'/.fpilot/project-sync'),
                 'snapshot_directory':directory(value['root'].rstrip('/')+'/.fpilot/project-sync'),
                 'server':None,'operation':None,'error':None,'paused':paused}
        write_json(sync.local_path(pid),updated)
        return sync.status(pid)


def preview(pid,alias,direction,snapshot_directory,version=None):
    with sync.project_lock(pid):
        previous=sync.local(pid)
        if previous and previous.get('transfer_mode')!='snapshot':raise ValueError('请先切换为快照传输；旧交接记录保留')
        if uncertain(previous):raise ValueError('上次快照传输回执待核验，请先核查原地址')
        if previous and previous.get('restore_required') and direction!='download':raise ValueError('上次还原未完成，只允许重新下载还原；原备份保留')
        config=location(alias,snapshot_directory)
        fact=sync.remote(config,'status',pid);config['server']=fact['server'];state=fact['state']
        current=WorkspaceStore(sync.ex.ROOT).read()
        here=any(p['id']==pid for p in (current['workspace'] or {}).get('projects',[]))
        local_manifest=capture(pid)[0] if here else None
        same=previous and previous.get('alias')==alias and previous.get('snapshot_directory')==config['root']
        if direction=='upload':
            if not here:raise ValueError('上传项目不在本机')
            if state['version'] and (not same or previous.get('version')!=state['version']):raise ValueError('目标库已有不同版本，请先下载并核对，不覆盖未知版本')
            manifest=local_manifest
        elif direction=='download':
            selected=version or state['version']
            if not selected:raise ValueError('这个目录尚无项目快照；请检查保存位置')
            if selected!=state['version'] and selected not in [v['version'] for v in sync.remote(config,'versions',pid)['versions']]:raise ValueError('历史快照不存在')
            manifest=json.loads(sync.get_object(config,pid,selected))
            if manifest.get('schema') not in (snap.SCHEMA,SCHEMA) or manifest['project']['id']!=pid:raise ValueError('项目快照身份或版本不支持')
        else:raise ValueError('请选择上传或下载')
        token=uuid.uuid4().hex
        info={'id':token,'project_id':pid,'config':config,'direction':direction,'state':state,
              'version':version or state['version'],'workspace_revision':current['revision'],
              'local_fingerprint':snap.fingerprint(local_manifest) if here else None,'manifest':manifest,'created_at':sync.now(),'transfer_mode':'snapshot'}
        write_json(sync.ex.ROOT/'project-sync/previews'/(token+'.json'),info)
        dirty=here and (not previous or previous.get('fingerprint')!=snap.fingerprint(local_manifest))
        return {'preview_id':token,'project_id':pid,'name':manifest['project']['name'],'direction':direction,
                'counts':manifest['counts'],'files':len(manifest['files']),'bytes':sum(e['size'] for e in manifest['files'].values()),
                'missing':manifest['missing'],'local_changes':bool(dirty),'local_counts':local_manifest['counts'] if here else None,
                'version':info['version'],'remote_operation':fact.get('operation'),'updated_at':state.get('updated_at'),
                'read_only':False,'transfer_mode':'snapshot','snapshot_directory':config['root'],'absolute_directory':fact['server']['root']}


def transfer(pid,path,info,choose_server):
    with sync.project_lock(pid):
        current=WorkspaceStore(sync.ex.ROOT).read()
        if current['revision']!=info['workspace_revision']:raise ValueError('草稿已变化，请重新预览')
        here=any(p['id']==pid for p in (current['workspace'] or {}).get('projects',[]))
        old,old_objects=capture(pid) if here else (None,{})
        if (snap.fingerprint(old) if old else None)!=info['local_fingerprint']:raise ValueError('本机记录已变化，请重新预览')
        previous=sync.local(pid)
        if uncertain(previous):raise ValueError('上次回执待核验，请先核查')
        dirty=here and (not previous or previous.get('fingerprint')!=snap.fingerprint(old))
        if info['direction']=='download' and dirty and not choose_server:raise ValueError('本机有修改，须明确选择服务器版本；会先备份')
        if any(json.loads(r['body'])['state']=='DISPATCHING' for r in (old or {}).get('tables',{}).get('attempts',[])):raise ValueError('项目正在派发，请等待该次回执')
        config,state=info['config'],info['state']
        # Pause before any SSH write. Failed uploads retain this pause.
        paused=pause(pid) if here else {'chains':0,'scans':0,'analyses':0}
        value={**config,'mode':'TRANSFERRING','operation':uuid.uuid4().hex,'purpose':info['direction'],
               'version':state['version'],'paused':paused,'started_at':sync.now()}
        for key in ('legacy_handoff','imported','calculation_connections'):
            if previous and key in previous:value[key]=copy.deepcopy(previous[key])
        write_json(sync.local_path(pid),value)
        try:
            if old:value['backup']=sync.save_backup(pid,old,old_objects);write_json(sync.local_path(pid),value)
            fact=sync.remote(config,'status',pid);sync.verify_server(config,fact['server'])
            if fact['state']!=state:raise ValueError('目标快照版本已变化，请重新预览')
            sync.remote(config,'acquire',pid,generation=state['generation'],version=state['version'],device=sync.device()['id'],token=value['operation'],purpose=info['direction']+'_snapshot')
            if info['direction']=='upload':
                manifest,objects=capture(pid);manifest.update(snapshot_id=uuid.uuid4().hex,snapshot_created_at=sync.now())
                fingerprint=snap.fingerprint(manifest);raw=snap.packed(manifest);version=snap.digest(raw);objects[version]=raw
                value.update(target_version=version,fingerprint=fingerprint);write_json(sync.local_path(pid),value)
                missing=sync.remote(config,'missing',pid,hashes=list(objects))['missing']
                for sha in missing:sync.remote(config,'put',pid,data=objects[sha],sha256=sha,token=value['operation'],device=sync.device()['id'])
                if snap.fingerprint(capture(pid)[0])!=fingerprint:raise ValueError('上传期间本机记录变化，未发布，请重新预览')
                result=sync.remote(config,'publish',pid,version=version,token=value['operation'],device=sync.device()['id'],at=sync.now())
                value['version']=result['state']['version']
            else:
                manifest=info['manifest'];objects={sha:sync.get_object(config,pid,sha) for sha in {e['sha256'] for e in manifest['files'].values()}}
                snap.validate(manifest,objects)
                value.update(target_version=info['version'],restore_started=True,imported=False);write_json(sync.local_path(pid),value)
                snap.restore(sync.ex.ROOT,manifest,objects,current['revision'],pause_automatic=True)
                connections={}
                for row in manifest['tables'].get('attempts',[]):
                    record=json.loads(row['body']);connection=(record.get('settings_snapshot') or {}).get('connection');alias=record.get('config',{}).get('alias')
                    if alias and connection:connections.setdefault(alias,[]).append(connection)
                value.update(imported=True,calculation_connections=connections,paused=pause(pid),fingerprint=snap.fingerprint(capture(pid)[0]),version=info['version']);write_json(sync.local_path(pid),value)
                result={'state':state}
            sync.remote(config,'release',pid,token=value['operation'],device=sync.device()['id'])
            value.update(mode='SNAPSHOT',operation=None,error=None,synced_at=sync.now());write_json(sync.local_path(pid),value);path.unlink()
            return {'local':value,'state':result['state'],'workspace':WorkspaceStore(sync.ex.ROOT).read(),'backup':value.get('backup')}
        except Exception as exc:
            value.update(mode='STOPPED',error=str(exc));write_json(sync.local_path(pid),value)
            raise ValueError(str(exc)+'；自动派发保持暂停，请核查原地址的回执') from exc


def recover(pid):
    with sync.project_lock(pid):
        value=sync.local(pid);fact=sync.remote(value,'status',pid);sync.verify_server(value,fact['server']);operation=fact.get('operation')
        if operation and (operation['token']!=value.get('operation') or operation['device']!=sync.device()['id']):return {**sync.status(pid),'message':'其他操作尚未完成，本次仅核查'}
        if operation:sync.remote(value,'release',pid,token=operation['token'],device=sync.device()['id'])
        if value.get('restore_started') and not value.get('imported'):
            value.update(mode='SNAPSHOT',operation=None,restore_required=True);write_json(sync.local_path(pid),value)
            return {**sync.status(pid),'message':'已核查并释放本机下载锁；还原未完成，只允许重新下载还原，原完整备份保留'}
        if value.get('purpose')=='upload' and fact['state']['version']==value.get('target_version'):value['version']=fact['state']['version']
        value.update(mode='SNAPSHOT',operation=None,error=None);write_json(sync.local_path(pid),value)
        return {**sync.status(pid),'message':'已核查回执；自动流程仍暂停，未重发上传或运行命令'}
