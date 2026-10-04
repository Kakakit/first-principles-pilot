"""Portable extension CLI helper; no workbench imports or database access."""
import argparse,importlib.util,json,math,sys
from pathlib import Path

def clean(value):
    if isinstance(value,dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)): return [clean(v) for v in value]
    if hasattr(value,'tolist'): return clean(value.tolist())
    if hasattr(value,'item'): return clean(value.item())
    return value

def dump(path,value):
    Path(path).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def file(request,role,index=0):
    return Path(request['inputs'][role][index]['path'])

def artifact(path,label,media_type='application/json'):
    return {'path':Path(path).name,'label':label,'media_type':media_type}

def main(check,analyze):
    parser=argparse.ArgumentParser(description='doctor / check / run through the v2 JSON file protocol')
    parser.add_argument('action',choices=['doctor','check','run'])
    parser.add_argument('--request',type=Path)
    args=parser.parse_args()
    manifest=json.loads((Path(__file__).resolve().parents[1]/'extension.manifest.json').read_text())
    if args.action=='doctor':
        missing=[]
        for module in manifest['dependencies']:
            try:
                if importlib.util.find_spec(module) is None: missing.append(module)
            except (ModuleNotFoundError,ValueError): missing.append(module)
        ready=sys.version_info>=(3,9) and not missing
        print(json.dumps({'schema':'fpilot.extension-doctor.v2','ready':ready,'missing':missing,'python':sys.version.split()[0],
                          'reason':'环境满足' if ready else '需要 Python >=3.9 和列出的模块'},ensure_ascii=False));return 0
    request=None
    try:
        if not args.request: raise ValueError('--request is required')
        request=json.loads(args.request.read_text())
        if request.get('schema')!='fpilot.extension-request.v2': raise ValueError('unsupported request schema')
        output=Path(request['output_dir']).resolve();output.mkdir(parents=True,exist_ok=True)
        target=Path(request['result_file']).resolve()
        if target.parent!=output: raise ValueError('result_file must be inside output_dir')
        for items in request['inputs'].values():
            for item in items:
                source=Path(item['path'])
                if not source.is_file() or source.is_symlink() or source.stat().st_size==0: raise ValueError('input must be a nonempty regular file')
        if args.action=='check':
            reason=check(request)
            print(json.dumps({'schema':'fpilot.extension-check.v2','status':'APPLICABLE','reason':reason or '输入与参数适用'},ensure_ascii=False));return 0
        payload=analyze(request)
        payload={'schema':'fpilot.extension-result.v2','status':'COMPLETED','summary':'分析完成','warnings':[],
                 'scientific_validation':'NOT_PERFORMED','tables':[],'curves':[],'artifacts':[],**payload,'sources':request['sources']}
        dump(target,payload)
        print(json.dumps({'schema':'fpilot.extension-run.v2','result_file':str(target)},ensure_ascii=False));return 0
    except Exception as exc:
        if args.action=='check':
            print(json.dumps({'schema':'fpilot.extension-check.v2','status':'NOT_APPLICABLE','reason':str(exc)},ensure_ascii=False));return 0
        print(str(exc),file=sys.stderr);return 2
