"""Remote extraction with the same scientific parsers used by local plots.

Only derived, individually bounded files cross the controlled transfer boundary.
"""
import inspect
import gzip
import json
import shlex
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
import numpy as np
from . import execution, remote_paths, remote_transport, safe_files, recycle
from .storage import ROOT

VERSION = 2
CACHE = ROOT / 'compact-plot-data'


def identity(record, kind):
    return execution.hashlib.sha256(json.dumps([VERSION, record['id'], record['config']['alias'],
        execution.attempt_path(record)['shell'], kind]).encode()).hexdigest()


def local(record, kind):
    recycle.assert_available('attempts', record['id'], record)
    directory = CACHE / identity(record,kind)
    path = directory / 'receipt.json'
    if not path.exists() or not (directory / 'data.npz').exists(): return None
    value = json.loads(path.read_text())
    return {**value, 'cached': True, 'directory': str(directory)}


def script(record, kind, output):
    from . import interactive_plots as plots
    from .extension_remote import runtimes
    cores=max(1,int(runtimes().get(record['config']['alias'],{}).get('cores') or 1))
    functions = ['_sha','_pband_names','_species','_canonical','_doscar','_eigenval','_pband',
                 '_outcar_info','_unsupported_vaspkit_cli','_kpath_labels']
    source = '\n'.join(inspect.getsource(getattr(plots,name)) for name in functions)
    source = ('from __future__ import annotations\nimport os,json,re,math,hashlib,stat,shutil,subprocess,time\n'
              'from pathlib import Path\n'
              'for name in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"):os.environ[name]='+repr(str(cores))+'\n'
              'import numpy as np\n'
              +'NUMBER='+repr(plots.NUMBER)+'\nPBAND_COLUMNS='+repr(plots.PBAND_COLUMNS)+'\n'+source)
    request = {'root': execution.attempt_path(record)['shell'], 'output': output, 'kind':kind,
               'attempt_id':record['id'],'host':record['config']['alias']}
    source += '\nREQUEST='+repr(request)+'\n'+EXTRACT
    return shlex.quote(remote_transport._interpreter(record['config']['alias']))+' -c '+shlex.quote(source)


