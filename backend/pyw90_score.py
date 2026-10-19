"""Evaluate cached arrays with the installed PyW90; no scientific input or plot I/O."""
import json
import sys


def score(request):
    try:
        import numpy as np
        from pyw90.lib.w90 import W90
        from pyw90.utility.utility import parse_kernel
        from importlib.metadata import version
    except ImportError as exc:return {'status':'UNAVAILABLE','reason':str(exc)}
    if request.get('spinors'):return {'status':'NOT_APPLICABLE','reason':'SOC 使用通用分段误差，不套用原生共线评分'}
    vx,wx=np.asarray(request['dft_x']),np.asarray(request['wannier_x'])
    ve,we=np.asarray(request['dft_energy']).T,np.asarray(request['wannier_energy']).T
    if ve.shape[0]<we.shape[0]:return {'status':'NOT_APPLICABLE','reason':'DFT 参考带数不足，无法计算完整原生评分'}
    if min(len(np.unique(vx)),len(np.unique(wx)))<9:
        return {'status':'NOT_APPLICABLE','reason':'采样点不足以执行 PyW90 原生 9 点平滑'}
    if not all(np.isfinite(values).all() for values in (vx,wx,ve,we)):
        return {'status':'FAILED','reason':'原生评分输入含非有限值'}
    class CachedW90(W90):
        def __init__(self):self.path='';self.vasp_bnd='dft';self.w90_bnd='wannier'
        def _parse_dat(self,path):return (vx,ve) if path=='dft' else (wx,we)
    lo,hi=request['metric_window_relative_eV'];fermi=request['fermi_eV']
    kernel=parse_kernel('unit',fermi+(lo+hi)/2,(hi-lo)/2)
    obj=CachedW90()
    try:
        value=float(obj.evaluate(mode='AbAk',kernel=kernel))
        if not np.isfinite(value):raise ValueError('评分非有限；评价范围内可能没有原生加权样本')
        return {'status':'COMPLETED','quality_value':value,'quality_unit':'meV','pyw90_version':version('pyw90'),
                'mode':'AbAk','kernel':'unit','metric_window_relative_eV':[lo,hi],
                'dft_band_first':int(obj.nbnds_excl)+1,'dft_band_last':int(obj.nbnds_excl)+we.shape[0],
                'smoothing':{'method':'Savitzky-Golay','window':9,'order':2},
                'note':'原生匹配和加权平滑口径；与固定带段、未平滑主误差分别解读'}
    except Exception as exc:return {'status':'FAILED','reason':str(exc)[-600:]}


if __name__=='__main__':print('FPILOT_PYW90_SCORE='+json.dumps(score(json.load(sys.stdin)),allow_nan=False))
