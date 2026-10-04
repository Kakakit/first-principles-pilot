"""Run against an isolated temporary workspace, never the user's workspace."""
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:8001'

def call(path, data=None, method=None):
    body=None if data is None else json.dumps(data).encode()
    req=urllib.request.Request(base+path,data=body,method=method,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)

def start(directory):
    process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8001'],cwd=root,env={**os.environ,'FPILOT_WORKSPACE':directory},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            if process.poll() is not None:raise RuntimeError('测试服务退出，可能端口8001已占用。')
            try:
                result=call('/api/workspace')
                if result['path'] != directory:raise RuntimeError('端口被其他工作区占用。')
                return process
            except urllib.error.URLError:time.sleep(.1)
        raise RuntimeError('测试服务未就绪。')
    except BaseException:
        process.terminate();process.wait();raise

with tempfile.TemporaryDirectory(prefix='fpilot-2b-api-') as directory:
    directory=str(Path(directory).resolve());process=start(directory)
    try:
        s=call('/api/structures/parse',{'filename':'Si.POSCAR','format':'poscar','base64':base64.b64encode((root/'tests/fixtures/Si.POSCAR').read_bytes()).decode()})
        w={'activeId':'p','projects':[{'id':'p','name':'API test','remoteSlug':'api-test','updatedAt':'2026-09-12','nodes':[{'id':'s','type':'step','position':{'x':0,'y':0},'data':{'kind':'structure','label':'Si','note':'','params':{},'executionMode':'vasp','stageSequence':1,'stageSlug':'structure','structure':s}}],'edges':[]}]}
        migration={'workspace':copy.deepcopy(w),'expected_revision':0,'reason':'migration'}
        first=call('/api/workspace',migration,'PUT');assert first['revision']==1
        assert call('/api/workspace',migration,'PUT')['revision']==1
        changed=call('/api/structures/edit',{'parent':s,'lattice':s['lattice'],'atoms':[dict(id=a['id'],element=a['element'],coordinates=[v+.1 for v in a['fractional']],selective=a['selective']) for a in s['atoms']],'coordinate_mode':'fractional'})
        assert changed['source']==s['source'];assert changed['atoms'][0]['id']==s['atoms'][0]['id']
        w['projects'][0]['nodes'][0]['data']['structure']=changed
        assert call('/api/workspace',{'workspace':w,'expected_revision':1},'PUT')['revision']==2
        try:call('/api/workspace',migration,'PUT');raise AssertionError('stale revision accepted')
        except urllib.error.HTTPError as exc:assert exc.code==409
        assert call('/api/workspace/history/1')['workspace']['projects'][0]['nodes'][0]['data']['structure']==s
        potcar=call('/api/potcars',{'base64':base64.b64encode(b'TITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\nENMAX = 245.0;\nEnd of Dataset\n').decode()})
        p=copy.deepcopy(w['projects'][0])
        p['parameters']={'shared':{'values':{'XC_FUNCTIONAL':'PBE','ENCUT':'400','EDIFF':'1e-6','ISPIN':'1','ISMEAR':'0','SIGMA':'0.05','LDAU':'false'}}}
        p['nodes'].append({'id':'c','type':'step','position':{'x':100,'y':0},'data':{'kind':'scf','label':'SCF','note':'','params':{},'executionMode':'vasp','stageSequence':2,'stageSlug':'scf','parameters':{'preset':'basic','values':{}},'inputDraft':{'potcar_id':potcar['id'],'grid':'4 4 4','center':'Gamma'}}})
        p['edges']=[{'id':'s-to-c','source':'s','target':'c'}]
        request={'project':p,'node_id':'c','reason':'synthetic API test only'}
        assert call('/api/inputs/preview',request)['ready']
        frozen=call('/api/inputs/freeze',request)
        with urllib.request.urlopen(base+frozen['download']) as response:
            import hashlib
            assert hashlib.sha256(response.read()).hexdigest()==frozen['id']
        assert call('/api/inputs/freeze',request)['id']==frozen['id']
        process.terminate();process.wait(timeout=10);process=start(directory)
        after=call('/api/workspace');assert after['revision']==2 and after['workspace']==w
        assert len(call('/api/workspace/history'))==2
        assert call('/api/inputs/history?project_id='+p['id'])[0]['id']==frozen['id']
        print(json.dumps({'migration':'pass','idempotent_retry':'pass','geometry_edit':'pass','conflict_409':'pass','history_original':'pass','process_restart_restores_revision_2':'pass','input_preview_freeze_download_hash':'pass','frozen_history_after_restart':'pass'}))
    finally:
        process.terminate();process.wait(timeout=10)