EXTRACT = r'''
started=time.monotonic()
root=Path(os.path.expandvars(REQUEST['root']));out=Path(os.path.expandvars(REQUEST['output']))
out.parent.mkdir(parents=True,exist_ok=True)
work=out.parent/'arrays';work.mkdir(exist_ok=True)
sources=[];before={}
def take(path):
    s=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(s.st_mode) or s.st_size<=0:raise ValueError('missing ordinary nonempty file: '+str(path))
    before[str(path)]=(s.st_size,s.st_mtime_ns)
    row={'attempt_id':REQUEST['attempt_id'],'host':REQUEST['host'],'remote_dir':str(path.parent),
         'name':path.name,'size_bytes':s.st_size,'modified_at':str(s.st_mtime_ns),
         'verification_mode':'metadata'}
    if path.name in ('KPOINTS','POSCAR','INCAR','EIGENVAL'):row['sha256']=_sha(path);row['verification_mode']='content'
    sources.append(row)
    return path
poscar=take(root/'POSCAR');outcar=take(root/'OUTCAR')
species=_species(poscar);elements=list(dict.fromkeys(species))
outcar_info=_outcar_info(outcar);reference=outcar_info['reference']
if reference is None:raise ValueError('OUTCAR has no Fermi energy')
sources[-1].update(sha256=outcar_info['sha256'],verification_mode='content')
generation=None
if REQUEST['kind']=='band':
    eigenval=take(root/'EIGENVAL');kpoints=take(root/'KPOINTS')
    found=_pband_names(elements,{p.name for p in root.glob('PBAND_*.dat') if p.is_file() and p.stat().st_size>0})
    origin=root
    if not found:
        tool=shutil.which('vaspkit')
        if not tool:raise ValueError('VASPKIT is missing; PROCAR stays on server')
        origin=out.parent/'vaspkit';origin.mkdir(exist_ok=True)
        for name in ('INCAR','POSCAR','KPOINTS','EIGENVAL','DOSCAR','PROCAR'):
            path=take(root/name)
            if path.name in ('PROCAR','DOSCAR','EIGENVAL'):
                target=origin/name
                if target.exists() or target.is_symlink():target.unlink()
                target.symlink_to(path)
            else:shutil.copyfile(path,origin/name)
        with (origin/'vaspkit.log').open('w') as log:
            result=subprocess.run([tool,'-task','213'],cwd=str(origin),stdout=log,stderr=subprocess.STDOUT,timeout=1200)
            log.flush()
            if result.returncode and _unsupported_vaspkit_cli(origin/'vaspkit.log'):
                result=subprocess.run([tool],input='213\n',text=True,cwd=str(origin),stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        if result.returncode:raise ValueError('VASPKIT 213 failed; see independent analysis log')
        found=_pband_names(elements,{p.name for p in origin.glob('PBAND_*.dat') if p.is_file() and p.stat().st_size>0})
        if not found:raise ValueError('VASPKIT did not produce complete element and spin projections')
        generation={'tool':tool,'task':213,'directory':str(origin)}
    mode,names=found
    files=[take(origin/name) for name in names]
    if (mode=='soc')!=outcar_info['noncollinear']:raise ValueError('PBAND SOC mode does not match OUTCAR')
    meta=_pband(files,elements,mode,eigenval,work)
    meta['kpath_labels']=_kpath_labels(kpoints,meta['nk'])
    meta['kpath_breaks']=[r['index'] for r in meta['kpath_labels'][2::2]]
else:
    meta=_doscar(take(root/'DOSCAR'),species,work)
meta['energy_reference']=reference
for name,(size,stamp) in before.items():
    s=os.stat(name,follow_symlinks=False)
    if (s.st_size,s.st_mtime_ns)!=(size,stamp):raise ValueError('source changed during extraction: '+name)
arrays={p.stem:np.load(p,mmap_mode='r') for p in work.glob('*.npy')}
receipt={'schema':'fpilot.compact-projection.v1','metadata':meta,'sources':sources,'generation':generation,
         'parse_seconds':time.monotonic()-started,'kind':REQUEST['kind'],
         'poscar_text':poscar.read_text()}
if REQUEST['kind']=='band':
    receipt['kpoints_text']=kpoints.read_text()
    receipt['eigenval_text']=eigenval.read_text()
# Preserve all orbitals and k points; no downsampling and no partitioned transfer.
np.savez_compressed(str(out),metadata=np.array(json.dumps(receipt)),**arrays)
if out.stat().st_size>10485760:
    out.replace(out.with_name('data-full.npz'))
    receipt.update(remote_only=True,remote_directory=str(work))
    receipt.pop('eigenval_text',None)
    np.savez_compressed(str(out),metadata=np.array(json.dumps(receipt)))
    if out.stat().st_size>10485760:raise ValueError('绘图摘要仍超过 10 MiB，完整数据保留在服务器')
print('FPILOT_COMPACT='+json.dumps({'bytes':out.stat().st_size,'parse_seconds':receipt['parse_seconds']}))
'''


def fetch(record, kind, force=False):
    from . import remote_cache, remote_energy
    key=identity(record,kind)
    with remote_cache.singleflight('plot-data',{'key':key}):
        cached=local(record,kind)
        if cached and not force:return cached
        from . import servers
        output=servers.scratch_root(record['config']['alias'],'compact-plots')+'/'+key+'/data.npz'
        companion=script(record,kind,output)
        scf=None
        if kind=='band' and (record.get('dependency_snapshot') or {}).get('upstream_attempt_id'):
            scf=execution.get(record['dependency_snapshot']['upstream_attempt_id'])
            recycle.assert_available('attempts',scf['id'],scf)
            if scf['config']['alias']!=record['config']['alias']:raise ValueError('绘图 SCF 来源服务器不一致')
            companion += '\n'+remote_energy.command(scf,remote_cache.read('energy',remote_energy.identity(scf)))
        started=time.monotonic()
        from .analysis_queue import slot
        with slot(record['config']['alias']),tempfile.TemporaryDirectory(prefix='fpilot-compact-') as temporary:
            recycle.assert_available('attempts',record['id'],record)
            target=Path(temporary)/'data.npz'
            receipt=safe_files.pull_many(record['config']['alias'],[{'remote_path':output,'destination':str(target)}],
                companion_command=companion,timeout=1500,lane='analysis')
            with np.load(target,allow_pickle=False) as data:
                value=json.loads(str(data['metadata']))
            if scf:
                reference=remote_energy.store(scf,receipt['companion_stdout'])
                value['metadata']['energy_reference']={**reference,'scf_attempt_id':scf['id']}
                value['sources'].append({'attempt_id':scf['id'],'host':scf['config']['alias'],
                    'name':'OUTCAR','sha256':reference['outcar_sha256'],**reference['source_receipt']})
            directory=CACHE/key;directory.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(target,directory/'data.npz')
            value.update(transferred_bytes=target.stat().st_size,prepare_seconds=time.monotonic()-started,
                         timing=receipt.get('timing'),cached=False,directory=str(directory))
            (directory/'receipt.json').write_text(json.dumps(value,ensure_ascii=False))
            return value


