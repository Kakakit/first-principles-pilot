"""Lossless text overlays using the same parser as the remote input audit."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'resources/skills/calc-review/scripts/input_audit.py'
_spec = importlib.util.spec_from_file_location('fpilot_incar_text', _path)
_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parser)
parse = _parser.parse_incar
statements = _parser.incar_statements
remove = _parser.remove_incar_tags
merge = _parser.merge_incar_text
MAX_BYTES = 262144
BOOLS = {'LDAU','LWAVE','LCHARG','LASPH','LSORBIT','LNONCOLLINEAR','GGA_COMPAT','LORBMOM',
         'LCALCPOL','LWANNIER90','LWANNIER90_RUN','LWRITE_MMN_AMN'}


def semantic(tag, value):
    if tag in BOOLS:
        if value.upper() in _parser.TRUE_VALUES: return 'true'
        if value.upper() in _parser.FALSE_VALUES: return 'false'
    return value.strip()


def resolve(layers):
    """(label, form values, optional text), in increasing layer precedence."""
    values, origins, active, documents = {}, {}, {}, []
    for label, fields, text in layers:
        for tag, value in fields.items():
            values[tag], origins[tag] = value, label
            active.pop(tag, None)
            if tag == 'XC_FUNCTIONAL':
                active.pop('GGA', None); values.pop('GGA', None)
        if not isinstance(text, str) or len(text.encode()) > MAX_BYTES or '\0' in text:
            raise ValueError('手动 INCAR 文本须为不超过 256 KiB 的普通文本')
        tags, _, malformed = parse(text)
        if malformed: raise ValueError('INCAR 文本的引号、参数块或赋值未闭合：行 '+','.join(map(str,malformed)))
        documents.append(text)
        for tag, value in tags.items():
            values[tag], origins[tag], active[tag] = semantic(tag, value), label+' · 手动文本', len(documents)-1
            if tag == 'GGA' and value.upper() in ('PE','PS'):
                values['XC_FUNCTIONAL'] = 'PBE' if value.upper() == 'PE' else 'PBEsol'
                origins['XC_FUNCTIONAL'] = label+' · 手动文本'
    fragments=[]
    for index, text in enumerate(documents):
        tags = parse(text)[0]
        text = remove(text, {tag for tag in tags if active.get(tag) != index})
        fragments.append(remove(text, {tag for tag in tags if active.get(tag) == index}, keep_last=True))
    return values, origins, active, fragments


def render(tags, fragments):
    text = ''.join(f'{key} = {value}\n' for key,value in tags.items())
    for fragment in fragments:
        if fragment: text = merge(text, fragment)
    return text


def replace(text, tag, value):
    """Update a scan's cloned manual assignment; preserve the original draft."""
    return merge(text, tag+' = '+value+'\n')
