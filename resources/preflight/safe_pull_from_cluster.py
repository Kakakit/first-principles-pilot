#!/usr/bin/env python3
"""Safely copy one small regular file from an approved school cluster alias."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


MAX_BYTES = 10 * 1024 * 1024
SCHOOL_HOSTS = {*(f"jutai-{index}" for index in range(1, 6)), "gengzi-187"}
WILDCARDS = set("*?[]{}")


class PullError(RuntimeError):
    pass


def allowed_hosts():
    registered=json.loads(os.environ.get('FPILOT_ALLOWED_HOSTS','[]'))
    if not isinstance(registered,list) or any(not isinstance(alias,str) or not __import__('re').fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',alias) for alias in registered):
        raise PullError('登记的 SSH 别名无效')
    return SCHOOL_HOSTS|set(registered)


SMALL_READER = r'''
import base64,hashlib,json,os,stat,sys
for item in json.loads(sys.argv[1]):
    original=item['remote_path']
    path=os.path.expandvars(original)
    if item.get('fallback_path') and (not os.path.exists(path) or os.stat(path,follow_symlinks=False).st_size==0):path=os.path.expandvars(item['fallback_path'])
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(descriptor,'rb') as source:
        before=os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>10485760:
            raise ValueError('Only a known-size regular file <= 10 MiB may be pulled')
        raw=source.read(10485761)
        after=os.fstat(source.fileno())
    if len(raw)!=before.st_size or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('Source changed during controlled transfer')
    if len(raw)>10485760:raise ValueError('Transfer exceeds 10 MiB')
    print('FPILOT_SAFE_FILE='+json.dumps({'remote_path':original,'resolved_path':path,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'content':base64.b64encode(raw).decode()}))
'''


def prepare_batch(files, interpreter, companion=''):
    if not isinstance(files,list) or len(files)>128: raise PullError('明确文件清单无效')
    targets={}
    for item in files:
        remote=item['remote_path']
        if not remote or any(c in remote for c in '\x00\n') or any(c in remote for c in WILDCARDS):
            raise PullError('必须指定单个普通文件，不允许目录或通配符')
        fallback=item.get('fallback_path','')
        if fallback and (any(c in fallback for c in '\x00\n') or any(c in fallback for c in WILDCARDS)):
            raise PullError('备用来源必须是明确普通文件')
        targets[remote]=destination_path(remote,item['destination'])
    if not interpreter.startswith('/') or '\n' in interpreter: raise PullError('必须使用登记解释器')
    command='set -e\n'+companion+'\n'+shlex.quote(interpreter)+' -c '+shlex.quote(SMALL_READER)+' '+shlex.quote(json.dumps(files))+'\n'
    return command,targets


def publish_batch(host, targets, receipt):
    if receipt.get('exit_code')!=0 or receipt.get('stdout_truncated'):
        raise PullError('会话回传失败：'+receipt.get('stderr','')[-500:])
    copied=[];companion_lines=[]
    for line in receipt['stdout'].splitlines():
        if not line.startswith('FPILOT_SAFE_FILE='): companion_lines.append(line);continue
        item=json.loads(line.split('=',1)[1]);raw=base64.b64decode(item['content']);target=targets.pop(item['remote_path'])
        if len(raw)!=item['bytes'] or len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=item['sha256']:
            raise PullError('受控回传大小或内容回执无效')
        descriptor,name=tempfile.mkstemp(prefix='.'+target.name+'.',suffix='.part',dir=target.parent)
        try:
            with os.fdopen(descriptor,'wb') as handle:handle.write(raw)
            os.link(name,target)
        finally: Path(name).unlink(missing_ok=True)
        copied.append({k:v for k,v in item.items() if k!='content'})
    if targets: raise PullError('受控回传文件回执不完整')
    return {'status':'downloaded','host':host,'files':copied,'companion_stdout':'\n'.join(companion_lines),
            'timing':receipt.get('timing'),'limit_bytes':MAX_BYTES}


def session_pull(host, request):
    if host not in allowed_hosts():raise PullError('不允许的 SSH 别名')
    files=request.get('files',[])
    command,targets=prepare_batch(files,request['interpreter'],request.get('companion_command',''))
    address=os.environ['FPILOT_REMOTE_SOCKET']
    if address.startswith('tcp:127.0.0.1:'):
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);endpoint=('127.0.0.1',int(address.rsplit(':',1)[1]))
    else:
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);endpoint=address
    sock.settimeout(request.get('timeout',180)+65)
    try:
        sock.connect(endpoint);stream=sock.makefile('rwb')
        stream.write(json.dumps({'alias':host,'command':command,'scope':os.environ.get('FPILOT_REMOTE_SCOPE'),
            'token':os.environ.get('FPILOT_REMOTE_TOKEN'),
            'lane':request.get('lane','normal'),'timeout':request.get('timeout',180),
            'stdout_limit':sum(2*MAX_BYTES for _ in files)+1024*1024}).encode()+b'\n');stream.flush()
        receipt=json.loads(stream.readline())
    finally: sock.close()
    return publish_batch(host,targets,receipt)


def remote_stat(host: str, remote_path: str) -> int:
    quoted = shlex.quote(remote_path)
    command = f"LC_ALL=C stat -c '%F\\t%s' -- {quoted}"
    result = subprocess.run(
        ["ssh", host, command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "remote stat failed"
        raise PullError(f"无法确认远端文件类型或大小：{detail}")
    line = result.stdout.strip().splitlines()
    # Some remote GNU stat builds pass the escaped separator through
    # literally (``\\t``) instead of emitting an actual tab.  Accept both
    # forms while retaining the single-line, typed-size contract.
    separator = "\t" if len(line) == 1 and "\t" in line[0] else "\\t"
    if len(line) != 1 or separator not in line[0]:
        raise PullError("远端 stat 输出无法解析；大小未知时不下载。")
    kind, raw_size = line[0].split(separator, 1)
    if kind not in {"regular file", "regular empty file"}:
        raise PullError(f"只允许单个普通文件，远端对象类型为：{kind}")
    try:
        size = int(raw_size)
    except ValueError as exc:
        raise PullError("远端文件大小不是有效整数；大小未知时不下载。") from exc
    if size < 0:
        raise PullError("远端文件大小无效。")
    return size


def destination_path(remote_path: str, destination: str | None) -> Path:
    remote_name = Path(remote_path).name
    if not remote_name or remote_name in {".", ".."}:
        raise PullError("远端路径必须指向一个有文件名的普通文件。")
    if destination is None:
        target = Path.cwd() / remote_name
    else:
        candidate = Path(destination).expanduser()
        target = candidate / remote_name if candidate.is_dir() else candidate
    target = target.resolve()
    if not target.parent.is_dir():
        raise PullError(f"本地目标目录不存在：{target.parent}")
    if target.exists():
        raise PullError(f"为避免覆盖，目标已存在：{target}")
    return target


def pull(host: str, remote_path: str, destination: str | None = None) -> dict[str, object]:
    if host not in allowed_hosts():
        raise PullError(f"不允许的 SSH 别名：{host}")
    if not remote_path or "\x00" in remote_path or "\n" in remote_path:
        raise PullError("远端路径为空或包含非法控制字符。")
    if any(char in remote_path for char in WILDCARDS):
        raise PullError("远端路径不能包含通配符；请指定一个普通文件。")

    size = remote_stat(host, remote_path)
    if size > MAX_BYTES:
        raise PullError(
            f"远端文件为 {size} bytes，超过 10 MiB 限制；请留在服务器处理并只回传小型结果。"
        )
    target = destination_path(remote_path, destination)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source = f"{host}:{shlex.quote(remote_path)}"
        result = subprocess.run(
            ["scp", "--", source, str(temporary)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "scp failed"
            raise PullError(f"回传失败：{detail}")
        if not temporary.is_file() or temporary.is_symlink():
            raise PullError("回传结果不是普通本地文件。")
        local_size = temporary.stat().st_size
        if local_size != size:
            raise PullError(f"大小校验失败：远端 {size} bytes，本地 {local_size} bytes。")
        if local_size > MAX_BYTES:
            raise PullError("本地文件超过 10 MiB；已删除临时文件。")
        os.link(temporary, target)
        temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "status": "downloaded",
        "host": host,
        "remote_path": remote_path,
        "local_path": str(target),
        "bytes": size,
        "limit_bytes": MAX_BYTES,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull one school-cluster file only after a 10 MiB size check."
    )
    parser.add_argument("host", choices=sorted(allowed_hosts()))
    parser.add_argument("remote_path", nargs="?")
    parser.add_argument("destination", nargs="?")
    parser.add_argument('--batch',action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.batch:
            if not os.environ.get('FPILOT_REMOTE_SOCKET'): raise PullError('批量回传需要工作台会话适配')
            receipt=session_pull(args.host,json.load(sys.stdin))
        else:
            receipt = pull(args.host, args.remote_path, args.destination)
    except (PullError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
