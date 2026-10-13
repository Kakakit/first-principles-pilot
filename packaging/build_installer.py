"""Native self-contained release builder using a verified standalone CPython.

Application selection is shared with the existing portable builder. Never read
user configuration or project directories. Build on the target OS/architecture.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def safe_extract(archive, destination):
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or (path.parts and path.parts[0] != 'python'):
                raise ValueError('Unsafe runtime archive path: ' + member.name)
            if member.isdev() or member.isfifo():
                raise ValueError('Unexpected runtime archive entry: ' + member.name)
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                target = path.parent / link if member.issym() else link
                if link.is_absolute() or '..' in target.parts or not target.parts or target.parts[0] != 'python':
                    raise ValueError('Unsafe runtime archive link: ' + member.name)
        # CPython release archives have internal relative symlinks on macOS.
        tar.extractall(destination)


def run(command, **kwargs):
    subprocess.run([str(x) for x in command], check=True, **kwargs)


def runtime(target, destination, cache):
    lock = json.loads((ROOT / 'packaging/python-runtime.json').read_text())['targets'][target]
    archive = cache / (target + '-python.tar.gz')
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != lock['sha256']:
        with urlopen(lock['url'], timeout=120) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['sha256']:
        raise ValueError('Standalone Python SHA256 mismatch')
    safe_extract(archive, destination)
    python = destination / 'python' / ('python.exe' if target.startswith('windows') else 'bin/python3')
    formats=['--only-binary=:all:']
    if target=='macos-x64':
        # This exact pymatgen version has no CPython 3.11 Intel Mac wheel.
        # Build it on the native CI machine instead of changing its version.
        formats.append('--no-binary=pymatgen')
    run([python, '-m', 'pip', 'install', *formats, '--no-compile',
         '-r', ROOT / 'packaging/requirements-runtime.txt'])
    run([python, '-m', 'pip', 'check'])
    # Runtime metadata stays in the package. No developer interpreter is copied.
    (destination / 'PYTHON_RUNTIME.json').write_text(json.dumps(lock, indent=2), encoding='utf-8')
    return python


def notices(destination, python):
    code = "import importlib.metadata as m,json;print(json.dumps([{'name':d.metadata['Name'],'version':d.version,'license':d.metadata.get('License','see installed license files')} for d in m.distributions()]))"
    records = json.loads(subprocess.check_output([str(python), '-c', code], text=True))
    text = '# Third party components\n\nFrontend licenses are in THIRD_PARTY_LICENSES.\n\n'
    text += 'Standalone CPython and binary dependencies are bundled. Original licenses remain in the Python distribution and installed package metadata.\n\n'
    text += '| Python package | Version | License |\n|---|---|---|\n'
    text += '\n'.join('| ' + r['name'] + ' | ' + r['version'] + ' | ' + str(r['license']).replace('\n', ' ').replace('|', '/')[:300] + ' |' for r in sorted(records, key=lambda r:r['name']))
    text += '\n\nVASP, POTCAR, SSH credentials and external research programs are not included.\n'
    (destination / 'PYTHON_THIRD_PARTY_NOTICES.md').write_text(text, encoding='utf-8')
    front = destination / 'THIRD_PARTY_NOTICES.md'
    text = front.read_text(encoding='utf-8')
    text = text.replace('Python dependencies are installed separately from PyPI with their original licenses; no Python runtime or dependency binaries are redistributed in this ZIP.',
                        'Python runtime and dependencies are bundled; see PYTHON_THIRD_PARTY_NOTICES.md and their original installed license files.')
    front.write_text(text, encoding='utf-8')


def prepare(output, target):
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('portable_builder', ROOT / 'packaging/build.py')
    portable = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(portable)
    portable.build(output / 'application', 'windows' if target.startswith('windows') else 'macos')
    app = output / 'application/FPilot'
    for pattern in ('*.cmd', '*.command'):
        for path in app.glob(pattern):
            path.unlink()
    for name in ('installed_launcher.py', 'workbench-control.html'):
        shutil.copyfile(ROOT / 'scripts' / name, app / 'scripts' / name)
    shutil.copyfile(ROOT / 'docs/INSTALLATION.md', app / 'INSTALLATION.md')
    for name in ('WINDOWS_README.md', 'MAC_README.md'):
        if (app / name).exists():
            (app / name).unlink()
    cache = output / 'cache'
    cache.mkdir()
    python = runtime(target, app, cache)
    notices(app, python)
    release = json.loads((ROOT / 'packaging/release.json').read_text())
    manifest = json.loads((app / 'RELEASE_MANIFEST.json').read_text())
    manifest.update(release=release['version'], target=target, self_contained_python=True,
                    native_startup_acceptance='Run packaging/smoke_installer.py after building',
                    signature='UNSIGNED_TEST_RELEASE')
    # Manifest covers application resources; dependency inventories have their
    # own locked versions and upstream runtime digest, avoiding expensive hashing
    # of the entire Python installation whenever diagnostics are requested.
    manifest['files'] = [{'path':p.relative_to(app).as_posix(), 'bytes':p.stat().st_size,
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(app.rglob('*'))
        if p.is_file() and 'python' not in p.relative_to(app).parts and p.name != 'RELEASE_MANIFEST.json']
    (app / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'build.json').write_text(json.dumps({'application':str(app.resolve()), 'python':str(python.resolve()),
        'target':target, 'version':release['version']}, indent=2), encoding='utf-8')
    return app, release['version']


def package_windows(app, version, output):
    compiler = shutil.which('ISCC') or shutil.which('iscc') or Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe'
    run([compiler, '/DRuntimeDir=' + str(app.resolve()), '/DReleaseVersion=' + version,
         '/DOutputDir=' + str((output / 'assets').resolve()), ROOT / 'packaging/windows/installer.iss'])


def package_mac(app, version, output, target):
    staging = output / 'dmg'
    bundle = staging / 'First Principles Pilot.app'
    contents = bundle / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Resources').mkdir()
    shutil.move(str(app), str(contents / 'Resources/FPilot'))
    import plistlib
    with (contents / 'Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleExecutable':'launcher', 'CFBundleIdentifier':'org.fpilot.workbench',
            'CFBundleName':'First Principles Pilot', 'CFBundleDisplayName':'First Principles Pilot',
            'CFBundlePackageType':'APPL', 'CFBundleShortVersionString':version.split('-')[0],
            'CFBundleVersion':'1101', 'LSMinimumSystemVersion':'11.0', 'NSHighResolutionCapable':True}, f)
    launcher = contents / 'MacOS/launcher'
    launcher.write_text('#!/bin/sh\nAPP_PATH="$(CDPATH= cd -- "$(dirname -- "$0")/../Resources/FPilot" && pwd)"\nexec "$APP_PATH/python/bin/python3" -X utf8 "$APP_PATH/scripts/installed_launcher.py"\n')
    launcher.chmod(0o755)
    run(['codesign', '--force', '--deep', '--sign', '-', bundle])
    (staging / 'Applications').symlink_to('/Applications')
    shutil.copyfile(ROOT / 'docs/INSTALLATION.md', staging / '安装与使用说明.md')
    run(['hdiutil', 'create', '-volname', 'First Principles Pilot', '-srcfolder', staging,
        '-ov', '-format', 'UDZO', output / 'assets' / ('FPilot-' + version + '-' + target + '.dmg')])
    actual = contents / 'Resources/FPilot'
    build = json.loads((output / 'build.json').read_text())
    build.update(application=str(actual.resolve()), python=str((actual / 'python/bin/python3').resolve()))
    (output / 'build.json').write_text(json.dumps(build, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('windows-x64', 'macos-arm64', 'macos-x64'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    native = 'windows-x64' if sys.platform == 'win32' else 'macos-arm64' if platform.machine() == 'arm64' else 'macos-x64'
    if args.target != native or sys.platform not in ('win32', 'darwin'):
        raise ValueError('Build on the target platform and architecture')
    output = args.output.resolve()
    app, version = prepare(output, args.target)
    (output / 'assets').mkdir()
    package_windows(app, version, output) if args.target == 'windows-x64' else package_mac(app, version, output, args.target)
    assets = output / 'assets'
    for p in assets.iterdir():
        if p.suffix in ('.exe', '.dmg'):
            p.with_suffix(p.suffix + '.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n')


if __name__ == '__main__':
    main()
