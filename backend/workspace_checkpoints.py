"""Small recovery checkpoints, separate from the latest autosaved draft."""
import hashlib
import json
import re
from datetime import datetime

INTERVAL_SECONDS = 10 * 60
MAX_CHECKPOINTS = 30
MAX_BYTES = 64 * 1024 * 1024


def content_digest(workspace):
    # Layout, the active project and edit timestamps are not scientific edits.
    projects = []
    for project in workspace['projects']:
        value = {k: v for k, v in project.items() if k != 'updatedAt'}
        value['nodes'] = [{k: v for k, v in node.items() if k != 'position'} for node in project['nodes']]
        projects.append(value)
    raw = json.dumps(projects, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def seconds(value):
    return datetime.fromisoformat(value).timestamp()


def seed(db):
    """Index a spaced subset of legacy saves without copying their payloads."""
    if db.execute('SELECT 1 FROM workspace_checkpoints LIMIT 1').fetchone():
        return
    last = None
    kept = 0
    for revision, created_at, reason in db.execute('SELECT revision,created_at,reason FROM revisions ORDER BY revision DESC').fetchall():
        stamp = seconds(created_at)
        if last is None or last - stamp >= INTERVAL_SECONDS or reason != 'autosave':
            db.execute('INSERT INTO workspace_checkpoints VALUES(?,?,?,?,?)',
                       (revision, None, '历史恢复点' if reason == 'autosave' else reason, '', created_at))
            last = stamp
            kept += 1
            if kept >= MAX_CHECKPOINTS:
                break


def remember(db, root, revision, workspace, created_at, reason):
    latest = db.execute('''SELECT c.content_digest,COALESCE(NULLIF(c.created_at,''),r.created_at),r.digest,c.revision FROM workspace_checkpoints c
        JOIN revisions r ON r.revision=c.revision ORDER BY r.revision DESC LIMIT 1''').fetchone()
    if latest and reason == 'autosave':
        if seconds(created_at) - seconds(latest[1]) < INTERVAL_SECONDS:
            return
        try:
            before = latest[0] or content_digest(json.loads((root / 'snapshots' / latest[2]).read_bytes()))
            if latest[0] is None:
                db.execute('UPDATE workspace_checkpoints SET content_digest=? WHERE revision=?', (before, latest[3]))
            if content_digest(workspace) == before:
                return
        except (OSError, ValueError, KeyError):
            pass  # A broken old checkpoint must not block a new valid draft.
    db.execute('INSERT INTO workspace_checkpoints VALUES(?,?,?,?,?)',
               (revision, content_digest(workspace), '自动检查点' if reason == 'autosave' else reason, '', created_at))


def trim(db, root):
    """Called under the workspace lock, in the same transaction as publication."""
    current = db.execute('SELECT r.revision,r.digest FROM current c JOIN revisions r ON c.revision=r.revision').fetchone()
    if not current:
        return
    kept = [current[0]]
    sizes = {current[1]: (root / 'snapshots' / current[1]).stat().st_size}
    checkpoints = db.execute('''SELECT c.revision,r.digest FROM workspace_checkpoints c
        JOIN revisions r ON c.revision=r.revision ORDER BY r.revision DESC''').fetchall()
    count = 0
    for revision, digest in checkpoints:
        try:
            extra = 0 if digest in sizes else (root / 'snapshots' / digest).stat().st_size
        except OSError:
            continue
        if revision != current[0] and (count >= MAX_CHECKPOINTS or sum(sizes.values()) + extra > MAX_BYTES):
            continue
        kept.append(revision)
        sizes[digest] = sizes.get(digest, extra)
        count += 1
    marks = ','.join('?' for _ in kept)
    db.execute('DELETE FROM workspace_checkpoints WHERE revision NOT IN (' + marks + ')', kept)
    db.execute('DELETE FROM revisions WHERE revision NOT IN (' + marks + ')', kept)


def collect(db, root):
    """Only after a successful commit; research originals and outputs are untouched."""
    referenced = {row[0] for row in db.execute('SELECT DISTINCT digest FROM revisions')}
    removed = 0
    for path in (root / 'snapshots').iterdir():
        if re.fullmatch(r'[a-f0-9]{64}', path.name) and path.name not in referenced and path.is_file() and not path.is_symlink():
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    return removed
