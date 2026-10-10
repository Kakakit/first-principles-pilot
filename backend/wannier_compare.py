"""Independent local DFT/Wannier band comparison from explicitly selected attempts."""
from __future__ import annotations

from . import wannier_lineage

import csv
import hashlib
import json
import math
import re
import shlex
import shutil
import sys
import uuid
import threading
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import artifacts, execution, pyw90_remote, remote_paths, wannier_bands, wannier_execution
from . import compact_plots, safe_files, remote_transport, recycle
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


def _pyw90_diagnostic(band, w90, output_record, seed, fermi, sources, spinors):
    """Keep pre band/cmp artifacts remote; only return bounded diagnostics."""
    alias = w90['config']['alias']
    if alias not in pyw90_remote.PYTHON:
        return {'status': 'UNAVAILABLE', 'reason': '该服务器未登记 PyW90 运行时'}
    if str(spinors).strip().lower() in ('.true.', 'true', 't'):
        return {'status': 'NOT_APPLICABLE', 'reason': 'SOC spinor 不套用 PyW90 共线 cmp；本机叠图独立生成'}
    band_path = execution.attempt_path(band)['shell']
    output_path = execution.attempt_path(output_record)['shell']
    model_path = execution.attempt_path(w90)['shell']
    expected = {(item['attempt_id'], item.get('path', item.get('name'))): item['sha256']
                for item in sources if item.get('sha256')}
    checks = []
    guards = []
    for record, path, names in ((band, band_path, ('EIGENVAL', 'KPOINTS', 'POSCAR')),
                                (output_record, output_path, (seed + '_band.dat', seed + '_band.labelinfo.dat'))):
        for name in names:
            digest = expected.get((record['id'], name))
            if not digest:
                return {'status': 'SOURCE_CHANGED', 'reason': name + ' 缺少叠图来源身份'}
            file_path = remote_paths.shell_path(path + '/' + name)
            guards.append('test "$(sha256sum -- '+file_path+' | cut -d\' \' -f1)" = '+shlex.quote(digest))
            checks.append({'attempt_id': record['id'], 'name': name, 'sha256': digest})
    py = shlex.quote(pyw90_remote.PYTHON[alias])
    package = pyw90_remote.PACKAGE
    pre = 'pre band --path .' + (' --spin-down' if seed.endswith('.2') else '')
    command = 'set -e\n' + '\n'.join(guards) + '\n' + f'''
band_dir={remote_paths.shell_path(band_path)}
plot_dir={remote_paths.shell_path(output_path)}
model_dir={remote_paths.shell_path(model_path)}
scratch_root="$HOME/230269265lht/.fpilot/pyw90-band-compare"
mkdir -p "$scratch_root"
scratch=$(mktemp -d "$scratch_root/run.XXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
for name in EIGENVAL KPOINTS POSCAR; do ln -s "$band_dir/$name" "$scratch/$name"; done
for name in {shlex.quote(seed + '_band.dat')} {shlex.quote(seed + '_band.labelinfo.dat')}; do ln -s "$plot_dir/$name" "$scratch/$name"; done
ln -s "$model_dir/{seed}.wout" "$scratch/{seed}.wout"
cd "$scratch"
PYTHONPATH="{package}" MPLBACKEND=Agg timeout 180 {py} -c 'from pyw90.pyw90_cli import main_cli; main_cli()' {pre} > pre.log 2>&1
test -s bnd.dat
printf 'FPILOT_PRE_BAND_BYTES=%s\n' "$(wc -c < bnd.dat)"
PYTHONPATH="{package}" MPLBACKEND=Agg timeout 180 {py} -c 'from pyw90.pyw90_cli import main_cli; main_cli()' cmp FPilot --path . --seedname {shlex.quote(seed)} --vasp bnd.dat --efermi {fermi:.12g} > cmp.log 2>&1
grep -E 'Final Quality|Quality|Error' cmp.log | tail -8
'''
    receipt = remote_transport.run(alias, command, timeout=420, stdout_limit=5000, lane='analysis')
    if receipt['exit_code'] != 0 or receipt.get('stdout_truncated'):
        return {'status': 'FAILED', 'reason': (receipt.get('stderr') or receipt.get('stdout') or 'PyW90 pre band/cmp 失败')[-500:],
                'source_hashes': checks}
    size = re.search(r'^FPILOT_PRE_BAND_BYTES=(\d+)$', receipt['stdout'], re.M)
    quality = re.search(r'Final Quality\s+([0-9.Ee+-]+)\s*(meV|eV)?', receipt['stdout'])
    return {'status': 'COMPLETED', 'pre_band_bytes': int(size.group(1)) if size else None,
            'quality_value': float(quality.group(1)) if quality else None,
            'quality_unit': quality.group(2) if quality else None,
            'summary': receipt['stdout'][-1500:], 'source_hashes': checks,
            'note': 'PyW90 数值仅作诊断，不替代本机曲线叠图或科学验证。'}


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


