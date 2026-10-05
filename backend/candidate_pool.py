"""Shared full-node candidate contracts for chain and scan dispatch."""
import hashlib

from . import servers


def capacity_wait(rendered):
    blockers = rendered.get('blockers') or []
    # Scheduler-idle nodes can still carry load after a job finishes. Keep
    # waiting for a clean live observation; never submit through this gate.
    if rendered.get('node_status') not in ('BUSY', 'FULLY_IDLE', 'PARTIAL', 'SUSPECT_LOAD'):
        return False
    prefixes = ('selected node CPUS_FREE must be greater than zero',
                'selected node STATUS must be FULLY_IDLE or PARTIAL',
                'user override cannot exceed CPUS_FREE')
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


def select(config, probe, candidates, occupied=(), render=None):
    waiting, invalid = [], []
    for c in candidates:
        try:
            value = (render or servers.preview)(config, probe['id'], c['node'], c['cores'], c['reason'])
        except ValueError as exc:
            invalid.append(c['node']+'：'+str(exc))
            continue
        # These contracts use one whole node per job. Local receipts also hold
        # the node while the scheduler's next capacity snapshot catches up.
        if c['node'] in occupied or capacity_wait(value):
            waiting.append(c['node'] + ('（调度器空闲但负载未恢复，等待复检）'
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
