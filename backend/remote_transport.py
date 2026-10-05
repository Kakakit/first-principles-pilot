"""Reuse an SSH exec session, with bounded lanes, operation batching and receipts."""
import atexit
import base64
import concurrent.futures
import contextlib
import contextvars
import hashlib
import json
import os
import queue
import shlex
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from .platform_runtime import ssh_options

SCOPE = contextvars.ContextVar('remote_operation', default=None)
SESSIONS = {}
LOCK = threading.RLock()
SCOPES = {}
BRIDGE = None
IDLE_SECONDS = 900


@contextlib.contextmanager
def operation(label='remote-action'):
    existing = SCOPE.get()
    if existing is not None:
        yield existing
        return
    value = {'id': uuid.uuid4().hex, 'label': label, 'memo': {}, 'events': [], 'lock': threading.RLock()}
    token = SCOPE.set(value)
    with LOCK: SCOPES[value['id']] = value
    try:
        yield value
    finally:
        with LOCK: SCOPES.pop(value['id'], None)
        SCOPE.reset(token)


def parallel(functions):
    """Independent validators share one operation and therefore one batched round trip."""
    with operation() as scope:
        def invoke(function):
            token = SCOPE.set(scope)
            try: return function()
            finally: SCOPE.reset(token)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(functions))) as pool:
            futures = [pool.submit(invoke, function) for function in functions]
            return [future.result() for future in futures]


def _options(alias):
    return ssh_options(alias)


def _interpreter(alias):
    from .servers import load_registry
    host = load_registry()['hosts'].get(alias, {})
    python = host.get('python', {})
    value = python.get('known_interpreter') or next(iter(python.get('known_interpreters', [])), None)
    if not isinstance(value, str) or not value.startswith('/'):
        raise ValueError('服务器未登记已有 Python 解释器，请在服务器设置中登记；不会创建环境')
    return value


