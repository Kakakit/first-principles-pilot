"""Short-lived SSH-side snapshot protocol. Standard library only; no daemon.

The operation lock does not expire: an uncertain dispatcher must be inspected,
never displaced by a clock-based takeover while qsub might still be running.
"""
import base64
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
from pathlib import Path


def run(root, request, payload=b''):
    root = Path(root).expanduser()
    lightweight=bool(request.get('snapshot_directory'))
    if not root.is_absolute() or (not lightweight and not root.is_dir()) or (root.exists() and not root.is_dir()):
        raise ValueError('登记工作区不存在')
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise ValueError('同步目录不能经过符号链接')
    if lightweight:
        ancestor=root
        while not ancestor.exists():ancestor=ancestor.parent
        if not ancestor.is_dir() or not os.access(ancestor,os.W_OK):raise ValueError('快照目录或最近存在的父目录不可写')
    base = root if lightweight else root / '.fpilot' / 'project-sync'
    if base.is_symlink() or base.parent.is_symlink():
        raise ValueError('同步目录含符号链接')
    action = request['action']
    if action == 'attempt':
        relative = request['relative']
        parts = Path(relative).parts
        if Path(relative).is_absolute() or '..' in parts or len(parts) != 8 or parts[0] != 'material-projects' or parts[2:5] != ('flows', 'main', 'stages') or parts[6] != 'attempts':
            raise ValueError('历史 attempt 路径不受控')
        target = root.joinpath(*parts)
        if any(p.is_symlink() for p in [target, *target.parents]):
            raise ValueError('历史目录含符号链接')
        if not target.is_dir():return {'available': False, 'reason': '远端目录不存在，记录保留；不重投'}
        for filename in ('_fpilot/attempt.json', '.job-id'):
            path = target / filename
            if path.is_symlink() or path.parent.is_symlink():raise ValueError('任务身份记录含符号链接')
            if path.exists() and path.stat().st_size > 262144:raise ValueError('任务身份记录大小无效')
        marker = target / '_fpilot/attempt.json'
        if marker.exists():
            binding = json.loads(marker.read_bytes())
            if binding.get('id') != request['attempt_id'] or (binding.get('project_id') and binding['project_id'] != request['project_id']):
                raise ValueError('远端 attempt 身份不符，未更新记录')
        job = target / '.job-id'
        if job.exists() and request.get('job_id') and job.read_text().strip().split(';')[0] != request['job_id']:
            raise ValueError('远端精确 job ID 不符，未更新记录')
        return {'available': True, 'identity_marker': marker.exists()}
    if action == 'catalog':
        items = []
        if base.exists():
            for path in sorted(base.glob('*/state.json')):
                if path.is_symlink() or path.parent.is_symlink():
                    raise ValueError('同步目录含符号链接')
                items.append(json.loads(path.read_text()))
        return {'projects': items, 'server': {'host': socket.gethostname(), 'user': os.getuid(), 'root': str(root.resolve())}}
    pid = request['project_id']
    if not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', pid):
        raise ValueError('同步项目 ID 无效')
    directory = base / pid
    for path in (base.parent, base, directory):
        if path.is_symlink():
            raise ValueError('同步目录含符号链接')
    statefile = directory / 'state.json'
    if statefile.is_symlink() or (directory / 'operation.json').is_symlink():
        raise ValueError('同步记录含符号链接')
    state = json.loads(statefile.read_text()) if statefile.is_file() else {'project_id': pid, 'version': None, 'owner': None, 'generation': 0}
    lockfile = directory / 'operation.json'
    operation = json.loads(lockfile.read_text()) if lockfile.is_file() else None
    server = {'host': socket.gethostname(), 'user': os.getuid(), 'root': str(root.resolve())}
    if action == 'status':
        return {'state': state, 'operation': operation, 'server': server}
    if action == 'versions':
        folder = directory / 'versions'
        if folder.is_symlink():raise ValueError('版本目录含符号链接')
        versions = []
        for path in folder.glob('*.json'):
            if path.is_symlink():raise ValueError('版本记录含符号链接')
            versions.append(json.loads(path.read_bytes()))
        return {'versions': sorted(versions, key=lambda v: v['updated_at'], reverse=True), 'state': state}
    if action == 'acquire':
        directory.mkdir(parents=True, exist_ok=True)
        if state['generation'] != request['generation'] or state['version'] != request['version']:
            raise ValueError('服务器版本或设备归属已变化，请重新预览')
        if request['purpose'] in ('dispatch', 'upload') and state['owner'] not in (None, request['device']):
            raise ValueError('项目由其他设备执行，请先下载并接管')
        if request['purpose'] == 'dispatch' and state['owner'] != request['device']:
            raise ValueError('项目已交接，本机只能查看和刷新')
        value = {'token': request['token'], 'device': request['device'], 'purpose': request['purpose'], 'generation': state['generation']}
        try:
            fd = os.open(str(lockfile), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise ValueError('项目操作尚未完成，请先只读核查回执')
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream);stream.flush();os.fsync(stream.fileno())
        return {'state': state, 'operation': value, 'server': server}
    if action in ('get', 'missing'):
        hashes = request.get('hashes', [])
        if action == 'get':
            hashes = [request['sha256']]
        if any(not re.fullmatch(r'[a-f0-9]{64}', h) for h in hashes):
            raise ValueError('附件身份无效')
        if action == 'missing':
            return {'missing': [h for h in hashes if not (directory / 'objects' / h).is_file()]}
        path = directory / 'objects' / hashes[0]
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('附件路径含符号链接')
        with path.open('rb') as stream:
            stream.seek(request.get('offset', 0));raw = stream.read(1024 * 1024)
        return {'data': base64.b64encode(raw).decode(), 'size': path.stat().st_size}
    if not operation or operation['token'] != request.get('token') or operation['device'] != request['device']:
        raise ValueError('项目锁身份不匹配；未修改')
    if action == 'release':
        lockfile.unlink()
        return {'state': state, 'server': server}
    if action == 'put':
        sha = request['sha256']
        if not re.fullmatch(r'[a-f0-9]{64}', sha) or hashlib.sha256(payload).hexdigest() != sha:
            raise ValueError('附件身份校验失败')
        objects = directory / 'objects'
        if objects.is_symlink():
            raise ValueError('附件目录含符号链接')
        objects.mkdir(exist_ok=True)
        destination = objects / sha
        if destination.is_symlink():
            raise ValueError('附件不能是符号链接')
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                raise ValueError('服务器已有附件损坏')
            return {'sha256': sha, 'reused': True}
        atomic_bytes(destination, payload)
        return {'sha256': sha, 'reused': False}
    if action == 'publish':
        sha = request['version']
        metadata = directory / 'objects' / sha
        if not re.fullmatch(r'[a-f0-9]{64}', sha) or metadata.is_symlink():
            raise ValueError('快照身份无效')
        raw = metadata.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('快照身份校验失败')
        manifest = json.loads(raw)
        if manifest['project']['id'] != pid:
            raise ValueError('项目身份不符')
        for entry in manifest['files'].values():
            path = directory / 'objects' / entry['sha256']
            if path.is_symlink() or not path.is_file() or path.stat().st_size != entry['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('附件缺失或损坏；未发布快照')
        state.update(version=sha, owner=state.get('owner') if manifest.get('schema')=='fpilot.project-snapshot.v2' else None, generation=state['generation'] + 1,
                     name=manifest['project']['name'], counts=manifest['counts'], updated_at=request['at'], uploader=request['device'])
        versions = directory / 'versions'
        if versions.is_symlink():
            raise ValueError('版本目录含符号链接')
        versions.mkdir(exist_ok=True)
        atomic_bytes(versions / (sha + '.json'), json.dumps(state).encode())
        atomic_bytes(statefile, json.dumps(state).encode())
        return {'state': state, 'server': server}
    if action == 'claim':
        if request['version'] != state['version']:
            raise ValueError('服务器快照已变化')
        state.update(owner=request['device'], generation=state['generation'] + 1, claimed_at=request['at'])
        atomic_bytes(statefile, json.dumps(state).encode())
        return {'state': state, 'server': server}
    raise ValueError('同步操作不支持')


def atomic_bytes(path, raw):
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.sync-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.buffer.readline())
        result = run(sys.argv[1], request, sys.stdin.buffer.read())
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(2)
