"""Bundled user scripts with immutable source hashes and explicit host binding."""
import hashlib
from pathlib import Path
from . import servers

SOURCE_ROOT = servers.RESOURCES / 'templates'
# Pins are the exact files reviewed on 2026-09-19. A changed source needs review.
SOURCES = [
    ('jutai-1', 'submit_vasp_6.5.1_verified.pbs', 'bd0156874758ab8fff530b7c1321a4d8a0a72fde2eaa5d89622155da05c841ba', 'vasp_std', 'SMOKE_CONFIRMED · 24675.node01 / C / exit 0 / OUTCAR timing', True, 'CPU PBS；资源按本次诊断映射，正文保持原稿'),
    ('jutai-2', 'submit_vasp_6.5.1_verified.pbs', '8bee1c597c716bdc2be83a6010f1ff21fb9369cdb216612d61ed4f9feeb2b1b0', 'vasp_std', 'PROJECT_EVIDENCE · 对应项目 OUTCAR timing', False, '可导入使用；主机诊断前补全运行时路径'),
    ('gengzi-187', 'submit_vasp_6.2.1_verified.slurm', 'b22ecb10621df02f29f54b0f7521050e8d27792539680198139eb87b10892aae', 'vasp_std', '历史作业 22219 / COMPLETED / OUTCAR timing', False, '可导入 Slurm 脚本'),
    ('jutai-3', 'submit_vasp_6.3.2_candidate_gpu.pbs', 'e950ed6225d4312f725144b793cc7795753d30258480d98d7fb2829ae69ab35c', 'gpu_vasp', 'CANDIDATE · 未有该脚本一一对应的作业闭环', False, '可导入 GPU 脚本；运行命令由用户维护'),
    ('jutai-4', 'submit_vasp_6.3.0_verified_gpu.pbs', '01c3228d8f51e0d101dd21b70e13206668cfd49464c2c35649bb2ac6277e591e', 'gpu_vasp', 'PROJECT_EVIDENCE · 原目录配对 OUTCAR timing', False, '可导入 GPU 脚本；运行命令由用户维护'),
]


def catalog():
    result = []
    registered = servers.load_registry()['hosts']
    for alias, filename, sha, program, evidence, supported, reason in SOURCES:
        if alias not in registered:
            continue
        path = SOURCE_ROOT / alias / filename
        try:
            intact = hashlib.sha256(path.read_bytes()).hexdigest() == sha
        except OSError:
            intact = False
        eligible = intact
        result.append(dict(key=alias, alias=alias, name=filename, scheduler='slurm' if filename.endswith('.slurm') else 'pbs',
                           program=program, source=str(path), source_hash=sha, evidence=evidence,
                           executable=eligible, reason='可按用户选择导入；提交前仍需资源和输入检查' if intact else '原稿缺失或 hash 已改变，可手动重新导入', source_intact=intact))
    return result


def source(key):
    entry = next((item for item in catalog() if item['key'] == key), None)
    if not entry or not entry['source_intact']:
        raise ValueError('已核对模板不存在或原稿发生变化；未导入')
    raw = Path(entry['source']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['source_hash']:
        raise ValueError('原稿 hash 已改变；未导入')
    return entry, raw.decode('utf-8')


def import_verified(key):
    from . import job_templates
    entry, text = source(key)
    original = job_templates.import_template(text, entry['alias'], entry['program'], entry['source'], entry['name'])
    return dict(original=original, template=original, adaptation_diff='')


def matches(text, alias):
    """Permit only the reviewed CPU body; resource headers remain separately validated."""
    from .job_templates import code_lines
    try:
        entry, expected = source(alias)
        config = servers.select_profile(alias)
        bound_paths = [
            'source ' + config['environment_script'],
            'MPI_RUN=' + config['mpi_executable'],
            'VASP_STD=' + config['vasp_executable'],
        ]
        return (entry['executable'] and text.splitlines()[0] == expected.splitlines()[0]
                and all(line in code_lines(expected) for line in bound_paths)
                and code_lines(text) == code_lines(expected))
    except (ValueError, OSError, IndexError):
        return False
