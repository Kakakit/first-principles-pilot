"""Header-only catalog and bounded-memory byte concatenation of a local library."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from .storage import ROOT


def library_root():
    config = ROOT / 'potcar-library.json'
    value = os.environ.get('FPILOT_POTCAR_LIBRARY')
    if not value and config.exists():
        value = json.loads(config.read_text())['path']
    if not value or not Path(value).is_dir():
        raise ValueError('请配置存在的本机赝势库目录')
    return Path(value).resolve()


def header(path):
    with path.open('rb') as stream:
        text = stream.read(8192).decode('utf-8', errors='replace')
    title = re.search(r'^\s*TITEL\s*=\s*(.+)', text, re.M)
    xc = re.search(r'^\s*LEXCH\s*=\s*(\S+)', text, re.M)
    enmax = re.search(r'ENMAX\s*=\s*([\d.Ee+-]+)', text)
    if not title or not xc or not enmax:
        raise ValueError('头部元信息不完整')
    title = title.group(1).strip()
    parts = title.split()
    if not parts or not parts[0].startswith('PAW') or len(parts) < 2:
        raise ValueError('当前仅支持 PAW 数据集')
    # Fractional-valence hydrogen and special datasets require separate workflows.
    variant = parts[1]
    if not re.fullmatch(r'[A-Z][a-z]?(?:_[A-Za-z0-9]+)*', variant):
        raise ValueError('特殊价态数据集未纳入普通元素选择')
    family = {'PE': 'PBE', 'CA': 'LDA'}.get(xc.group(1).upper())
    if not family:
        raise ValueError('当前表单不支持此泛函家族')
    if parts[0] == 'PAW_PBE' and family != 'PBE':
        raise ValueError('TITEL 与 LEXCH 泛函标记冲突')
    return {'element': variant.split('_')[0], 'variant': variant, 'family': family,
            'titel': title, 'enmax': float(enmax.group(1)), 'size_bytes': path.stat().st_size}


def catalog():
    root = library_root(); entries = []; skipped = 0
    for path in sorted(root.rglob('POTCAR')):
        try:
            path.resolve().relative_to(root)
            entry = header(path)
            entries.append(dict(entry, id=path.relative_to(root).as_posix()))
        except (ValueError, OSError):
            skipped += 1
    return {'path': str(root), 'entries': entries, 'skipped': skipped,
            'default_policy': '元素同名基础版本仅为候选；无唯一同名版本时必须选择，不自动选 pv/sv/GW'}


def configure(path):
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('目录不存在')
    ROOT.mkdir(parents=True, exist_ok=True)
    config = ROOT / 'potcar-library.json'
    with tempfile.NamedTemporaryFile(mode='w', dir=ROOT, delete=False) as out:
        json.dump({'path': str(root)}, out); name = out.name
    os.replace(name, config)
    return {'path': str(root)}


def assemble(species, selections, functional):
    if functional not in ('PBE', 'PBEsol', 'LDA') or not species or len(species) > 1500:
        raise ValueError('请明确泛函与结构物种')
    root = library_root(); family = 'LDA' if functional == 'LDA' else 'PBE'
    if set(species) != set(selections):
        raise ValueError('每种元素必须且只能选择一个赝势')
    paths = {}; entries = {}
    for element, key in selections.items():
        path = (root / key).resolve()
        path.relative_to(root)
        if path.name != 'POTCAR':
            raise ValueError('必须选择 POTCAR 数据集')
        info = header(path)
        if info['element'] != element or info['family'] != family:
            raise ValueError('元素或泛函与所选赝势不匹配')
        paths[element] = path; entries[element] = dict(info, id=key)
    directory = ROOT / 'potcars'; directory.mkdir(parents=True, exist_ok=True)
    total = 0; combined = hashlib.sha256(); lineage = []
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
        temp = Path(output.name)
        try:
            for element in species:
                source_hash = hashlib.sha256(); tail = b''; markers = 0; size = 0
                with paths[element].open('rb') as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk); size += len(chunk)
                        if total > 32 * 1024 * 1024:
                            raise ValueError('合并 POTCAR 超过当前 32 MiB 上限')
                        scan = tail + chunk
                        markers += scan.count(b'End of Dataset')
                        tail = scan[-13:]
                        source_hash.update(chunk); combined.update(chunk); output.write(chunk)
                if markers != 1 or not tail.endswith(b'\n'):
                    raise ValueError('数据集结束标记或末尾换行不完整，未修改源文件')
                lineage.append(dict(entries[element], sha256=source_hash.hexdigest(), size_bytes=size))
            output.flush(); os.fsync(output.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    key = combined.hexdigest(); destination = directory / key
    try:
        os.link(temp, destination)
    except FileExistsError:
        check = hashlib.sha256()
        with destination.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                check.update(chunk)
        if check.hexdigest() != key:
            raise ValueError('已有装配文件损坏，未覆盖')
    finally:
        temp.unlink(missing_ok=True)
    receipt = {'library_path': str(root), 'functional': functional, 'records': lineage, 'sha256': key}
    receipt_raw = json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode()
    receipt_id = hashlib.sha256(receipt_raw).hexdigest()
    receipt_path = directory / (receipt_id + '.library.json')
    if not receipt_path.exists():
        receipt_path.write_bytes(receipt_raw)
    return {'id': key, 'size_bytes': total, 'provenance': receipt, 'provenance_id': receipt_id}
