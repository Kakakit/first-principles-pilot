"""Role-based frozen sources. Candidate listing reads local evidence only."""
from . import source_files

MODES = ('new', 'self', 'upstream', 'manual')


def structure_file(mode, purpose):
    if mode == 'upstream' or purpose == 'continue':
        return 'CONTCAR'
    return 'POSCAR' if mode == 'self' else None


def input_poscar_hash(record, manifest):
    """A chain's actual POSCAR is its materialized source, not POSCAR.reference."""
    for name, info in record.get('source_file_plan', {}).items():
        if info.get('target', 'POSCAR' if name == 'CONTCAR' else name) == 'POSCAR':
            return info.get('sha256')
    return manifest.get('files', {}).get('POSCAR', {}).get('sha256')


def draft(project, node_id):
    node = next(n for n in project['nodes'] if n['id']==node_id)
    value = node['data'].get('sourceDraft') or {}
    parent = next((e['source'] for e in project['edges'] if e['target']==node_id), None)
    nodes = {n['id']:n for n in project['nodes']}
    default = 'upstream' if parent and nodes[parent]['data']['kind']!='structure' else 'new'
    value = {'mode':default, 'purpose':'charge', 'files':'charge', **value}
    if value['mode'] not in MODES or value['purpose'] not in ('charge','continue') or value['files'] not in ('charge','both'):
        raise ValueError('计算来源配置无效')
    return value, parent


def eligible(record, mode, purpose):
    evidence = record.get('evidence') or {}
    reasons = []
    if record.get('recycled') or record.get('path_schema')!='fpilot.remote-attempt-path' or not record.get('job_id'):
        reasons.append('来源已回收或缺少实际运行记录')
    from . import execution as ex
    try:
        manifest=ex.bundle(record['bundle_id'],allow_dependencies=True)[0]
        if manifest.get('node_id')!=record.get('node_id') or ex.frozen_project(record['bundle_id']).get('id')!=record.get('project_id'):
            reasons.append('来源记录与实际冻结输入的项目或节点不同')
    except (ValueError,KeyError,OSError):
        manifest={};reasons.append('来源实际冻结输入不可读取')
    if mode=='upstream':
        if record.get('state')!='COMPLETED' or not evidence.get('vasp_timing') or evidence.get('wrapper_exit') not in (None,0):
            reasons.append('程序尚未正常完成')
        if evidence.get('ediff_reached') is not True:
            reasons.append('缺少电子收敛证据')
        stage=manifest.get('stage',record.get('stage_slug',''))
        if stage=='relax' and evidence.get('ionic_converged') is not True and evidence.get('ionic_accuracy') is not True:
            reasons.append('缺少离子收敛证据')
        if record.get('scan_batch_id'):
            from . import scans
            if (scans.get(record['scan_batch_id']).get('promotion') or {}).get('attempt_id')!=record['id']:
                reasons.append('扫描结果尚未提升')
    elif record.get('state') not in ('COMPLETED','FAILED'):
        reasons.append('请先确认本轮已终止，UNKNOWN 不能用作续算来源')
    return reasons


