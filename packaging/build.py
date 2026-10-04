"""Build a clean portable runtime ZIP; never traverse a user's data directory."""
import argparse,hashlib,json,re,shutil,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES={'launch.py':'launch.py','scripts/watch_execution.py':'scripts/watch_execution.py',
       'scripts/collect_diagnostics.py':'scripts/collect_diagnostics.py',
       'packaging/requirements-runtime.txt':'requirements-runtime.txt'}
PLATFORMS={
    'windows':{'target':'Windows 10/11 x64; CPython 3.11','archive':'FPilot-Windows-Test-20261004-r2.zip',
        'files':{**{'packaging/windows/'+name:name for name in ('start.cmd','check.cmd','install-dependencies.cmd','diagnostics.cmd')},
            'packaging/windows/README.md':'WINDOWS_README.md','packaging/windows/resolve-python.cmd':'scripts/resolve-python.cmd'}},
    'macos':{'target':'macOS; existing Python 3.9-3.12, recommended 3.11; Apple Silicon / Intel',
        'archive':'FPilot-macOS-Test-20261004.zip',
        'files':{**{'packaging/macos/'+name:name for name in ('start.command','check.command','install-dependencies.command','diagnostics.command')},
            'packaging/macos/README.md':'MAC_README.md','packaging/macos/resolve-python.sh':'scripts/resolve-python.sh'}}}


def resource(path):
    parts=path.relative_to(ROOT).parts
    if '__pycache__' in parts or '.DS_Store' in parts or path.suffix=='.pyc':return False
    if parts[:2]==('resources','templates'):return False
    if parts[:3]==('resources','jobs','wannier90'):return False
    # Historical source copies have been kept in development backups. The
    # independent extension CLI implementations and their fixtures are shipped.
    if 'source' in parts and path.name.startswith('original_'):return False
    # Legacy SDK installation still validates tests/fixtures. They are runtime
    # examples, even though their directory name contains "tests".
    if parts[1]=='extensions' and 'tests' in parts and 'fixtures' not in parts and parts[2]!='_template':return False
    return True


