"""User-directed local recycling; never cancel jobs or remove remote files."""
import json
import hashlib
import re
import sqlite3
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .storage import ROOT, validate_workspace

KINDS = {'inputs', 'wannier-inputs', 'job-templates', 'attempts', 'projects', 'analyses', 'plots', 'local-analyses', 'chains', 'scans', 'saved-plots','comparisons'}

DIRECTORIES = {'analyses':'analyses-v2','plots':'plots','local-analyses':'analyses','saved-plots':'plot-datasets','comparisons':'wannier-comparisons'}
TABLES = {'attempts':'attempts','chains':'chain_runs','scans':'scan_batches'}


def _rows(table):
    database=ROOT/'execution.sqlite3'
    if not database.exists():return []
    with sqlite3.connect('file:'+str(database.resolve())+'?mode=ro',uri=True) as connection:
        try:return [json.loads(row[0]) for row in connection.execute('SELECT body FROM '+table)]
        except sqlite3.OperationalError:return []


def _references(kind,key):
    """Keep the small contracts used by downstream tasks; never inspect a server."""
    refs=[]
    for item in _rows('attempts'):
        if kind in ('inputs','wannier-inputs') and item.get('bundle_id')==key:refs.append('任务 '+item['id'][:12])
        if kind=='attempts' and item.get('id')!=key:
            if key in json.dumps(item,ensure_ascii=False):
                refs.append('下游任务 '+item['id'][:12])
    for table in ('chain_runs','scan_batches') if kind in ('inputs','wannier-inputs','attempts') else ():
        for item in _rows(table):
            # Include frozen plans when describing the effect of local cleanup.
            if key in json.dumps(item,ensure_ascii=False):refs.append('计算链/批次 '+item['id'][:12])
    if kind=='attempts':
        for path in (ROOT/'inputs').glob('*.zip'):
            with zipfile.ZipFile(path) as archive:
                manifest=json.loads(archive.read('manifest.json'))
                if manifest.get('band_path_source_attempt_id')==key or key in json.dumps(manifest.get('source_contract',{})):
                    refs.append(('冻结能带输入 ' if manifest.get('band_path_source_attempt_id')==key else '冻结来源输入 ')+path.stem[:12])
        for base in ('analyses-v2','analyses','plots','wannier-bundles','plot-datasets','wannier-comparisons'):
            pattern='*.json' if base=='wannier-bundles' else '*/record.json' if base in ('plot-datasets','wannier-comparisons') else '*/analysis.json'
            for path in (ROOT/base).glob(pattern):
                if key in path.read_text():refs.append('本地分析/输入 '+path.parent.name[:12])
    return sorted(set(refs))


def _record(kind,key):
    if kind in DIRECTORIES:return json.loads((ROOT/DIRECTORIES[kind]/key/('record.json' if kind in ('saved-plots','comparisons') else 'analysis.json')).read_text())
    if kind in ('chains','scans'):
        item=next((r for r in _rows(TABLES[kind]) if r['id']==key),None)
        if not item:raise ValueError('记录不存在')
        return item
    raise ValueError('记录类型不支持')


