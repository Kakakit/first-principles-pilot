"""Switch a single, explicit VASP launch in the execution copy only.

This bounded adapter does not interpret shell programs. Unknown wrappers,
multiple launches and mutable executable bindings remain explicit choices.
"""
import re
import shlex
from pathlib import PurePosixPath

PROGRAMS = ('vasp_std', 'vasp_ncl')
LAUNCHERS = ('mpirun', 'mpiexec', 'srun')
VALUE_OPTIONS = {'-np', '-n', '--np', '--ntasks', '-machinefile', '--machinefile',
                 '-hostfile', '--hostfile', '-host', '--host', '--hosts',
                 '--mpi', '--bind-to', '--map-by', '--cpu-bind', '--export'}
FLAG_OPTIONS = {'--oversubscribe', '--exclusive', '--overlap', '--allow-run-as-root'}
WORD = re.compile(r'''(?:[^\s'"\\;&|<>`]+|'[^']*'|"(?:\\.|[^"\\])*"|\\.)+''')
VARIABLE = re.compile(r'\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})')
PATH = re.compile(r'(?:[A-Za-z0-9_./+~-]|\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\})+')


def words(line):
    """Decode simple words while retaining exact spans for a minimal edit."""
    try: shlex.split(line, comments=True)
    except ValueError: return []
    result = []
    for match in WORD.finditer(line):
        if match[0].startswith('#'): break
        try: value = shlex.split(match[0])
        except ValueError: return []
        if len(value) != 1: return []
        result.append((value[0], match.start(), match.end()))
    return result


def inspect(text):
    lines = text.splitlines(keepends=True)
    assignments = {}
    for i, line in enumerate(lines):
        tokens = words(line)
        if tokens and tokens[0][0] == 'export': tokens = tokens[1:]
        if len(tokens) != 1: continue
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)=(.+)', tokens[0][0])
        if match and PATH.fullmatch(match[2]):
            assignments.setdefault(match[1], []).append((match[2], i, tokens[0]))

    def resolve(value, i):
        variable = VARIABLE.fullmatch(value)
        if not variable: return value, None
        name = variable[1] or variable[2]
        entries = assignments.get(name, [])
        # Count all writes, including unrecognized/conditional assignments.
        writes = sum(bool(re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'=', l))
                     for l in lines if not l.lstrip().startswith('#'))
        if len(entries) != 1 or writes != 1 or entries[0][1] >= i: return '', None
        path, assigned, token = entries[0]
        # A later source/eval may override the binding we would otherwise edit.
        if any(re.match(r'\s*(?:source|\.|eval)\s', l) for l in lines[assigned+1:i]): return '', None
        return path, (name, assigned, token)

    launches, uncertain = [], False
    for i, line in enumerate(lines):
        tokens = words(line)
        if not tokens:
            if not line.lstrip().startswith('#') and re.search(r'\bvasp_(std|ncl)\b',line): uncertain = True
            continue
        if tokens[0][0] == 'export' or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[0][0]): continue
        if tokens[0][0] == 'exec': tokens = tokens[1:]
        if not tokens: continue
        command, _ = resolve(tokens[0][0], i)
        launcher = PurePosixPath(command).name in LAUNCHERS
        start = 1 if launcher else 0
        # Shell compound commands are outside this adapter; comments are fine.
        prefix = line[:tokens[-1][2]]
        complex_line = any(v in prefix for v in (';', '|', '&', '`', '$(', '\\\n'))
        args = []
        for token in tokens[start:]:
            if re.search(r'[<>]', line[(args[-1][2] if args else tokens[0][2]):token[1]]): break
            args.append(token)
        if launcher:
            n = 0
            while n < len(args) and args[n][0].startswith('-'):
                option, sep, _ = args[n][0].partition('=')
                if option in FLAG_OPTIONS: n += 1
                elif option in VALUE_OPTIONS: n += 1 if sep else 2
                else: break
            args = args[n:]
        else: args = tokens[:1]
        if args:
            path, binding = resolve(args[0][0], i)
            program = PurePosixPath(path).name
            if program in PROGRAMS and PATH.fullmatch(path):
                if complex_line or launcher and len(args) != 1:
                    uncertain = True
                else:
                    launches.append(dict(line=i, token=args[0], path=path, program=program, binding=binding))
                continue
        # Do not silently miss a launch inside a wrapper or shell branch.
        safe_data = tokens[0][0] in ('echo', 'printf', 'test', '[', 'export') or '=' in tokens[0][0]
        named = re.search(r'(?<![A-Za-z0-9_])vasp_(?:std|ncl)(?![A-Za-z0-9_])', line)
        if not safe_data and (named or any(PurePosixPath(resolve(t[0], i)[0]).name in PROGRAMS for t in tokens)): uncertain = True
        if safe_data and '$(' in line and named: uncertain = True
        if launcher: uncertain = True
    if uncertain or len(launches) != 1:
        return {'reason': '程序启动命令不唯一或包含未支持的包装/动态赋值，无法自动切换；请明确程序命令后保存副本'}
    return launches[0]


def capabilities(text, declared):
    original = 'vasp_std' if declared == 'gpu_vasp' else declared
    if original not in PROGRAMS: return []
    result = inspect(text)
    return list(PROGRAMS) if result.get('program') == original else []


def adapt(text, target):
    result = inspect(text)
    if target not in PROGRAMS or result.get('program') == target: return text, None, []
    if 'reason' in result: return text, None, [result['reason']]
    lines = text.splitlines(keepends=True)
    path, binding = result['path'], result['binding']
    target_path = str(PurePosixPath(path).with_name(target))
    if binding:
        _, line, token = binding
    else:
        line, token = result['line'], result['token']
    value, begin, end = token
    raw = lines[line][begin:end]
    # Exact basename in the known executable word/assignment, preserving quotes.
    at = raw.rfind(result['program'])
    replacement = raw[:at] + target + raw[at+len(result['program']):]
    guard_word = lines[result['line']][result['token'][1]:result['token'][2]] if binding else replacement
    if '/' not in path:
        variable = '_FPILOT_VASP_VARIANT_EXECUTABLE'
        if variable in text: return text, None, ['脚本已使用适配器保留变量，无法自动切换；请保存明确的程序副本']
        guard = (variable + '=$(command -v ' + shlex.quote(path) + ') || exit 127\n'
                 'case "$' + variable + '" in /*/' + result['program'] + ') ;; *) echo "Cannot resolve VASP executable directory" >&2; exit 127 ;; esac\n'
                 + variable + '="${' + variable + '%/*}/' + target + '"\n')
        replacement = ('"$' + variable + '"') if not binding else raw[:raw.index('=')+1] + '"$' + variable + '"'
        guard_word = ('"$' + variable + '"')
    else: guard = ''
    lines[line] = lines[line][:begin] + replacement + lines[line][end:]
    guard += 'if ! test -x ' + guard_word + '; then echo "Required ' + target + ' executable is missing or not executable" >&2; exit 127; fi\n'
    # For PATH bindings, resolve before assigning. Otherwise check after env setup.
    insertion = line if binding and '/' not in path else result['line']
    lines.insert(insertion, guard)
    return ''.join(lines), {'source_program': result['program'], 'target_program': target,
                           'source_command': path, 'target_command': target_path,
                           'strategy': 'same_directory', 'runtime_executable_check': True}, []
