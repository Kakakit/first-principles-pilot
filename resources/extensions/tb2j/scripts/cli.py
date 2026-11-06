#!/usr/bin/env python3
"""Independent v2 CLI: selected Python → official TB2J CLI → native output reader."""
import argparse
import contextlib
import importlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

FLAGS=['--path','--posfile','--efermi','--kmesh','--elements','--index_magnetic_atoms','--spinor','--groupby','--prefix_spinor','--prefix_up','--prefix_down','--emin','--emax','--nz','--np','--rcut','--orb_decomposition','--output_path']


def doctor():
    result={'schema':'fpilot.extension-doctor.v2','ready':False,'missing':[],'python':sys.executable,'version':None,'reason':''}
    try:
        with contextlib.redirect_stdout(sys.stderr):
            for name in ('numpy','ase','TB2J','TB2J.scripts.wann2J','TB2J.io_exchange.io_exchange'):
                try:importlib.import_module(name)
                except Exception as exc:result['missing'].append(name);result['reason']=str(exc)
            if result['missing']:return result
            module=importlib.import_module('TB2J.versioninfo');result['version']=str(module.__version__)
            reader=importlib.import_module('TB2J.io_exchange.io_exchange').SpinIO
            if not callable(getattr(reader,'load_pickle',None)):raise ValueError('TB2J 缺少 SpinIO.load_pickle 读取接口')
        response=subprocess.run([sys.executable,'-m','TB2J.scripts.wann2J','--help'],capture_output=True,text=True,timeout=20)
        help_text=response.stdout+response.stderr
        result['unsupported_flags']=[flag for flag in FLAGS if flag not in help_text]
        if response.returncode or result['unsupported_flags']:raise ValueError('TB2J CLI 参数能力不满足：'+', '.join(result['unsupported_flags'])+' '+response.stderr[-500:])
        result.update(ready=True,reason='所选 Python 可实际导入 TB2J，版本与 CLI 能力已检查')
    except Exception as exc:result['reason']=str(exc)
    return result


def request_read(path):
    request=json.loads(path.read_text())
    if request.get('schema')!='fpilot.extension-request.v2':raise ValueError('请求协议无效')
    output=Path(request['output_dir']).resolve();target=Path(request['result_file']).resolve()
    if target.parent!=output:raise ValueError('result_file 须在 output_dir 中')
    for items in request['inputs'].values():
        for item in items:
            parent=Path(item['path']).resolve().parent
            if output==parent or parent in output.parents:raise ValueError('输出目录不得覆盖或位于原输入目录内')
    output.mkdir(parents=True,exist_ok=True)
    return request,output,target


def run(request,output,target):
    from inputs_v2 import inspect,normalize,argv
    from results_v2 import dump,parse
    checked=inspect(request);environment=doctor();dump(output/'environment.json',environment)
    if not environment['ready']:raise ValueError('TB2J 环境不可用：'+environment['reason'])
    model,prefixes=normalize(checked,output);raw=output/'TB2J_results';raw.mkdir()
    args=argv(checked,request['options'],model,prefixes,raw)
    receipt={'python':sys.executable,'TB2J_version':environment['version'],'argv':args,'command_display':shlex.join(args),'options':request['options'],'energy_reference':checked['energy_reference'],'orbital_mapping':checked['mapping'],'sources':request['sources']}
    from wf_report import digest
    receipt['model_bindings']=[{'prefix':prefixes[i],'channel':m['seed'],'hr_sha256':digest(m['ham']['path']),'win_sha256':digest(m['win']['path'])} for i,m in enumerate(checked['models']) if m['ham']['name'].endswith('_hr.dat')]
    receipt['wf_identity_models']=checked.get('wf_identity_models',[]);receipt['wf_spin_alignment']=checked.get('wf_spin_alignment')
    dump(output/'command.json',receipt)
    with (output/'tb2j.stdout.log').open('w') as stdout,(output/'tb2j.stderr.log').open('w') as stderr:
        response=subprocess.run(args,cwd=str(output),stdout=stdout,stderr=stderr,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
    receipt['exit_code']=response.returncode;dump(output/'command.json',receipt)
    if response.returncode:raise ValueError('TB2J 非零退出 '+str(response.returncode)+'；查看 tb2j.stderr.log，独立目录产物保留')
    with contextlib.redirect_stdout(sys.stderr):result=parse(raw,checked,request['options'],output)
    result.update(schema='fpilot.extension-result.v2',status='COMPLETED',scientific_validation='NOT_PERFORMED',sources=request['sources'])
    # Raw products are retained, including large artifacts that remain remote.
    for file in sorted(output.rglob('*')):
        if not file.is_file() or file.is_symlink() or file==target:continue
        relative=str(file.relative_to(output))
        if any(a['path']==relative for a in result['artifacts']):continue
        result['artifacts'].append({'path':relative,'label':relative,'media_type':'application/json' if file.suffix=='.json' else 'text/plain' if file.suffix in ('.log','.out','.txt','.win','.wout','.xyz','.dat') else 'application/octet-stream'})
    encoded=json.dumps(result,ensure_ascii=False,allow_nan=False)
    if len(encoded.encode())>10*1024**2:raise ValueError('页面结果超过 10 MiB；完整产物已保留')
    dump(target,result)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['doctor','check','run']);parser.add_argument('--request',type=Path);args=parser.parse_args()
    if args.action=='doctor':print(json.dumps(doctor(),ensure_ascii=False));return 0
    try:
        if not args.request:raise ValueError('--request 必填')
        request,output,target=request_read(args.request)
        if args.action=='check':
            from inputs_v2 import inspect
            inspect(request);print(json.dumps({'schema':'fpilot.extension-check.v2','status':'APPLICABLE','reason':'来源、文件组合、能量参考和参数适用；数值与物理验证仍待完成'},ensure_ascii=False));return 0
        run(request,output,target);print(json.dumps({'schema':'fpilot.extension-run.v2','result_file':str(target)}));return 0
    except Exception as exc:
        if args.action=='check':print(json.dumps({'schema':'fpilot.extension-check.v2','status':'NOT_APPLICABLE','reason':str(exc)},ensure_ascii=False));return 0
        print(str(exc),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
