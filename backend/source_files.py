"""Remote file receipts. Metadata verification never claims content identity."""
import re
import shlex

from . import remote_paths


def is_large(name):
    return name in ('CHGCAR', 'WAVECAR') or name.endswith(('.amn', '.mmn', '.eig', '.chk'))


def snapshot(alias, path, names, ssh, mode='lightweight'):
    if mode not in ('lightweight', 'full') or not names:
        raise ValueError('文件核验策略或文件清单无效')
    commands = ['set -e', 'cd ' + remote_paths.shell_path(path)]
    for name in names:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            raise ValueError('来源文件名无效')
        quoted = shlex.quote(name)
        commands += ['test -f '+quoted+' && test ! -L '+quoted+' && test -s '+quoted,
                     "printf 'FPILOT_FILE="+name+"|'",
                     ("printf -- '-|'" if mode == 'lightweight' and is_large(name) else
                      "sha256sum "+quoted+" | awk '{printf $1\"|\"}'"),
                     "stat -c '%s|%y' "+quoted]
    receipt = ssh(alias, '\n'.join(commands)+'\n', timeout=180)
    if receipt['exit_code'] != 0:
        raise ValueError('来源文件不是非空普通文件，或不可读取')
    found = {}
    for match in re.finditer(r'^FPILOT_FILE=([A-Za-z0-9_.-]+)\|(-|[a-f0-9]{64})\|(\d+)\|([^\r\n]+)$', receipt['stdout'], re.M):
        name, sha, size, mtime = match.groups()
        found[name] = {'size_bytes': int(size), 'mtime': mtime,
                       'verification_mode': 'metadata' if sha == '-' else 'content',
                       **({'sha256': sha} if sha != '-' else {})}
    if set(found) != set(names) or any(item['size_bytes'] <= 0 for item in found.values()):
        raise ValueError('来源文件核验回执不完整')
    if any(bool(info.get('sha256')) != (mode == 'full' or not is_large(name)) for name, info in found.items()):
        raise ValueError('来源文件核验回执与所选策略不符')
    return found


def guard(path, info, copied=False):
    commands = ['test -f '+path+' && test ! -L '+path+' && test -s '+path,
                'test "$(stat -c %s '+path+')" = '+str(info['size_bytes'])]
    if not copied and info.get('mtime'):
        commands.append('test "$(stat -c %y '+path+')" = '+shlex.quote(info['mtime']))
    if info.get('sha256'):
        commands.append('test "$(sha256sum '+path+' | awk \'{print $1}\')" = '+shlex.quote(info['sha256']))
    return commands


def structure_header(alias, path, name, ssh):
    """Read only the bounded POSCAR header in CHGCAR; never transfer the grid."""
    if name != 'CHGCAR':
        raise ValueError('结构文件头来源无效')
    command = "set -e\ncd " + remote_paths.shell_path(path) + "\ntest -f CHGCAR && test ! -L CHGCAR && test -s CHGCAR\n"
    command += "awk 'NR==7{for(i=1;i<=NF;i++){if($i!~/^[0-9]+$/)exit 42;n+=$i};if(n<1||n>5000)exit 42} NR==8{end=8+n;if(tolower($1)~/^s/)end++} {print} NR==end{exit}' CHGCAR\n"
    receipt = ssh(alias, command, timeout=45, stdout_limit=262144)
    if receipt['exit_code'] != 0:
        raise ValueError('CHGCAR 结构文件头不可读取或超出审查上限')
    return receipt['stdout'].encode()


def wave_header(alias, path, ssh):
    """Read two direct-access WAVECAR records (120 bytes), not coefficients."""
    command = '''set -e
cd %s
test -f WAVECAR && test ! -L WAVECAR && test -s WAVECAR
printf 'FPILOT_WAVE_FIRST='
od -An -v -t f8 -N 24 WAVECAR
recl=$(od -An -t f8 -N 8 WAVECAR | awk '{printf "%%.0f",$1}')
case "$recl" in ''|*[!0-9]*) exit 42;; esac
test "$recl" -ge 96 && test "$recl" -le 1000000000
printf 'FPILOT_WAVE_SECOND='
od -An -v -t f8 -j "$recl" -N 96 WAVECAR
printf 'FPILOT_WAVE_SIZE='
stat -c %%s WAVECAR
''' % remote_paths.shell_path(path)
    receipt = ssh(alias, command, timeout=45, stdout_limit=8192)
    match = re.search(r'FPILOT_WAVE_FIRST=(.*?)FPILOT_WAVE_SECOND=(.*?)FPILOT_WAVE_SIZE=(\d+)', receipt.get('stdout', ''), re.S)
    if receipt['exit_code'] != 0 or not match:
        raise ValueError('WAVECAR 实际头部不可读取；请选择仅 CHGCAR 或新计算')
    import math
    try:
        first, second = [list(map(float, part.split())) for part in match.groups()[:2]]
        if len(first) != 3 or len(second) != 12 or not all(math.isfinite(v) for v in first + second):
            raise ValueError()
        recl, spin, precision = first
        nk, nb, encut = second[:3]
        if any(v != int(v) or v < 1 for v in (recl, spin, precision, nk, nb)) or spin not in (1, 2) or encut <= 0:
            raise ValueError()
        if int(match.group(3)) < recl * (2 + spin * nk * (nb + 1)):
            raise ValueError()
    except (ValueError, OverflowError):
        raise ValueError('WAVECAR 头部或系数记录不完整；不能通过确认差异绕过')
    return {'record_bytes': int(recl), 'spin_channels': int(spin), 'precision': int(precision),
            'nkpoints': int(nk), 'nbands': int(nb), 'encut_eV': encut,
            'lattice': [second[3+i*3:6+i*3] for i in range(3)]}
