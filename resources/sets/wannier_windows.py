"""Standalone, standard-library JSON CLI for window feasibility and wout evidence.

Matrix files remain on their source host. No scheduler operations or package imports.
"""
import json
import math
import re
import sys
import hashlib
from array import array
from pathlib import Path

KEYS = ('dis_win_min', 'dis_win_max', 'dis_froz_min', 'dis_froz_max')
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?'


def scalar(text, key, default=None):
    values = re.findall(r'^\s*'+re.escape(key)+r'\s*[=:]\s*([^!#\n]+)', text, re.M | re.I)
    if len(values) > 1:
        raise ValueError('重复参数：'+key)
    return values[0].strip() if values else default


def number(value):
    result = float(str(value).replace('D', 'E').replace('d', 'e'))
    if not math.isfinite(result):
        raise ValueError('需要有限数值')
    return result


def validate_base(text, allow_defaults=False):
    # These modes select additional/non-energy states and need a different checker.
    for key in ('dis_froz_proj', 'dis_proj_min', 'dis_proj_max', 'dis_spheres_num', 'dis_spheres_first_wann'):
        value = scalar(text, key)
        if value is not None and value.lower() not in ('false', '.false.', 'f', '0'):
            raise ValueError('普通能窗迭代暂不支持 '+key)
    if re.search(r'^\s*begin\s+(?:dis_spheres|kpt_dis)', text, re.M | re.I):
        raise ValueError('普通能窗迭代暂不支持特殊 k 点解缠区域')
    if scalar(text, 'restart', '').lower() not in ('', 'default'):
        raise ValueError('扫描必须重新局域化；请使用完整计算输入，不能使用 restart=plot/旧 checkpoint')
    nw, nb = int(scalar(text, 'num_wann', '0')), int(scalar(text, 'num_bands', '0'))
    if not 0 < nw < nb:
        raise ValueError('能窗迭代要求 0 < num_wann < num_bands 的解缠输入')
    windows = {key: number(scalar(text,key)) for key in KEYS if scalar(text,key) is not None} if allow_defaults else {key:number(scalar(text,key)) for key in KEYS}
    grid = [int(x) for x in scalar(text, 'mp_grid', '').split()]
    if len(grid) != 3 or any(x <= 0 for x in grid):
        raise ValueError('mp_grid 无效')
    blocks = re.findall(r'^\s*begin\s+kpoints\s*$([\s\S]*?)^\s*end\s+kpoints\s*$', text, re.M | re.I)
    if len(blocks) != 1:
        raise ValueError('需要完整均匀网格 kpoints')
    points = []
    for line in blocks[0].splitlines():
        fields = re.split(r'[!#]',line)[0].split()
        if not fields: continue
        if len(fields) != 3: raise ValueError('kpoints 每行需要三个分数坐标')
        points.append(tuple(round(number(x)%1.0,10)%1.0 for x in fields))
    if len(points) != math.prod(grid) or len(set(points)) != len(points):
        raise ValueError('kpoints 网格缺失或重复')
    for axis, count in enumerate(grid):
        values = sorted({p[axis] for p in points})
        if len(values) != count or any(not math.isclose((values[(i+1)%count]-v)%1.0,1.0/count if count>1 else 0.0,abs_tol=1e-7) for i,v in enumerate(values)):
            raise ValueError('kpoints 与完整均匀 mp_grid 不一致')
    return {'num_wann': nw, 'num_bands': nb, 'num_kpts': math.prod(grid), 'windows': windows}


