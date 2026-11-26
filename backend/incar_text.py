"""Lossless text overlays using the same parser as the remote input audit."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'resources/skills/calc-review/scripts/input_audit.py'
_spec = importlib.util.spec_from_file_location('fpilot_incar_text', _path)
_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parser)
native_spacing = _parser.native_spacing
conditional_files = _parser.conditional_files
parse = _parser.parse_incar
statements = _parser.incar_statements
remove = _parser.remove_incar_tags
merge = _parser.merge_incar_text
MAX_BYTES = 262144
BOOLS = {'LDAU','LWAVE','LCHARG','LASPH','LSORBIT','LNONCOLLINEAR','GGA_COMPAT','LORBMOM',
         'LCALCPOL','LWANNIER90','LWANNIER90_RUN','LWRITE_MMN_AMN'}


def external_update_issue(tags):
    if _parser._tag_int(tags, 'ICHARG') == 5:
        return 'ICHARG=5 需要外部程序逐步提供 GAMMA 或 vaspgamma.h5 并通过 vasp.lock 同步；当前提交方式未接入该程序'


def complete(layer):
    return layer.get('incarMode', 'overlay') == 'complete'


def complete_values(layer):
    text = layer.get('completeIncarText', '')
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_BYTES or '\0' in text:
        raise ValueError('完整 INCAR 须为非空且不超过 256 KiB 的普通文本')
    tags, _, malformed = parse(text)
    values = {tag: semantic(tag, value) for tag, value in tags.items()}
    if str(values.get('GGA', '')).upper() in ('PE', 'PS'):
        values['XC_FUNCTIONAL'] = 'PBE' if values['GGA'].upper() == 'PE' else 'PBEsol'
    return text, values, malformed


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


def duplicates(layers):
    """Report overlap for user editing; leave both source drafts untouched."""
    seen, result = {}, []
    for label, fields, text in layers:
        for tag, value in fields.items():
            if value is not None: seen[tag] = (str(value), label)
            else: seen.pop(tag, None)
        for tag, value in parse(text)[0].items():
            previous = seen.get(tag)
            if previous and not previous[1].endswith(' · 文本'):
                result.append({'tag': tag, 'form_value': previous[0], 'form_layer': previous[1],
                               'text_value': value, 'text_layer': label})
            seen[tag] = (value, label+' · 文本')
    return result


def replace(text, tag, value):
    """Update a scan's cloned manual assignment; preserve the original draft."""
    return merge(text, tag+' = '+value+'\n')
