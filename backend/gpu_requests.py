"""Imported-script GPU requests, without guessing paths or modifying inputs."""
import json
import re


def hints(text, program, resources=None):
    resources = resources or {}
    devices = []
    masks = re.findall(r'^\s*(?:export\s+)?CUDA_VISIBLE_DEVICES\s*=\s*[\"\']?([0-9]+(?:,[0-9]+)*)[\"\']?\s*$', text, re.M)
    if len(set(masks)) == 1:
        devices = masks[0].split(',')
    gpu = re.search(r'(?:\bgpus=|#SBATCH\s+--gpus(?:-per-node)?(?:=|\s+))(\d+)', text)
    gres = re.search(r'#SBATCH\s+--gres(?:=|\s+)gpu(?::([A-Za-z0-9_.-]+))?:(\d+)\s*$', text, re.M)
    if not (program == 'gpu_vasp' or gpu or gres or masks):
        return None
    ranks = set(re.findall(r'^\s*(?!#).*?\b(?:mpirun|mpiexec|srun)\b[^\n]*?(?:-np|-n|--ntasks)(?:=|\s+)(\d+)\b', text, re.M))
    count = int(gpu[1]) if gpu else int(gres[2]) if gres else len(devices) or None
    rank_count = int(next(iter(ranks))) if len(ranks) == 1 else resources.get('ppn') if not ranks else None
    result = {'mode': 'gpu', 'allocation': 'whole_node', 'mpi_ranks': rank_count, 'gpus': count}
    if devices: result['devices'] = devices
    if gres and gres[1]: result['gpu_type'] = gres[1]
    return result


def decode(value):
    if not value: return {}
    try: value = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc: raise ValueError('GPU 资源请求格式无效') from exc
    if not isinstance(value, dict): raise ValueError('GPU 资源请求须为对象')
    return value


def resolve(config, record=None):
    program = config.get('program') or 'vasp_std'
    supplied = decode(config.get('resource_request'))
    if 'by_program' in supplied:
        choices = supplied['by_program']
        if not isinstance(choices, dict) or set(choices) - {'vasp_std', 'vasp_ncl', 'wannier90'}:
            raise ValueError('按程序配置的 GPU 资源请求无效')
        supplied = choices.get(program, {})
    if not isinstance(supplied, dict): raise ValueError('当前程序的 GPU 资源请求须为对象')
    inferred = record.get('resource_request') if record else None
    if not inferred and supplied.get('mode') != 'gpu': return None
    if inferred and supplied.get('mode') == 'cpu': raise ValueError('GPU 脚本不能使用 CPU 资源模式')
    request = {**(inferred or {}), **supplied, 'mode': 'gpu', 'allocation': 'whole_node'}
    # Device masks and typed GRES are properties of the selected immutable body.
    for key in ('devices', 'gpu_type'):
        if inferred and key in inferred: request[key] = inferred[key]
        else: request.pop(key, None)
    if supplied.get('allocation', 'whole_node') != 'whole_node':
        raise ValueError('GPU 任务当前仅支持整节点占用')
    for key in ('mpi_ranks', 'gpus'):
        value = request.get(key)
        if type(value) is not int or not 1 <= value <= 100000:
            raise ValueError('请填写 GPU 脚本的' + ('MPI 进程数' if key == 'mpi_ranks' else '所需 GPU 数量'))
    return request
