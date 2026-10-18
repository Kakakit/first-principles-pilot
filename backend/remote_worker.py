"""Ephemeral, stdlib-only request runner. Lives only inside an OpenSSH session."""
import base64
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import time
import threading


class Tail:
    def __init__(self,limit):
        self.limit=limit;self.value=bytearray();self.total=0;self.lock=threading.Lock()
    def append(self,data):
        with self.lock:
            self.total+=len(data)
            if self.limit:
                self.value.extend(data)
                if len(self.value)>self.limit:del self.value[:-self.limit]
    def snapshot(self):
        with self.lock:return bytes(self.value),self.total


def execute(job,on_output=None):
    started=time.monotonic()
    environment=dict(os.environ)
    environment.pop('BASH_ENV',None);environment.pop('ENV',None)
    environment.update(LC_ALL='C',LANG='C')
    data=base64.b64decode(job.get('data',''))
    process=subprocess.Popen(job.get('argv') or ['/bin/bash','--noprofile','--norc','-c',job['command']],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env=environment,start_new_session=True,bufsize=0)
    output=Tail(max(0,job.get('stdout_limit',524288)));error=Tail(32768);local_errors=[]
    def kill():
        try:
            if os.name=='nt':process.kill()
            else:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    def drain(stream,tail):
        try:
            while True:
                block=os.read(stream.fileno(),65536)
                if not block:break
                tail.append(block)
                if tail is output and on_output:
                    try:on_output(block)
                    except Exception as exc:
                        local_errors.append(str(exc));kill();break
        except (OSError,ValueError):pass
    def feed():
        try:
            view=memoryview(data)
            while view:
                written=process.stdin.write(view)
                if not written:break
                view=view[written:]
        except (OSError,ValueError):pass
        finally:
            try:process.stdin.close()
            except (OSError,ValueError):pass
    readers=[threading.Thread(target=drain,args=(stream,tail),daemon=True)
             for stream,tail in ((process.stdout,output),(process.stderr,error))]
    for reader in readers:reader.start()
    writer=threading.Thread(target=feed,daemon=True);writer.start()
    try:code=process.wait(timeout=job.get('timeout',90))
    except subprocess.TimeoutExpired:
        kill()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:pass
        code=None
    # A D-state descendant may retain a pipe; never wait indefinitely for EOF.
    for reader in readers:reader.join(timeout=.25 if code is None else 1)
    for stream in (process.stdin,process.stdout,process.stderr):
        try:stream.close()
        except (OSError,ValueError):pass
    out,total=output.snapshot();err,_=error.snapshot()
    if code is None:err=(err+b'\nRemote command timeout; do not replay a dispatch.')[-32768:]
    if local_errors:
        code=1;err=(err+('\n本机接收失败：'+local_errors[0]).encode())[-32768:]
    return {'exit_code':code,'stdout':base64.b64encode(out).decode(),
            'stdout_truncated':total>output.limit,'stderr':base64.b64encode(err).decode(),
            'remote_seconds':time.monotonic()-started,'received_bytes':total,'sent_bytes':len(data)}


def main():
    print('FPILOT_SESSION_READY', flush=True)
    for line in sys.stdin.buffer:
        request = json.loads(line)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, request['jobs']))
        print(json.dumps({'id': request['id'], 'results': results}, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
