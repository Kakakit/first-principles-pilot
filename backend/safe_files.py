"""Adapter to the existing controlled CLI; never bypass its per-file 10 MiB gate."""
import json
import os
import subprocess
from pathlib import Path
from . import remote_transport
from .platform_runtime import local_python, controlled_pull

PYTHON=local_python()
CONTROLLED=controlled_pull()


def pull_many(alias, files, companion_command='', timeout=180, lane='normal'):
    with remote_transport.operation() as scope:
        environment={**os.environ,'FPILOT_REMOTE_SOCKET':remote_transport.bridge_path(),
                     'FPILOT_REMOTE_SCOPE':scope['id'],'FPILOT_REMOTE_TOKEN':remote_transport.bridge_token(),
                     'FPILOT_ALLOWED_HOSTS':json.dumps([alias])}
        request={'files':files,'companion_command':companion_command,'timeout':timeout,'lane':lane,
                 'interpreter':remote_transport._interpreter(alias)}
        helper=CONTROLLED if alias in {'jutai-1','jutai-2','jutai-3','jutai-4','jutai-5','gengzi-187'} else Path(__file__).resolve().parents[1]/'resources/preflight/safe_pull_from_cluster.py'
        result=subprocess.run([str(PYTHON),str(helper),alias,'--batch'],input=json.dumps(request),
                              text=True,capture_output=True,env=environment,timeout=timeout+65)
        if result.returncode: raise ValueError('受控小文件回传失败：'+result.stderr[-800:])
        return json.loads(result.stdout)
