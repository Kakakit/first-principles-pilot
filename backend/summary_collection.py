"""Durable, serial collection of finished VASP run summaries."""
from contextlib import closing, contextmanager
import logging
import threading
import time

from filelock import FileLock, Timeout
from . import execution as ex, result_overview as overview
from .file_lifecycle import remote_is_cleaned

TABLE = """CREATE TABLE IF NOT EXISTS summary_queue(
    attempt TEXT PRIMARY KEY, failures INTEGER DEFAULT 0, due REAL DEFAULT 0,
    error TEXT DEFAULT '')"""
MAX_FAILURES = 5


def eligible(record):
    return (record.get('state') == 'COMPLETED' and record.get('kind') != 'wannier90'
            and (record.get('evidence') or {}).get('vasp_timing') is True)


def enqueue(connection, record):
    connection.execute(TABLE)
    connection.execute('INSERT OR IGNORE INTO summary_queue(attempt) VALUES(?)', (record['id'],))


def _record(project_id, key):
    record = ex.get(key)
    if record.get('project_id') != project_id or not eligible(record):
        raise ValueError('当前任务不符合 VASP 完成摘要采集条件')
    return record


def _status(connection, record):
    row = connection.execute('SELECT failures,error,due FROM summary_queue WHERE attempt=?',
                             (record['id'],)).fetchone()
    if row:
        failures, error, due = row
        state = 'failed' if failures >= MAX_FAILURES else 'waiting' if failures else 'pending'
        return {'state': state, 'error': error, 'retry_at': due}
    cached = overview.read_cache(record)
    return {'state': 'ready' if cached and cached['row'].get('metrics') else 'missing'}


def status(project_id, key):
    record = _record(project_id, key)
    with closing(ex.db()) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='summary_queue'").fetchone():
            cached = overview.read_cache(record)
            return {'state': 'ready' if cached and cached['row'].get('metrics') else 'missing'}
        return _status(connection, record)


# Only explicit refreshes carry the timestamp of the result the user saw.
REFRESH_TABLE = 'CREATE TABLE IF NOT EXISTS summary_refresh(attempt TEXT PRIMARY KEY, checked_at TEXT NOT NULL)'


def request(project_id, key, retry=False, checked_at=None):
    record = _record(project_id, key)
    with closing(ex.db()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute(TABLE)
        cached = overview.read_cache(record)
        force = bool(checked_at and cached and checked_at == cached['checked_at'])
        if cached and cached['row'].get('metrics') and not force:
            return _status(connection, record)
        if ex.recycle.inactive('attempts', key, record) or remote_is_cleaned('attempts', key):
            raise ValueError('原运行目录已清理或记录已回收，无法补采摘要')
        enqueue(connection, record)
        if force:
            connection.execute(REFRESH_TABLE)
            connection.execute('INSERT OR REPLACE INTO summary_refresh VALUES(?,?)', (key, checked_at))
        if retry:
            connection.execute("UPDATE summary_queue SET failures=0,due=0,error='' WHERE attempt=? AND failures>=?",
                               (key, MAX_FAILURES))
        return _status(connection, record)


def process_one():
    """SQLite registers the transition atomically. The OS lock also covers SSH."""
    lock = FileLock(str(ex.ROOT / 'summary-collection.lock'), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return False
    try:
        with closing(ex.db()) as connection:
            connection.execute(TABLE)
            row = connection.execute('SELECT attempt,failures FROM summary_queue WHERE failures<? AND due<=? '
                                     'ORDER BY due,rowid LIMIT 1', (MAX_FAILURES, time.time())).fetchone()
            if not row:
                return False
            connection.execute(REFRESH_TABLE)
            key, failures = row
            refresh = connection.execute('SELECT checked_at FROM summary_refresh WHERE attempt=?', (key,)).fetchone()
            try:
                record = ex.get(key)
                cached = overview.read_cache(record)
                if (eligible(record) and not ex.recycle.inactive('attempts', key, record)
                        and not remote_is_cleaned('attempts', key)
                        and (not cached or not cached['row'].get('metrics')
                             or (refresh and refresh[0] == cached['checked_at']))):
                    from . import scan_results
                    ex.assert_recovery_target(record)
                    metrics = scan_results._extract(record)
                    connection.execute('BEGIN IMMEDIATE')
                    overview.cache(record, {'metrics': metrics})
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                delay = min(3600, 60 * 2 ** failures)
                connection.execute('UPDATE summary_queue SET failures=failures+1,due=?,error=? WHERE attempt=?',
                                   (time.time() + delay, str(exc)[:300], key))
            else:
                connection.execute('DELETE FROM summary_refresh WHERE attempt=?', (key,))
                connection.execute('DELETE FROM summary_queue WHERE attempt=?', (key,))
            connection.commit()
            return True
    finally:
        lock.release()


@contextmanager
def service():
    stop = threading.Event()
    def collect():
        while not stop.is_set():
            try:
                if process_one():
                    continue
            except Exception:
                logging.getLogger(__name__).exception('Result summary collection interrupted')
            stop.wait(4)
    ex.ROOT.mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=collect, name='vasp-result-summary', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)