class Session:
    def __init__(self, alias, lane):
        self.alias, self.lane = alias, lane
        self.process = None
        self.reader = None
        self.pipe_stop = None
        self.buffer = b''
        self.jobs = queue.Queue()
        self.last_used = time.monotonic()
        self.closed = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        if self.reader is not None:
            if self.pipe_stop:self.pipe_stop.set()
            self.reader = None
        if process is not None:
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None:stream.close()
        self.buffer = b''

    def _line(self, deadline):
        while time.monotonic() < deadline:
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                return line
            try:kind,block=self.reader.get(timeout=min(.2,max(.001,deadline-time.monotonic())))
            except queue.Empty:
                if self.process.poll() is not None:raise ConnectionError('SSH 会话已结束')
                continue
            if kind=='stdout':self.buffer+=block
            elif kind=='eof':raise ConnectionError('SSH 会话已断开')
        raise TimeoutError('SSH 请求超时')

    def _start(self):
        if self.process is not None and self.process.poll() is None: return 0.0
        self.close()
        started = time.monotonic()
        source = Path(__file__).with_name('remote_worker.py').read_text()
        command = 'exec '+shlex.quote(_interpreter(self.alias))+' -u -c '+shlex.quote(source)
        self.process = subprocess.Popen(['ssh', '-T', *_options(self.alias), '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
            '-o', 'StrictHostKeyChecking=yes', self.alias, command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self._pipe_readers()
        deadline = time.monotonic()+30
        while self._line(deadline) != b'FPILOT_SESSION_READY': pass
        return time.monotonic()-started

    def _pipe_readers(self):
        # Windows select() accepts sockets, not subprocess pipes.
        output=queue.Queue(maxsize=512);stop=threading.Event()
        self.reader=output;self.pipe_stop=stop
        def read(stream,kind):
            try:
                while not stop.is_set():
                    block=stream.read(65536)
                    if not block:break
                    if kind=='stderr':continue
                    while not stop.is_set():
                        try:output.put((kind,block),timeout=.2);break
                        except queue.Full:pass
            except (OSError,ValueError):pass
            finally:
                if kind=='stdout' and not stop.is_set():
                    while not stop.is_set():
                        try:output.put(('eof',b''),timeout=.2);break
                        except queue.Full:pass
        for kind in ('stdout','stderr'):
            threading.Thread(target=read,args=(getattr(self.process,kind),kind),daemon=True).start()

    def _request(self, group):
        startup = self._start()
        identifier = uuid.uuid4().hex
        payload = json.dumps({'id': identifier, 'jobs': [item['job'] for item in group]}, separators=(',', ':')).encode()+b'\n'
        started = time.monotonic()
        view = memoryview(payload)
        while view:
            written = self.process.stdin.write(view)
            if not written: raise ConnectionError('SSH 请求写入中断')
            view = view[written:]
        self.process.stdin.flush()
        deadline = time.monotonic()+max(item['job']['timeout'] for item in group)+5
        result = json.loads(self._line(deadline))
        if result.get('id') != identifier or len(result['results']) != len(group):
            raise ConnectionError('SSH 请求回执身份不一致')
        elapsed = time.monotonic()-started
        for index, (item, receipt) in enumerate(zip(group, result['results'])):
            receipt['stdout'] = base64.b64decode(receipt['stdout']).decode(errors='replace')
            receipt['stderr'] = base64.b64decode(receipt['stderr']).decode(errors='replace')
            receipt['timing'] = {'session_start_seconds': startup if index==0 else 0, 'roundtrip_seconds': elapsed,
                'remote_seconds': receipt.pop('remote_seconds'), 'batch_size': len(group),
                'received_bytes':receipt.get('received_bytes',0),'sent_bytes':receipt.get('sent_bytes',0),
                'ssh_requests': 1 if index == 0 else 0, 'queue_seconds': max(0, started-item['queued']-startup)}
            if item['scope'] is not None:
                with item['scope']['lock']: item['scope']['events'].append(receipt['timing'])
            item['future'].set_result(receipt)

    def _serve(self):
        pending = None
        while not self.closed:
            try:
                first = pending or self.jobs.get(timeout=1)
                pending = None
            except queue.Empty:
                if time.monotonic()-self.last_used > IDLE_SECONDS: self.close()
                continue
            group = [first]
            # Validators from one operation arrive together; unrelated actions stay independent.
            end = time.monotonic()+.1
            while first['scope'] is not None and time.monotonic() < end and len(group) < 8:
                try: item = self.jobs.get(timeout=max(.001, end-time.monotonic()))
                except queue.Empty: break
                if item['scope'] is first['scope']: group.append(item)
                else: pending = item; break
            try:
                self._request(group)
            except (OSError, ValueError, TimeoutError, ConnectionError) as exc:
                self.close()
                # Mutations may have run; they are never replayed automatically.
                if all(not item['mutating'] for item in group):
                    try: self._request(group)
                    except Exception as retry:
                        for item in group: item['future'].set_exception(retry)
                else:
                    for item in group: item['future'].set_exception(exc)
            self.last_used = time.monotonic()


def run(alias, command, data=None, timeout=90, stdout_limit=524288, lane='normal', mutating=None):
    from .site_registration import connection_identity
    connection=tuple(sorted(connection_identity(alias).items()))
    if mutating is None:
        mutating = bool(data) or any(token in command for token in ('qsub ', 'sbatch ', 'mkdir ', 'rm ', 'cp ', 'ln ', 'tar -x'))
    scope = SCOPE.get()
    identity = (alias, connection, command, hashlib.sha256(data or b'').hexdigest(), stdout_limit, timeout, lane)
    if scope is not None and not mutating:
        with scope['lock']:
            if identity in scope['memo']:
                value = dict(scope['memo'][identity])
                value['timing'] = {'ssh_requests': 0, 'cache_hit': True}
                return value
    with LOCK:
        key = (os.getpid(), alias, lane, connection)
        for old in list(SESSIONS):
            if old[:3]==key[:3] and old!=key:
                SESSIONS[old].closed=True;SESSIONS[old].close();del SESSIONS[old]
        if key not in SESSIONS: SESSIONS[key] = Session(alias, lane)
        session = SESSIONS[key]
    future = concurrent.futures.Future()
    job = {'command': command, 'data': base64.b64encode(data or b'').decode(),
           'timeout': timeout, 'stdout_limit': stdout_limit}
    session.jobs.put({'job': job, 'future': future, 'scope': scope, 'mutating': mutating, 'queued': time.monotonic()})
    try: receipt = future.result(timeout=timeout+65)
    except Exception as exc:
        return {'exit_code': None, 'stdout': '', 'stdout_truncated': False,
                'stderr': 'SSH 会话失败：'+str(exc)+'；提交结果不明确时禁止自动重投'}
    if scope is not None and not mutating:
        with scope['lock']: scope['memo'][identity] = dict(receipt)
    return receipt


def close_all():
    global BRIDGE
    with LOCK:
        for session in SESSIONS.values(): session.closed = True; session.close()
        SESSIONS.clear()
        if BRIDGE:
            BRIDGE[0].close()
            if not BRIDGE[1].startswith('tcp:'):Path(BRIDGE[1]).unlink(missing_ok=True)
            BRIDGE = None


def bridge_path():
    """Local IPC adapter used only by the existing controlled transfer CLI."""
    global BRIDGE
    with LOCK:
        if BRIDGE is not None: return BRIDGE[1]
        auth_token=uuid.uuid4().hex
        if os.name=='nt' or os.environ.get('FPILOT_IPC_MODE')=='tcp':
            listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            try:listener.bind(('127.0.0.1',0))
            except OSError:listener.close();raise
            path='tcp:127.0.0.1:'+str(listener.getsockname()[1])
        else:
            directory=Path('/tmp')/('fpilot-pull-'+str(os.getuid()))
            directory.mkdir(mode=0o700,exist_ok=True)
            path=str(directory/(str(os.getpid())+'.sock'));Path(path).unlink(missing_ok=True)
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            listener.bind(path);os.chmod(path,0o600)
        listener.listen(8)
        def serve_client(client):
            with client:
                stream=client.makefile('rwb')
                try:
                    request=json.loads(stream.readline())
                    if path.startswith('tcp:') and request.get('token')!=auth_token:
                        raise ValueError('本机会话身份无效')
                    with LOCK: scope=SCOPES.get(request.get('scope'))
                    token=SCOPE.set(scope)
                    try:
                        receipt=run(request['alias'],request['command'],timeout=request.get('timeout',180),
                                    stdout_limit=request.get('stdout_limit',16*1024*1024),
                                    lane=request.get('lane','normal'),mutating=False)
                    finally: SCOPE.reset(token)
                    stream.write(json.dumps(receipt).encode()+b'\n');stream.flush()
                except Exception as exc:
                    stream.write(json.dumps({'exit_code':None,'stdout':'','stderr':str(exc)}).encode()+b'\n');stream.flush()
        def accept():
            while True:
                try: client,_=listener.accept()
                except OSError: break
                threading.Thread(target=serve_client,args=(client,),daemon=True).start()
        threading.Thread(target=accept,daemon=True).start()
        BRIDGE=(listener,path,auth_token)
        return path


def bridge_token():
    bridge_path()
    return BRIDGE[2]


atexit.register(close_all)
