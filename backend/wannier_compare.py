"""Independent local DFT/Wannier band comparison from explicitly selected attempts."""
from __future__ import annotations

from . import wannier_lineage

import csv
import hashlib
import json
import math
import re
import shutil
import sys
import uuid
import threading
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import artifacts, execution, remote_paths, wannier_bands, wannier_execution
from . import compact_plots, safe_files, remote_transport, recycle
from . import wannier_diagnostics as diagnostics
from .recycle import is_recycled
from .storage import ROOT

EXT_SOURCE = Path(__file__).resolve().parents[1] / 'resources/extensions/electronic-structure-characterization/src'
if str(EXT_SOURCE) not in sys.path:
    sys.path.insert(0, str(EXT_SOURCE))
from banddos_core.common import read_fermi_from_outcar  # noqa: E402
from banddos_core.plotting import plot_compare, remap_channel_to_ticks  # noqa: E402
from banddos_core.vasp import vasp_band_channels_from_eigenval  # noqa: E402
from banddos_core.wannier import wannier_channel  # noqa: E402

ROOT_COMPARE = ROOT / 'wannier-comparisons'


def _same_path(kpoints_file, proposal):
    lines = kpoints_file.read_text().splitlines()
    if len(lines) < 6 or lines[2].strip().lower() != 'line-mode' or not lines[3].strip().lower().startswith('rec'):
        raise ValueError('DFT KPOINTS 不是可核对的倒空间 line-mode 路径')
    try:
        if int(lines[1].strip()) != int(proposal['bands_num_points']):
            raise ValueError('DFT 与 Wannier90 每段采样点数不同')
    except (TypeError, KeyError) as exc:
        raise ValueError('冻结路径缺少每段采样点数') from exc
    dft_rows = [line.strip() for line in lines[4:] if line.strip()]
    wan_rows = proposal.get('path_lines') or []
    if len(dft_rows) != 2 * len(wan_rows) or not wan_rows:
        raise ValueError('DFT 与 Wannier90 高对称路径段数不同')
    def label(value):
        return value.replace('$', '').replace('_', '').replace('GAMMA', 'Γ').replace('Gamma', 'Γ').upper()
    for index, line in enumerate(wan_rows):
        fields = line.split()
        if len(fields) != 8:
            raise ValueError('冻结 Wannier90 路径行格式无效')
        for endpoint, part in enumerate((fields[:4], fields[4:])):
            dft = dft_rows[2 * index + endpoint]
            if '!' not in dft:
                raise ValueError('DFT KPOINTS 缺少端点标签')
            coords, name = dft.split('!', 1)
            numbers = coords.split()
            if len(numbers) < 3 or label(part[0]) != label(name.strip()):
                raise ValueError('DFT 与 Wannier90 路径端点标签不同')
            try:
                if any(abs(float(part[offset + 1]) - float(numbers[offset])) > 1e-4 for offset in range(3)):
                    raise ValueError('DFT 与 Wannier90 路径端点坐标不同，不能叠加')
            except (ValueError, IndexError) as exc:
                if isinstance(exc, ValueError) and '坐标不同' in str(exc):
                    raise
                raise ValueError('DFT 或 Wannier90 路径坐标无效') from exc


def _w90(record):
    if record.get('kind') != 'wannier90' or record.get('state') != 'COMPLETED' or not (record.get('evidence') or {}).get('wannier_all_done'):
        raise ValueError('请选择有 Wannier90 完成证据的终态 attempt')
    path = ROOT / 'wannier-bundles' / (record['bundle_id'] + '.json')
    bundle = json.loads(path.read_text())
    if bundle.get('project_id') != record.get('project_id'):
        raise ValueError('Wannier90 冻结来源项目不一致')
    parent = execution.get(bundle['parent_attempt_id'])
    return bundle, parent


def _band(record, project_id, alias, scf_id):
    if (record.get('project_id') != project_id or record.get('state') != 'COMPLETED' or
        record.get('config', {}).get('alias') != alias or
        not (record.get('evidence') or {}).get('vasp_timing') or
        wannier_lineage.scf_id(record) != scf_id):
        raise ValueError('DFT 能带须为同项目、同服务器、同 SCF 来源的已完成 attempt')
    manifest, _ = execution.bundle(record['bundle_id'], allow_dependencies=True)
    if manifest.get('stage') != 'bands':
        raise ValueError('所选 DFT 来源不是能带阶段')


