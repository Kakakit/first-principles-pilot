"""Read-only node usage checks, including recycled immutable contracts."""
import json
import sqlite3
import zipfile
from pathlib import Path

VASP_KINDS = {'relax', 'scf', 'bands', 'dos', 'vasp-wannier'}
STAGE_SEQUENCES = {kind: index for index, kind in enumerate(
    ('structure', 'relax', 'scf', 'bands', 'dos', 'vasp-wannier', 'wannier90', 'scan'), 1)}


def usage(root, project_id, expected_kinds=None):
    root = Path(root)
    blockers = {}
    def add(node_id, reason, kind=None):
        if isinstance(node_id, str) and node_id:
            reasons = blockers.setdefault(node_id, [])
            if reason not in reasons: reasons.append(reason)
            if expected_kinds is not None and kind in STAGE_SEQUENCES:
                expected_kinds.setdefault(node_id, set()).add(kind)
    records = {}
    database = root / 'execution.sqlite3'
    if database.exists():
        with sqlite3.connect('file:' + str(database.resolve()) + '?mode=ro', uri=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('attempts', 'chain_runs', 'scan_batches'):
                records[table] = [json.loads(r[0]) for r in db.execute('SELECT body FROM ' + table)] if table in tables else []
    attempts = {r['id']: r for r in records.get('attempts', [])}

    def references(value, reason, kinds=None):
        kinds = kinds or {}
        if isinstance(value, dict):
            for key, item in value.items():
                # Full graph snapshots include unrelated nodes; they are not usage.
                if key in ('project', 'nodes', 'edges'): continue
                if key in ('node_id', 'parent_id', 'parent_node_id', 'source_node_id',
                           'scan_node_id', 'base_node_id', 'execution_scope'):
                    add(item, reason, kinds.get(item))
                elif key == 'node_ids' and isinstance(item, list):
                    for node_id in item: add(node_id, reason, kinds.get(node_id))
                elif key.endswith('attempt_id') and isinstance(item, str):
                    source = attempts.get(item, {})
                    if source.get('project_id') == project_id:
                        from .calculation_nodes import owner
                        add(owner(source), reason, kinds.get(owner(source)))
                        add(source.get('node_id'), reason, kinds.get(source.get('node_id')))
                else: references(item, reason, kinds)
        elif isinstance(value, list):
            for item in value: references(item, reason, kinds)

    def node_kinds(project):
        return {n['id']: n['data']['kind'] for n in (project or {}).get('nodes', [])}

    for table, label in (('attempts', '已有准备或执行记录'), ('chain_runs', '已被计算链合同引用'),
                         ('scan_batches', '已被扫描批次合同引用')):
        for record in records.get(table, []):
            if record.get('project_id') == project_id: references(record, label, node_kinds(record.get('project')))
    for path in (root / 'inputs').glob('*.zip'):
        try:
            with zipfile.ZipFile(path) as archive:
                project = json.loads(archive.read('project.json'))
                if project.get('id') != project_id: continue
                references(json.loads(archive.read('manifest.json')), '已有冻结输入或被冻结来源引用', node_kinds(project))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise ValueError('冻结输入记录无法核查，请修复记录后再切换类型') from exc
    for path in (root / 'wannier-bundles').glob('*.json'):
        record = json.loads(path.read_text())
        if record.get('project_id', (record.get('project') or {}).get('id')) == project_id:
            references(record, '已被 Wannier 冻结输入引用', node_kinds(record.get('project')))
    return blockers


def status(root, project):
    blockers = usage(root, project['id'])
    return {'project_id': project['id'], 'nodes': {
        node['id']: {'can_switch_scan': node['data']['kind']=='scan' and not blockers.get(node['id']),
                     'can_switch': node['data']['kind'] in VASP_KINDS and not blockers.get(node['id']),
                     'reasons': blockers.get(node['id'], []) if node['data']['kind'] in VASP_KINDS
                     else ['仅 VASP 计算草稿支持类型切换']}
        for node in project['nodes']}}


def assert_changes(root, previous, incoming):
    """Validate only identity edits; ordinary parameter saves need no history scan."""
    old_projects = {p['id']: p for p in previous.get('projects', [])}
    for project in incoming['projects']:
        old = old_projects.get(project['id'])
        if not old: continue
        if old.get('stagePolicy') != project.get('stagePolicy'):
            raise ValueError('已有项目的阶段编号规则不能更改，请新建项目')
        old_nodes = {n['id']: n for n in old['nodes']}
        changed = [n for n in project['nodes'] if n['id'] in old_nodes and
                   old_nodes[n['id']]['data']['kind'] != n['data']['kind']]
        added = [n for n in project['nodes'] if n['id'] not in old_nodes]
        scan_changes = [n for n in project['nodes'] if n['id'] in old_nodes and n['data']['kind']=='scan' and n['data'].get('scanSpec') != old_nodes[n['id']]['data'].get('scanSpec')]
        if scan_changes:
            from .calculation_nodes import kind
            scan_usage=usage(root, project['id'])
            for node in scan_changes:
                before=old_nodes[node['id']]
                if before['data'].get('scanSpec') and not node['data'].get('scanSpec'): raise ValueError('独立迭代设置不能退回旧模式')
                if kind(before,old)!=kind(node,project) and scan_usage.get(node['id']): raise ValueError('迭代已有冻结或执行引用，请复制节点后更改计算类型')
        if not changed and not added: continue
        kinds = {}
        blockers = usage(root, project['id'], kinds)
        for node in added:
            if blockers.get(node['id']) and kinds.get(node['id']) != {node['data']['kind']}:
                raise ValueError('恢复的节点类型与历史合同不符，请使用新节点身份')
        for node in changed:
            before = old_nodes[node['id']]['data']
            if before['kind'] not in VASP_KINDS or node['data']['kind'] not in VASP_KINDS:
                raise ValueError('仅支持 VASP 计算类型之间切换')
            if blockers.get(node['id']):
                raise ValueError('该节点已有冻结或执行引用，请先复制节点再切换类型：' + '；'.join(blockers[node['id']]))
            if not project.get('stagePolicy') and before['stageSequence'] != node['data']['stageSequence']:
                raise ValueError('旧项目类型切换须保留原阶段编号')
