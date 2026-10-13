"""Serve the prebuilt workbench with an existing Python; no Node is needed."""
import argparse,importlib,json,os,signal,socket,subprocess,sys,time,uuid,webbrowser
from pathlib import Path

APP_ROOT=Path(__file__).resolve().parent


def configuration(path):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).exists() else {}
    if not isinstance(value,dict):raise ValueError('本机配置应为 JSON 对象')
    return value


def doctor():
    names=('fastapi','uvicorn','pymatgen.io.vasp','numpy','scipy','matplotlib','seekpath','jsonschema','filelock')
    missing=[]
    for name in names:
        try:importlib.import_module(name)
        except (ImportError,OSError,ValueError) as exc:missing.append({'module':name,'reason':str(exc)[:300]})
    version_ok=sys.version_info>=(3,9) and (os.name!='nt' or sys.version_info[:2]==(3,11))
    return {'python':sys.executable,'version':sys.version.split()[0],'platform':sys.platform,'architecture':__import__('struct').calcsize('P')*8,
        'missing':missing,'ready':not missing and version_ok and __import__('struct').calcsize('P')==8, 'ssh_client':__import__('shutil').which('ssh')}


def port_available(port):
    with socket.socket() as sock:
        # POSIX listener connections can leave TIME_WAIT after a clean stop.
        # Match Uvicorn's reuse policy; an active listener still rejects bind.
        if os.name!='nt':sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:sock.bind(('127.0.0.1',port))
        except OSError:return False
    return True


def start(args):
    config_path=Path(args.config or APP_ROOT/'fpilot.local.json').expanduser().resolve()
    value=configuration(config_path)
    port=args.port if args.port is not None else value.get('port',8765)
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('端口须为 1024–65535 的整数')
    workspace=Path(args.workspace or value.get('workspace') or APP_ROOT/'workspace-data').expanduser().resolve()
    workspace.mkdir(parents=True,exist_ok=True)
    import tempfile
    with tempfile.TemporaryFile(dir=workspace) as test:test.write(b'fpilot')
    environment={'FPILOT_WORKSPACE':str(workspace),'FPILOT_CONFIG':str(config_path),'FPILOT_PORT':str(port),'PYTHONUTF8':'1'}
    if value.get('safe_pull'):environment['FPILOT_SAFE_PULL']=value['safe_pull']
    for key,name in (('research_root','FPILOT_RESEARCH_ROOT'),('potcar_library','FPILOT_POTCAR_LIBRARY')):
        if value.get(key):
            directory=Path(value[key]).expanduser().resolve()
            if not directory.is_dir():raise ValueError(key+' 目录不存在，请修正 '+str(config_path))
            environment[name]=str(directory)
    os.environ.update(environment)
    report=doctor()
    if args.check:
        print(json.dumps({**report,'workspace':str(workspace),'port':port,'port_available':port_available(port)},ensure_ascii=False,indent=2))
        return 0 if report['ready'] and port_available(port) else 2
    if not report['ready']:
        print(json.dumps(report,ensure_ascii=False,indent=2))
        print('请用当前解释器运行 install-dependencies.cmd；不创建新环境。');return 2
    if not (APP_ROOT/'dist/index.html').is_file():raise ValueError('缺少预构建页面 dist/index.html；开发版先运行 npm run build')
    if not port_available(port):raise ValueError('端口 '+str(port)+' 已占用；在 fpilot.local.json 修改 port 或使用 --port。不会关闭其他程序。')
    from backend.app import app
    from fastapi.staticfiles import StaticFiles
    shutdown_path=Path(args.shutdown_file).resolve() if args.shutdown_file else None
    if shutdown_path:
        from fastapi.responses import JSONResponse, RedirectResponse
        @app.middleware('http')
        async def installed_lifecycle(request,call_next):
            if shutdown_path.exists() and request.method not in ('GET','HEAD','OPTIONS'):
                response=JSONResponse({'detail':'工作台正在安全停止，请完成重启后再操作。'},status_code=503)
            else:response=await call_next(request)
            response.headers['X-FPilot-Instance']=str(os.getpid())
            return response
        @app.get('/_fpilot/control',include_in_schema=False)
        def installed_control():
            return RedirectResponse(os.environ['FPILOT_CONTROL_URL'])
    app.mount('/',StaticFiles(directory=str(APP_ROOT/'dist'),html=True),name='workbench')
    import uvicorn
    worker=None;stop_file=shutdown_path or workspace/('launcher-stop-'+uuid.uuid4().hex)
    env={**os.environ,'FPILOT_STOP_FILE':str(stop_file),'FPILOT_LAUNCHER_PID':str(os.getpid())}
    # Uvicorn re-raises captured signals after shutdown. A Python handler lets
    # finally stop the owned worker before the process exits on SIGTERM.
    old_term=signal.getsignal(signal.SIGTERM)
    def stop_signal(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_signal)
    try:
        if not args.no_worker and value.get('worker_enabled',True):
            worker=subprocess.Popen([sys.executable,'-X','utf8','-m','backend.worker'],cwd=APP_ROOT,env=env,start_new_session=True)
        print('工作台：http://127.0.0.1:'+str(port)+'\n数据目录：'+str(workspace)+'\n按 Ctrl+C 停止。',flush=True)
        if not args.no_browser:
            import threading
            def open_ready():
                from urllib.request import urlopen
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    try:
                        with urlopen('http://127.0.0.1:'+str(port)+'/api/health',timeout=1) as response:
                            if response.status==200:webbrowser.open('http://127.0.0.1:'+str(port));return
                    except OSError:time.sleep(.2)
            threading.Thread(target=open_ready,daemon=True).start()
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port))
        if shutdown_path:
            import threading
            def watch_shutdown():
                from backend.platform_runtime import process_alive
                parent=os.environ.get('FPILOT_LAUNCHER_PID')
                while not server.should_exit:
                    if shutdown_path.exists() or (parent and not process_alive(int(parent))):
                        stop_file.touch();server.should_exit=True;return
                    time.sleep(.2)
            threading.Thread(target=watch_shutdown,daemon=True).start()
        server.run()
    finally:
        if worker is not None:
            stop_file.touch()
            if shutdown_path:
                # The worker finishes its current receipt and exits cooperatively.
                # Installed stop/restart must not force-kill a scheduler request.
                worker.wait()
            else:
                try:worker.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    from backend.platform_runtime import stop_child
                    stop_child(worker,force=True);worker.wait(timeout=5)
            stop_file.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM,old_term)
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config');parser.add_argument('--workspace');parser.add_argument('--port',type=int)
    parser.add_argument('--no-worker',action='store_true');parser.add_argument('--no-browser',action='store_true');parser.add_argument('--check',action='store_true')
    parser.add_argument('--shutdown-file',help=argparse.SUPPRESS)
    try:return start(parser.parse_args())
    except KeyboardInterrupt:return 0
    except (OSError,ValueError) as exc:print('无法启动：'+str(exc),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
