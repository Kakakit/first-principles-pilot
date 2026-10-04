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
