"""Apply selective dynamics to node-local POSCAR bytes without moving sites."""
import re


def apply(text, flags):
    lines = text.splitlines(keepends=True)
    count = sum(map(int, lines[6].split()))
    if not isinstance(flags, str) or len(flags) != 3 * count or not re.fullmatch('[TF]+', flags):
        raise ValueError('每个原子需要三个 T/F 约束。')
    selective = lines[7].strip().lower().startswith('s')
    if not selective:
        lines.insert(7, 'Selective dynamics' + ('\r\n' if lines[6].endswith('\r\n') else '\n'))
    for index in range(count):
        line = lines[9 + index]
        match = re.match(r'^(\s*\S+\s+\S+\s+\S+)' + (r'\s+[TFtf]\s+[TFtf]\s+[TFtf](?=\s|$)' if selective else ''), line)
        if not match:
            raise ValueError(f'无法定位原子 {index + 1} 的约束。')
        lines[9 + index] = match[1] + ' ' + ' '.join(flags[index*3:index*3+3]) + line[match.end():]
    return ''.join(lines)