def resolve(project, node_id, execution_scope=None):
    from . import execution as ex
    from . import branch_bindings
    value, parent = draft(project,node_id)
    mode = value['mode']
    candidates=[]
    from . import inputs
    try: current_report=inputs.generate(project,node_id)
    except (ValueError,KeyError,OSError):current_report=None
    binding = branch_bindings.inspect(project['id'],branch_bindings.scopes(project,execution_scope or node_id))
    for rec in ex.history(None, limit=10000):
        if mode=='self' and (rec.get('project_id')!=project['id'] or rec.get('node_id')!=node_id or rec.get('scan_batch_id')):
            continue
        if mode=='upstream' and (rec.get('project_id')!=project['id'] or rec.get('node_id')!=parent):
            continue
        if mode=='new':
            continue
        reasons=eligible(rec,mode,value['purpose'])
        if binding['alias'] and rec.get('config',{}).get('alias')!=binding['alias']:
            reasons.append('来源服务器与本分支不同')
        differences=[]
        listing=ex.ROOT/'artifact-cache'/(rec['id']+'.json')
        if listing.is_file():
            import json
            known={f['path'] for f in json.loads(listing.read_text()).get('files',[]) if f.get('size_bytes',0)>0}
            required={'CHGCAR'} if mode in ('self','manual') else {'CONTCAR'}
            if structure_file(mode, value['purpose']):required.add(structure_file(mode, value['purpose']))
            if mode=='upstream' and value.get('read_charge',True):required.add('CHGCAR')
            if value['files']=='both':required.add('WAVECAR')
            if value['purpose']=='continue':required.add('CONTCAR')
            missing=required-known
            if missing:reasons.append('缺少 '+ '、'.join(sorted(missing)))
        try:
            old=ex.frozen_project(rec['bundle_id'])
            old_node=next(n for n in old['nodes'] if n['id']==rec['node_id'])
            new_node=next(n for n in project['nodes'] if n['id']==node_id)
            if old_node['data'].get('parameters')!=new_node['data'].get('parameters') or old.get('parameters')!=project.get('parameters'):
                differences.append('物理参数与来源冻结输入不同，请审阅实际输入差异')
        except (ValueError,KeyError,StopIteration,TypeError):
            differences.append('来源冻结输入不可读取')
        if current_report and not current_report['errors']:
            try:
                _,old_files=ex.bundle(rec['bundle_id'],allow_dependencies=True)
                for tag in ('GGA','ISPIN','LDAU','LDAUTYPE','LDAUL','LDAUU','LDAUJ','LMAXMIX','ENCUT','PREC','LSORBIT','LNONCOLLINEAR','SAXIS','GGA_COMPAT','MAGMOM'):
                    a,b=ex._incar_value(old_files['INCAR'],tag),ex._incar_value(current_report['files']['INCAR'].encode(),tag)
                    if a!=b:differences.append(f'{tag}：来源 {a or "未设置"} → 本次 {b or "未设置"}')
                if not structure_file(mode, value['purpose']) and current_report.get('geometry_sha256')!=ex.bundle(rec['bundle_id'],allow_dependencies=True)[0].get('geometry_sha256'):differences.append('配置结构与来源冻结结构不同')
                if ex.hashlib.sha256(old_files['POTCAR']).hexdigest()!=current_report['potcar']['sha256']:differences.append('POTCAR 不同')
                if value['files']=='both' and old_files.get('KPOINTS')!=current_report['files']['KPOINTS'].encode():differences.append('WAVECAR k 点不同')
            except (ValueError,KeyError,OSError):differences.append('来源输入不可核对')
        candidates.append({'id':rec['id'],'alias':rec.get('config',{}).get('alias'),'node_id':rec.get('node_id'),
                           'state':rec['state'],'job_id':rec.get('job_id'),'created_at':rec.get('created_at',''),
                           'eligible':not reasons,'blockers':reasons,'differences':differences,
                           'structure_file':structure_file(mode, value['purpose'])})
    candidates.sort(key=lambda x:(x['created_at'],x['id']),reverse=True)
    selected=value.get('attempt_id') or next((c['id'] for c in candidates if c['eligible']),None)
    chosen=next((c for c in candidates if c['id']==selected),None)
    blockers=[] if mode=='new' else (chosen['blockers'] if chosen else ['没有可用来源，请完成相连上游或选择已终止的同节点运行'])
    return {'draft':value,'candidates':candidates,'selected_attempt_id':selected,'blockers':blockers,'binding':binding}