def purge(kind,key,confirm_local=False,include_children=True):
    """Explicit irreversible cleanup of one recycled local record, never remote files."""
    path=marker(kind,key)
    if confirm_local is not True or not path.is_file():raise ValueError('请先回收记录，再确认彻底清理本地数据')
    if kind == 'projects':
        record=json.loads(path.read_text())
        results=[]
        for child in ([item for item in all_items() if item['kind']!='projects' and item.get('project_id')==key] if include_children else []):
            if marker(child['kind'],child['key']).exists():
                results.append(purge(child['kind'],child['key'],True))
        deleted_marker(kind,key).parent.mkdir(parents=True,exist_ok=True)
        deleted_marker(kind,key).write_text(json.dumps({'kind':kind,'key':key}))
        path.unlink()
        pending=[r['cleanup_directory'] for r in results if r.get('cleanup_pending')]
        return {'kind':kind,'key':key,'deleted':True,'remote_deleted':False,'children':results,
                'cleanup_pending':bool(pending),'cleanup_directory':'；'.join(pending) if pending else None}
    refs=_references(kind,key)
    _pause_dependents(kind,key,'本地来源已清理，请重新选择来源并审查')
    targets=[]
    if kind=='inputs':targets=[ROOT/'inputs'/(key+'.zip')]
    elif kind=='wannier-inputs':targets=[ROOT/'wannier-bundles'/(key+'.json')]
    elif kind=='job-templates':
        record=json.loads((ROOT/'job-templates'/'metadata'/(key+'.json')).read_text())
        targets=[ROOT/'job-templates'/'metadata'/(key+'.json')]
        others=[json.loads(f.read_text()) for f in (ROOT/'job-templates'/'metadata').glob('*.json') if f.stem!=key]
        if not any(r.get('script_hash')==record.get('script_hash') for r in others):targets.append(ROOT/'job-templates'/'scripts'/record['script_hash'])
    elif kind in DIRECTORIES:targets=[ROOT/DIRECTORIES[kind]/key]
    elif kind=='attempts':
        targets=[ROOT/(key+'.watch.log'),ROOT/'artifact-cache'/(key+'.json'),ROOT/'artifact-cache'/'inputs'/key]
        targets.append(ROOT/'window-summaries'/key)
        targets += [ROOT/base/(key+'.json') for base in ('failure-notes','failure-snapshots','result-summaries')]
    if kind in ('attempts','wannier-inputs'):
        field={'attempts':'source_attempt_id','wannier-inputs':'source_frozen_id'}[kind]
        for cache in (ROOT/'window-checks').glob('*.json'):
            if json.loads(cache.read_text()).get(field)==key:targets.append(cache)
    if kind=='scans':
        batch=next((r for r in _rows(TABLES['scans']) if r['id']==key),{})
        campaign=batch.get('campaign_id')
        if campaign and not any(r['id']!=key and r.get('campaign_id')==campaign for r in _rows(TABLES['scans'])):
            targets.append(ROOT/'window-comparisons'/(campaign+'.json'))
    if kind=='attempts':
        listing=ROOT/'artifact-cache'/(key+'.json')
        if listing.is_file():
            for entry in json.loads(listing.read_text()).get('files',[]):
                identity=f"{key}|{entry['path']}|{entry['size_bytes']}|{entry['modified_at']}"
                cached_key=hashlib.sha256(identity.encode()).hexdigest()
                targets.extend([ROOT/'artifact-cache'/'inputs'/cached_key,ROOT/'artifact-cache'/'input-owners'/(cached_key+'.json')])
        for owner in (ROOT/'artifact-cache'/'input-owners').glob('*.json'):
            if json.loads(owner.read_text()).get('attempt_id')==key:
                targets.extend([ROOT/'artifact-cache'/'inputs'/owner.stem,owner])
        for cached in (ROOT/'artifact-cache'/'previews').glob('*.json'):
            if json.loads(cached.read_text()).get('attempt_id')==key:targets.append(cached)
        # Legacy preparation-only bundles belong to the attempt. Independently
        # frozen configurations have their own history and cleanup action.
        attempt=next(r for r in _rows('attempts') if r['id']==key)
        bundle=attempt.get('bundle_id')
        if attempt.get('kind')=='wannier90' and bundle and not any(r['id']!=key and r.get('bundle_id')==bundle for r in _rows('attempts')):
            bundle_path=ROOT/'wannier-bundles'/(bundle+'.json')
            if bundle_path.is_file() and 'project' not in json.loads(bundle_path.read_text()):
                targets.append(bundle_path)
    if kind=='attempts':
        targets.append(ROOT/'projection-cache'/'energy'/key)
        from . import interactive_plots
        for job in interactive_plots.JOBS.values():
            if key in job.get('source_attempt_ids',[]):job['discarded']=True
        for cached in (ROOT/'projection-cache').glob('*/metadata.json'):
            metadata=json.loads(cached.read_text())
            sources={metadata.get('band_attempt_id'),metadata.get('dos_attempt_id')}-{None,key}
            if key not in (metadata.get('band_attempt_id'),metadata.get('dos_attempt_id')):continue
            if any(r['id'] in sources for r in _rows('attempts')):continue
            job=interactive_plots.JOBS.get(cached.parent.name,{})
            job['discarded']=True
            targets.append(cached.parent)
    if kind=='analyses':
        from .extension_runs import stop_local
        stop_local(key)
    targets=list(dict.fromkeys(targets))
    targets.append(path)  # Recycle marker participates in the rollback too.
    for target in targets:
        if any(parent.is_symlink() for parent in target.parents if parent!=ROOT and ROOT in parent.parents):
            raise ValueError('本地清理对象位于链接目录内，未清理外部目录')
    # Move all owned files first. Restore them if the database commit fails.
    # The quarantine is within the data directory, so no cross-volume copying occurs.
    quarantine=ROOT/'recycle'/'local-cleanup'/uuid.uuid4().hex
    quarantine.mkdir(parents=True)
    moved=[]
    try:
        for index,target in enumerate(targets):
            if target.exists() or target.is_symlink():
                temporary=quarantine/str(index);target.replace(temporary);moved.append((target,temporary))
        if kind in TABLES:
            with sqlite3.connect(ROOT/'execution.sqlite3') as connection:
                connection.execute('DELETE FROM '+TABLES[kind]+' WHERE id=?',(key,))
                if kind=='attempts':connection.execute('DELETE FROM execution_events WHERE attempt_id=?',(key,))
    except Exception as exc:
        for target,temporary in reversed(moved):temporary.replace(target)
        shutil.rmtree(quarantine)
        if isinstance(exc,sqlite3.Error):raise ValueError('本地数据库清理失败，记录与文件已恢复：'+str(exc)) from exc
        raise
    deleted=deleted_marker(kind,key);deleted.parent.mkdir(parents=True,exist_ok=True)
    deleted.write_text(json.dumps({'kind':kind,'key':key}))
    try:
        shutil.rmtree(quarantine)
        cleanup_pending=False
    except OSError:
        cleanup_pending=True
    return {'kind':kind,'key':key,'deleted':True,'remote_deleted':False,
            'references':refs,'cleanup_pending':cleanup_pending,'cleanup_directory':str(quarantine) if cleanup_pending else None}