def candidates(project_id, wannier_attempt_id):
    w90 = execution.get(wannier_attempt_id)
    if w90.get('project_id') != project_id:
        raise ValueError('Wannier90 attempt 不属于当前项目')
    _, parent = _w90(w90)
    scf_id = wannier_lineage.scf_id(parent)
    if not scf_id:
        raise ValueError('VASP-Wannier 来源未记录 SCF 依赖，不能确认同源能带')
    result = []
    for record in execution.history(project_id):
        try:
            _band(record, project_id, w90['config']['alias'], scf_id)
        except (ValueError, KeyError, OSError):
            continue
        result.append({'id': record['id'], 'job_id': record.get('job_id'),
                       'node_id': record.get('node_id'), 'path': record.get('remote_path_absolute'),
                       'alias': record['config']['alias']})
    return result


def _pull(record, name, directory):
    listing = artifacts.refresh(record['id'])
    matches = [item for item in listing['files'] if item['path'] == name and item['size_bytes'] > 0]
    if len(matches) != 1:
        raise ValueError(f'{record["id"]} 缺少非空 {name}')
    if matches[0]['size_bytes'] > artifacts.MAX_PULL_BYTES:
        raise ValueError(name + ' 超过单文件受控回传上限；请先生成紧凑能带数据')
    raw, _ = artifacts.pull(record['id'], name)
    target = directory / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return {'attempt_id': record['id'], 'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
            'size_bytes': len(raw)}


