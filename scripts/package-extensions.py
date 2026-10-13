"""Build reproducible extension distribution ZIPs; never imports or runs extensions."""
import hashlib
import json
import re
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'resources/extensions'
OUTPUT = ROOT / 'extensions'
SKIP = {'__pycache__', '.git', '.DS_Store', 'node_modules', '.venv', 'runs', 'outputs'}


def exported_bytes(path):
    raw = path.read_bytes()
    if path.suffix == '.json':
        obj = json.loads(raw)
        def portable(value):
            if isinstance(value, dict):
                return {key: ('archived-development-source/' + Path(item).name
                              if key == 'source_path' and isinstance(item, str) and item.startswith('/Users/')
                              else portable(item)) for key, item in value.items()}
            if isinstance(value, list):
                return [portable(item) for item in value]
            if isinstance(value, str):
                return value.replace('/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python', 'python')
            return value
        updated = portable(obj)
        if updated != obj:
            raw = (json.dumps(updated, ensure_ascii=False, indent=2) + '\n').encode()
    if path.suffix == '.md':
        text = raw.decode().replace('/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python', 'python')
        text = text.replace('/Users/kafka/Documents/FeCrO论文/plugins/first-principles-pilot/extensions/', 'resources/extensions/')
        text = re.sub(r"/Users/kafka/[^\s`\"']+", lambda match: 'archived-development-source/' + match.group().split('/')[-1], text)
        raw = text.encode()
    return raw


def package_extensions():
    OUTPUT.mkdir(exist_ok=True)
    packages = OUTPUT / 'packages'
    packages.mkdir(exist_ok=True)
    entries = []
    for folder in sorted(SOURCE.iterdir()):
        if not folder.is_dir() or folder.name.startswith('_'):
            continue
        manifest_name = 'extension.manifest.json' if (folder / 'extension.manifest.json').exists() else 'extension.json'
        manifest = json.loads((folder / manifest_name).read_text())
        version = manifest['version']
        if not version or any(c not in '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-+' for c in version):
            raise ValueError('Unsafe package version: ' + version)
        paths = []
        for p in sorted(folder.rglob('*')):
            relative = p.relative_to(folder)
            if set(relative.parts) & SKIP or p.suffix == '.pyc':
                continue
            if p.is_symlink():
                raise ValueError('Symlink in source package: ' + str(p))
            if p.is_file():
                if p.name in {'POTCAR', 'WAVECAR', 'CHGCAR', 'fpilot.local.json'} or p.name.startswith('.env'):
                    raise ValueError('Private/runtime artifact in source package: ' + str(p))
                paths.append(p)
        if not paths or len(paths) > 1000 or sum(p.stat().st_size for p in paths) > 20 * 1024 * 1024:
            raise ValueError('Package exceeds host limits: ' + folder.name)
        target = packages / (folder.name + '-' + version + '.zip')
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in paths:
                info = zipfile.ZipInfo(folder.name + '/' + path.relative_to(folder).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (0o755 if path.stat().st_mode & 0o111 else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, exported_bytes(path))
        with zipfile.ZipFile(target) as archive:
            if archive.testzip():
                raise ValueError('ZIP checksum failure: ' + str(target))
            for path in paths:
                if archive.read(folder.name + '/' + path.relative_to(folder).as_posix()) != exported_bytes(path):
                    raise ValueError('Source changed while packaging: ' + str(path))
        files = [{'path':p.relative_to(folder).as_posix(), 'sha256':hashlib.sha256(exported_bytes(p)).hexdigest()} for p in paths]
        entries.append({'id':folder.name, 'name':manifest.get('name',folder.name), 'version':version,
                        'schema':manifest['schema'], 'install_mode':'workbench-v2' if manifest_name=='extension.manifest.json' else 'legacy-cli',
                        'source':'resources/extensions/' + folder.name, 'package':target.relative_to(ROOT).as_posix(),
                        'sha256':hashlib.sha256(target.read_bytes()).hexdigest(), 'size_bytes':target.stat().st_size, 'files':files})
    catalog = {'schema':'fpilot.extension-distribution.v1', 'packages':entries}
    (OUTPUT / 'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
    rows = ['| 扩展 | 版本 | 接口 | 下载 | 源码与说明 |', '|---|---|---|---|---|']
    for item in entries:
        label = '工作台 ZIP 导入' if item['install_mode']=='workbench-v2' else '旧版 CLI'
        rows.append('| '+item['name']+' | '+item['version']+' | '+label+' | [ZIP]('+item['package'].removeprefix('extensions/')+') | [README](../'+item['source']+'/README.md) |')
    readme = '''# 扩展统一发布目录

这里集中管理现有 9 个扩展的版本、可下载 ZIP 和 SHA256。源码统一维护在 [`resources/extensions/`](../resources/extensions/)，本目录由 `scripts/package-extensions.py` 生成，不手工改 ZIP。

'''+ '\n'.join(rows)+'''

## 安装与更新

- 标记“工作台 ZIP 导入”的 6 个包：打开工作台“扩展管理” → 导入 ZIP，选择所需 ZIP；登记已有 Python 环境并检查依赖。GitHub 的 ZIP 页面选择 Download raw file 下载原文件。ZIP 不带 Python 或第三方运行环境。
- 标记“旧版 CLI”的 3 个包尚未提供新版工作台安装清单；解压后按各自 README 使用，不承诺可以从新版扩展管理直接导入。
- EOS 的冻结重新拟合需要宿主支持 `refresh-view` 和 `view_state`；本次源码同步包含该通用接口。旧安装器不会因下载 ZIP 自动升级宿主，请使用包含本次提交的工作台源码版本。其他依赖和科学边界见各包 README。
- 更新已有分析的交互界面时使用“更新结果展示”；不改原始研究输入，不自动重新运行分析或研究计算。

## 可复现管理

从仓库根目录使用已有 Python 3.9+ 执行 `python scripts/package-extensions.py`，生成固定排序、时间戳的 ZIP，导出说明和旧清单中的开发机路径改为可移植占位；原开发源保持。不执行扩展、不连接服务器、不安装依赖。`catalog.json` 保存接口、版本、下载路径、ZIP SHA256 及包内各文件 SHA256，可用 `shasum -a 256 extensions/packages/*.zip` 核对下载。

包内包括代码、说明、第三方许可和最小测试样例；不包括研究任务目录、POTCAR、SSH 配置、密钥、虚拟环境或运行缓存。保留各第三方许可；本目录不是对上游代码重新授权。
'''
    (OUTPUT / 'README.md').write_text(readme)
    print(json.dumps({'packages':len(entries),'v2':sum(e['install_mode']=='workbench-v2' for e in entries),'zip_bytes':sum(e['size_bytes'] for e in entries)},ensure_ascii=False))


if __name__ == '__main__':
    package_extensions()