def check(request):
    nb, nk, nw = (request[key] for key in ('num_bands', 'num_kpts', 'num_wann'))
    if any(type(x) is not int or x <= 0 for x in (nb, nk, nw)) or nw > nb:
        raise ValueError('矩阵维度无效')
    paths = {key: Path(request[key]) for key in ('eig', 'amn', 'mmn')}
    def identity(path):
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise ValueError('矩阵不是非空普通文件：'+path.name)
        st = path.stat()
        return [st.st_size, st.st_mtime_ns]
    before = {key: identity(path) for key, path in paths.items()}
    for key in ('amn', 'mmn'):
        with paths[key].open() as stream:
            next(stream, '')
            header = next(stream, '').split()
        try: dimensions = list(map(int, header[:3]))
        except ValueError: raise ValueError(key+' 头部无效')
        if len(dimensions) != 3 or dimensions[:2] != [nb, nk] or dimensions[2] <= 0:
            raise ValueError(key+' 与 .win/.eig 维度不一致')
        if key == 'amn' and dimensions[2] != nw:
            raise ValueError('.amn 投影数与 num_wann 不一致')
    candidates = request['candidates']
    limit=64 if request.get('allow_defaults') else 5
    if not isinstance(candidates,list) or not 1 <= len(candidates) <= limit:
        raise ValueError('候选数量超出允许范围')
    if request.get('allow_defaults'):
        windows=[]
        for c in candidates:
            w={key:number(value) for key,value in c.items() if key in KEYS and value is not None}
            w.setdefault('dis_win_min',float('-inf'));w.setdefault('dis_win_max',float('inf'))
            w['dis_froz_min']=w.get('dis_froz_min',w['dis_win_min']) if 'dis_froz_max' in w else None
            w.setdefault('dis_froz_max',None);windows.append(w)
    else:windows = [{key:number(c[key]) for key in KEYS} for c in candidates]
    counts = [[ [0]*nk, [0]*nk ] for _ in windows]
    seen = bytearray(nb*nk)
    energies = array('d', [0.0]) * (nb*nk)
    total = 0
    with paths['eig'].open() as stream:
        for line in stream:
            if not line.strip(): continue
            fields = line.split()
            if len(fields) != 3: raise ValueError('.eig 需要 band、k 点、能量三列')
            band, k = int(fields[0]), int(fields[1])
            energy = number(fields[2])
            if not 1 <= band <= nb or not 1 <= k <= nk: raise ValueError('.eig 索引越界')
            index = (k-1)*nb+band-1
            if seen[index]: raise ValueError('.eig 存在重复 band/k 点')
            seen[index] = 1; total += 1
            energies[index] = energy
            for w, (outer, frozen) in zip(windows, counts):
                outer[k-1] += w['dis_win_min'] <= energy <= w['dis_win_max']
                frozen[k-1] += w['dis_froz_max'] is not None and w['dis_froz_min'] <= energy <= w['dis_froz_max']
    if total != nb*nk: raise ValueError('.eig 网格不完整，不能用平均态数替代')
    for k in range(nk):
        if any(energies[k*nb+b] > energies[k*nb+b+1] for b in range(nb-1)):
            raise ValueError('.eig 能量未按 band 顺序递增，官方解缠窗口要求有序本征值')
    if before != {key: identity(path) for key, path in paths.items()}:
        raise ValueError('检查过程中矩阵文件发生变化')
    rows = []
    for w, (outer, frozen) in zip(windows, counts):
        errors = []
        if not math.isfinite(w['dis_win_min']):w['dis_win_min']=min(energies)
        if not math.isfinite(w['dis_win_max']):w['dis_win_max']=max(energies)
        if w['dis_froz_min'] is not None and not math.isfinite(w['dis_froz_min']):w['dis_froz_min']=min(energies)
        if w['dis_win_min'] >= w['dis_win_max']:errors.append('外窗下界必须小于上界')
        if w['dis_froz_max'] is not None and not w['dis_win_min'] <= w['dis_froz_min'] <= w['dis_froz_max'] <= w['dis_win_max']:
            errors.append('冻窗须包含于外窗且下界不大于上界')
        bad_outer = [i+1 for i, n in enumerate(outer) if n < nw]
        bad_frozen = [i+1 for i, n in enumerate(frozen) if n > nw]
        if bad_outer: errors.append('外窗态数不足，k 点 '+','.join(map(str,bad_outer[:8])))
        if bad_frozen: errors.append('冻窗态数超过 num_wann，k 点 '+','.join(map(str,bad_frozen[:8])))
        margin_o, margin_f = min(outer)-nw, nw-max(frozen)
        rows.append({'errors':errors, 'absolute_windows':w, 'frozen_enabled':w['dis_froz_max'] is not None, 'outer_min':min(outer), 'outer_max':max(outer),
                     'frozen_min':min(frozen), 'frozen_max':max(frozen),
                     'outer_margin':margin_o, 'disentanglement_margin':margin_f,
                     'outer_counts':outer, 'frozen_counts':frozen,
                     'warnings':['存在等号边界；官方允许，请关注解缠余量'] if not errors and (margin_o == 0 or margin_f == 0) else []})
    return {'rows':rows, 'matrix_identity':before, 'num_kpts':nk}


