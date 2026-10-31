"""Portable geometry/occupation reference. No host, SSH or external file reads."""
import base64,json,copy
import numpy as np
SCHEMA='fpilot.local-orbital-reference.v1'
BUNDLE='fpilot.local-orbital-references.v1'
LIMIT=2*1024*1024
TOL=1e-4

def structure_record(structure):
    return {'lattice_ang':structure.lattice.matrix.tolist(),'atoms':[{'element':a.specie.symbol,'frac':a.frac_coords.tolist()} for a in structure]}

def export_reference(model):
    if 'structure' not in model:raise ValueError('旧占据记录未保存冻结结构，请从同一冻结来源新建后处理，不补用最新草稿')
    sites=[]
    for a in model['atoms']:
        if not a.get('geometry'):continue
        sites.append({k:copy.deepcopy(a[k]) for k in ['atom','element','polyhedron','geometry','orbital_labels','source_lines']})
        sites[-1]['occupation_reference']=[{'spin':s['id'],'trace':s['global']['trace'],'natural_occupations':s['global']['natural_occupations'],'local_diagonal':s['local']['diagonal']} for s in a['spins']]
    if not sites:raise ValueError('请先填写 CrO6、FeO4 等多面体类型，才能导出局域配位参考')
    return {'schema':SCHEMA,'structure':copy.deepcopy(model['structure']),'sites':sites,'source':copy.deepcopy(model.get('source',{})),'settings':copy.deepcopy(model['settings']),'matrix_evidence':copy.deepcopy(model['evidence']),'meaning':'局域几何轴与PAW占据参考；不是最终WF身份、Wannier gauge或能带拟合验证','axis_convention':'axes列为局域x/y/z在全局笛卡尔坐标的分量；d顺序dz2,dxz,dyz,dx2-y2,dxy','physical_validation':'NOT_PERFORMED'}

def decode_reference(value):
    if not value:return None
    if not isinstance(value,str):raise ValueError('局域轨道参考须为JSON文件内容')
    if len(value)>LIMIT*2:raise ValueError('局域轨道参考超过2 MiB，请导出单一来源')
    try:
        if value.startswith('data:'):
            head,body=value.split(',',1)
            if not head.endswith(';base64'):raise ValueError()
            raw=base64.b64decode(body,validate=True)
        else:raw=value.encode('utf-8')
        if len(raw)>LIMIT:raise ValueError('size')
        obj=json.loads(raw,parse_constant=lambda v:(_ for _ in ()).throw(ValueError(v)))
        if not isinstance(obj,dict) or obj.get('schema') not in (SCHEMA,BUNDLE):raise ValueError()
    except (ValueError,TypeError,UnicodeError):raise ValueError('局域轨道参考不是有效的扩展JSON文件，或超过2 MiB') from None
    return obj

def _array(value,shape):
    try:a=np.asarray(value,dtype=float)
    except (ValueError,TypeError):raise ValueError('参考文件的几何数组无效') from None
    if a.shape!=shape or not np.isfinite(a).all():raise ValueError('参考文件的几何数组维度或数值无效')
    return a

def match_structure(ref,structure):
    try:
        frozen=ref['structure'];cell=_array(frozen['lattice_ang'],(3,3));atoms=frozen['atoms']
        if not np.allclose(cell,structure.lattice.matrix,rtol=0,atol=TOL) or len(atoms)!=len(structure):return False
        for a,b in zip(atoms,structure):
            if a['element']!=b.specie.symbol:return False
            frac=_array(a['frac'],(3,))
            if structure.lattice.get_distance_and_image(frac,b.frac_coords)[0]>TOL:return False
        return True
    except (KeyError,TypeError):raise ValueError('参考文件缺少冻结结构或原子身份') from None