def marker(kind, key):
    if kind not in KINDS or not isinstance(key, str):
        raise ValueError('回收对象无效')
    filename = key
    if not re.fullmatch(r'[a-f0-9-]{32,64}', key):
        if kind != 'projects' or not key.strip() or len(key) > 200:
            raise ValueError('回收对象无效')
        # Workspace/recipe identities are not content hashes. Keep their exact
        # identity in the record, with a stable filename independent of its text.
        filename = 'id-' + hashlib.sha256(key.encode()).hexdigest()
    return ROOT / 'recycle' / kind / filename


def is_recycled(kind, key):
    return marker(kind, key).exists()


def move(kind, key, project=None):
    path = marker(kind, key)
    project_id = None
    label = key[:12]
    if kind == 'inputs':
        from .execution import frozen_project
        frozen = frozen_project(key)
        if frozen is None:
            raise ValueError('冻结输入项目身份缺失')
        project_id = frozen['id']
        with zipfile.ZipFile(ROOT/'inputs'/(key+'.zip')) as archive:manifest=json.loads(archive.read('manifest.json'))
        node=next((n for n in frozen.get('nodes',[]) if n['id']==manifest.get('node_id')),None)
        label=(node or {}).get('data',{}).get('label') or manifest.get('stage') or '冻结输入'
        if manifest.get('reason'):label+=' · '+manifest['reason'][:80]
    elif kind == 'wannier-inputs':
        record=json.loads((ROOT/'wannier-bundles'/(key+'.json')).read_text())
        project_id=record.get('project_id')
        label='Wannier90 · '+record.get('seed','wannier90')
    elif kind == 'job-templates':
        from .job_templates import get_template
        label=get_template(key).get('name') or '任务脚本'
    elif kind == 'attempts':
        from .execution import get
        attempt = get(key)
        project_id = attempt.get('project_id')
        label = (attempt.get('job_id') or key[:12])+' · '+attempt['state']
    elif kind in DIRECTORIES or kind in ('chains','scans'):
        record=_record(kind,key)
        project_id=record.get('project_id')
        if project_id is None and record.get('attempt_id'):
            from .execution import get
            project_id=get(record['attempt_id']).get('project_id')
        label = (record.get('manifest') or {}).get('name') or record.get('title') or record.get('reason') or key[:12]
    else:
        if not isinstance(project, dict) or project.get('id') != key:
            raise ValueError('请提供完整项目备份')
        validate_workspace({'activeId': key, 'projects': [project]})
        project_id = key
        label = project['name']
    _pause_dependents(kind,key,'记录已移入回收站，请恢复并重新确认执行')
    if kind=='analyses':
        from .extension_runs import stop_local
        stop_local(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({'kind': kind, 'key': key, 'project_id': project_id, 'label': label, 'at': datetime.now(timezone.utc).isoformat(),
                                    'project': project if kind == 'projects' else None}, ensure_ascii=False))
    if kind=='analyses':
        record=_record(kind,key)
        if record.get('location')=='local' and record.get('state') in ('QUEUED','WAIT_RESOURCE','RUNNING','DISPATCHING'):
            from .extension_protocol import atomic_json
            record.update(state='FAILED',error='已由用户回收，本机分析已停止；恢复后可重新运行分析。')
            atomic_json(ROOT/DIRECTORIES[kind]/key/'analysis.json',record)
    if kind=='projects':
        children=[]
        for child_kind,child_key in _project_objects(key):
            child_path=marker(child_kind,child_key)
            if child_path.exists():continue
            move(child_kind,child_key)
            child=json.loads(child_path.read_text());child['cascaded_by']=key;child_path.write_text(json.dumps(child,ensure_ascii=False))
            children.append({'kind':child_kind,'key':child_key})
        record=json.loads(path.read_text());record['children']=record.get('children',[])+children
        path.write_text(json.dumps(record,ensure_ascii=False))
    return {'kind': kind, 'key': key, 'recycled': True}