def portable_metadata(value):
    if isinstance(value,dict):return {k:portable_metadata(v) for k,v in value.items()}
    if isinstance(value,list):return [portable_metadata(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'/Users/kafka/Documents/FeCrO论文/06_工具/\.venv-figure/bin/python','python',value)
        if '/Users/kafka/' in value:
            value=re.sub(r'/Users/kafka/[^\s"\n]+',lambda match:'archived-development-source/'+Path(match[0]).name,value)
    return value


def licenses(dest):
    lock=json.loads((ROOT/'package-lock.json').read_text(encoding='utf-8'))
    rows=[]
    for name,value in sorted(lock['packages'].items()):
        if not name or value.get('dev'):continue
        package=ROOT/name
        for path in sorted(package.glob('*')):
            if path.is_file() and re.match(r'^(LICENSE|COPYING|NOTICE)',path.name,re.I):
                target=dest/'THIRD_PARTY_LICENSES'/name.removeprefix('node_modules/')/path.name
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        rows.append('| '+name.removeprefix('node_modules/')+' | '+value.get('version','')+' | '+str(value.get('license','see package license'))+' |')
    text='# Third party components\n\nThe prebuilt frontend uses the following locked npm packages. Available license texts are in THIRD_PARTY_LICENSES.\n\n| Package | Version | License |\n|---|---|---|\n'+'\n'.join(rows)
    text+='\n\nPython dependencies are installed separately from PyPI with their original licenses; no Python runtime or dependency binaries are redistributed in this ZIP. filelock 3.19.1 uses the Unlicense.\n\nThis private testing bundle does not grant licenses to VASP, POTCAR datasets or external analysis tools.\n'
    (dest/'THIRD_PARTY_NOTICES.md').write_text(text,encoding='utf-8')


def build(output,platform):
    if not (ROOT/'dist/index.html').is_file():raise ValueError('Build the frontend first')
    output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    dest=output/'FPilot'
    if dest.exists():raise ValueError('Output already contains FPilot; use a fresh output directory')
    dest.mkdir()
    spec=PLATFORMS[platform]
    selected=[(ROOT/path,Path(name)) for path,name in {**FILES,**spec['files']}.items()]
    runtime=sorted((ROOT/'backend').glob('*.py'))
    runtime += [p for p in (ROOT/'dist').rglob('*') if p.is_file()]
    runtime += [p for p in (ROOT/'resources').rglob('*') if p.is_file() and resource(p)]
    runtime += [p for p in (ROOT/'extension-templates').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store']
    runtime += [ROOT/'docs/extensions-b/PROTOCOL.md']
    selected += [(p,p.relative_to(ROOT)) for p in runtime]
    for source,name in selected:
        if source.is_symlink():raise ValueError('No symlinks in release: '+str(source))
        target=dest/name;target.parent.mkdir(parents=True,exist_ok=True)
        raw=source.read_bytes()
        if name.as_posix()=='resources/preflight/hpc_cluster_registry.json':
            raw=b'{"schema":"fpilot.cluster-registry.v1","hosts":{}}\n'
        elif source.suffix=='.json' and b'/Users/kafka/' in raw:
            raw=json.dumps(portable_metadata(json.loads(raw)),ensure_ascii=False,indent=2).encode('utf-8')
        elif source.suffix=='.cmd':raw=raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        target.write_bytes(raw)
        if target.suffix in ('.command','.sh'):target.chmod(0o755)
    (dest/'resources/jobs/wannier90').mkdir(parents=True,exist_ok=True)
    (dest/'resources/jobs/wannier90/README.md').write_text('Import your own Wannier90 script in Settings. No personal server scripts are bundled.\n',encoding='utf-8')
    (dest/'resources/templates').mkdir(parents=True,exist_ok=True)
    (dest/'resources/templates/README.md').write_text('Import your own PBS/Slurm scripts in Settings and save defaults per server/program.\n',encoding='utf-8')
    (dest/'examples').mkdir()
    shutil.copyfile(ROOT/'resources/extensions/structure-symmetry/fixtures/Si.POSCAR',dest/'examples/Si.POSCAR')
    licenses(dest)
    names={};records=[]
    for path in sorted(dest.rglob('*')):
        if not path.is_file():continue
        name=path.relative_to(dest).as_posix();key=name.casefold()
        if key in names:raise ValueError('Windows case collision: '+name)
        names[key]=True
        for part in Path(name).parts:
            if re.search(r'[<>:"|?*\\]',part) or part.endswith((' ','.')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part,re.I):raise ValueError('Windows-invalid filename: '+name)
        raw=path.read_bytes()
        if b'/Users/kafka/' in raw and path.suffix in ('.py','.json','.js','.html','.css'):
            raise ValueError('Personal path remains in runtime: '+name)
        records.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    manifest={'schema':'fpilot.release.v1','release':'2026-10-04-rc2','target':spec['target'],
        'native_windows_acceptance':'PENDING','macos_acceptance':'current-machine isolated startup; other Macs pending',
        'frontend_prebuilt':True,'personal_data_included':False,'files':records}
    (dest/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=output/spec['archive']
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as zipped:
        for path in sorted(dest.rglob('*')):
            if not path.is_file():continue
            info=zipfile.ZipInfo('FPilot/'+path.relative_to(dest).as_posix(),(2026,10,4,0,0,0))
            info.create_system=3
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o100755 if path.suffix in ('.command','.sh') else 0o100644)<<16
            zipped.writestr(info,path.read_bytes())
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'zip':str(archive),'sha256':digest,'bytes':archive.stat().st_size,'files':len(records)+1},ensure_ascii=False))
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--target',choices=PLATFORMS,required=True)
    args=parser.parse_args();build(args.output,args.target)
