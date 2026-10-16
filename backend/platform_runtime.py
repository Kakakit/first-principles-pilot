"""Small OS adapters; only exact local child processes may be stopped."""
import contextlib
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from filelock import FileLock, Timeout


@contextlib.contextmanager
def exclusive_lock(path):
    lock=FileLock(str(path),timeout=0)
    try:lock.acquire()
    except Timeout:yield False;return
    try:yield True
    finally:lock.release()


def _windows_alive(pid):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return ctypes.get_last_error()==5
    try:
        code=wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
    finally:kernel.CloseHandle(handle)


def process_alive(pid):
    if type(pid) is not int or pid<=0:return False
    # os.kill(pid, 0) on Windows can terminate the process.
    if os.name=='nt':return _windows_alive(pid)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def stop_child(process,force=False):
    if process.poll() is not None:return
    if os.name=='nt':
        # process must be an owned Popen instance, never a historical PID.
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10)
        if process.poll() is None:process.kill()
    else:
        try:os.killpg(process.pid,signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:pass


def ssh_options(alias, resolved=None):
    if os.name=='nt':return []
    # /tmp keeps Unix socket names short, including when TMPDIR is long.
    base=Path('/tmp') if Path('/tmp').is_dir() else Path(tempfile.gettempdir())
    path=base/('fpilot-ssh-'+str(os.getuid()))
    path.mkdir(mode=0o700,exist_ok=True);path.chmod(0o700)
    import hashlib
    from .site_registration import connection_identity
    resolved=resolved if resolved is not None else connection_identity(alias)
    if resolved.get('controlpath') not in (None, 'none'): return []
    identity=alias+repr(sorted(resolved.items()))
    return ['-o','ControlMaster=auto','-o','ControlPersist=300','-o','ControlPath='+str(path/hashlib.sha256(identity.encode()).hexdigest()[:20])]


def local_python():
    import sys
    return Path(sys.executable)


def controlled_pull():
    configured=os.environ.get('FPILOT_SAFE_PULL')
    if configured:return Path(configured)
    return Path(__file__).resolve().parents[1]/'resources/preflight/safe_pull_from_cluster.py'
