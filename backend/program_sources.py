"""Pin upstream attempt files and stage verified copies on the same host."""
import re
import shlex
from pathlib import PurePosixPath

from . import branch_bindings, execution as ex, recycle


def resolve(project, node_id, alias, draft, document):
    from .program_tasks import filename
    references = draft.get('sources', [])
    if not isinstance(references,list) or len(references)+len(document['files'])>64:
        raise ValueError('文本和上游输入合计最多 64 个')
    parents = {e['source'] for e in project['edges'] if e['target']==node_id}
    used = set(document['files']) | set(document['outputs'])
    selected = []
    commands = ['set -e']
    for ref in references:
        if not isinstance(ref,dict) or not isinstance(ref.get('source_attempt_id'),str):
            raise ValueError('上游运行引用无效')
        source = ex.get(ref['source_attempt_id'])
        owner = branch_bindings.owner(source)
        if (source.get('project_id')!=project['id'] or owner not in parents or ref.get('node_id')!=owner or owner==node_id
                or source.get('state')!='COMPLETED' or source['config']['alias']!=alias):
            raise ValueError('请选择同项目、同服务器、已连接上游节点的一次已完成运行')
        recycle.assert_available('attempts',source['id'],source)
        ex.assert_recovery_target(source)
        name, target = filename(ref.get('file')), filename(ref.get('name'))
        if source.get('kind')=='program' and name not in source.get('expected_outputs',[]):
            raise ValueError('自定义程序来源须选择该次运行声明的输出')
        if target in used:
            raise ValueError('输入或输出文件重名：'+target)
        used.add(target)
        root = ex.attempt_path(source)['shell']
        if not root.startswith('/'):
            raise ValueError('上游运行缺少已核对的绝对路径')
        path = str(PurePosixPath(root)/name)
        quoted = shlex.quote(path)
        commands.extend(['test -f '+quoted,'test ! -L '+quoted,'sha256sum < '+quoted])
        label = next(n['data'].get('label',owner) for n in project['nodes'] if n['id']==owner)
        selected.append(dict(source_attempt_id=source['id'],node_id=owner,node_label=label,
                             bundle_id=source.get('bundle_id'),file=name,name=target,path=path))
    if selected:
        result = ex.ssh(alias,'\n'.join(commands),timeout=150,mutating=False)
        hashes = re.findall(r'^([0-9a-f]{64})  -$',result.get('stdout',''),re.M)
        if result.get('exit_code')!=0 or len(hashes)!=len(selected):
            raise ValueError('上游文件缺失、不可读取或已成为符号链接，请核对原运行目录')
        for ref,digest in zip(selected,hashes):
            ref['sha256']=digest
    return selected


def copy_commands(refs):
    commands = []
    for ref in refs:
        source = shlex.quote(ref['path'])
        target = shlex.quote('./'+ref['name'])
        commands.extend(['test -f '+source,'test ! -L '+source,'cp -- '+source+' '+target])
    return ''.join(line+'\n' for line in commands)


def candidates(project_id,node_id,alias):
    return [{k:r[k] for k in ('id','created_at','kind','expected_outputs') if k in r}
            for r in ex.history(project_id,limit=None) if branch_bindings.owner(r)==node_id
            and r['state']=='COMPLETED' and r['config']['alias']==alias]


def editable_text(key,project_id,name):
    import base64
    from .program_tasks import filename
    source=ex.get(key)
    if source.get('project_id')!=project_id or source['state']!='COMPLETED':
        raise ValueError('请选择当前项目已完成的运行')
    if source.get('kind')=='program' and name not in source.get('expected_outputs',[]):
        raise ValueError('请选择已声明的输出')
    recycle.assert_available('attempts',key,source)
    ex.assert_recovery_target(source)
    path=shlex.quote(str(PurePosixPath(ex.attempt_path(source)['shell'])/filename(name)))
    limit=2*1024*1024
    command=('set -e\ntest -f '+path+'\ntest ! -L '+path+'\nsize=$(wc -c < '+path+')\ntest "$size" -le '+str(limit)
             +'\nprintf "FPILOT_TEXT=%d\\n" "$size"\nhead -c '+str(limit+1)+' '+path+' | base64')
    resp=ex.ssh(source['config']['alias'],command,timeout=60,stdout_limit=3*1024*1024,mutating=False)
    match=re.search(r'^FPILOT_TEXT=(\d+)\n([A-Za-z0-9+/=\s]*)\Z',resp.get('stdout',''),re.M)
    if resp.get('exit_code')!=0 or not match:
        raise ValueError('只能复制不超过 2 MiB 的普通 UTF-8 文本文件')
    raw=base64.b64decode(''.join(match[2].split()),validate=True)
    if len(raw)!=int(match[1]) or len(raw)>limit or b'\0' in raw:
        raise ValueError('文件已变化或含有二进制数据')
    return {'text':raw.decode('utf-8')}
