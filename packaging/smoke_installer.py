"""Start the actual installed backend and worker, with isolated user data.

No scheduler fixtures, SSH connections or research submission. This validates
native runtime relocation, HTTP/UI resources, structure parsing and lifecycle.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen


def get(url, payload=None):
    data=None if payload is None else json.dumps(payload).encode()
    request=Request(url,data=data,headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=10) as response:
        return json.load(response)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    app=args.app.resolve()
    python=app/'python'/('python.exe' if os.name=='nt' else 'bin/python3')
    launcher=app/'scripts/installed_launcher.py'
    receipt={'schema':'fpilot.native-startup.v1','platform':os.name,'real_ssh_requests':0,'checks':[]}
    with tempfile.TemporaryDirectory(prefix='fpilot-native-') as temp:
        home=Path(temp)/'中文 用户数据'
        home.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        config={'workspace':str(home/'项目 数据'),'port':port,'worker_enabled':True}
        (home/'fpilot.local.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
        env={**os.environ,'FPILOT_HOME':str(home),'PYTHONUTF8':'1','FPILOT_NOTIFICATIONS':'0'}
        def command(*options):
            return subprocess.run([str(python),'-X','utf8',str(launcher),*options],cwd=app,env=env,capture_output=True,text=True,timeout=90)
        try:
            first=command('--no-browser')
            assert first.returncode==0,first.stdout+first.stderr+(home/'controller/workbench.log').read_text(errors='replace')
            identity=json.loads((home/'controller/identity.json').read_text())
            state=get(identity['url']+'/status')
            pid=state['processes'][0]['pid']
            assert state['phase']=='running'
            receipt['checks'].append('bundled_runtime_api_and_ui')
            assert get(state['url']+'/api/health')['status']=='ok'
            with urlopen(state['url'],timeout=5) as response:assert b'<html' in response.read().lower()
            with urlopen(state['url']+'/_fpilot/control',timeout=5) as response:assert response.url.rstrip('/')==identity['url'].rstrip('/')
            setup=get(state['url']+'/api/local-setup')
            assert Path(setup['current']['workspace']).resolve()==(home/'项目 数据').resolve(),setup
            structure=get(state['url']+'/api/structures/parse',{'filename':'Si.POSCAR','format':'poscar',
                'base64':base64.b64encode((app/'examples/Si.POSCAR').read_bytes()).decode()})
            assert structure['atoms']
            receipt['checks'].append('unicode_user_data_and_real_poscar_parse')
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                worker=get(state['url']+'/api/execution-control')['worker']
                if worker.get('healthy'):break
                time.sleep(.2)
            assert worker.get('healthy'),worker
            receipt['checks'].append('single_healthy_worker')
            again=command('--no-browser')
            assert again.returncode==0,again.stderr
            assert get(identity['url']+'/status')['processes'][0]['pid']==pid
            assert command('--can-update').returncode==1
            receipt['checks'].append('repeated_open_reuses_instance_and_update_guard')
            marker=home/'项目 数据/acceptance-note.txt'
            marker.write_text('保存后应保留',encoding='utf-8')
            original=hashlib.sha256(marker.read_bytes()).hexdigest()
            stopped=command('--stop','--no-browser')
            assert stopped.returncode==0,stopped.stderr
            assert not (home/'controller/identity.json').exists()
            assert not json.loads((home/'项目 数据/worker-state.json').read_text())['running']
            assert command('--can-update').returncode==0
            assert hashlib.sha256(marker.read_bytes()).hexdigest()==original
            restarted=command('--no-browser')
            assert restarted.returncode==0,restarted.stderr+(home/'controller/workbench.log').read_text(errors='replace')[-3500:]
            assert hashlib.sha256(marker.read_bytes()).hexdigest()==original
            receipt['checks'].append('cooperative_stop_restart_and_data_preservation')
        finally:
            result=command('--stop','--no-browser')
            if result.returncode:
                raise RuntimeError('Native smoke could not safely stop its own controller: '+result.stderr)
    receipt['status']='PASS'
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':main()
