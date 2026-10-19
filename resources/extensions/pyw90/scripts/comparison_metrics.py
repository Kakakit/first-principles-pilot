"""Pure array comparison shared by manual diagnostics and existing scan callers.

No file access, transport, ranking gates, job execution or optimizer state.
"""
from __future__ import annotations
import numpy as np

REVISION='fpilot.band-metrics.v1'


def align_segments(dft_x,dft,dft_segments,wannier_x,wannier,wannier_segments):
    if len(dft_segments)!=len(wannier_segments):raise ValueError('路径段数不同')
    references=[];models=[]
    for (d0,d1),(w0,w1) in zip(dft_segments,wannier_segments):
        dx,wx=np.asarray(dft_x[d0:d1+1]),np.asarray(wannier_x[w0:w1+1])
        if len(dx)<2 or len(wx)<2 or np.any(np.diff(dx)<=0) or np.any(np.diff(wx)<=0):
            raise ValueError('每条路径段至少需要两个严格递增的采样点')
        df=(dx-dx[0])/(dx[-1]-dx[0]);wf=(wx-wx[0])/(wx[-1]-wx[0])
        references.append(dft[d0:d1+1])
        models.append(np.column_stack([np.interp(df,wf,wannier[w0:w1+1,b]) for b in range(wannier.shape[1])]))
    return np.vstack(references),np.vstack(models)


def first_point_offset(dft,wannier):
    count=min(dft.shape[1],wannier.shape[1])
    if count<1:raise ValueError('没有可配对能带')
    # The first comparison suggests one continuous DFT block; future comparisons
    # use the saved offset instead of optimizing it again against their residuals.
    return min(range(dft.shape[1]-count+1),key=lambda off:(float(np.abs(dft[0,off:off+count]-wannier[0,:count]).sum()),off))


def stats(residual):
    residual=np.asarray(residual,dtype=float)
    if not residual.size:return {'paired_values':0,'mae_meV':None,'rmse_meV':None,'max_abs_diff_meV':None}
    if not np.isfinite(residual).all():raise ValueError('配对能带包含非有限值')
    return {'paired_values':int(residual.size),'mae_meV':float(np.abs(residual).mean()*1000),
            'rmse_meV':float(np.sqrt(np.square(residual).mean())*1000),
            'max_abs_diff_meV':float(np.abs(residual).max()*1000)}


def reference_metrics(dft,wannier,fermi,window,offset,group_size=6,allow_partial=False):
    dft,wannier=np.asarray(dft,dtype=float),np.asarray(wannier,dtype=float)
    if dft.ndim!=2 or wannier.ndim!=2 or dft.shape[0]!=wannier.shape[0]:raise ValueError('能带采样形状不同')
    if not np.isfinite(dft).all() or not np.isfinite(wannier).all():raise ValueError('能带含非有限值')
    if len(window)!=2 or not np.isfinite(window).all() or window[0]>=window[1]:raise ValueError('评价范围无效')
    if type(offset) is not int or offset<0:raise ValueError('DFT 起始带号无效')
    if type(group_size) is not int or not 1<=group_size<=256:raise ValueError('每组带数须为 1～256 的整数')
    expected=wannier.shape[1];matched=max(0,min(expected,dft.shape[1]-offset))
    if matched<expected and not allow_partial:raise ValueError('DFT band offset exceeds available reference bands')
    parts=[];per_band=[]
    for band in range(expected):
        residual=np.empty(0)
        if band<matched:
            reference=dft[:,offset+band]-fermi
            mask=(reference>=window[0])&(reference<=window[1])
            residual=(wannier[:,band]-dft[:,offset+band])[mask]
        parts.append(residual)
        item={'wannier_band_1_based':band+1,'dft_band_1_based':offset+band+1,
              **stats(residual),'reference_available':band<matched}
        item['paired_points']=item['reference_points']=item['paired_values'];per_band.append(item)
    measured=stats(np.concatenate(parts) if parts else np.empty(0));groups=[]
    for start in range(0,expected,group_size):
        end=min(start+group_size,expected)
        groups.append({'wannier_first':start+1,'wannier_last':end,'dft_first':offset+start+1,'dft_last':offset+end,
                       **stats(np.concatenate(parts[start:end]))})
    complete=matched==expected
    overall=measured if complete else stats([])
    return {'revision':REVISION,'status':'PARTIAL' if not complete else 'MEASURED' if measured['paired_values'] else 'EMPTY',
            'offset':offset,'dft_band_first':offset+1,'dft_band_last':offset+expected,
            'matched_bands':matched,'expected_bands':expected,'reference_values':measured['paired_values'],
            'sample_selector':'dft_reference_energy','interpolation':'segment-local linear',
            'metric_window_relative_eV':list(window),'group_size':group_size,**overall,
            'partial_metrics':None if complete else measured,'per_band':per_band,'groups':groups}


def scan_reference_metrics(dft,wannier,fermi,window,requested_offset):
    """Compatibility with the pre-existing scan scoring contract, without its workflow."""
    maximum=dft.shape[1]-wannier.shape[1]
    if maximum<0:raise ValueError('Wannier band count exceeds DFT band count')
    offsets=range(maximum+1) if requested_offset=='auto' else [int(requested_offset)]
    rows=[reference_metrics(dft,wannier,fermi,window,offset) for offset in offsets]
    rows=[row for row in rows if row['paired_values']]
    if not rows:raise ValueError('no reference DFT energies lie in the requested ranking window')
    chosen=min(rows,key=lambda row:(row['rmse_meV'],row['offset']))
    chosen['selection']='minimum RMSE over order-preserving offsets using fixed DFT-reference samples'
    return chosen