def restore(kind, key):
    path = marker(kind, key)
    if not path.exists():
        raise ValueError('回收记录不存在')
    record = json.loads(path.read_text())
    if kind == 'projects':
        for child in record.get('children',[]):
            child_path=marker(child['kind'],child['key'])
            if child_path.exists() and json.loads(child_path.read_text()).get('cascaded_by')==key:
                child_path.unlink()
    path.unlink()
    return record


def list_items(kind, project_id=None):
    if kind not in KINDS:
        raise ValueError('回收类型无效')
    result = []
    for path in sorted((ROOT / 'recycle' / kind).glob('*')):
        if path.is_file():
            item = json.loads(path.read_text())
            if project_id is None or item.get('project_id') == project_id:
                result.append(item)
    return result


def deleted_marker(kind,key):
    return ROOT/'recycle'/'deleted'/kind/marker(kind,key).name


def inactive(kind,key,record=None):
    try:
        if is_recycled(kind,key) or deleted_marker(kind,key).exists():return True
    except ValueError:return False
    project_id=(record or {}).get('project_id')
    return bool(project_id and (is_recycled('projects',project_id) or deleted_marker('projects',project_id).exists()))


def assert_available(kind,key,record=None):
    if inactive(kind,key,record):raise ValueError('记录或所属项目已移入回收站或清理；没有派发')
    if record:
        for source_kind,source_key in source_ids(record):
            if inactive(source_kind,source_key):raise ValueError('所需输入或来源已回收或清理，请返回配置重新审查；没有派发')