def inherit(options,structure):
    """Validate all imported geometry before deriving any Wannier candidates."""
    from polyhedron_types import parse_types
    obj=decode_reference(options.get('local_reference'))
    if obj is None:return dict(options),None,{}
    refs=obj.get('references') if obj.get('schema')==BUNDLE else [obj]
    if not isinstance(refs,list) or not refs or len(refs)>64:raise ValueError('局域轨道参考来源列表无效')
    if any(not isinstance(r,dict) or r.get('schema')!=SCHEMA for r in refs):raise ValueError('局域轨道参考版本无效')
    matched=[r for r in refs if match_structure(r,structure)]
    if len(matched)!=1:raise ValueError('局域轨道参考与当前晶胞、原子顺序或坐标不符' if not matched else '多个参考匹配同一结构，请在占据结果页导出单一来源')
    ref=matched[0];sites=ref.get('sites');by_atom={};types=[]
    if not isinstance(sites,list) or not sites:raise ValueError('局域轨道参考没有配位位点')
    for site in sites:
        try:
            idx=site['atom'];element=site['element'];typ=site['polyhedron'];g=site['geometry'];q=_array(g['axes'],(3,3))
            if type(idx)!=int or not 1<=idx<=len(structure) or idx in by_atom or structure[idx-1].specie.symbol!=element:raise ValueError('参考位点原子编号重复或身份不符')
            if not np.allclose(q.T@q,np.eye(3),atol=1e-8,rtol=0) or not np.isclose(np.linalg.det(q),1,atol=1e-8):raise ValueError('参考局域轴不是正交真旋转')
            spec=parse_types({'polyhedron_types':[typ]})
            if element not in spec:raise ValueError('参考多面体与中心元素不符')
            spec=spec[element];ns=g['neighbors']
            if len(ns)!=spec['coordination']:raise ValueError('参考配位数不符')
            seen=set()
            for n in ns:
                i=n['atom'];image=_array(n['image'],(3,))
                if type(i)!=int or not 1<=i<=len(structure) or not np.array_equal(image,np.round(image)):raise ValueError('参考配体编号或周期镜像无效')
                key=(i,tuple(image))
                if key in seen:raise ValueError('参考配体重复')
                seen.add(key)
                if n['element']!=spec['ligand'] or structure[i-1].specie.symbol!=spec['ligand']:raise ValueError('参考配体元素不符')
                frozen_center=_array(ref['structure']['atoms'][idx-1]['frac'],(3,));frozen_ligand=_array(ref['structure']['atoms'][i-1]['frac'],(3,))
                v=(frozen_ligand+image-frozen_center)@structure.lattice.matrix
                if not np.allclose(v,_array(n['vector_ang'],(3,)),rtol=0,atol=TOL) or not np.isclose(np.linalg.norm(v),n['distance_ang'],atol=TOL,rtol=0):raise ValueError('参考配体向量或键长与冻结结构不符')
            # Distances and identities define the frozen shell; do not refit its axes.
            from onsite_core import neighbors
            _,fresh=neighbors(structure,idx-1,[spec['ligand']],spec['coordination'])
            if not np.allclose(sorted(n['distance_ang'] for n in ns),sorted(n['distance_ang'] for n in fresh['neighbors']),rtol=0,atol=TOL):raise ValueError('参考配位壳层不是当前结构的最近邻壳层')
            if typ not in types:types.append(typ)
            by_atom[idx]=site
        except (KeyError,TypeError,IndexError):raise ValueError('局域轨道参考缺少完整的位点、局域轴或配体几何') from None
    existing=parse_types(options)
    inherited=parse_types({'polyhedron_types':types})
    if existing and existing!=inherited:raise ValueError('当前多面体类型与导入参考冲突，请清空类型以继承，或选择匹配的参考')
    if any(options.get(k) for k in ['elements','ligands','coordination']):raise ValueError('导入局域参考时请清空旧元素/配体/配位参数')
    for i,a in enumerate(structure,1):
        if a.specie.symbol in inherited and i not in by_atom:raise ValueError('参考缺少所选中心元素的部分位点')
    radius=float(options.get('search_radius_angstrom',3))
    if any(n['distance_ang']>radius for s in sites for n in s['geometry']['neighbors']):raise ValueError('参考配体超出当前最大键长，请核对高级设置')
    opt=dict(options,polyhedron_types=types)
    summary={'status':'STRUCTURE_AND_FRAME_MATCHED','source':copy.deepcopy(ref.get('source',{})),'site_count':len(sites),'structure_tolerance_ang':TOL,'wf_identity':'UNVERIFIED','lineage':'来源声明随参考保留；结构匹配不证明电子设置或DFT父任务相同','meaning':ref.get('meaning'),'occupation_comparison':'PAW与Wannier子空间不同，不自动相减或要求占据相等'}
    return opt,summary,by_atom

def public_options(options):
    return {k:v for k,v in options.items() if k!='local_reference'}
