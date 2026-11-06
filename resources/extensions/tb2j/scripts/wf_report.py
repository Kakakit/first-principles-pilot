"""Portable WF identity contract; standard library only; no host access."""
import base64,hashlib,json,math
from pathlib import Path
SCHEMA='fpilot.wf-identity.v1'
EXPORT_HINT = '请在「轨道占据、能级与劈裂分析」中打开同一对上、下自旋的 Wannier 能级结果，点击「下载给 TB2J 用的文件」，选入 wf-identity.json。'
LOCAL_REFERENCE_SCHEMAS = ('fpilot.local-orbital-reference.v1', 'fpilot.local-orbital-references.v1')

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def read_report(value):
    if not value:return None
    if isinstance(value, str) and len(value) > 12 * 1024**2:
        raise ValueError('轨道成分文件超过导入大小上限。' + EXPORT_HINT)
    try:
        raw = base64.b64decode(value.split(',', 1)[1], validate=True) if value.startswith('data:') else value.encode()
        d = json.loads(raw.decode('utf-8-sig'), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (AttributeError, ValueError, IndexError):
        raise ValueError('无法读取这份 JSON 文件。' + EXPORT_HINT) from None
    schema = d.get('schema') if isinstance(d, dict) else None
    if schema in LOCAL_REFERENCE_SCHEMAS:
        raise ValueError('你选的是 VASP 局域坐标参考，请把它放在 Wannier 能级分析的「VASP 局域坐标参考」。' + EXPORT_HINT)
    if schema != SCHEMA:
        raise ValueError('这里需要 TB2J 的轨道成分文件。' + EXPORT_HINT)
    try:
        if not isinstance(d.get('models'), list) or not 1 <= len(d['models']) <= 64:
            raise ValueError()
        for m in d['models']:
            if not m.get('binding',{}).get('hr_sha256') or not m['binding'].get('win_sha256') or not isinstance(m['orbitals'],list):raise ValueError()
            if type(m['num_wann'])!=int or not 1<=m['num_wann']<=512:raise ValueError()
            if m['binding'].get('udis_sha256') and m.get('window_validation',{}).get('status')!='MATCHED_HR_DIAGONAL':raise ValueError()
            ids=[r['wf'] for r in m['orbitals']]
            if ids!=list(range(1,len(ids)+1)) or len(ids)!=m['num_wann']:raise ValueError()
            for row in m['orbitals']:
                if not isinstance(row['label'],str) or len(row['label'])>160:raise ValueError()
                if type(row.get('atom'))!=int or row['atom']<1 or not isinstance(row.get('status'),str):raise ValueError()
                if not isinstance(row.get('components'),list) or len(row['components'])>512:raise ValueError()
                for c in row['components']:
                    if not isinstance(c.get('label'),str) or len(c['label'])>160 or c.get('l') not in (0,1,2):raise ValueError()
                    w=c.get('relative_weight')
                    if isinstance(w,bool) or not isinstance(w,(int,float)) or not math.isfinite(w) or not 0<=w<=1+1e-8:raise ValueError()
                for k in ['coverage','home_fraction','owner_fraction','dominant_fraction']:
                    v=row[k]
                    if not isinstance(v,(int,float)) or not math.isfinite(v) or not -1e-8<=v<=1+1e-8:raise ValueError()
        for model in d['models']:
            h=model['home_overlap']
            for key in ['real','imag']:
                if len(h[key])!=model['num_wann'] or any(len(row)!=model['num_wann'] for row in h[key]):raise ValueError()
                if any(not isinstance(v,(int,float)) or not math.isfinite(v) for row in h[key] for v in row):raise ValueError()
        return d
    except (AttributeError, ValueError, KeyError, TypeError, IndexError, OverflowError):
        raise ValueError('这份轨道成分文件的数据不完整或有无效数值。' + EXPORT_HINT) from None

def bind_report(report,hr,win,channel=None):
    if not report:return None
    h=digest(hr);w=digest(win);matches=[m for m in report['models'] if m['binding']['hr_sha256']==h and m['binding']['win_sha256']==w and (channel is None or m['channel']==channel)]
    if len(matches)!=1:raise ValueError('WF身份报告不属于此冻结hr/win模型，或包含重复来源')
    return matches[0]

def mapping_rows(model,basis_rows):
    if not model:return []
    result=[];orbitals={r['wf']:r for r in model['orbitals']}
    for b in basis_rows:
        wf=b['wannier_index'];r=orbitals.get(wf)
        if r is None:raise ValueError('TB2J基底WF编号超出身份报告')
        same=r.get('atom')==b['atom_index']
        result.append({**b,'label':('胞内参考占比较低 WF' if r['status'] in ('CROSS_CELL_MIXED','LOW_HOME_OVERLAP') else r['label']) if same else 'WF'+str(wf)+' · 原子归属待核对','group':'','source':'最终WF参考重叠','status':r['status'] if same else 'ATOM_ASSIGNMENT_CONFLICT','coverage':r['coverage'],'home_fraction':r['home_fraction'],'owner_fraction':r['owner_fraction'],'dominant_fraction':r['dominant_fraction'],'components':r['components'],'meaning':'原WF基底的交换贡献；未换基底或按成分权重重分配J'})
    return result
