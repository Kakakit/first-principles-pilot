"""Finite electronic-iteration evidence; raw scheduler/file receipts stay intact."""
import json
import math
import re

NUMERIC_FIELDS = ('ediff_value', 'last_delta_e', 'last_delta_eps')


def read_record(raw):
    """Project legacy non-finite numbers as missing evidence without rewriting history."""
    record = json.loads(raw)
    evidence = record.get('evidence')
    if isinstance(evidence, dict):
        invalid = [key for key in NUMERIC_FIELDS
                   if isinstance(evidence.get(key), float) and not math.isfinite(evidence[key])]
        if invalid:
            evidence.update({key: None for key in invalid})
            evidence.update(ediff_reached=False, numeric_error=_numeric_error(invalid))
    return record


def _numeric_error(fields):
    return '电子迭代数值无效：' + '、'.join(fields) + '；保留原始回执，不能据此确认收敛'


def electronic(out):
    match = re.search(r'^FPILOT_EDIFF_NUMERIC=([^|\s]+)\|(\d+)\|([^|\s]+)\|([^\s]+)', out, re.M)
    marker = re.search(r'^FPILOT_EDIFF=([1-9]\d*)', out, re.M) is not None
    result = dict(ediff_reached=marker if not match else False,
                  ediff_source='OSZICAR_NUMERIC' if match else 'OUTCAR_MARKER' if marker else None,
                  ediff_value=None, last_electronic_step=None, last_delta_e=None, last_delta_eps=None)
    if not match:
        return result
    result['last_electronic_step'] = int(match.group(2))
    invalid = []
    for key, raw in zip(NUMERIC_FIELDS, (match.group(1), match.group(3), match.group(4))):
        try:
            value = float(raw)
            if not math.isfinite(value) or (key == 'ediff_value' and value <= 0):
                raise ValueError('invalid electronic evidence')
            result[key] = value
        except ValueError:
            invalid.append(key)
    if invalid:
        result['numeric_error'] = _numeric_error(invalid)
    else:
        result['ediff_reached'] = all(abs(result[key]) <= result['ediff_value']
                                      for key in NUMERIC_FIELDS[1:])
    return result
