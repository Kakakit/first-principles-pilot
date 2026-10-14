"""Browser workbench controller for installed, self-contained distributions.

Only the backend created by this process is stopped. Application files are
read-only; settings, controller identity and project data live under user data.
"""
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen
import webbrowser

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
sys.dont_write_bytecode = True


def user_home():
    if os.environ.get('FPILOT_HOME'):
        return Path(os.environ['FPILOT_HOME']).expanduser().resolve()
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'FirstPrinciplesPilot'
    return Path.home() / 'Library/Application Support/FirstPrinciplesPilot'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def atomic_json(path, value):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        name = f.name
        f.write(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
    try:
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def dispatching_count(workspace):
    database = workspace / 'execution.sqlite3'
    if not database.exists():
        return 0
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as connection:
        return connection.execute("SELECT count(*) FROM attempts WHERE state='DISPATCHING'").fetchone()[0]


def hidden_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}


def tail(path):
    if not path.exists():
        return ''
    with path.open('rb') as f:
        f.seek(max(0, path.stat().st_size - 16384))
        return f.read().decode('utf-8', errors='replace')


class Controller:
    def __init__(self, home):
        self.home = home
        self.runtime = home / 'controller'
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.config_path = home / 'fpilot.local.json'
        self.phase, self.error, self.busy = 'stopped', '', False
        self.child = None
        self.started = None
        self.mutex = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.shutdown_file = self.runtime / 'stop-backend'
        self.log_path = self.runtime / 'workbench.log'
        self.config = {}
        self.server = None
        self.reload()

    def reload(self):
        self.config = read_json(self.config_path)
        if not isinstance(self.config, dict):
            raise ValueError('本机配置应为 JSON 对象。')
        self.workspace = Path(self.config.get('workspace') or self.home / 'workspace-data').expanduser().resolve()
        self.port = self.config.get('port', 8765)
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError('网页端口须为 1024–65535 的整数。')

    def state(self):
        if self.child is not None and self.child.poll() is not None and self.phase == 'running':
            self.phase, self.error = 'error', '工作台进程已退出，请查看启动日志。'
        return {'phase': self.phase, 'error': self.error, 'busy': self.busy,
                'startedAt': self.started, 'workspace': str(self.workspace),
                'python': sys.executable, 'url': 'http://127.0.0.1:' + str(self.port),
                'processes': [{'name': '工作台', 'pid': self.child.pid}] if self.child and self.child.poll() is None else [],
                'workerEnabled': self.config.get('worker_enabled', True), 'logPath': str(self.log_path)}

    def request(self, action):
        if action not in ('start', 'stop', 'restart', 'quit'):
            raise ValueError('未知操作。')
        with self.mutex:
            if self.busy:
                if action == 'start':
                    return
                raise ValueError('正在处理上一项操作，请等待。')
            if action == 'start' and self.state()['phase'] == 'running':
                return
            if action != 'start' and self.child and self.child.poll() is None:
                if dispatching_count(self.workspace):
                    raise ValueError('任务正在上传或提交，等待派发回执落盘后再停止或重启。')
            self.busy = True
            threading.Thread(target=self.operate, args=(action,), daemon=True).start()

    def operate(self, action):
        try:
            if action != 'start':
                self.stop()
            if action in ('start', 'restart'):
                self.start()
            elif action == 'quit':
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except Exception as exc:
            self.error = str(exc)
            self.phase = 'error'
        finally:
            self.busy = False

    def start(self):
        if self.child and self.child.poll() is None:
            raise ValueError('上一个工作台进程仍在退出，请等待；不会重复启动。')
        self.reload()
        self.phase, self.error = 'starting', ''
        self.shutdown_file.unlink(missing_ok=True)
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE':'1',
               'MPLCONFIGDIR':str(self.home / 'cache/matplotlib'),
               'FPILOT_INSTALLED': '1', 'FPILOT_CONFIG': str(self.config_path),
               'FPILOT_CONTROL_URL': 'http://127.0.0.1:' + str(self.server.server_port),
               'FPILOT_LAUNCHER_PID': str(os.getpid())}
        # Avoid stale development-machine variables redirecting an installed app.
        for key in ('FPILOT_WORKSPACE', 'FPILOT_POTCAR_LIBRARY', 'FPILOT_RESEARCH_ROOT', 'FPILOT_PORT', 'FPILOT_SAFE_PULL'):
            env.pop(key, None)
        if self.log_path.exists() and self.log_path.stat().st_size > 5 * 1024 * 1024:
            os.replace(self.log_path, self.log_path.with_suffix('.previous.log'))
        with self.log_path.open('ab', buffering=0) as log:
            self.child = subprocess.Popen([sys.executable, str(APP_ROOT / 'launch.py'), '--no-browser',
                '--workspace', str(self.workspace), '--config', str(self.config_path),
                '--shutdown-file', str(self.shutdown_file)], cwd=APP_ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **hidden_options())
        deadline = time.monotonic() + 60
        while self.child.poll() is None and time.monotonic() < deadline:
            try:
                with urlopen(self.state()['url'] + '/api/health', timeout=1) as response:
                    if response.status == 200 and response.headers.get('X-FPilot-Instance') == str(self.child.pid):
                        self.phase = 'running'
                        self.started = datetime.now(timezone.utc).isoformat()
                        return
            except (OSError, URLError):
                pass
            time.sleep(.2)
        if self.child.poll() is None:
            self.shutdown_file.touch()
            raise ValueError('启动超过 60 秒；已请求安全停止。请查看日志，停止完成前不会启动第二个实例。')
        raise ValueError('工作台启动失败：\n' + tail(self.log_path)[-2500:])

    def stop(self):
        self.phase = 'stopping'
        if self.child and self.child.poll() is None:
            self.shutdown_file.touch()
            # Never terminate an upload or scheduler request to meet a timeout.
            self.child.wait()
        self.child = None
        self.shutdown_file.unlink(missing_ok=True)
        self.phase = 'stopped'


