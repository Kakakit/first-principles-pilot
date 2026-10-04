"""Ordinary local watcher: fixed attempt, 300s interval, no submit capability."""
import json
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.execution import refresh, get, save

key=sys.argv[1]
while True:
    try:
        record=refresh(key)
        if record['state'] in ('COMPLETED','FAILED'):
            if not record.get('notification'):
                message=f"{record['config']['alias']} {record['job_id']}：{record['state']}，科学结论未审核"
                result=subprocess.run(['osascript','-e','on run argv','-e','display notification (item 1 of argv) with title "计算工作台" sound name "Glass"','-e','end run',message],capture_output=True,text=True)
                record=get(key);record['notification']={'returncode':result.returncode,'stderr':result.stderr[:500]};save(record)
            break
    except Exception as exc:
        print(str(exc),flush=True)
    time.sleep(300)
