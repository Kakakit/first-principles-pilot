"""Disposable SQLite metadata index; detailed JSON files remain authoritative."""
import json
import os
import sqlite3
from pathlib import Path


def paths(root, folder, filename='analysis.json', project_id=None, attempt_id=None):
    base=Path(root)/folder
    if not base.is_dir():return []
    database=Path(root)/'history-index.sqlite3'
    with sqlite3.connect(database,timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS records (folder TEXT, id TEXT, size INTEGER, mtime INTEGER, project TEXT, attempts TEXT, created TEXT, PRIMARY KEY(folder,id))')
        known={r[0]:r[1:] for r in db.execute('SELECT id,size,mtime FROM records WHERE folder=?',(folder,))}
        seen=set()
        # Stat is cheap; JSON is read only on insertion/change, never for unrelated projects.
        with os.scandir(base) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):continue
                path=Path(entry.path)/filename
                try:
                    if path.is_symlink():continue
                    stamp=path.stat();seen.add(entry.name)
                    if known.get(entry.name)==(stamp.st_size,stamp.st_mtime_ns):continue
                    record=json.loads(path.read_text())
                    if record.get('id')!=entry.name:
                        db.execute('DELETE FROM records WHERE folder=? AND id=?',(folder,entry.name));continue
                    sources=record.get('sources') or []
                    if isinstance(sources,dict):sources=list(sources.values())
                    ids=[s['attempt_id'] for s in sources if isinstance(s,dict) and s.get('attempt_id')]
                    if record.get('attempt_id'):ids.append(record['attempt_id'])
                    db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?)',
                        (folder,entry.name,stamp.st_size,stamp.st_mtime_ns,record.get('project_id'),json.dumps(ids),record.get('created_at','')))
                except (ValueError,OSError,TypeError,KeyError):
                    db.execute('DELETE FROM records WHERE folder=? AND id=?',(folder,entry.name))
        for key in known.keys()-seen:db.execute('DELETE FROM records WHERE folder=? AND id=?',(folder,key))
        query='SELECT id,attempts FROM records WHERE folder=?';values=[folder]
        if project_id is not None:query+=' AND project=?';values.append(project_id)
        query+=' ORDER BY created DESC,id'
        return [base/key/filename for key,ids in db.execute(query,values) if attempt_id is None or attempt_id in json.loads(ids)]