def run(project_id, wannier_attempt_id, band_attempt_id, emin=-4, emax=4, diagnostic=False):
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (emin, emax)) or not -100 <= emin < emax <= 100:
        raise ValueError('能量范围无效')
    w90 = execution.get(wannier_attempt_id)
    if w90.get('project_id') != project_id:
        raise ValueError('Wannier90 attempt 不属于当前项目')
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
        plot_compare([dft[index]], [wannier], directory / 'comparison', emin, emax,
                     ['.png', '.svg'], 'DFT–Wannier band comparison')
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
        try:
            pyw90 = _pyw90_diagnostic(band, w90, output_record, seed, fermi, sources, bundle.get('spinors')) if diagnostic else {'status':'NOT_REQUESTED'}
        except (ValueError, OSError) as exc:
            pyw90 = {'status': 'FAILED', 'reason': str(exc)[:500]}
        receipt = {'schema': 'fpilot.wannier-band-comparison.v1', 'id': identifier,
                   'project_id': project_id, 'wannier_attempt_id': wannier_attempt_id,
                   'wannier_plot_attempt_id': output_record['id'] if output_record['id'] != wannier_attempt_id else None,
                   'band_attempt_id': band_attempt_id, 'scf_attempt_id': scf_id,
                   'seed': seed, 'fermi_eV': fermi, 'energy_window_eV': [emin, emax],
                   'sources': sources, 'files': ['comparison.png', 'comparison.svg', 'curves.csv'],
                   'pyw90_diagnostic': pyw90,'prepare_seconds':prepare_seconds,
                   'remote_requests':sum(e.get('ssh_requests',0) for e in timings),'timings':timings,
                   'local_path': str(directory), 'created_at': datetime.now(timezone.utc).isoformat(),
                   'note': '同源采样路径的叠加对比；不代表插值质量或物理模型已验证。'}
        (directory / 'record.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        return receipt
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
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
    if filename not in ('comparison.png', 'comparison.svg', 'curves.csv') or not re.fullmatch(r'[a-f0-9]{32}', identifier):
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
        job={'id':key,'state':'RUNNING','step':'准备来源并生成叠图','started_at':time.time()};JOBS[key]=job
    def work():
        try:
            result=run(**options)
            with JOB_LOCK:job.update(state='READY',result=result,seconds=time.time()-job['started_at'])
        except Exception as exc:
            with JOB_LOCK:job.update(state='FAILED',error=str(exc)[:1000])
    threading.Thread(target=work,daemon=True).start();return dict(job)


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
            band=execution.get(record['band_attempt_id'])
            output_record=execution.get(record.get('wannier_plot_attempt_id') or w90['id'])
            for item in (w90, band, output_record):recycle.assert_available('attempts',item['id'],item)
            with remote_transport.operation('pyw90-diagnostic') as scope:
                result=_pyw90_diagnostic(band,w90,output_record,record['seed'],record['fermi_eV'],record['sources'],bundle.get('spinors'))
            recycle.assert_available('comparisons',identifier,record)
            record.update(pyw90_diagnostic=result,diagnostic_timings=scope['events'])
            temporary=path.with_suffix('.part');temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2));temporary.replace(path)
            with JOB_LOCK:job.update(state='READY',result=result,seconds=time.time()-job['started_at'])
        except Exception as exc:
            with JOB_LOCK:job.update(state='FAILED',error=str(exc)[:1000])
    threading.Thread(target=work,daemon=True).start();return dict(job)