def run(project_id, wannier_attempt_id, band_attempt_id, emin=-4, emax=4, diagnostic=False, title=None,
        metric_emin=None,metric_emax=None,group_size=6,dft_band_start=None,on_plot_ready=None):
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (emin, emax)) or not -100 <= emin < emax <= 100:
        raise ValueError('能量范围无效')
    metric_window=[emin if metric_emin is None else metric_emin,emax if metric_emax is None else metric_emax]
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in metric_window) or not -100<=metric_window[0]<metric_window[1]<=100:
        raise ValueError('评价范围无效')
    w90 = execution.get(wannier_attempt_id)
    if w90.get('project_id') != project_id:
        raise ValueError('Wannier90 attempt 不属于当前项目')
    if title is not None and (not isinstance(title,str) or not title.strip() or len(title)>500):raise ValueError('叠图标题须为 1–500 字')
    bundle, parent = _w90(w90)
    scf_id = wannier_lineage.scf_id(parent)
    if not scf_id:
        raise ValueError('VASP-Wannier 来源缺少 SCF 依赖')
    band = execution.get(band_attempt_id)
    _band(band, project_id, w90['config']['alias'], scf_id)
    output_record = w90
    seed = bundle['seed']
    recycle.assert_available('attempts',w90['id'],w90)
    path_record = bundle.get('band_path')
    original_ready = bool(path_record and path_record.get('band_attempt_id') == band_attempt_id)
    if not original_ready:
        children = [item for item in execution.history(project_id)
                    if item.get('kind') == 'wannier90-plot' and item.get('plot_source_attempt_id') == wannier_attempt_id
                    and item.get('band_attempt_id') == band_attempt_id and item.get('state') == 'COMPLETED'
                    and (item.get('evidence') or {}).get('wannier_all_done')]
        if not children:
            raise ValueError('局域化能带文件缺失或缺少与所选 DFT 路径绑定的来源；请先使用 restart=plot 补画并等待完成')
        output_record = children[0]
        path_record = wannier_execution._bundle(output_record)['band_path']
    recycle.assert_available('attempts',band['id'],band)
    recycle.assert_available('attempts',output_record['id'],output_record)
    scf = execution.get(scf_id)
    if scf.get('project_id') != project_id or scf.get('config', {}).get('alias') != w90['config']['alias']:
        raise ValueError('SCF 能量参考项目或服务器不一致')
    identifier = uuid.uuid4().hex
    directory = ROOT_COMPARE / identifier
    directory.mkdir(parents=True, exist_ok=False)
    sources = []
    try:
        dft_dir, wannier_dir = directory / 'dft', directory / 'wannier'
        with remote_transport.operation('band-comparison') as scope:
            start_time=time.monotonic()
            dft_data,wannier_data=remote_transport.parallel([lambda:compact_plots.fetch(band,'band'),lambda:_wannier_data(output_record,seed)])
            dft_dir.mkdir(parents=True,exist_ok=True);wannier_dir.mkdir(parents=True,exist_ok=True)
            for name in ('EIGENVAL','KPOINTS','POSCAR'):
                (dft_dir/name).write_text(dft_data[name.lower()+'_text'])
            for name in (seed+'_band.dat',seed+'_band.labelinfo.dat'):
                shutil.copyfile(Path(wannier_data['directory'])/name,wannier_dir/name)
            sources.extend(dft_data['sources']);sources.extend(wannier_data['sources'])
            if hashlib.sha256((dft_dir/'KPOINTS').read_bytes()).hexdigest()!=path_record['kpoints_sha256'] or hashlib.sha256((dft_dir/'POSCAR').read_bytes()).hexdigest()!=path_record['poscar_sha256']:
                raise ValueError('DFT 结构或路径与冻结合同不同，请刷新来源并重新审查')
            _same_path(dft_dir/'KPOINTS',path_record)
            fermi=dft_data['metadata']['energy_reference']['fermi_eV']
            prepare_seconds=time.monotonic()-start_time
            timings=list(scope['events'])
        dft, _ = vasp_band_channels_from_eigenval(dft_dir / 'EIGENVAL', dft_dir / 'KPOINTS',
                                                  dft_dir / 'POSCAR', 'auto', fermi)
        index = 1 if seed.endswith('.2') else 0
        if index >= len(dft):
            raise ValueError('Wannier90 .2 通道与 DFT 单通道能带不匹配')
        wannier = wannier_channel(wannier_dir / (seed + '_band.dat'),
                                  wannier_dir / (seed + '_band.labelinfo.dat'), fermi, seed)
        # This also rejects a mismatched high-symmetry path instead of drawing a false overlay.
        normalize = lambda labels: [str(label).replace('$', '').replace('GAMMA', 'Γ').replace('Gamma', 'Γ') for label in labels]
        if normalize(wannier.labels) != normalize(dft[index].labels):
            raise ValueError('DFT 与 Wannier90 的高对称路径标签不一致，不能叠加')
        mapped = remap_channel_to_ticks(wannier, dft[index])
        report=diagnostics.evaluate(ROOT_COMPARE,{'project_id':project_id,'band_attempt_id':band_attempt_id,'seed':seed},
                                    dft[index],wannier,metric_window,group_size,dft_band_start)
        diagnostics.save_tables(directory,report)
        diagnostics.atomic(directory/'score-input.json',{
            'dft_x':dft[index].x.tolist(),'dft_energy':(dft[index].energies+fermi).tolist(),
            'wannier_x':wannier.x.tolist(),'wannier_energy':(wannier.energies+fermi).tolist()})
        with (directory / 'curves.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['method', 'spin', 'band', 'k_index', 'path_x_A^-1', 'energy_relative_eV'])
            wannier_axis = [float('nan')] * len(wannier.x)
            for (start, end), segment in zip(wannier.segments, mapped):
                for k, x in zip(range(start, end + 1), segment):
                    wannier_axis[k] = float(x)
            if not all(math.isfinite(x) for x in wannier_axis):
                raise ValueError('Wannier90 路径坐标不完整')
            for channel, axis, method in ((dft[index], dft[index].x, 'DFT'),
                                          (wannier, wannier_axis, 'Wannier90')):
                for b in range(channel.energies.shape[1]):
                    for k in range(channel.energies.shape[0]):
                        writer.writerow([method, seed, b + 1, k + 1, float(axis[k]), float(channel.energies[k, b])])
        pyw90={'status':'PENDING' if diagnostic else 'NOT_REQUESTED'}
        window_meta=wannier_execution.window_metadata(bundle)
        receipt = {'schema': 'fpilot.wannier-band-comparison.v1', 'id': identifier,
                   'project_id': project_id, 'title':title.strip() if title else None, 'wannier_attempt_id': wannier_attempt_id,
                   'wannier_plot_attempt_id': output_record['id'] if output_record['id'] != wannier_attempt_id else None,
                   'band_attempt_id': band_attempt_id, 'scf_attempt_id': scf_id,
                   'seed': seed, 'fermi_eV': fermi, 'energy_window_eV': [emin, emax],
                   **window_meta,'plot_relative_windows':{tag:value-fermi for tag,value in window_meta['absolute_windows'].items()},
                   'energy_reference':bundle.get('energy_reference',{}),'win_sha256':bundle.get('win_sha256'),'input_reason':bundle.get('input_reason'),
                   'sources': sources, 'files': ['comparison.png', 'comparison.svg', 'curves.csv','diagnostics.json','per_band.csv','groups.csv'],
                   'diagnostics':report,
                   'pyw90_diagnostic': pyw90,'prepare_seconds':prepare_seconds,
                   'remote_requests':sum(e.get('ssh_requests',0) for e in timings),'timings':timings,
                   'local_path': str(directory), 'created_at': datetime.now(timezone.utc).isoformat(),
                   'note': '同源采样路径的叠加对比；不代表插值质量或物理模型已验证。'}
        receipt['diagnostic_input_sha256']=hashlib.sha256((directory/'score-input.json').read_bytes()).hexdigest()
        diagnostics.atomic(directory/'diagnostics.json',{**report,'pyw90_diagnostic':pyw90,'plot_relative_windows':receipt['plot_relative_windows']})
        _render(directory,receipt,dft[index],wannier)
        diagnostics.atomic(directory/'record.json',receipt)
        if on_plot_ready:on_plot_ready(receipt)
        if diagnostic:receipt=_augment(receipt,bundle.get('spinors'))
        return receipt
    except Exception:
        if not (directory/'record.json').is_file():shutil.rmtree(directory, ignore_errors=True)
        raise


def saved(project_id):
    result = []
    for path in ROOT_COMPARE.glob('*/record.json'):
        try:
            record = json.loads(path.read_text())
            if record.get('project_id') == project_id and not is_recycled('comparisons',record['id']):
                result.append(record)
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item['created_at'], reverse=True)


