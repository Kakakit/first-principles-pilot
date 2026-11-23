"""Read formatted VASP interface dimensions without transferring matrix bodies."""
import math
import re
import shlex


def probe(seed):
    # The caller selects the working directory. Output is bounded independently
    # of file size. Stream the eigenvalue table and stop at the first bad index.
    header = """NR == 2 {
        if (NF == 3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/)
            print "FPILOT_DIM_" ext "=" $1, $2, $3
        exit
    }"""
    eig = r"""NF {
        if (NF != 3 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ ||
            $3 !~ /^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eEdD][+-]?[0-9]+)?$/) { bad=1; exit }
        if ($2 == k+1 && $1 == 1) {
            if (k && nb && b != nb) { bad=1; exit }
            if (k) nb=b
            k++; b=0
        }
        if (!k || $2 != k || $1 != b+1) { bad=1; exit }
        b++
    }
    END { if (!bad && k && (!nb || nb == b)) print "FPILOT_DIM_eig=" b, k }"""
    return ''.join(f'LC_ALL=C awk -v ext={ext} {shlex.quote(header)} {shlex.quote(seed+"."+ext)}\n'
                   for ext in ('amn', 'mmn')) + f'LC_ALL=C awk {shlex.quote(eig)} {shlex.quote(seed+".eig")}\n'


def receipt(stdout, seed):
    result = {}
    for ext, count in [('amn', 3), ('mmn', 3), ('eig', 2)]:
        rows = re.findall(r'^FPILOT_DIM_' + ext + r'=([0-9 ]+)\r?$', stdout, re.M)
        dims = list(map(int, rows[0].split())) if len(rows) == 1 else []
        if len(dims) != count or min(dims) < 1:
            reason = '能带、k 点索引或能量列无效' if ext == 'eig' else '第二行维度无效'
            raise ValueError(f'{seed}.{ext}：{reason}')
        result[ext] = dims
    return result


def validate(document, contract, dims, seed, *, final=True):
    value = lambda name: contract._first_value(document, name)
    nb, nw = int(value('num_bands')), int(value('num_wann'))
    grid = contract._mp_grid(value('mp_grid'))
    nk = math.prod(grid)
    for ext in ('amn', 'mmn', 'eig'):
        bands, points = dims[ext][:2]
        if (bands, points) != (nb, nk):
            raise ValueError(f'{seed}.{ext} 有 {bands} 条能带、{points} 个 k 点；.win 要求 {nb} 条、{nk} 点')
    projections = dims['amn'][2]
    selection = value('select_projections')
    if final and projections > nw and not selection:
        raise ValueError(f'{seed}.amn 有 {projections} 个投影，请用 select_projections 选择 num_wann={nw} 个')
    if final and selection:
        intervals = []
        for token in re.split(r'[,\s]+', re.sub(r'\s*-\s*', '-', selection.strip())):
            match = re.fullmatch(r'(\d+)(?:-(\d+))?', token)
            if not match:
                raise ValueError('select_projections 的索引或范围无效')
            lo, hi = int(match[1]), int(match[2] or match[1])
            if not 1 <= lo <= hi <= projections:
                raise ValueError(f'select_projections 超出 {seed}.amn 的投影范围 1–{projections}')
            intervals.append((lo, hi))
        intervals.sort()
        if (any(right[0] <= left[1] for left, right in zip(intervals, intervals[1:])) or
                sum(hi-lo+1 for lo, hi in intervals) != nw):
            raise ValueError('select_projections 需要恰好 num_wann 个互不重复的投影')
    elif projections < nw:
        raise ValueError(f'{seed}.amn 仅有 {projections} 个投影，少于 num_wann={nw}')
    if seed.endswith(('.1', '.2')) and contract._truthy(value('spinors')):
        raise ValueError(f'共线自旋通道 {seed} 与 .win 的 spinors=true 不相容，请核对接口来源')
    # Index each coordinate relative to the first point. This accepts shifted
    # grids, negative coordinates and any ordering of the uniform mesh.
    sites = set()
    origin = None
    for row in document.blocks['kpoints'][0].body:
        try:
            point = [float(x.replace('D','E').replace('d','e')) for x in row.split()]
            if len(point) != 3 or not all(map(math.isfinite, point)):
                raise ValueError()
            if origin is None:
                origin = point
            steps = [(x-base)*n for x,base,n in zip(point, origin, grid)]
            indices = [round(step) for step in steps]
        except (ValueError, OverflowError):
            raise ValueError('.win 的 kpoints 需要三个有限分数坐标') from None
        if any(abs(step-index) > min(0.01, 2e-6*n) for step,index,n in zip(steps, indices, grid)):
            raise ValueError('.win 的 kpoints 偏离 mp_grid 均匀网格')
        site = tuple(index % n for index,n in zip(indices,grid))
        if site in sites:
            raise ValueError('.win 的 kpoints 包含周期等价的重复点')
        sites.add(site)
