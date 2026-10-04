"""Persistent completed-source caches, with local availability checks at every use."""
import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
from .storage import ROOT

LOCKS={}
GUARD=threading.Lock()
VERSION=1


def key(kind, identity):
    return hashlib.sha256(json.dumps([VERSION,kind,identity],sort_keys=True,separators=(',',':')).encode()).hexdigest()


@contextlib.contextmanager
def singleflight(kind, identity):
    digest=key(kind,identity)
    with GUARD: lock=LOCKS.setdefault(digest,threading.RLock())
    with lock: yield digest


def read(kind, identity, max_age=None):
    path=ROOT/'remote-cache'/kind/(key(kind,identity)+'.json')
    try:
        value=json.loads(path.read_text())
        if value['version']!=VERSION or value['identity']!=identity: return None
        if max_age is not None and time.time()-value['checked_epoch']>max_age: return None
        return {**value['value'],'cached':True,'checked_epoch':value['checked_epoch']}
    except (OSError,ValueError,KeyError): return None


def write(kind, identity, value):
    path=ROOT/'remote-cache'/kind/(key(kind,identity)+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    data={'version':VERSION,'identity':identity,'checked_epoch':time.time(),'value':value}
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as handle:
        json.dump(data,handle,ensure_ascii=False);handle.flush();os.fsync(handle.fileno());temporary=handle.name
    os.replace(temporary,path)
    return {**value,'cached':False,'checked_epoch':data['checked_epoch']}


def clear():
    count=0
    for path in (ROOT/'remote-cache').glob('*/*.json'):
        path.unlink();count+=1
    return {'cleared':count,'research_files_deleted':False}