def summary(request):
    path = Path(request['wout'])
    if request.get('expected_win_sha256'):
        if hashlib.sha256(path.with_suffix('.win').read_bytes()).hexdigest() != request['expected_win_sha256']:
            raise ValueError('运行目录 .win 与冻结输入不一致，不能归属收敛证据')
    text = path.read_text(errors='replace')
    win = request['win']
    nw, nb = int(scalar(win,'num_wann','0')), int(scalar(win,'num_bands','0'))
    dis_iter = int(scalar(win, 'dis_num_iter', '200'))
    num_iter = int(scalar(win, 'num_iter', '100'))
    conv_window = int(scalar(win, 'conv_window', '-1'))
    done = bool(re.search(r'All done:\s*wannier90 exiting',text,re.I))
    dis = 'not_run' if nw == nb or dis_iter == 0 or not re.search(r'<--\s*DIS|disentanglement',text,re.I) else 'confirmed' if 'disentanglement convergence criteria satisfied' in text.lower() else 'not_satisfied'
    loc = 'not_run' if num_iter == 0 or not re.search(r'<--\s*(?:CONV|SPRD)|Final State|Wannierisation',text,re.I) else 'criteria_disabled' if conv_window <= 1 else 'confirmed' if re.search(r'wannierisation convergence criteria satisfied',text,re.I) else 'not_satisfied'
    # Do not infer convergence merely from Final State or fewer printed iterations.
    spreads = re.findall(r'Sum of centres and spreads\s*\([^\n)]*\)\s*('+NUMBER+r')',text,re.I)
    omega = re.findall(r'Omega\s+Total\s*=\s*('+NUMBER+r')',text,re.I)
    elapsed = re.findall(r'Total Execution Time\s*=?\s*('+NUMBER+r')',text,re.I)
    unit = scalar(win, 'length_unit', 'ang').lower()
    printed_units = re.findall(r'(?:Final Spread|Spreads)\s*\(\s*(\w+)\^2\s*\)',text,re.I)
    if printed_units:
        unit = printed_units[-1].lower()
    if unit not in ('ang', 'angstrom', 'bohr'):
        raise ValueError('无法识别 spread 长度单位：'+unit)
    # Wannier90 3.x prints spreads in length_unit; the table always uses Å².
    factor = 0.529177210903**2 if unit == 'bohr' else 1.0
    spread = number(spreads[-1]) if spreads else number(omega[-1]) if omega else None
    return {'program_done':done, 'disentanglement':dis, 'localization':loc,
            'spread_A2':spread*factor if spread is not None else None, 'spread_source_unit':unit+'^2',
            'elapsed_s':number(elapsed[-1]) if elapsed else None,
            'scientific_validation':'NOT_PERFORMED'}


def main():
    request = json.loads(sys.argv[2]) if len(sys.argv)>2 else json.load(sys.stdin)
    result = check(request) if sys.argv[1]=='check' else summary(request)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))


if __name__ == '__main__':
    try: main()
    except (ValueError,KeyError,OSError,TypeError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));sys.exit(2)
