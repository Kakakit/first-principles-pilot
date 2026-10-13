#!/usr/bin/env python3
import argparse,importlib,importlib.metadata,json,sys
from pathlib import Path

def dump(path,data):Path(path).write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['doctor','check','run']);parser.add_argument('--request',type=Path);args=parser.parse_args()
    if args.action=='doctor':
        missing=[];versions={}
        for name in ['numpy','scipy','pymatgen']:
            try:importlib.import_module(name);versions[name]=importlib.metadata.version(name)
            except ImportError:missing.append(name)
        print(json.dumps({'schema':'fpilot.extension-doctor.v2','ready':not missing and sys.version_info>=(3,9),'missing':missing,'python':sys.executable,'python_version':sys.version.split()[0],'versions':versions}));return 0
    try:
        from eos_core import collect,fit_groups,write_csv,FIELDS
        r=json.loads(args.request.read_text())
        if r.get('schema')!='fpilot.extension-request.v2':raise ValueError('未知请求协议')
        if not 1<=len(r['sources'])<=64:raise ValueError('需要 1–64 个来源')
        for items in r['inputs'].values():
            for item in items:
                p=Path(item['path'])
                if p.is_symlink() or not p.is_file() or not p.stat().st_size:raise ValueError('输入必须是非空普通文件')
        data=collect(r)
        if args.action=='check':
            print(json.dumps({'schema':'fpilot.extension-check.v2','status':'APPLICABLE','reason':'完整步数据可提取；拟合资格在数据表逐点显示'},ensure_ascii=False));return 0
        out=Path(r['output_dir']);out.mkdir(parents=True,exist_ok=True);target=Path(r['result_file'])
        if target.resolve().parent!=out.resolve():raise ValueError('结果必须在输出目录')
        data['fits']=fit_groups(data['points'],data['options']);dump(out/'pressure-eos.json',data);write_csv(out/'pressure-eos.csv',data)
        columns=[{'key':'source','label':'来源'},{'key':'volume','label':'最终体积','unit':'Å³'},{'key':'energy','label':'energy(sigma→0)','unit':'eV'},{'key':'a','label':'a','unit':'Å'},{'key':'b','label':'b','unit':'Å'},{'key':'c','label':'c','unit':'Å'},{'key':'blockers','label':'拟合排除原因'}]
        for col in columns:col.setdefault('unit','')
        rows=[{'source':p['label'],'volume':p['values']['volume_a3'],'energy':p['values']['energy_sigma0_ev'],'a':p['values']['a_ang'],'b':p['values']['b_ang'],'c':p['values']['c_ang'],'blockers':'; '.join(p['fit_blockers'])} for p in data['points']]
        result={'schema':'fpilot.extension-result.v2','status':'COMPLETED','summary':f"已提取 {len(rows)} 个压力结构点；拟合不等于物理验证",'scientific_validation':'NOT_PERFORMED','sources':r['sources'],
                'warnings':list(dict.fromkeys(w for p in data['points'] for w in p['warnings'])),'tables':[{'id':'pressure-points','title':'压力结构原始数据','columns':columns,'rows':rows}],'curves':[],
                'artifacts':[{'path':'pressure-eos.json','label':'完整数据与证据','media_type':'application/json'},{'path':'pressure-eos.csv','label':'原始数据表','media_type':'text/csv'}]}
        dump(target,result);print(json.dumps({'schema':'fpilot.extension-run.v2','result_file':str(target)}));return 0
    except Exception as exc:
        if args.action=='check':print(json.dumps({'schema':'fpilot.extension-check.v2','status':'NOT_APPLICABLE','reason':str(exc)},ensure_ascii=False));return 0
        print(str(exc),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
