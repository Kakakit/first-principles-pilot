"""Read-only adapters for current and historical VASP source bindings."""


def binding(record):
    candidates = []
    for snapshot in (record.get('dependency_snapshot'), record.get('chain_source_snapshot'),
                     (record.get('input_audit') or {}).get('dependency_snapshot')):
        if not snapshot:
            continue
        source = snapshot.get('upstream_attempt_id') or snapshot.get('source_attempt_id')
        if source and snapshot.get('kind', 'dependency') == 'dependency':
            candidates.append({'attempt_id': source, 'files': snapshot.get('files') or {}})
    if len({item['attempt_id'] for item in candidates}) > 1:
        raise ValueError('历史来源记录冲突，请核查实际 SCF 绑定')
    return candidates[0] if candidates else {'attempt_id': None, 'files': {}}


def scf_id(record):
    return binding(record)['attempt_id']