def install(value, destination):
    with np.load(Path(value['directory'])/'data.npz',allow_pickle=False) as data:
        for name in data.files:
            if name!='metadata':np.save(destination/(name+'.npy'),data[name])


def selected_series(meta, selected, emin, emax, directory):
    """Keep oversized arrays remote; return one bounded selected projection, with no sampling loss."""
    from . import interactive_plots as plots, remote_cache
    plots._selection(meta,selected)
    cache_identity={'sources':meta['sources'],'selected':selected,'emin':emin,'emax':emax,'version':VERSION}
    cached=remote_cache.read('selected-projection',cache_identity)
    if cached:return cached['series']
    def part(kind):
        info={**meta,'band':meta['band'] if kind=='band' else None,'dos':meta['dos'] if kind=='dos' else None}
        remote=meta.get('remote_parts',{}).get(kind)
        if not remote:return plots._series_data(info,directory,selected,emin,emax)
        record=execution.get(remote['attempt_id']);recycle.assert_available('attempts',record['id'],record)
        root=remote['remote_directory']
        functions=(' _selection _orbital_indexes _reciprocal_lattice _series_data').split()
        source='from __future__ import annotations\nimport json,math,gzip,os\nfrom pathlib import Path\nimport numpy as np\n'
        source+='ORBITAL_GROUPS='+repr(plots.ORBITAL_GROUPS)+'\n'
        source+='\n'.join(inspect.getsource(getattr(plots,name)) for name in functions)
        # The same series function uses the attempt POSCAR for reciprocal coordinates.
        source+='\nmeta='+repr(info)+'\nroot=Path('+repr(root)+')\n'
        source+='folder=root/'+repr(record['id'])+';folder.mkdir(exist_ok=True)\n'
        source+='(folder/"POSCAR").write_text('+repr((directory/record['id']/'POSCAR').read_text())+')\n'
        source+='data=_series_data(meta,root,'+repr(selected)+','+repr(emin)+','+repr(emax)+')\n'
        digest=remote_cache.key('selected-projection',cache_identity)
        output=str(PurePosixPath(root).parent/('selection-'+digest+'.json.gz'))
        source+='output=Path('+repr(output)+')\n'
        source+='with gzip.open(str(output),"wt") as stream:json.dump(data,stream)\n'
        source+='if output.stat().st_size>10485760:raise ValueError("所选投影仍超过 10 MiB，请减少同时显示的轨道；不缩减 k 点或能带")\n'
        command=shlex.quote(remote_transport._interpreter(record['config']['alias']))+' -c '+shlex.quote(source)
        from .analysis_queue import slot
        with slot(record['config']['alias']),tempfile.TemporaryDirectory(prefix='fpilot-selected-') as temp:
            recycle.assert_available('attempts',record['id'],record)
            target=Path(temp)/'selected.json.gz'
            safe_files.pull_many(record['config']['alias'],[{'remote_path':output,'destination':str(target)}],companion_command=command,lane='analysis',timeout=180)
            with gzip.open(target,'rt') as stream:return json.load(stream)
    with remote_cache.singleflight('selected-projection',cache_identity),remote_transport.operation('selected-projection'):
        cached=remote_cache.read('selected-projection',cache_identity)
        if cached:return cached['series']
        kinds=[kind for kind in ('band','dos') if meta.get(kind)]
        values=remote_transport.parallel([lambda kind=kind:part(kind) for kind in kinds])
        result={'id':meta['id'],'band':None,'dos':None,'selection':selected,'emin':emin,'emax':emax,
                'compatible':meta.get('compatible',True),'compatibility_note':meta.get('compatibility_note','')}
        for kind,value in zip(kinds,values):result[kind]=value[kind]
        remote_cache.write('selected-projection',cache_identity,{'series':result})
        return result