def source_ids(value):
    result=set()
    def visit(item):
        if isinstance(item,dict):
            for key,entry in item.items():
                kind='attempts' if key.endswith('attempt_id') or key in ('retry_of','chain_source_id','source_attempt_ids','attempts') else 'inputs' if key in ('bundle_id','root_bundle_id') else 'job-templates' if key=='template_id' else None
                if kind:
                    for identity in entry if isinstance(entry,list) else [entry]:
                        if isinstance(identity,str) and re.fullmatch(r'[a-f0-9-]{32,64}',identity):result.add((kind,identity))
                        if kind=='inputs' and isinstance(identity,str) and re.fullmatch(r'[a-f0-9]{64}',identity):result.add(('wannier-inputs',identity))
                visit(entry)
        elif isinstance(item,list):
            for entry in item:visit(entry)
    visit(value);return result


def _pause_dependents(kind,key,reason):
    database=ROOT/'execution.sqlite3'
    if not database.exists():return
    for plan_kind,table in (('chains','chain_runs'),('scans','scan_batches')):
        for item in _rows(table):
            owns=kind==plan_kind and item['id']==key or kind=='projects' and item.get('project_id')==key
            references=kind in ('inputs','wannier-inputs','attempts','job-templates') and key in json.dumps(item)
            if not (owns or references):continue
            if item.get('state') not in ('FROZEN','COMPLETE'):item['state']='PAUSED'
            item['local_cleanup_blocker']=reason
            item['revision']=item.get('revision',0)+1
            with sqlite3.connect(database) as connection:
                connection.execute('UPDATE '+table+' SET state=?,body=? WHERE id=?',(item['state'],json.dumps(item,ensure_ascii=False),item['id']))


def _project_objects(project_id):
    for kind,table in TABLES.items():
        for item in _rows(table):
            if item.get('project_id')==project_id:yield kind,item['id']
    for path in (ROOT/'inputs').glob('*.zip'):
        try:
            with zipfile.ZipFile(path) as archive:project=json.loads(archive.read('project.json'))
            if project.get('id')==project_id:yield 'inputs',path.stem
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):continue
    for path in (ROOT/'wannier-bundles').glob('*.json'):
        item=json.loads(path.read_text())
        if item.get('project_id')==project_id:yield 'wannier-inputs',path.stem
    for kind,directory in DIRECTORIES.items():
        for path in (ROOT/directory).glob('*/'+('record.json' if kind in ('saved-plots','comparisons') else 'analysis.json')):
            item=json.loads(path.read_text());owner=item.get('project_id')
            if not owner and item.get('attempt_id'):
                owner=next((a.get('project_id') for a in _rows('attempts') if a['id']==item['attempt_id']),None)
            if owner==project_id:yield kind,path.parent.name


def all_items():
    return sorted([item for kind in sorted(KINDS) for item in list_items(kind)],key=lambda r:(r['kind']=='projects',r['at']),reverse=True)


def empty(items,confirm_local=False):
    if confirm_local is not True or not isinstance(items,list):raise ValueError('请确认清空本地回收站')
    # Clear only the explicit selection visible at confirmation; newly recycled records stay.
    results=[]
    for item in sorted(items,key=lambda r:r['kind']=='projects'):
        kind,key=item['kind'],item['key']
        if marker(kind,key).exists():results.append(purge(kind,key,True,include_children=False))
    return {'deleted':results,'remote_deleted':False}


def purged(kind,key):
    try:return deleted_marker(kind,key).exists()
    except ValueError:return False


def reactivate(kind,key):
    """Only an explicit re-import/re-freeze may revive identical local content."""
    marker(kind,key).unlink(missing_ok=True)
    deleted_marker(kind,key).unlink(missing_ok=True)