def freeze(project, node_id, report, verification_mode, execution_scope=None):
    from . import execution as ex
    from . import branch_bindings
    value,parent=draft(project,node_id)
    mode=value['mode']
    roles={}
    requested_mode=mode
    if mode=='new' and report.get('dependencies'):
        mode='upstream'
        value={**value,'mode':'upstream','read_charge':False,'files':'charge'}
        from copy import deepcopy
        project=deepcopy(project)
        next(n for n in project['nodes'] if n['id']==node_id)['data']['sourceDraft']=value
    if mode!='new':
        selection=resolve(project,node_id,execution_scope)
        if selection['blockers']:
            raise ValueError('来源不可用：'+'；'.join(selection['blockers']))
        source_id=value.get('attempt_id') or selection['selected_attempt_id']
        rec=ex.get(source_id)
        if eligible(rec,mode,value['purpose']):
            raise ValueError('来源证据不合格')
        if mode=='self' and (rec.get('project_id')!=project['id'] or rec.get('node_id')!=node_id):
            raise ValueError('同节点续算只能读取当前节点的历史运行')
        if mode in ('self','manual') and report['stage'] not in ('relax','scf'):raise ValueError('本阶段必须沿用固定上游合同')
        required={d['file'] for d in report.get('dependencies',[])} if mode=='upstream' else {'CHGCAR'}
        if mode in ('self','manual') and value['purpose']=='continue':
            if report['stage']!='relax':raise ValueError('继续未完成优化仅适用于 relax')
            required.add('CONTCAR')
        structure_name = structure_file(mode, value['purpose'])
        if structure_name: required.add(structure_name)
        if mode=='upstream' and value.get('read_charge'):required.add('CHGCAR')
        if value['files']=='both': required.update(('CHGCAR','WAVECAR'))
        path=ex.attempt_path(rec)['shell']
        files=source_files.snapshot(rec['config']['alias'],path,sorted(required),ex.ssh,verification_mode)
        old_manifest,old_files=ex.bundle(rec['bundle_id'],allow_dependencies=True)
        if mode=='upstream':
            allowed={'relax':('relax',),'scf':('relax','scf'),'bands':('scf',),'dos':('scf',),'vasp-wannier':('scf',)}
            if old_manifest.get('stage') not in allowed.get(report['stage'],()):
                raise ValueError('相连来源阶段不符合当前节点的文件合同')
        if structure_name:
            from . import artifacts
            observed=artifacts.preview(source_id,structure_name,'head',262144)
            if observed.get('partial') or not observed.get('structure'):raise ValueError('来源 '+structure_name+' 不完整或不可解析')
            if observed['structure']['source']['sha256']!=files[structure_name]['sha256']:raise ValueError(structure_name+' 在审查期间发生变化')
            elements=[a['element'] for a in observed['structure']['atoms']]
            symbols=report['files'].get('POSCAR',report['files'].get('POSCAR.reference','')).splitlines()[5].split()
            counts=list(map(int,report['files'].get('POSCAR',report['files'].get('POSCAR.reference','')).splitlines()[6].split()))
            if elements!=[symbol for symbol,count in zip(symbols,counts) for _ in range(count)]:raise ValueError(structure_name+' 原子顺序与当前输入不同')
            if structure_name == 'POSCAR':
                if input_poscar_hash(rec, old_manifest) != files['POSCAR']['sha256']:
                    raise ValueError('来源实际 POSCAR 与该次运行保存的输入身份不符，请核对原任务；不会退回项目原始结构')
                import base64
                from .structure_edit import geometry_hash
                raw = base64.b64decode(observed['structure']['source']['base64'], validate=True)
                if ex.hashlib.sha256(raw).hexdigest() != files['POSCAR']['sha256']:
                    raise ValueError('POSCAR 原文与结构解析身份不符')
                report['files']['POSCAR'] = raw.decode('utf-8')
                report['geometry_sha256'] = geometry_hash(observed['structure'])
                report['source_sha256'] = files['POSCAR']['sha256']
                report['structure_source'] = {'attempt_id':source_id,'job_id':rec.get('job_id'),
                                            'file':'POSCAR','sha256':files['POSCAR']['sha256'],
                                            'volume_A3':observed['structure']['volume']}
        old_poscar=old_files.get('POSCAR',old_files.get('POSCAR.reference',b'')).decode()
        new_poscar=report['files'].get('POSCAR',report['files'].get('POSCAR.reference',''))
        if [line.split() for line in old_poscar.splitlines()[5:7]] != [line.split() for line in new_poscar.splitlines()[5:7]]:
            raise ValueError('来源结构的元素顺序或原子数量不同，不能复用电子文件')

        diffs=[]
        potcar=report['potcar']['sha256']
        if ex.hashlib.sha256(old_files['POTCAR']).hexdigest()!=potcar:diffs.append('POTCAR 与来源不同')
        new_incar=report['files']['INCAR'].encode()
        for tag in ('GGA','ISPIN','LDAU','LDAUTYPE','LDAUL','LDAUU','LDAUJ','LMAXMIX','ENCUT','PREC','LSORBIT','LNONCOLLINEAR','SAXIS','GGA_COMPAT','MAGMOM'):
            a,b=ex._incar_value(old_files['INCAR'],tag),ex._incar_value(new_incar,tag)
            if a!=b:diffs.append(f'{tag}：来源 {a or "未设置"} → 本次 {b or "未设置"}')
        if not structure_name and old_manifest.get('geometry_sha256')!=report.get('geometry_sha256'):diffs.append('本次配置结构与来源冻结结构不同')
        if 'WAVECAR' in required and old_files.get('KPOINTS')!=report['files']['KPOINTS'].encode():diffs.append('WAVECAR 的 k 点与本次不同')
        if diffs and not value.get('acknowledge_differences'):raise ValueError('来源输入存在差异，请审阅并确认：'+'；'.join(diffs))
        if report['stage']=='bands' and (report.get('band_path_source_attempt_id')!=source_id or report.get('band_path_source_sha256')!=files.get('CONTCAR',{}).get('sha256')):
            raise ValueError('能带路径必须依据所选 SCF 最终 CONTCAR 生成')
        for name,info in files.items():
            role={'POSCAR':'structure','CONTCAR':'structure','CHGCAR':'charge','WAVECAR':'wavefunction'}[name]
            roles[role]={'attempt_id':source_id,'alias':rec['config']['alias'],'node_id':rec['node_id'],
                         'file':name,'source_path':path,'metadata':info,'source_bundle_id':rec['bundle_id'],'differences':diffs}
    contract={'schema':'fpilot.source-contract.v1','mode':requested_mode,'purpose':value['purpose'],'files':value['files'],
              'structure':'source' if 'structure' in roles else 'configured','verification_mode':verification_mode,'roles':roles}
    branch_bindings.check(project,node_id,source_contract=contract,scope=execution_scope)
    return contract


def materialize(contract, alias):
    from . import execution as ex
    files={}
    for role in contract['roles'].values():
        rec=ex.get(role['attempt_id'])
        if rec.get('recycled') or role['alias']!=alias or rec.get('config',{}).get('alias')!=alias:
            raise ValueError('冻结来源已回收或服务器不同，请返回计算配置重新审查')
        # Exact source and exact metadata, never fall back to latest.
        fresh=source_files.snapshot(alias,role['source_path'],[role['file']],ex.ssh,contract['verification_mode'])[role['file']]
        if fresh!=role['metadata']:raise ValueError('冻结来源文件已变化，请重新审查')
        files[role['file']]={**fresh,'source_path':role['source_path']}
    if not files:return None
    return {'schema':'fpilot.role-source.v1','files':files,'source_path':next(iter(contract['roles'].values()))['source_path'],
            'source_attempt_id':next(iter(contract['roles'].values()))['attempt_id'],'mode':contract['mode']}
