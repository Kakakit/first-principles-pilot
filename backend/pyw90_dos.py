"""Process-local DOS numerics; this file can also be sent to the remote adapter."""
import hashlib
import re
from collections import OrderedDict

DOS_REVISION = 'fpilot.dos-integral.v1'
DOS_METHODS = ('spline', 'gauss300')


class DosIntegrator:
    def __init__(self, method='spline'):
        if method not in DOS_METHODS:raise ValueError('积分方式须为 spline 或 gauss300')
        self.method=method;self.curves=OrderedDict();self.values={};self.fits=0

    def __call__(self, energy, density, window):
        import numpy as np
        from scipy.interpolate import BSpline,splrep
        from scipy.integrate import fixed_quad
        x,y=np.asarray(energy,dtype=float),np.asarray(density,dtype=float)
        if (x.ndim!=1 or y.shape!=x.shape or len(x)<4 or not np.isfinite(x).all()
                or not np.isfinite(y).all() or not np.all(np.diff(x)>0)):
            raise ValueError('DOS 能量网格须严格递增，至少 4 点，密度与能量须完整且有限')
        lo,hi=map(float,window)
        if not np.isfinite([lo,hi]).all() or lo>hi:raise ValueError('DOS 积分区间无效')
        lo,hi=max(float(x[0]),lo),min(float(x[-1]),hi)
        if lo>=hi:return 0.0
        key=hashlib.sha256(x.tobytes()+y.tobytes()).digest()
        if key not in self.curves:
            if len(self.curves)>=256:
                removed,_=self.curves.popitem(last=False)
                self.values={k:v for k,v in self.values.items() if k[0]!=removed}
            self.curves[key]=BSpline(*splrep(x,y,s=0));self.fits+=1
        self.curves.move_to_end(key)
        bounds=(key,lo,hi)
        if bounds not in self.values:
            spline=self.curves[key]
            self.values[bounds]=float(spline.integrate(lo,hi) if self.method=='spline' else
                                     fixed_quad(spline,lo,hi,n=300)[0])
        return self.values[bounds]

    def metadata(self):
        return {'revision':DOS_REVISION,'method':self.method,'order':300 if self.method=='gauss300' else None,
                'interpolation':'cubic splrep s=0','spline_fits':self.fits,
                'note':'积分精度针对已有 DOS 样条，不代表原始 DOS 网格收敛'}


def dos_ratio(pdos,tdos):
    import math
    if not all(math.isfinite(v) for v in (pdos,tdos)) or tdos<=0:
        return {'pdos':pdos if math.isfinite(pdos) else None,'tdos':tdos if math.isfinite(tdos) else None,
                'percent':None,'warning':'TDOS 积分非正或非有限，比例不可计算'}
    ratio=pdos/tdos
    return {'pdos':pdos,'tdos':tdos,'percent':ratio,
            'warning':'轨道贡献比例超出 0～100%，请核对投影与 DOS 数据' if not 0<=ratio<=1 else ''}


def projection_keys(lines,frame):
    """Map only explicit species:orbital or species@site:orbital selections.

    Site numbers in this diagnostic selector are zero-based, matching the DOS table.
    Coordinates, hybrids, spin quantization axes and custom trial functions need a
    separate explicit PDOS selection; they must never silently become auto orbitals.
    """
    available=set(frame['key_string']);selected=set()
    for line in lines:
        line=re.split(r'[!#]',line,1)[0].strip()
        if not line:continue
        match=re.fullmatch(r'([A-Z][a-z]?)(?:@(\d+))?\s*:\s*([A-Za-z0-9_; ,+-]+)',line)
        if not match:raise ValueError('投影无法直接映射到 PDOS；请手动选择元素／站点和 s、p、d 等轨道')
        species,site,orbitals=match.groups()
        rows=frame[frame['species']==species]
        if site is not None:rows=rows[rows['structure_id']==int(site)]
        if rows.empty:raise ValueError('PDOS 中找不到所选元素或站点：'+species+(('@'+site) if site else ''))
        for orbital in filter(None,re.split(r'[;,\s]+',orbitals)):
            names=({'s':['s'],'p':['px','py','pz'],'d':['dxy','dyz','dz2','dxz','dx2'],
                    'f':['f_3','f_2','f_1','f0','f1','f2','f3']}).get(orbital,[orbital])
            if orbital not in ('s','p','d','f') and orbital not in set(rows['orb_name']):
                raise ValueError('不支持的 PDOS 轨道：'+orbital)
            matches=rows[rows['orb_name'].isin(names)]
            if set(matches['orb_name'])!=set(names):raise ValueError('所选轨道的 PDOS 不完整：'+species+':'+orbital)
            selected.update(matches['key_string'])
    if not selected or not selected<=available:raise ValueError('请指定用于诊断的投影轨道')
    return sorted(selected)
