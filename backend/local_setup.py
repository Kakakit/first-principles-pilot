"""Portable launcher settings. Moving a workspace requires an explicit restart."""
import json,os,shutil,sys,tempfile
from pathlib import Path
from .storage import ROOT
from .execution_control import write_json

CONFIG=Path(os.environ.get('FPILOT_CONFIG',Path(__file__).resolve().parents[1]/'fpilot.local.json'))


def read():
    value=json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {}
    if not isinstance(value,dict):raise ValueError('本机配置应为 JSON 对象，请修正 '+str(CONFIG))
    potcar=os.environ.get('FPILOT_POTCAR_LIBRARY','')
    if not potcar and (ROOT/'potcar-library.json').is_file():
        potcar=json.loads((ROOT/'potcar-library.json').read_text(encoding='utf-8')).get('path','')
    from .site_registration import ssh_aliases
    return {'saved':value,'current':{'workspace':str(ROOT),'research_root':os.environ.get('FPILOT_RESEARCH_ROOT',''),
            'potcar_library':potcar,'port':int(os.environ.get('FPILOT_PORT','5173'))},
        'python':sys.executable,'python_version':sys.version.split()[0],'platform':sys.platform,
        'ssh_client':shutil.which('ssh'),'ssh_aliases':ssh_aliases(),'config_file':str(CONFIG),
        'first_run':not (ROOT/'server-config.json').exists() and not (ROOT/'potcar-library.json').exists() and not value.get('setup_completed',False)}


def validate(value):
    result={}
    for key in ('workspace','research_root','potcar_library'):
        text=value.get(key,'')
        if not isinstance(text,str) or any(c in text for c in '\x00\r\n'):raise ValueError('本机路径无效：'+key)
        if not text:
            if key=='workspace':raise ValueError('请选择本机数据目录')
            result[key]='';continue
        path=Path(text).expanduser()
        if not path.is_absolute():raise ValueError('请填写本机绝对路径：'+key)
        if key!='workspace' and not path.is_dir():raise ValueError('目录不存在：'+text)
        if key=='workspace':
            path.mkdir(parents=True,exist_ok=True)
            try:
                with tempfile.TemporaryFile(dir=path) as test:test.write(b'fpilot')
            except OSError as exc:raise ValueError('数据目录不可写：'+text) from exc
        result[key]=str(path.resolve())
    port=value.get('port',8765)
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('端口须为 1024–65535 的整数')
    result.update(port=port,worker_enabled=bool(value.get('worker_enabled',True)),setup_completed=True)
    return result


def save(value):
    previous=read()
    valid=validate(value);valid['python']=sys.executable
    if os.environ.get('FPILOT_SAFE_PULL'):
        valid['safe_pull']=os.environ['FPILOT_SAFE_PULL']
    if valid['potcar_library']:
        from .potcar_library import configure
        configure(valid['potcar_library'])
        os.environ['FPILOT_POTCAR_LIBRARY']=valid['potcar_library']
    write_json(CONFIG,valid)
    restart=any(valid[key]!=previous['current'][key] for key in ('workspace','port','research_root')) or valid['worker_enabled']!=previous['saved'].get('worker_enabled',True)
    return {'saved':valid,'restart_required':restart,
        'note':'配置已保存。更换数据目录、研究目录、端口或自动推进开关后，请停止并重新启动工作台；原目录保留，不自动搬迁。'}
