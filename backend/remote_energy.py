"""Parse OUTCAR once on its source host; subsequent checks only stat the file."""
import json
import shlex
from . import execution, remote_cache, remote_paths, remote_transport, recycle

PARSER = r'''
import hashlib,json,math,os,re,stat,subprocess,sys
path=sys.argv[1];cached=json.loads(sys.argv[2]);before=os.stat(path,follow_symlinks=False)
if not stat.S_ISREG(before.st_mode) or before.st_size<=0:raise ValueError('OUTCAR is not a nonempty regular file')
stamp=subprocess.check_output(['stat','-c','%y',path],text=True).strip()
identity={'size_bytes':before.st_size,'mtime':stamp,'verification_mode':'metadata'}
if cached and cached.get('source_receipt')==identity:
    result={**cached,'source_receipt':identity}
else:
    patterns={'precise':r'Fermi energy:\s*([-+\d.Ee]+)','short':r'E-fermi\s*:\s*([-+\d.Ee]+)',
              'vbm_eV':r'val\. band max:\s*([-+\d.Ee]+)','cbm_eV':r'cond\. band min:\s*([-+\d.Ee]+)'}
    digest=hashlib.sha256();found={}
    with open(path,'rb') as stream:
        for raw in stream:
            digest.update(raw);line=raw.decode(errors='replace')
            for name,pattern in patterns.items():
                values=re.findall(pattern,line)
                if values:found[name]=float(values[-1])
    if 'precise' not in found and 'short' not in found:raise ValueError('OUTCAR has no Fermi energy')
    after=os.stat(path,follow_symlinks=False)
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('OUTCAR changed while parsing')
    fermi=found['precise'] if 'precise' in found else found['short']
    if not math.isfinite(fermi):raise ValueError('OUTCAR Fermi energy is not finite')
    vbm,cbm=found.get('vbm_eV'),found.get('cbm_eV')
    if vbm is None or cbm is None or cbm<=vbm:vbm=cbm=None
    result={'fermi_eV':fermi,'fermi_source':'Fermi energy' if 'precise' in found else 'E-fermi',
            'vbm_eV':vbm,'cbm_eV':cbm,'outcar_sha256':digest.hexdigest(),'source_receipt':identity}
print('FPILOT_ENERGY='+json.dumps(result,separators=(',',':')))
'''


def identity(record):
    return {'attempt_id':record['id'],'host':record['config']['alias'],
            'remote_path':execution.attempt_path(record)['shell'].rstrip('/')+'/OUTCAR','parser':1}


def command(record, cached=None):
    info=identity(record)
    cached={k:v for k,v in (cached or {}).items() if k not in ('cached','checked_epoch')}
    return (shlex.quote(remote_transport._interpreter(info['host']))+' -c '+shlex.quote(PARSER)+' '
            +remote_paths.shell_path(info['remote_path'])+' '+shlex.quote(json.dumps(cached))+'\n')


def store(record, stdout):
    lines=[line.split('=',1)[1] for line in stdout.splitlines() if line.startswith('FPILOT_ENERGY=')]
    if len(lines)!=1:raise ValueError('OUTCAR 能量参考回执不完整')
    info=identity(record);value=json.loads(lines[0])
    return remote_cache.write('energy',info,{**value,**{k:v for k,v in info.items() if k!='parser'}})


def reference(record, force=False):
    recycle.assert_available('attempts',record['id'],record)
    info=identity(record)
    with remote_cache.singleflight('energy',info):
        cached=remote_cache.read('energy',info)
        if cached and not force:return cached
        receipt=execution.ssh(info['host'],command(record,cached),timeout=180,stdout_limit=8192)
        if receipt['exit_code']!=0:raise ValueError('服务器能量参考读取失败：'+receipt.get('stderr','')[-400:])
        return store(record,receipt['stdout'])