def output(project_id, identifier, filename):
    if filename not in ('comparison.png', 'comparison.svg', 'curves.csv','diagnostics.json','per_band.csv','groups.csv') or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('对比图文件名无效')
    directory = ROOT_COMPARE / identifier
    record = json.loads((directory / 'record.json').read_text())
    if record.get('project_id') != project_id:
        raise ValueError('对比结果不属于当前项目')
    return (directory / filename).read_bytes()


def _wannier_data(record,seed,force=False):
    from . import remote_cache
    recycle.assert_available('attempts',record['id'],record)
    key=hashlib.sha256(json.dumps([record['id'],record['config']['alias'],execution.attempt_path(record)['shell'],seed]).encode()).hexdigest()
    directory=ROOT_COMPARE/'data'/key
    with remote_cache.singleflight('comparison-input',{'key':key}):
        receipt_file=directory/'receipt.json'
        if receipt_file.exists() and not force:
            return {**json.loads(receipt_file.read_text()),'cached':True,'directory':str(directory)}
        with tempfile.TemporaryDirectory(prefix='fpilot-compare-') as temp:
            names=(seed+'_band.dat',seed+'_band.labelinfo.dat')
            rows=[{'remote_path':execution.attempt_path(record)['shell']+'/'+name,'destination':str(Path(temp)/name)} for name in names]
            receipt=safe_files.pull_many(record['config']['alias'],rows,lane='analysis')
            directory.mkdir(parents=True,exist_ok=True)
            sources=[]
            for name,item in zip(names,receipt['files']):
                shutil.copyfile(Path(temp)/name,directory/name)
                sources.append({'attempt_id':record['id'],'path':name,'sha256':item['sha256'],'size_bytes':item['bytes']})
            value={'directory':str(directory),'sources':sources,'cached':False,'timing':receipt.get('timing')}
            receipt_file.write_text(json.dumps(value));return value


JOBS={}
JOB_LOCK=threading.Lock()
def start(**options):
    key=hashlib.sha256(json.dumps(options,sort_keys=True).encode()).hexdigest()
    with JOB_LOCK:
        old=JOBS.get(key)
        if old and old['state']=='RUNNING':return dict(old)
        if old and old['state']=='READY' and (ROOT_COMPARE/old['result']['id']/'record.json').is_file():
            recycle.assert_available('comparisons',old['result']['id'],old['result'])
            for name in ('wannier_attempt_id','band_attempt_id'):
                record=execution.get(options[name]);recycle.assert_available('attempts',record['id'],record)
            return {**old,'cached':True}
        job={'id':key,'state':'RUNNING','step':'准备来源并生成叠图','started_at':time.time()};JOBS[key]=job
    def work():
        try:
            def ready(record):
                with JOB_LOCK:job.update(plot_ready=True,comparison_id=record['id'],result=record,step='叠图已生成，正在补充原生评分')
            result=run(**options,diagnostic=True,on_plot_ready=ready)
            with JOB_LOCK:job.update(state='READY',result=result,seconds=time.time()-job['started_at'])
        except Exception as exc:
            with JOB_LOCK:job.update(state='FAILED',error=str(exc)[:1000])
    from .analysis_queue import executor
    executor.submit(work);return dict(job)


