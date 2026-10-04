"""Local diagnostics only: no SSH, credentials, POTCAR or project contents."""
import hashlib,importlib.metadata,json,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launch


def integrity():
    manifest=ROOT/'RELEASE_MANIFEST.json'
    if not manifest.exists():return {'status':'DEVELOPMENT_SOURCE','changed':[],'missing':[]}
    records=json.loads(manifest.read_text(encoding='utf-8'))['files']
    changed=[];missing=[]
    for record in records:
        path=ROOT/record['path']
        if not path.is_file():missing.append(record['path'])
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:changed.append(record['path'])
    return {'status':'PASS' if not changed and not missing else 'CHANGED','changed':changed,'missing':missing,'files':len(records)}


def collect():
    config=launch.configuration(ROOT/'fpilot.local.json')
    versions={}
    for name in ('fastapi','uvicorn','pymatgen','numpy','scipy','matplotlib','seekpath','jsonschema','filelock'):
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    return {'schema':'fpilot.release-diagnostics.v1','startup':launch.doctor(),'dependencies':versions,
        'port':config.get('port',8765),'port_available':launch.port_available(config.get('port',8765)),
        'configuration_saved':bool(config),'worker_enabled':config.get('worker_enabled',True),'integrity':integrity(),
        'note':'只读本机检查，不连接服务器、不包含项目内容、密码、SSH config 或私钥。工作台正在运行时端口占用正常。'}


if __name__=='__main__':
    try:
        report=collect();target=ROOT/'release-diagnostics.json'
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2));print('Saved: '+str(target))
    except (OSError,ValueError) as exc:print('Diagnostics failed: '+str(exc),file=sys.stderr);raise SystemExit(2)
