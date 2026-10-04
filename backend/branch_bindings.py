"""Persistent execution ownership; local-structure edges never join branches."""
import json
from .storage import ROOT


def owner(record):
    if record.get('execution_scope'):
        return record['execution_scope']
    if record.get('scan_batch_id'):
        from . import scans
        return scans.get(record['scan_batch_id'])['scan_node_id']
    return record.get('node_id')


def scopes(project, node_id):
    nodes = {n['id']: n for n in project.get('nodes',[])}
    links = []
    for edge in project.get('edges',[]):
        a, b = nodes.get(edge['source']), nodes.get(edge['target'])
        if not a or not b or a['data']['kind'] == 'structure':
            continue
        draft = b['data'].get('sourceDraft') or {}
        if draft.get('mode') == 'self' or draft.get('mode') == 'manual':
            continue
        if b['data']['kind'] == 'scan':
            # The base→scan edge supplies parameters, not remote output.
            for parent in project.get('edges',[]):
                p = nodes.get(parent['source'])
                if parent['target'] == a['id'] and p and p['data']['kind'] != 'structure':
                    links.append((p['id'], b['id']))
        else:
            links.append((a['id'], b['id']))
    group = {node_id}
    while True:
        expanded = group | {n for pair in links if group.intersection(pair) for n in pair}
        if expanded == group:
            return sorted(group)
        group = expanded


def _table(c):
    c.execute('CREATE TABLE IF NOT EXISTS branch_hosts(project_id TEXT NOT NULL, scope TEXT NOT NULL, alias TEXT NOT NULL, state TEXT NOT NULL, attempt_id TEXT NOT NULL, PRIMARY KEY(project_id,scope))')


def inspect(project_id, members, connection=None):
    from . import execution as ex
    from contextlib import closing
    if connection is None:
        with closing(ex.db()) as c:
            _table(c); c.commit()
            return inspect(project_id, members, c)
    c = connection
    _table(c)
    c.execute('CREATE TABLE IF NOT EXISTS legacy_branch_owners(attempt_id TEXT PRIMARY KEY, scope TEXT NOT NULL)')
    assignments=dict(c.execute('SELECT attempt_id,scope FROM legacy_branch_owners'))
    evidence = []
    for row in c.execute('SELECT scope,alias,state,attempt_id FROM branch_hosts WHERE project_id=?', (project_id,)):
        if row[0] in members:
            evidence.append(dict(scope=row[0], alias=row[1], state=row[2], attempt_id=row[3]))
    # Read original records without rewriting history. PREPARED never binds.
    for (body,) in c.execute('SELECT body FROM attempts'):
        rec = json.loads(body)
        if rec.get('project_id') != project_id or rec.get('state') == 'PREPARED':
            continue
        scope = assignments.get(rec['id'],owner(rec))
        if scope in members and (rec.get('job_id') or rec.get('state') in ('DISPATCHING', 'UNKNOWN')):
            evidence.append(dict(scope=scope, alias=rec['config']['alias'], state='BOUND' if rec.get('job_id') else 'RESERVED', attempt_id=rec['id']))
    aliases = sorted({e['alias'] for e in evidence})
    return {'scopes':members, 'alias':aliases[0] if len(aliases)==1 else None,
            'state':'CONFLICT' if len(aliases)>1 else 'BOUND' if any(e['state']=='BOUND' for e in evidence) else 'RESERVED' if aliases else 'UNBOUND',
            'evidence':evidence, 'aliases':aliases}


def check(project, node_id, alias=None, source_contract=None, scope=None):
    members = scopes(project, scope or node_id)
    result = inspect(project['id'], members)
    required = {r['alias'] for r in (source_contract or {}).get('roles', {}).values() if r.get('alias')}
    aliases = set(result['aliases']) | required
    if len(aliases)>1 or alias and aliases and alias not in aliases:
        raise ValueError('分支服务器冲突：'+'、'.join(sorted(aliases | ({alias} if alias else set())))+'；请复制为独立分支或在设置中整理旧分支归属')
    return {**result, 'alias':next(iter(aliases)) if aliases else None}


def reserve(c, record):
    members = record.get('branch_scopes') or [owner(record)]
    members = [m for m in members if m]
    if not members or not record.get('project_id'):return
    from .storage import WorkspaceStore
    from . import execution as ex
    # Current connections can add a conflicting remote dependency after preparation.
    if (ex.ROOT/'index.sqlite3').exists():
        workspace = WorkspaceStore(ex.ROOT).read().get('workspace')
        live = next((p for p in (workspace or {}).get('projects',[]) if p['id']==record['project_id']),None)
        if live:members=sorted(set(members)|set(scopes(live,owner(record))))
    current = inspect(record['project_id'], members, c)
    alias = record['config']['alias']
    if current['aliases'] and current['aliases'] != [alias]:
        raise ValueError('该分支已绑定或预留其他服务器；未派发')
    for scope in members:
        c.execute('INSERT INTO branch_hosts VALUES(?,?,?,?,?) ON CONFLICT(project_id,scope) DO NOTHING',
                  (record['project_id'], scope, alias, 'RESERVED', record['id']))
    record['branch_scopes'] = members


def commit(c, record):
    if not record.get('job_id') or not owner(record) or not record.get('branch_scopes'):
        return
    # A receipt binds the scopes reserved before dispatch. Later canvas edits
    # must not prevent monitoring an already submitted exact job.
    _table(c)
    for scope in record.get('branch_scopes') or [owner(record)]:
        bound = c.execute('SELECT alias FROM branch_hosts WHERE project_id=? AND scope=?',
                          (record['project_id'],scope)).fetchone()
        if not bound:
            c.execute('INSERT INTO branch_hosts VALUES(?,?,?,?,?)',
                      (record['project_id'],scope,record['config']['alias'],'RESERVED',record['id']))
        elif bound[0] != record['config']['alias']:
            raise ValueError('提交回执与分支预留服务器不同')
        c.execute('UPDATE branch_hosts SET state=?,attempt_id=? WHERE project_id=? AND scope=?',
                  ('BOUND', record['id'], record['project_id'], scope))


def resolve_legacy(project, node_id, alias):
    """Explicitly assign conflicting *legacy terminal* runs without editing attempts."""
    from . import execution as ex
    from contextlib import closing
    members=scopes(project,node_id)
    with closing(ex.db()) as c:
        c.execute('BEGIN IMMEDIATE')
        current=inspect(project['id'],members,c)
        if current['state']!='CONFLICT' or alias not in current['aliases']:
            raise ValueError('只能整理已识别的旧记录服务器冲突')
        c.execute('CREATE TABLE IF NOT EXISTS legacy_branch_owners(attempt_id TEXT PRIMARY KEY, scope TEXT NOT NULL)')
        for (body,) in c.execute('SELECT body FROM attempts').fetchall():
            rec=json.loads(body)
            if rec.get('project_id')!=project['id'] or owner(rec) not in members or rec.get('config',{}).get('alias')==alias:continue
            if rec.get('branch_scopes') or rec['state'] not in ('COMPLETED','FAILED'):
                raise ValueError('已有新绑定或尚未结束的任务不能解除；请保留原任务或复制独立分支')
            c.execute('INSERT OR REPLACE INTO legacy_branch_owners VALUES(?,?)',(rec['id'],'legacy:'+owner(rec)+':'+rec['config']['alias']))
        c.commit()
    return inspect(project['id'],members)
