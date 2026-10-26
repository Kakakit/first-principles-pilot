"""Spill completed UI receipts to disk; running tasks are never evicted."""
import json
import logging
import re
import time
from .execution_control import write_json


def prune(jobs, folder, limit=32, ttl=3600):
    terminal = sorted(((key, job) for key, job in jobs.items()
                       if job.get('finished_at') is not None), key=lambda pair: pair[1]['finished_at'])
    now = time.time()
    for index, (key, job) in enumerate(terminal):
        if len(terminal)-index <= limit and now-job['finished_at'] < ttl:break
        try:
            write_json(folder/(key+'.json'), job)
        except (OSError, ValueError, TypeError):
            logging.getLogger(__name__).warning('Could not spill completed plot receipt %s', key)
            continue
        jobs.pop(key, None)


def read(folder, key):
    if not re.fullmatch(r'(?:diagnostic-)?[a-f0-9]{32,64}', key):return None
    try:return json.loads((folder/(key+'.json')).read_text())
    except (OSError, ValueError):return None
