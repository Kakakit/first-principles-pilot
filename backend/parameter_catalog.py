"""One versioned VASP tag catalog shared by input generation and the editor."""
import hashlib
import json
import re
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'resources/vasp-parameter-catalog.json'
CATALOG = json.loads(PATH.read_text(encoding='utf-8'))
REVISION = hashlib.sha256(PATH.read_bytes()).hexdigest()
DEFINITIONS = {row['tag']: row for row in CATALOG['definitions']}
KNOWN = set(CATALOG['known_tags'])
MANAGED = set(CATALOG['managed_tags'])
CONTRACT_LOCKED = set(CATALOG['contract_locked_tags'])
SAFE_VALUE = re.compile(r'[A-Za-z0-9_.,+*/()\- ]{1,200}')
TAG = re.compile(r'[A-Z][A-Z0-9_]*')


def public_catalog():
    return {**CATALOG, 'revision': REVISION, 'managed_tags': sorted(MANAGED)}


def validate_value(tag, value, confirmed_custom=False):
    if not isinstance(tag, str) or not TAG.fullmatch(tag) or tag in MANAGED | CONTRACT_LOCKED:
        raise ValueError('tag 名称无效或由工作台管理')
    if not isinstance(value, str) or not value.strip() or not SAFE_VALUE.fullmatch(value) or any(c in value for c in '\n\r;!#='):
        raise ValueError('值必须是安全的单行 INCAR 文本')
    definition = DEFINITIONS.get(tag)
    if definition:
        kind = definition['type']
        if kind == 'enum' and value not in {choice[0] for choice in definition['choices']}:
            raise ValueError('不在当前强类型支持范围')
        if kind in ('number', 'integer', 'ediffg'):
            from .inputs import number
            n = number(value)
            if kind == 'integer' and not n.is_integer():
                raise ValueError('必须是整数')
            if definition.get('positive') and n <= 0 or 'min' in definition and n < definition['min']:
                raise ValueError('数值范围无效')
            if tag == 'EDIFFG' and n == 0 and value != '0':
                raise ValueError('仅步数停止须显式选择 EDIFFG=0')
        if kind == 'vector3':
            from .inputs import number
            parts = value.split()
            if len(parts) != 3 or all(number(part) == 0 for part in parts):
                raise ValueError('需要三个有限分量且不能为零向量')
            for part in parts: number(part)
        return 'typed'
    if tag in KNOWN:
        return 'official-passthrough'
    if not confirmed_custom:
        raise ValueError('未收录的自定义 tag 需要显式确认')
    return 'unverified-custom'
