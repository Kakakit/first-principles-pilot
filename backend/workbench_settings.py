"""Machine-scoped defaults; prepared attempts keep their original snapshots."""
import json
import os
from pathlib import Path
from .storage import ROOT


def read():
    path=ROOT/'workbench-settings.json'
    return json.loads(path.read_text()) if path.exists() else {'version':1,'runtimes':{},'defaults':{},'history_limit':5}


def snapshot(alias, program):
    current=read();key=alias+':'+program
    return {'alias':alias,'program':program,'runtime':current['runtimes'].get(key),
            'template_id':current['defaults'].get(key)}


def assert_current(saved):
    # Legacy preparations retain their original contract.
    if saved and saved != snapshot(saved['alias'],saved['program']):
        raise ValueError('服务器运行时或默认脚本设置已变化，请重新准备或冻结授权；没有提交')


def save(value):
    from . import servers, job_templates
    previous=read()
    if 'runtime' in value:
        config=servers.validate(value['runtime'])
        program=value.get('program','vasp_std')
        if program not in ('vasp_std','vasp_ncl','wannier90'):raise ValueError('程序类型无效')
        key=config['alias']+':'+program
        previous['runtimes'][key]=config
        template=value.get('template_id') or None
        if template:
            item=job_templates.get_template(template)
            if item.get('alias')!=config['alias']:raise ValueError('默认脚本必须属于当前主机')
        previous['defaults'][key]=template
    if 'history_limit' in value:
        n=value['history_limit']
        if n not in (3,5,10):raise ValueError('历史默认显示 3、5 或 10 条')
        previous['history_limit']=n
    ROOT.mkdir(parents=True,exist_ok=True)
    temp=ROOT/'workbench-settings.tmp'
    temp.write_text(json.dumps(previous,ensure_ascii=False,indent=2))
    os.replace(temp,ROOT/'workbench-settings.json')
    return previous
