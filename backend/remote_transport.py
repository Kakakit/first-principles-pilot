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
from filelock import FileLock, Timeout as LockTimeout
from . import remote_health

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
    # Removed aliases cannot be dispatched, but their exact historical runtime
    # remains available for status/file reads through frozen attempt identities.
    host = load_registry(include_removed=True)['hosts'].get(alias, {})
    python = host.get('python', {})
    value = python.get('known_interpreter') or next(iter(python.get('known_interpreters', [])), None)
    if not isinstance(value, str) or not value.startswith('/'):
        raise ValueError('服务器未登记已有 Python 解释器，请在服务器设置中登记；不会创建环境')
    return value


class RequestFailure(ConnectionError):
    def __init__(self, message, started):
        super().__init__(message)
        self.started = started


class Session:
    def __init__(self, alias, lane):
        self.alias, self.lane = alias, lane
        self.process = None
        self.reader = None
        self.pipe_stop = None
        self.buffer = b''
        self.jobs = queue.Queue(maxsize=32)
        self.pending_bytes=0
        self.pending_lock=threading.Lock()
        self.active_lock=threading.Lock()
        self.active_group=None
        self.stderr_tail = b''
        self.max_line_bytes = 1048576
        self.connection_attempted = False
        self.last_used = time.monotonic()
        self.closed = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def release(self,item):
        with self.pending_lock:
            self.pending_bytes-=item.pop('reserved_bytes',0)

    def close(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:pass
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
            if kind=='stdout':
                self.buffer+=block
                if len(self.buffer)>self.max_line_bytes:raise ConnectionError('SSH 回执超过本次读取预算')
            elif kind=='eof':raise ConnectionError('SSH 会话已断开')
        raise TimeoutError('SSH 请求超时')

    def _start(self):
        if self.process is not None and self.process.poll() is None: return 0.0
        self.close()
        started = time.monotonic()
        self.connection_attempted = True
        self.stderr_tail = b''
        source = Path(__file__).with_name('remote_worker.py').read_text()
        command = 'exec '+shlex.quote(_interpreter(self.alias))+' -u -c '+shlex.quote(source)
        self.process = subprocess.Popen(['ssh', '-T', *_options(self.alias), '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
            '-o', 'StrictHostKeyChecking=yes', self.alias, command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self._pipe_readers()
        deadline = min(time.monotonic()+30, self.request_deadline)
        try:
            while self._line(deadline) != b'FPILOT_SESSION_READY': pass
        except (ConnectionError,TimeoutError) as exc:
            raise ConnectionError(str(exc)+' '+self.stderr_tail.decode(errors='replace')) from exc
        return time.monotonic()-started

    def _pipe_readers(self):
        # Windows select() accepts sockets, not subprocess pipes.
        output=queue.Queue(maxsize=32);stop=threading.Event()
        self.reader=output;self.pipe_stop=stop
        def read(stream,kind):
            try:
                while not stop.is_set():
                    block=stream.read(65536)
                    if not block:break
                    if kind=='stderr':
                        self.stderr_tail=(self.stderr_tail+block)[-32768:]
                        continue
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
        # Only startup failure proves that no command bytes were written.
        # Mark a write as uncertain before trying it, including partial writes.
        with self.active_lock:self.active_group=group
        try:
            return self._send_request(group)
        except (OSError, ValueError, TimeoutError, ConnectionError) as exc:
            raise RequestFailure(str(exc), any(item.get('send_started') for item in group)) from exc
        finally:
            with self.active_lock:self.active_group=None

    def abort(self,item):
        # Terminate only this still-active wire operation. This also unblocks a
        # Windows pipe write; never kill a later request that reused the session.
        with self.active_lock:
            if self.active_group is not None and any(value is item for value in self.active_group):
                process=self.process
                if process is not None and process.poll() is None:
                    try:process.terminate()
                    except OSError:pass

    def _send_request(self, group):
        self.request_deadline=min(item['deadline'] for item in group)
        self.max_line_bytes=sum(item['job']['stdout_limit']*2+131072 for item in group)+65536
        startup = self._start()
        identifier = uuid.uuid4().hex
        payload = json.dumps({'id': identifier, 'jobs': [item['job'] for item in group]}, separators=(',', ':')).encode()+b'\n'
        started = time.monotonic()
        view = memoryview(payload)
        # Expiry and the send marker are synchronized with the waiting caller.
        for item in group:
            with item['send_lock']:
                if item['expired'] or time.monotonic()>=item['deadline']:
                    raise RequestFailure('请求已过期，未发送',False)
                item['send_started']=True
        if os.name!='nt' and isinstance(self.process.stdin.fileno(),int):os.set_blocking(self.process.stdin.fileno(),False)
        while view:
            if time.monotonic()>=self.request_deadline:raise TimeoutError('SSH 写入超时')
            try:written=self.process.stdin.write(view)
            except BlockingIOError:written=None
            if written is None:time.sleep(.005);continue
            if not written:raise ConnectionError('SSH 请求写入中断')
            view=view[written:]
        self.process.stdin.flush()
        deadline=min(self.request_deadline,time.monotonic()+max(item['job']['timeout'] for item in group)+5)
        result = json.loads(self._line(deadline))
        if result.get('id') != identifier or len(result['results']) != len(group):
            raise ConnectionError('SSH 请求回执身份不一致')
        elapsed = time.monotonic()-started
        for index, (item, receipt) in enumerate(zip(group, result['results'])):
            receipt['transport_state'] = 'RECEIVED'
            receipt['stdout'] = base64.b64decode(receipt['stdout']).decode(errors='replace')
            receipt['stderr'] = base64.b64decode(receipt['stderr']).decode(errors='replace')
            receipt['timing'] = {'session_start_seconds': startup if index==0 else 0, 'roundtrip_seconds': elapsed,
                'remote_seconds': receipt.pop('remote_seconds'), 'batch_size': len(group),
                'received_bytes':receipt.get('received_bytes',0),'sent_bytes':receipt.get('sent_bytes',0),
                'ssh_requests': 1 if index == 0 else 0, 'queue_seconds': max(0, started-item['queued']-startup)}
            if item['scope'] is not None:
                with item['scope']['lock']: item['scope']['events'].append(receipt['timing'])
            if not item['future'].done():item['future'].set_result(receipt)

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
            while first['scope'] is not None and time.monotonic() < end and len(group) < 2:
                try: item = self.jobs.get(timeout=max(.001, end-time.monotonic()))
                except queue.Empty: break
                if item['scope'] is first['scope']: group.append(item)
                else: pending = item; break
            discarded=[item for item in group if item['expired'] or item['future'].cancelled()]
            for item in discarded:self.release(item)
            group=[item for item in group if not item['expired'] and not item['future'].cancelled()]
            if not group:continue
            ticket=None;self.connection_attempted=False
            try:
                from .site_registration import connection_identity
                connection=tuple(sorted(connection_identity(self.alias).items()))
                if any(item.get('connection',connection)!=connection for item in group):
                    raise remote_health.Deferred('连接配置已变化；旧请求未发送，请按新目标重新确认')
                lane_path=remote_health.root()/'ssh-lanes'/remote_health.identity(self.alias,connection)
                lane_path.mkdir(parents=True,exist_ok=True)
                budget=max(0,min(30,min(item['deadline'] for item in group)-time.monotonic()))
                with FileLock(str(lane_path/(self.lane+'.lock')),timeout=budget):
                    if tuple(sorted(connection_identity(self.alias).items()))!=connection:
                        raise remote_health.Deferred('连接配置已变化；旧请求未发送，请按新目标重新确认')
                    if any(item['expired'] or time.monotonic()>=item['deadline'] for item in group):
                        raise remote_health.Deferred('排队请求已过期，未发送')
                    ticket=remote_health.claim(self.alias,connection,group[0].get('retry',False),max(i['job']['timeout'] for i in group))
                    self._request(group)
                    # A command timeout also indicates a potentially blocked host.
                    receipts=[i['future'].result() for i in group if i['future'].done() and not i['future'].cancelled()]
                    failed=next((r for r in receipts if r.get('exit_code') is None),None)
                    remote_health.finish(ticket,(failed.get('stderr') or '远程请求超时') if failed else None,
                        counters={'connection_attempts':int(self.connection_attempted),'session_reuses':int(not self.connection_attempted),'commands':len(group)})
            except (OSError, ValueError, TimeoutError, ConnectionError, LockTimeout) as exc:
                deferred=isinstance(exc,(remote_health.Deferred,LockTimeout))
                if not deferred:
                    self.close()
                    if ticket:
                        remote_health.finish(ticket,str(exc),counters={'connection_attempts':int(self.connection_attempted)})
                for item in group:
                    if item['scope'] is not None:
                        with item['scope']['lock']:item['scope']['events'].append({'ssh_requests':int(item.get('send_started',False)),
                            'connection_failures':int(not deferred),'deferred':deferred})
                    if not item['future'].done():
                        item['future'].set_exception(RequestFailure(str(exc),item.get('send_started',False)))
            for item in group:self.release(item)
            self.last_used = time.monotonic()


def run(alias, command, data=None, timeout=90, stdout_limit=524288, lane='normal', mutating=None, retry=False):
    if os.environ.get('FPILOT_REMOTE_DISABLED')=='1':
        return {'exit_code':None,'stdout':'','stderr':'隔离验收禁止远程连接；请求未发送','transport_state':'NOT_SENT','stdout_truncated':False}
    from .site_registration import connection_identity
    connection=tuple(sorted(connection_identity(alias).items()))
    if mutating is None:
        mutating = bool(data) or any(token in command for token in ('qsub ', 'sbatch ', 'mkdir ', 'rm ', 'cp ', 'ln ', 'tar -x'))
    if lane not in ('normal','analysis'):raise ValueError('未知 SSH 请求通道')
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
    size=len(command.encode())+((len(data or b'')+2)//3)*4+1024
    with session.pending_lock:
        if session.pending_bytes+size>64*1024*1024:
            return {'exit_code':None,'stdout':'','stderr':'SSH 在途数据已达 64 MiB，请等待已有请求完成；本次未发送',
                    'transport_state':'NOT_SENT','stdout_truncated':False}
        session.pending_bytes+=size
    job = {'command': command, 'data': base64.b64encode(data or b'').decode(),
           'timeout': timeout, 'stdout_limit': stdout_limit}
    item={'job':job,'future':future,'scope':scope,'mutating':mutating,'queued':time.monotonic(),
          'reserved_bytes':size,'enqueued':False,'deadline':time.monotonic()+timeout+45,'send_lock':threading.Lock(),'expired':False,'retry':retry,'connection':connection}
    try:
        session.jobs.put_nowait(item)
        item['enqueued']=True
        receipt=future.result(timeout=timeout+45)
    except Exception as exc:
        if not item['enqueued']:session.release(item)
        with item['send_lock']:
            item['expired']=True
            sent=item.get('send_started',False)
        future.cancel()
        if isinstance(exc,concurrent.futures.TimeoutError):session.abort(item)
        not_sent=(isinstance(exc,RequestFailure) and not exc.started) or not sent
        message='SSH 请求队列已满，请等待已有请求完成' if isinstance(exc,queue.Full) else str(exc)
        return {'exit_code':None,'stdout':'','stdout_truncated':False,
                'transport_state':'NOT_SENT' if not_sent else 'UNCERTAIN',
                'stderr':'SSH 会话失败：'+message+('；请求未发送' if not_sent else '；提交结果不明确时禁止自动重投')}
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


def recover(alias, manual=False):
    from .site_registration import connection_identity
    connection=tuple(sorted(connection_identity(alias).items()))
    requested_at=time.time()
    folder=remote_health.root()/'ssh-lanes'/remote_health.identity(alias,connection)
    folder.mkdir(parents=True,exist_ok=True)
    with FileLock(str(folder/'recovery.lock'),timeout=35):
        row=remote_health._read().get(remote_health.identity(alias,connection),{})
        if row.get('last_recovery_at',0)>=requested_at:
            error=row.get('last_recovery_error','')
            return {'exit_code':None if error else 0,'stdout':'' if error else 'FPILOT_CONNECTION_OK',
                    'stderr':error,'coalesced':True,'transport_state':'UNCERTAIN' if error else 'RECEIVED'}
        if not manual and row.get('state')=='READY':
            return {'exit_code':0,'stdout':'FPILOT_CONNECTION_OK','stderr':'','transport_state':'NOT_SENT'}
        command=remote_health.probe_command(alias,connection)
        retry='manual' if manual else True
        # Probe only the shell connection, without starting the scientific Python environment.
        args=['ssh',*_options(alias),'-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',alias,command]
        return legacy(args,timeout=30,stdout_limit=4096,retry=retry)


def legacy(argv,data=None,timeout=90,stdout_limit=524288,retry=False,lane='normal',on_output=None):
    """Compatibility transport uses the same cooldown and bounded output runner."""
    from .site_registration import connection_identity
    from . import remote_worker
    if os.environ.get('FPILOT_REMOTE_DISABLED')=='1':
        return {'exit_code':None,'stdout':'','stderr':'隔离验收禁止远程连接','transport_state':'NOT_SENT'}
    alias=argv[-2];connection=tuple(sorted(connection_identity(alias).items()))
    if lane not in ('normal','analysis'):raise ValueError('未知 SSH 请求通道')
    ticket=None
    try:
        lane_path=remote_health.root()/'ssh-lanes'/remote_health.identity(alias,connection)
        lane_path.mkdir(parents=True,exist_ok=True)
        with FileLock(str(lane_path/(lane+'.lock')),timeout=30):
            ticket=remote_health.claim(alias,connection,retry=retry,timeout=timeout)
            result=remote_worker.execute({'argv':argv,
                'data':base64.b64encode(data or b'').decode(),'timeout':timeout,'stdout_limit':stdout_limit},on_output=on_output)
            for name in ('stdout','stderr'):result[name]=base64.b64decode(result[name]).decode(errors='replace')
            failed=result['exit_code'] in (None,255) or bool(retry and
                (result['exit_code']!=0 or 'FPILOT_CONNECTION_OK' not in result['stdout']))
            remote_health.finish(ticket,(result['stderr'] or 'SSH 连接失败（无错误输出）') if failed else None,counters={'connection_attempts':1,'commands':1})
            if failed:
                result['exit_code']=None
                result['stderr']=result['stderr'] or 'SSH 连接失败（无错误输出）'
            result['transport_state']='UNCERTAIN' if failed else 'RECEIVED'
            return result
    except (OSError,ValueError,ConnectionError,LockTimeout) as exc:
        if ticket:remote_health.finish(ticket,str(exc))
        return {'exit_code':None,'stdout':'','stderr':str(exc),'transport_state':'NOT_SENT' if ticket is None else 'UNCERTAIN'}
