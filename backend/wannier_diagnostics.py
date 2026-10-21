"""Manual comparison diagnostics: local reference snapshots, cached arrays and bounded native scoring."""
import csv
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from filelock import FileLock
import numpy as np

from .builtin_analysis import comparison_metrics as metrics
NATIVE_SESSION=uuid.uuid4().hex


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,encoding='utf-8',delete=False) as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);temporary=stream.name
    os.replace(temporary,path)


def evaluate(root,identity,dft,wannier,window,group_size=6,dft_band_start=None):
    aligned,model=metrics.align_segments(dft.x,dft.energies,dft.segments,wannier.x,wannier.energies,wannier.segments)
    content=hashlib.sha256(np.asarray(dft.energies).tobytes()+np.asarray(dft.x).tobytes()).hexdigest()
    signature={**identity,'dft_content':content,'num_wann':int(model.shape[1])}
    key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    folder=root/'references'/key;folder.mkdir(parents=True,exist_ok=True)
    with FileLock(str(folder/'reference.lock'),timeout=5):
        path=folder/'current.json';reference=json.loads(path.read_text()) if path.is_file() else None
        if dft_band_start is not None and (type(dft_band_start) is not int or dft_band_start<1):raise ValueError('DFT 起始带号须为正整数')
        offset=dft_band_start-1 if dft_band_start is not None else reference['offset'] if reference else metrics.first_point_offset(aligned,model)
        if reference is None or reference['offset']!=offset:
            reference={'id':uuid.uuid4().hex,'identity':signature,'offset':offset,'dft_band_first':offset+1,
                       'dft_band_last':offset+model.shape[1],
                       'selection':'user_selected' if dft_band_start is not None else 'first_common_kpoint_continuous_band_match'}
            atomic(folder/(reference['id']+'.json'),reference);atomic(path,reference)
    report=metrics.reference_metrics(aligned,model,0,window,int(reference['offset']),group_size,allow_partial=True)
    return {**report,'reference':reference,'sample_grid':'dft_reference'}


def save_tables(directory,report):
    atomic(directory/'diagnostics.json',report)
    for name,items,fields in (
        ('per_band.csv',report.get('per_band',[]),['wannier_band_1_based','dft_band_1_based','reference_available','paired_points','mae_meV','rmse_meV','max_abs_diff_meV']),
        ('groups.csv',report.get('groups',[]),['wannier_first','wannier_last','dft_first','dft_last','paired_values','mae_meV','rmse_meV','max_abs_diff_meV'])):
        with (directory/name).open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(items)


def summary_lines(record):
    report=record.get('diagnostics') or {};native=record.get('pyw90_diagnostic') or {}
    number=lambda value:'unavailable' if value is None else f'{value:.3f}'
    lines=['Fixed DFT reference',f"Bands {report.get('dft_band_first','?')} - {report.get('dft_band_last','?')}",
           f"Coverage {report.get('matched_bands',0)}/{report.get('expected_bands',0)} bands",
           'Metric '+str(report.get('metric_window_relative_eV',[]))+' eV',
           'MAE   '+number(report.get('mae_meV'))+' meV','RMSE '+number(report.get('rmse_meV'))+' meV',
           'Max    '+number(report.get('max_abs_diff_meV'))+' meV']
    groups=[g for g in report.get('groups',[]) if g.get('rmse_meV') is not None]
    if groups:
        worst=max(groups,key=lambda g:g['rmse_meV'])
        lines+=['Worst W '+str(worst['wannier_first'])+'-'+str(worst['wannier_last']),
                '  RMSE '+number(worst['rmse_meV'])+' meV']
    lines+=['','PyW90 AbAk',number(native.get('quality_value'))+' meV' if native.get('status')=='COMPLETED' else native.get('status','PENDING')]
    windows=record.get('plot_relative_windows') or {}
    for prefix,tags in [('Outer',('dis_win_min','dis_win_max')),('Frozen',('dis_froz_min','dis_froz_max'))]:
        if all(tag in windows for tag in tags):lines.append(prefix+f" [{windows[tags[0]]:.3f}, {windows[tags[1]]:.3f}] eV")
    lines.append('All energies: E - E_F')
    return lines


def native(directory,record,alias,spinors):
    from . import extension_environments,remote_health,pyw90_remote,remote_transport
    from .analysis_queue import subprocess_environment,environment,slot
    request=json.loads((directory/'score-input.json').read_text())
    request.update(metric_window_relative_eV=record['diagnostics']['metric_window_relative_eV'],fermi_eV=record['fermi_eV'],spinors=spinors)
    source=Path(__file__).with_name('pyw90_score.py').read_text()
    python=extension_environments.local_default()['python']
    configured=pyw90_remote.runtime(alias) if pyw90_remote.available(alias) else None
    identity={'revision':hashlib.sha256(source.encode()).hexdigest(),'source':hashlib.sha256((directory/'score-input.json').read_bytes()).hexdigest(),
              'window':request['metric_window_relative_eV'],'spinors':spinors,'session':NATIVE_SESSION,
              'local_python':python,'remote_runtime':configured}
    from . import remote_cache
    with remote_cache.singleflight('pyw90-score',identity):
        cached=remote_cache.read('pyw90-score',identity)
        if cached:return cached
        payload=json.dumps(request,allow_nan=False)
        try:
            response=subprocess.run([python,'-c',source],input=payload,capture_output=True,text=True,
                                    timeout=45,env=subprocess_environment())
            result=_score_receipt(response.stdout,response.stderr,response.returncode)
            result['location']='local'
        except (OSError,subprocess.TimeoutExpired) as exc:result={'status':'FAILED','reason':str(exc)[-600:]}
        if result['status']=='UNAVAILABLE' and configured:
            states=remote_health.status(alias)
            if states and states[0]['state']!='READY':return {'status':'UNAVAILABLE','reason':'源服务器连接已暂停；未发送评分请求'}
            runtime=configured
            command=environment(runtime.get('cores'))+'PYTHONPATH='+remote_transport_path(runtime.get('package'))+' MPLBACKEND=Agg '+shlex.quote(runtime['python'])+' -c '+shlex.quote(source)
            try:
                with slot(alias,wait=False):receipt=remote_transport.run(alias,command,data=payload.encode(),timeout=60,stdout_limit=6000,lane='analysis')
            except ValueError as exc:return {'status':'UNAVAILABLE','reason':str(exc),'location':'remote'}
            result=_score_receipt(receipt.get('stdout',''),receipt.get('stderr',''),receipt.get('exit_code'))
            result['location']='remote'
        if result['status'] in ('COMPLETED','NOT_APPLICABLE'):return remote_cache.write('pyw90-score',identity,result)
        return result


def remote_transport_path(path):
    from .remote_paths import shell_path
    return shell_path(path) if path else "''"


def _score_receipt(stdout,stderr,code):
    if code==0:
        try:return json.loads(next(line.split('=',1)[1] for line in stdout.splitlines() if line.startswith('FPILOT_PYW90_SCORE=')))
        except (ValueError,StopIteration):pass
    return {'status':'FAILED','reason':(stderr or stdout or '未取得原生评分回执')[-600:]}