def status(key):
    with JOB_LOCK:
        if key not in JOBS:raise ValueError('本机绘图任务不存在，请重新生成')
        return dict(JOBS[key])


def start_diagnostic(project_id, identifier):
    """Supplement an existing overlay without regenerating the plot."""
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):raise ValueError('比较记录标识无效')
    path = ROOT_COMPARE / identifier / 'record.json'
    record = json.loads(path.read_text())
    if record['project_id'] != project_id:raise ValueError('比较记录不属于当前项目')
    recycle.assert_available('comparisons', identifier, record)
    key = 'diagnostic-' + identifier
    with JOB_LOCK:
        if key in JOBS and JOBS[key]['state']=='RUNNING':return dict(JOBS[key])
        job={'id':key,'state':'RUNNING','step':'PyW90 补充诊断','started_at':time.time()};JOBS[key]=job
    def work():
        try:
            w90=execution.get(record['wannier_attempt_id']);bundle,_=_w90(w90)
            recycle.assert_available('comparisons',identifier,record)
            result=_augment(record,bundle.get('spinors'))
            with JOB_LOCK:job.update(state='READY',result=result,seconds=time.time()-job['started_at'])
        except Exception as exc:
            with JOB_LOCK:job.update(state='FAILED',error=str(exc)[:1000])
    from .analysis_queue import executor
    executor.submit(work);return dict(job)


def _render(directory,record,dft=None,wannier=None):
    if dft is None:
        channels,_=vasp_band_channels_from_eigenval(directory/'dft/EIGENVAL',directory/'dft/KPOINTS',directory/'dft/POSCAR','auto',record['fermi_eV'])
        dft=channels[1 if record['seed'].endswith('.2') else 0]
        wannier=wannier_channel(directory/'wannier'/(record['seed']+'_band.dat'),directory/'wannier'/(record['seed']+'_band.labelinfo.dat'),record['fermi_eV'],record['seed'])
    from matplotlib import font_manager
    available={font.name for font in font_manager.fontManager.ttflist}
    family=next((name for name in ('PingFang SC','Noto Sans CJK SC','Microsoft YaHei','Arial Unicode MS','SimHei','WenQuanYi Zen Hei') if name in available),None)
    font=font_manager.FontProperties(family=family) if family else None
    plot_compare([dft],[wannier],directory/'comparison',*record['energy_window_eV'],['.png','.svg'],
                 record.get('title') or 'DFT–Wannier band comparison',title_font=font,summary_lines=diagnostics.summary_lines(record))


def _augment(record,spinors):
    from filelock import FileLock
    directory=ROOT_COMPARE/record['id']
    with FileLock(str(directory/'diagnostic.lock'),timeout=65):
        current=json.loads((directory/'record.json').read_text()) if (directory/'record.json').is_file() else record
        if (current.get('pyw90_diagnostic') or {}).get('status') in ('COMPLETED','NOT_APPLICABLE'):
            return current
        return _augment_locked(current,spinors)


def _augment_locked(record,spinors):
    directory=ROOT_COMPARE/record['id']
    if not record.get('diagnostics') or not (directory/'score-input.json').is_file():
        result={'status':'UNAVAILABLE','reason':'旧记录没有固定评价基准；请生成一份新的叠图，原图保留'}
    else:
        try:
            digest=hashlib.sha256((directory/'score-input.json').read_bytes()).hexdigest()
            if digest!=record.get('diagnostic_input_sha256'):raise ValueError('本机诊断输入已变化；原图保留，请重新生成新的叠图')
            result=diagnostics.native(directory,record,execution.get(record['wannier_attempt_id'])['config']['alias'],str(spinors).strip().lower() in ('.true.','true','t'))
        except Exception as exc:result={'status':'FAILED','reason':str(exc)[-600:]}
    previous=record.get('pyw90_diagnostic') or {}
    if previous.get('status') in ('FAILED','UNAVAILABLE','SOURCE_CHANGED'):
        record.setdefault('native_history',[]).append(previous)
    record['pyw90_diagnostic']=result
    diagnostics.atomic(directory/'record.json',record)
    if record.get('diagnostics'):
        diagnostics.atomic(directory/'diagnostics.json',{**record['diagnostics'],'pyw90_diagnostic':result,
                           'native_history':record.get('native_history',[]),'plot_relative_windows':record.get('plot_relative_windows',{})})
        try:_render(directory,record)
        except Exception as exc:record['annotation_error']=str(exc)[-600:];diagnostics.atomic(directory/'record.json',record)
    return record