def serve(home):
    from backend.platform_runtime import exclusive_lock
    with exclusive_lock(home / 'controller' / 'instance.lock') as acquired:
        if not acquired:
            return 0
        controller = Controller(home)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, status, value, content_type='application/json; charset=utf-8'):
                data = value.encode('utf-8') if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(data)

            def trusted_host(self):
                return self.headers.get('Host') == '127.0.0.1:' + str(self.server.server_port)

            def do_GET(self):
                if not self.trusted_host():
                    return self.send(403, {'error': '仅允许本机控制入口。'})
                if self.path == '/status':
                    return self.send(200, controller.state())
                if self.path == '/logs':
                    return self.send(200, {'text': tail(controller.log_path)})
                if self.path == '/':
                    html = (APP_ROOT / 'scripts/workbench-control.html').read_text(encoding='utf-8')
                    html=html.replace('打开工作台.command','First Principles Pilot 应用图标')
                    return self.send(200, html.replace('__CONTROL_TOKEN__', controller.token), 'text/html; charset=utf-8')
                return self.send(404, {'error': '没有此入口。'})

            def do_POST(self):
                origin = self.headers.get('Origin')
                if not self.trusted_host() or origin not in (None, 'http://127.0.0.1:' + str(self.server.server_port)):
                    return self.send(403, {'error': '仅允许本机控制入口。'})
                if not hmac.compare_digest(self.headers.get('X-FPilot-Control', ''), controller.token):
                    return self.send(403, {'error': '控制凭据无效，请重新打开控制页。'})
                try:
                    controller.request(self.path.removeprefix('/'))
                    return self.send(202, controller.state())
                except (ValueError, sqlite3.Error, OSError) as exc:
                    return self.send(409, {'error': str(exc)})

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        controller.server = server
        identity = home / 'controller' / 'identity.json'
        atomic_json(identity, {'schema': 'fpilot.installed-controller.v1', 'pid': os.getpid(),
            'root': str(APP_ROOT), 'url': 'http://127.0.0.1:' + str(server.server_port), 'token': controller.token})
        if os.name != 'nt':
            identity.chmod(0o600)
        try:
            server.serve_forever(poll_interval=.2)
        finally:
            controller.stop()
            server.server_close()
            identity.unlink(missing_ok=True)
    return 0


def existing(home):
    from backend.platform_runtime import process_alive
    value = read_json(home / 'controller' / 'identity.json')
    if value.get('schema') != 'fpilot.installed-controller.v1' or not process_alive(value.get('pid')):
        return None
    try:
        with urlopen(value['url'] + '/status', timeout=2) as response:
            value['state'] = json.load(response)
        return value
    except (OSError, URLError, ValueError):
        raise ValueError('已有控制中心尚未响应，请稍后重试；不会启动重复进程。')


def action(value, name):
    request = Request(value['url'] + '/' + name, data=b'', headers={'X-FPilot-Control': value['token']}, method='POST')
    try:
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    except URLError as exc:
        if hasattr(exc, 'read'):
            raise ValueError(json.loads(exc.read()).get('error', str(exc))) from exc
        raise


def main(argv=None):
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('org.fpilot.workbench')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--can-update', action='store_true')
    parser.add_argument('--diagnostics', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    home = user_home()
    (home / 'controller').mkdir(parents=True, exist_ok=True)
    if args.serve:
        return serve(home)
    if args.diagnostics:
        os.environ['FPILOT_CONFIG'] = str(home / 'fpilot.local.json')
        from scripts.collect_diagnostics import collect
        target = home / 'release-diagnostics.json'
        atomic_json(target, collect())
        if not args.no_browser:
            webbrowser.open(target.as_uri())
        return 0
    value = existing(home)
    if args.can_update:
        # Require explicit quit before update: even a stopped controller keeps
        # its Python executable and application files open on Windows.
        return 1 if value else 0
    if args.stop:
        if value:
            action(value, 'quit')
            deadline = time.monotonic() + 60
            while (home / 'controller' / 'identity.json').exists() and time.monotonic() < deadline:
                time.sleep(.2)
            return 1 if (home / 'controller' / 'identity.json').exists() else 0
        return 0
    if not value:
        with (home / 'controller' / 'launcher.log').open('ab') as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'], cwd=APP_ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **hidden_options())
        deadline = time.monotonic() + 15
        while not value and time.monotonic() < deadline:
            time.sleep(.2)
            value = existing(home)
        if not value:
            raise ValueError('控制中心未能启动，请查看 ' + str(home / 'controller/launcher.log'))
    if value.get('root') != str(APP_ROOT):
        if not args.no_browser:
            webbrowser.open(value['url'])
        raise ValueError('另一个安装目录的工作台仍在运行，请先退出它的控制中心。')
    if not value['state']['busy'] and value['state']['phase'] != 'running':
        action(value, 'start')
    deadline = time.monotonic() + 65
    while (value['state']['phase'] not in ('running', 'error') or value['state']['busy']) and time.monotonic() < deadline:
        time.sleep(.2)
        value = existing(home)
    if not args.no_browser:
        webbrowser.open(value['state']['url'] if value['state']['phase'] == 'running' else value['url'])
    return 0 if value['state']['phase'] == 'running' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        if sys.stderr:
            print('无法启动工作台：' + str(exc), file=sys.stderr)
        if os.name == 'nt' and not sys.stderr:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'First Principles Pilot', 0x10)
        raise SystemExit(1)
