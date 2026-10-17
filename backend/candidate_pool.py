"""Shared full-node candidate contracts for chain and scan dispatch."""
import hashlib
import json
from contextlib import closing

from . import servers


def capacity_wait(rendered):
    blockers = rendered.get('blockers') or []
    # Scheduler-idle nodes can still carry load after a job finishes. Keep
    # waiting for a clean live observation; never submit through this gate.
    if rendered.get('node_status') not in ('BUSY', 'FULLY_IDLE', 'PARTIAL', 'SUSPECT_LOAD'):
        return False
    if 'capacity_wait' in rendered:
        return rendered['capacity_wait'] is True
    prefixes = servers.selector.CAPACITY_BLOCKERS
    return bool(blockers) and all(str(item).startswith(prefixes) for item in blockers)


def validate(candidates):
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 64:
        raise ValueError('请选择 1–64 个候选计算节点')
    checked = []
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get('node'), str) or not item['node']:
            raise ValueError('候选计算节点无效')
        cores = item.get('cores')
        reason = item.get('reason', '')
        if type(cores) is not int or not 1 <= cores <= 100000:
            raise ValueError(item['node']+' 的总核数须为正整数')
        if not isinstance(reason, str) or len(reason) > 2000:
            raise ValueError('候选节点资源理由无效')
        checked.append({'node': item['node'], 'cores': cores, 'reason': reason})
    if len({c['node'] for c in checked}) != len(checked):
        raise ValueError('候选计算节点不可重复')
    return checked


def previews(config, probe, candidates):
    return {c['node']: servers.preview(config, probe['id'], c['node'], c['cores'], c['reason'])
            for c in candidates}


def approve(candidates, rendered, reviewed):
    if not isinstance(reviewed, dict) or set(reviewed) != {c['node'] for c in candidates}:
        raise ValueError('候选节点预览不完整，请重新准备提交')
    checked = []
    for c in candidates:
        value = rendered[c['node']]
        previous = reviewed[c['node']]
        if (not isinstance(previous, dict) or
                not (value['resource_ready'] or capacity_wait(value)) or
                not value.get('script') or not value.get('profile') or
                value['script'] != previous.get('script') or value['profile'] != previous.get('profile')):
            raise ValueError('节点资源或脚本预览已变化：'+c['node'])
        checked.append({**c, 'profile': value['profile'],
                        'script_sha256': hashlib.sha256(value['script'].encode()).hexdigest()})
    return checked


def occupied_nodes(alias,exclude=()):
    """Scheduler snapshots may lag a just-accepted job; keep its local reservation."""
    from . import execution as ex, execution_control as control, recycle
    with closing(ex.db()) as db:
        rows=db.execute('SELECT body FROM attempts WHERE state IN ('+','.join('?' for _ in control.ACTIVE)+')',control.ACTIVE).fetchall()
    return {r.get('node') for r in (json.loads(row[0]) for row in rows)
            if r['id'] not in exclude and r.get('config',{}).get('alias')==alias
            and not r.get('quota_released') and not recycle.inactive('attempts',r['id'],r)}-{None,''}


def select(config, probe, candidates, occupied=None, render=None):
    if occupied is None:occupied=occupied_nodes(config.get('alias'))
    waiting, invalid = [], []
    # Queue mode may use busy nodes, but still spreads new requests over the
    # approved pool before queuing another job behind a local in-flight one.
    for c in sorted(candidates,key=lambda c:c['node'] in occupied):
        try:
            value = (render or servers.preview)(config, probe['id'], c['node'], c['cores'], c['reason'])
        except ValueError as exc:
            invalid.append(c['node']+'：'+str(exc))
            continue
        if (value.get('profile', {}).get('resource_mode') == 'gpu' or c.get('profile', {}).get('resource_mode') == 'gpu') and (
                value.get('profile') != c['profile'] or not value.get('script') or
                hashlib.sha256(value['script'].encode()).hexdigest() != c['script_sha256']):
            invalid.append(c['node']+'：资源或脚本已偏离授权预览')
            continue
        # These contracts use one whole node per job. Local receipts also hold
        # the node while the scheduler's next capacity snapshot catches up.
        if config.get('resource_policy')!='scheduler_queue' and (c['node'] in occupied or capacity_wait(value)):
            gpu_detail = '；'.join(str(b) for b in value.get('blockers', []) if str(b).startswith(('GPU ', 'GPU node')))
            waiting.append(c['node'] + ('（'+gpu_detail+'）' if gpu_detail else '（调度器空闲但负载未恢复，等待复检）'
                                       if value.get('node_status') == 'SUSPECT_LOAD' else ''))
            continue
        if not value['resource_ready']:
            detail = '；'.join(map(str, value.get('blockers') or [])) or '资源诊断未通过'
            invalid.append(c['node']+'：资源检查未通过（'+str(value.get('node_status') or '未知状态')+'）：'+detail)
            continue
        if (value['profile'] != c['profile'] or
                hashlib.sha256(value['script'].encode()).hexdigest() != c['script_sha256']):
            invalid.append(c['node']+'：资源或脚本已偏离授权预览')
            continue
        return c, ''
    if waiting:
        message = '候选节点暂时占用：'+'、'.join(waiting)
        if invalid: message += '；其他节点不可用：'+'；'.join(invalid)
        return None, message
    raise ValueError('候选节点均不可用：'+'；'.join(invalid))
