import hashlib,json,subprocess,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
fixture=root/'fixtures/POSCAR'
before=hashlib.sha256(fixture.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory() as temp:
    output=Path(temp);source={'id':'fixture','kind':'structure','stage':'structure','name':'Si fixture'}
    request={'schema':'fpilot.extension-request.v2','analysis_id':'standalone','inputs':{'structure':[{'path':str(fixture),'name':'POSCAR','source_id':'fixture'}]},'sources':[source],'options':{'include_elements':True},'output_dir':str(output),'result_file':str(output/'result.json')}
    path=output/'request.json';path.write_text(json.dumps(request))
    for action in ['doctor','check','run']:
        argv=[sys.executable,str(root/'scripts/cli.py'),action]
        if action!='doctor':argv+=['--request',str(path)]
        result=subprocess.run(argv,capture_output=True,text=True,check=True);json.loads(result.stdout)
    result=json.loads((output/'result.json').read_text());data=json.loads((output/'statistics.json').read_text())
    assert result['status']=='COMPLETED' and data['atom_count']==2 and data['volume_angstrom3']>0 and (output/'elements.csv').is_file()
assert hashlib.sha256(fixture.read_bytes()).hexdigest()==before
print('standalone doctor/check/run, JSON/CSV and source preservation passed')
