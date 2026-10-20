"""Generic protocol fixtures owned by the host, with no optional package imports."""
import json
import shutil
from pathlib import Path

TEMPLATE=Path(__file__).with_name('fixtures')/'extension-package'


def attempt_package(folder,key='sample-attempt'):
    shutil.copytree(TEMPLATE,folder)
    manifest=json.loads((folder/'extension.manifest.json').read_text())
    manifest.update(id=key,name='Attempt protocol fixture',contexts=['attempt'],stages=['scf'],
                    sources={'min':1,'max':2},evidence=['COMPLETED','vasp_timing','ediff_reached'])
    manifest['inputs'].append({'role':'outcar','title':'Output','names':['OUTCAR'],'max_bytes':1099511627776})
    manifest['execution']['locations']=['local','ssh']
    manifest['options_schema']={'type':'object','additionalProperties':False,
        'properties':{'tolerance':{'type':'number','minimum':0}},'required':['tolerance']}
    (folder/'extension.manifest.json').write_text(json.dumps(manifest))
    # Deliberately synthetic values: tests verify transport/schema/units, not
    # the scientific implementation of any independently developed package.
    (folder/'scripts/cli.py').write_text('''from pathlib import Path
from protocol_cli import main,dump,artifact
def check(request):return 'synthetic protocol fixture'
def analyze(request):
    out=Path(request['output_dir']);dump(out/'statistics.json',{'fixture':True})
    return {'summary':'Fixture','scientific_validation':'INSUFFICIENT',
      'tables':[{'title':'Fixture','columns':[{'key':'raw','label':'Raw','unit':'C/m²'}],'rows':[{'raw':0},{'raw':0},{'raw':0}]}],
      'artifacts':[artifact(out/'statistics.json','Fixture')],'warnings':[]}
if __name__=='__main__':raise SystemExit(main(check,analyze))
''')
    return folder


def direct_package(folder):
    shutil.copytree(TEMPLATE,folder)
    path=folder/'extension.manifest.json';manifest=json.loads(path.read_text())
    manifest.update(id='sample-analysis',name='Direct protocol fixture',contexts=['attempt'],stages=['wannier90'],evidence=['COMPLETED','wannier_all_done'])
    manifest['execution'].update(locations=['ssh'],preferred_location='ssh')
    manifest['options_schema']={'type':'object','additionalProperties':False,'properties':{'nz':{'type':'integer'}},'required':['nz']}
    path.write_text(json.dumps(manifest));return folder
