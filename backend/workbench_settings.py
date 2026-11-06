"""Machine-scoped defaults; prepared attempts keep their original snapshots."""
import json
import os
import re
from pathlib import Path
from .storage import ROOT


def read():
    path=ROOT/'workbench-settings.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version':1,'runtimes':{},'defaults':{},'history_limit':5}


def snapshot(alias, program, config=None, template_id=None):
    if config is not None:
        from . import servers
        from .site_registration import connection_identity
        return {'version':2,'alias':alias,'program':program,'runtime':servers.runtime_config(config),
                'template_id':template_id,'connection':connection_identity(alias)}
    current=read();key=alias+':'+program
    return {'alias':alias,'program':program,'runtime':current['runtimes'].get(key),
            'template_id':current['defaults'].get(key)}


def assert_current(saved):
    # Legacy preparations retain their original contract.
    if saved and saved.get('version')==2:
        from .site_registration import connection_identity
        from .project_sync import continuation_connection
        if not continuation_connection(saved['connection'],connection_identity(saved['alias']),saved['alias']):raise ValueError('实际 SSH 连接目标已改变，请重新准备/授权；没有提交')
        return
    if saved and saved != snapshot(saved['alias'],saved['program']):
        current = snapshot(saved['alias'],saved['program'])
        # A recorded local repair affects new drafts only. Old explicit configs
        # still pass the independent frozen-script and live-target guards.
        path = ROOT/'defaults-migrations.json'
        if path.exists():
            migrations = json.loads(path.read_text(encoding='utf-8')).get('transitions', [])
            if any(saved == item.get('before') and current == item.get('after') for item in migrations):
                return
        return  # Explicit saved runtime/script remains authoritative for legacy preparations.


def save(value):
    from . import servers, job_templates
    previous=read()
    if 'workspace_root' in value:
        from .remote_paths import new_directory
        if not value['workspace_root']: raise ValueError('请填写通用默认工作区')
        previous['workspace_root']=new_directory(value['workspace_root'],previous.get('workspace_root'))
    if 'runtime' in value:
        config=servers.validate(value['runtime'])
        config.pop('command_mode', None)  # The registered host owns the default.
        program=value.get('program','vasp_std')
        if program not in ('vasp_std','vasp_ncl','wannier90'):raise ValueError('程序类型无效')
        key=config['alias']+':'+program
        template=value.get('template_id') or None
        if template:
            item=job_templates.get_template(template)
            if item.get('alias')!=config['alias']:raise ValueError('默认脚本必须属于当前主机')
            if item.get('scheduler')!=servers.normalize_scheduler(config['scheduler']):raise ValueError('默认脚本调度器须与所选服务器一致')
            if not job_templates.fits_program(item,program):raise ValueError('默认脚本须支持所选程序，且原稿分类正确')
            if not config['queue'] and item.get('resources',{}).get('queue'):config['queue']=item['resources']['queue']
        previous['runtimes'][key]=config
        previous['defaults'][key]=template
    if 'history_limit' in value:
        n=value['history_limit']
        if n not in (3,5,10):raise ValueError('历史默认显示 3、5 或 10 条')
        previous['history_limit']=n
    ROOT.mkdir(parents=True,exist_ok=True)
    temp=ROOT/'workbench-settings.tmp'
    temp.write_text(json.dumps(previous,ensure_ascii=False,indent=2), encoding='utf-8')
    os.replace(temp,ROOT/'workbench-settings.json')
    return previous


def repair_new_draft_defaults(updates):
    """Explicit local maintenance; keep legacy snapshots intact, never submit.

    Ordinary UI saves do not create compatibility transitions. A later edit
    must match neither endpoint and therefore invalidates the old preparation.
    """
    from .execution_control import write_json
    previous=read();path=ROOT/'defaults-migrations.json'
    prior_migrations=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version':1,'transitions':[]}
    before={}
    try:
        for value in updates:
            alias=value['runtime']['alias'];program=value.get('program','vasp_std')
            before[(alias,program)] = {'alias':alias,'program':program,'runtime':previous['runtimes'].get(alias+':'+program),'template_id':previous['defaults'].get(alias+':'+program)}
            save(value)
        migrations=json.loads(json.dumps(prior_migrations))
        for (alias,program),old in before.items():
            current=snapshot(alias,program)
            if old != current:
                migrations['transitions'].append({'before':old,'after':current})
        write_json(path,migrations)
    except Exception:
        write_json(ROOT/'workbench-settings.json',previous)
        write_json(path,prior_migrations)
        raise
    return read()
