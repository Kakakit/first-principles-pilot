"""Local input recipes. Pending calculation artifacts never become runnable inputs."""
from datetime import datetime
import base64
import hashlib
import importlib.util
import io
import json
import math
import re
import zipfile
import os
import tempfile
from pathlib import Path
from .storage import ROOT, validate_workspace
from .structure_edit import edit_structure, geometry_hash
from . import parameter_catalog

spec = importlib.util.spec_from_file_location("fpilot_vasp_contract", Path(__file__).resolve().parents[1] / "resources/sets/vasp.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def put_file(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("内容寻址文件已损坏；未覆盖原文件")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(raw); temp.flush(); os.fsync(temp.fileno())
        name = temp.name
    try:
        os.link(name, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError("内容冲突；未覆盖原文件")
    finally:
        os.unlink(name)


def number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value.strip()):
        raise ValueError("请输入有限的十进制数值")
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("数值不能是 NaN 或 Infinity")
    return n


def upload_potcar(encoded):
    raw = base64.b64decode(encoded, validate=True)
    if not 0 < len(raw) <= 32 * 1024 * 1024:
        raise ValueError("POTCAR 上限为 32 MiB")
    text = raw.decode("utf-8")
    if "TITEL" not in text or "End of Dataset" not in text:
        raise ValueError("未识别到完整 POTCAR 数据集标记")
    key = digest(raw)
    put_file(ROOT / "potcars" / key, raw)
    return {"id": key, "size_bytes": len(raw)}


def potcar_bytes(key):
    if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{64}", key):
        raise ValueError("请导入 POTCAR")
    from .file_cache import read_bytes
    raw = read_bytes(ROOT / "potcars" / key)
    if digest(raw) != key:
        raise ValueError("POTCAR 校验失败，请重新导入")
    return raw


def assemble_potcar(encoded_files, species):
    if not encoded_files or len(encoded_files) > 118 or not species or len(species) > 1500:
        raise ValueError("赝势文件或物种数量无效")
    by_element = {}
    for encoded in encoded_files:
        key = upload_potcar(encoded)["id"]
        raw = potcar_bytes(key)
        titles = re.findall(r"^\s*TITEL\s*=\s*(.*?)\s*$", raw.decode(), re.MULTILINE)
        if len(titles) != 1 or raw.count(b"End of Dataset") != 1:
            raise ValueError("逐元素装配时，每个文件必须只含一种赝势数据集")
        element = contract._potcar_element(titles[0])
        if not element or element in by_element:
            raise ValueError("元素无法识别或同一元素选择了多个赝势版本")
        by_element[element] = raw
    if set(by_element) != set(species):
        raise ValueError("所选赝势元素与结构物种不完全一致")
    raw = b"".join(by_element[element].rstrip(b"\r\n") + b"\n" for element in species)
    return upload_potcar(base64.b64encode(raw).decode())


class GenerationContext:
    """One validated, immutable project for an entire preview/freeze request."""
    def __init__(self, project):
        validate_workspace({"activeId": project["id"], "projects": [project]})
        self.project = project
        self.nodes = {n['id']: n for n in project['nodes']}
        self.parents = {key: [] for key in self.nodes}
        for edge in project['edges']:
            self.parents[edge['target']].append(edge['source'])
        self.geometry = {}


def generate(project, node_id, context=None):
    context = context or GenerationContext(project)
    if context.project is not project:
        raise ValueError('输入生成上下文与项目不一致')
    nodes = context.nodes
    node = nodes[node_id]
    kind = node["data"]["kind"]
    if kind not in ("relax", "scf", "bands", "dos", "vasp-wannier"):
        raise ValueError("请选择计算节点")
    errors, warnings, dependencies = [], [], []
    shared = project.get("parameters", {}).get("shared", {})
    local = node["data"].get("parameters", {})
    values, origins = {}, {}
    layers = [("共享设置", shared.get("values", {}))]
    if local.get("preset") == "basic":
        layers.append(("阶段预设", {"IBRION": "2", "ISIF": "2"} if kind == "relax" else {"NSW": "0", "IBRION": "-1"}))
    layers.append(("节点覆盖", local.get("values", {})))
    for origin, layer in layers:
        for tag, value in layer.items():
            values[tag], origins[tag] = value, origin
    if kind in ('bands', 'dos') and 'LORBIT' not in shared.get('values', {}) and 'LORBIT' not in local.get('values', {}):
        values['LORBIT'], origins['LORBIT'] = '11', '节点执行合同'
    confirmations = set(shared.get('confirmed_custom_tags', [])) | set(local.get('confirmed_custom_tags', []))
    validation_levels = {}
    for tag, value in values.items():
        if value is None:
            continue
        try:
            validation_levels[tag] = parameter_catalog.validate_value(tag, value, tag in confirmations)
            definition = parameter_catalog.DEFINITIONS.get(tag)
            if definition and definition.get('applies_to') and kind not in definition['applies_to']:
                raise ValueError('该参数不适用于当前节点类型')
        except ValueError as exc:
            errors.append(f"{tag}: {exc}")
    required = ["XC_FUNCTIONAL", "ENCUT", "EDIFF", "ISPIN", "ISMEAR", "NSW", "IBRION", "LDAU"]
    if kind == "relax":
        required += ["ISIF", "EDIFFG"]
    if values.get("ISMEAR") != "-5":
        required.append("SIGMA")
    for tag in required:
        if values.get(tag) is None:
            errors.append(f"请明确 {tag}")
    noncollinear = values.get('LSORBIT') == 'true' or values.get('LNONCOLLINEAR') == 'true'
    if values.get('LSORBIT') == 'true' and values.get('LNONCOLLINEAR') == 'false':
        errors.append('LSORBIT=true 会启用非共线框架，不能设置 LNONCOLLINEAR=false')
    if noncollinear and values.get('ISPIN') != '1':
        errors.append('非共线/SOC 请使用 ISPIN=1；VASP 6.5 起 ISPIN=2 与非共线 MAGMOM 组合会报错')
    if values.get('SAXIS') is not None and not noncollinear:
        errors.append('SAXIS 仅用于非共线/SOC 节点')
    if values.get('NPAR') is not None and values.get('NCORE') is not None:
        errors.append('NPAR 与 NCORE 不能同时设置')
    for tag, level in validation_levels.items():
        if level == 'official-passthrough':
            warnings.append(f'{tag} 按 VASP Wiki 已知 tag 原样传递；目标 VASP 版本与物理适用性未自动验证')
        elif level == 'unverified-custom':
            warnings.append(f'{tag} 为显式确认的未验证自定义 tag；需检查目标 VASP 是否识别')
    if errors:
        return {"errors": errors, "warnings": warnings, "dependencies": [], "files": {}, "ready": False}
    if kind == "relax":
        if number(values["NSW"]) <= 0 or values["IBRION"] not in ("1", "2", "3"):
            errors.append("优化需 NSW > 0 和离子优化算法")
    elif number(values["NSW"]) != 0 or values["IBRION"] != "-1":
        errors.append("静态、能带和 DOS 需 NSW=0、IBRION=-1")
    parents = context.parents[node_id]
    if len(parents) != 1:
        errors.append("计算节点必须具有唯一直接上游")
    visited, roots = set(), []
    def visit(key, stack):
        if key in stack:
            raise ValueError("存在循环依赖")
        if key in visited:
            return
        visited.add(key)
        n = nodes[key]
        if n["data"]["kind"] == "structure":
            roots.append(n)
        else:
            for parent_id in context.parents[key]:
                visit(parent_id, stack | {key})
    visit(node_id, set())
    if len(roots) != 1 or not roots[0]["data"].get("structure"):
        errors.append("需要唯一、已导入的上游结构")
        return {"errors": errors, "warnings": warnings, "dependencies": [], "files": {}, "ready": False}
    source = roots[0]["data"]["structure"]
    # Regenerate POSCAR from geometry; never trust cached POSCAR text from a backup.
    root_id = roots[0]['id']
    if root_id not in context.geometry:
        context.geometry[root_id] = edit_structure(source, source["lattice"], [dict(a, coordinates=a["fractional"]) for a in source["atoms"]], "fractional")
    geometry = context.geometry[root_id]
    species = []
    for atom in geometry["atoms"]:
        if not species or species[-1] != atom["element"]:
            species.append(atom["element"])
    parent = nodes[parents[0]] if len(parents) == 1 else None
    if parent and parent["data"]["kind"] != "structure":
        dependencies.append({"node_id": parent["id"], "file": "CONTCAR", "status": "pending", "check": "完成性、结构与原子顺序审查后再替换 POSCAR"})
        if kind == 'scf' and parent['data']['kind'] == 'scf':
            dependencies.append({"node_id": parent["id"], "file": "CHGCAR", "status": "pending", "check": "读取相连 SCF 的收敛电荷并继续自洽"})
    if kind in ("bands", "dos", "vasp-wannier"):
        if not parent or parent["data"]["kind"] != "scf":
            errors.append("能带 / DOS / VASP-Wannier 必须直接连接 SCF 节点")
        else:
            dependencies.append({"node_id": parent["id"], "file": "CHGCAR", "status": "pending", "check": "须验证 SCF 收敛、最终几何、赝势、泛函、自旋及 U / LMAXMIX 兼容性"})
    expected_icharg = ('11' if kind in ('bands', 'dos', 'vasp-wannier') else
                       '1' if kind == 'scf' and parent and parent['data']['kind'] == 'scf' else '2')
    if kind in ('bands', 'dos', 'vasp-wannier'):
        values['ICHARG'], origins['ICHARG'] = '11', '节点执行合同'
    source_draft = node['data'].get('sourceDraft') or {}
    if kind in ('bands','dos','vasp-wannier') and source_draft.get('mode') in ('new','self','manual'):
        errors.append('本阶段必须读取已连接 SCF 的固定电荷与最终结构')
    if source_draft.get('mode')=='new' and kind in ('relax','scf'):
        dependencies=[d for d in dependencies if d['file']=='CONTCAR']
        expected_icharg='2'
        values['ICHARG']='2'
        values['ISTART']='0'
        origins['ICHARG']=origins['ISTART']='计算来源合同'
    if source_draft.get('mode')=='upstream' and kind in ('relax','scf'):
        if parent and parent['data']['kind']!='structure':
            if not any(d['file']=='CHGCAR' for d in dependencies):
                dependencies.append({'node_id':parent['id'],'file':'CHGCAR','status':'pending','check':'读取已连接上游的收敛电荷'})
            if source_draft.get('files')=='both':dependencies.append({'node_id':parent['id'],'file':'WAVECAR','status':'pending','check':'显式读取已连接上游波函数'})
            expected_icharg='1'
            values['ICHARG']='1'
            values['ISTART']='1' if source_draft.get('files')=='both' else '0'
            origins['ICHARG']=origins['ISTART']='计算来源合同'
    has_restart = source_draft.get('mode') in ('self','manual')
    if has_restart:
        dependencies = []
        expected_icharg = '1'
        values['ISTART'] = '1' if source_draft.get('files') == 'both' else '0'
        values['ICHARG'] = '1'
        origins['ISTART'] = origins['ICHARG'] = '计算来源合同'
    if not has_restart and source_draft.get('mode')!='upstream' and values.get('ISTART') not in (None, '0'):
        errors.append('ISTART 当前锁定为 0；非零值需要已核对的 WAVECAR 重启合同')
    restart_required = kind in ('relax', 'scf') and values.get('ICHARG') == '1' and expected_icharg != '1'
    if values.get('ICHARG') not in (None, expected_icharg) and not restart_required:
        errors.append(f'ICHARG 当前节点锁定为 {expected_icharg}；其他值需要已核对的 CHGCAR 合同')
    if restart_required:
        warnings.append('ICHARG=1 需要在计算配置中选择电子文件来源并重新冻结')
    if kind == 'scf' and values.get('LCHARG') == 'false' and any(e['source'] == node_id and nodes[e['target']]['data']['kind'] in ('scf','bands','dos','vasp-wannier') for e in project['edges']):
        errors.append('该 SCF 有读取 CHGCAR 的下游，LCHARG 必须开启')
    if (values.get('EMIN') is None) != (values.get('EMAX') is None):
        errors.append('EMIN 与 EMAX 必须同时设置')
    elif values.get('EMIN') is not None and values.get('EMAX') is not None:
        try:
            if number(values['EMIN']) >= number(values['EMAX']): errors.append('EMIN 必须小于 EMAX')
        except ValueError: pass
    options = node["data"].get("inputDraft", {})
    effective_potcar_id = options.get('potcar_id') or project.get('sharedPotcar', {}).get('id')
    effective_potcar_provenance = options.get('potcar_provenance_id') if options.get('potcar_id') else project.get('sharedPotcar', {}).get('provenance_id')
    if kind in ("bands", "dos", "vasp-wannier") and parent and parent["data"]["kind"] == "scf":
        parent_layer = parent["data"].get("parameters", {})
        parent_values = dict(shared.get("values", {}))
        parent_values.update(parent_layer.get("values", {}))
        for tag in ("XC_FUNCTIONAL", "ENCUT", "ISPIN", "LDAU", "LSORBIT", "LNONCOLLINEAR", "SAXIS", "GGA_COMPAT"):
            if parent_values.get(tag) != values.get(tag):
                warnings.append(f"{tag} 与上游 SCF 不一致；固定电荷来源的模型差异须在授权前审阅")
        parent_potcar_id = parent["data"].get("inputDraft", {}).get("potcar_id") or project.get('sharedPotcar', {}).get('id')
        if effective_potcar_id != parent_potcar_id:
            warnings.append("POTCAR 与上游 SCF 不同；固定电荷来源差异须在授权前审阅")
        if values["LDAU"] == "true":
            for tag in ("LDAUTYPE", "LMAXMIX"):
                if parent_values.get(tag) != values.get(tag):
                    warnings.append(f"{tag} 与上游 SCF 不一致；须审阅固定电荷来源")
            parent_u = dict(shared.get("hubbard", {}), **parent_layer.get("hubbard", {}))
            current_u = dict(shared.get("hubbard", {}), **local.get("hubbard", {}))
            if any(parent_u.get(s) != current_u.get(s) for s in species):
                warnings.append("U / J / L 与上游 SCF 不一致；须审阅固定电荷来源")
        if values["ISPIN"] == "2" or noncollinear:
            parent_m = dict(shared.get("moments", {}), **parent_layer.get("moments", {}))
            current_m = dict(shared.get("moments", {}), **local.get("moments", {}))
            if any(parent_m.get(a["id"]) != current_m.get(a["id"]) for a in geometry["atoms"]):
                warnings.append("磁矩对称性设置与上游 SCF 不一致；须审阅固定电荷来源")
    files = {"POSCAR.reference" if dependencies else "POSCAR": geometry["poscar"]}
    tags = {k: str(v).strip() for k, v in values.items() if v is not None and k != "XC_FUNCTIONAL"}
    for tag in ('LWAVE', 'LCHARG', 'LASPH', 'LSORBIT', 'LNONCOLLINEAR', 'GGA_COMPAT', 'LORBMOM'):
        if tag in tags: tags[tag] = '.TRUE.' if tags[tag] == 'true' else '.FALSE.'
    tags["LDAU"] = ".TRUE." if values["LDAU"] == "true" else ".FALSE."
    if values["XC_FUNCTIONAL"] in ("PBE", "PBEsol"):
        tags["GGA"] = "PE" if values["XC_FUNCTIONAL"] == "PBE" else "PS"
    tags.setdefault('ISTART', '0')
    tags.setdefault('ICHARG', expected_icharg)
    tags.setdefault('LCHARG', '.FALSE.' if kind in ('bands', 'dos', 'vasp-wannier') else '.TRUE.')
    tags.setdefault('LWAVE', '.FALSE.')
    if kind == 'vasp-wannier':
        projection_lines = [line.strip() for line in options.get('projections', '').splitlines() if line.strip()]
        selected = {}
        for line in projection_lines:
            match = re.fullmatch(r'([A-Z][a-z]?):([spdf])', line)
            if not match or match.group(1) not in species or line in selected:
                errors.append('投影格式应为当前元素:轨道（s/p/d/f），且不能重复：' + line)
            else:
                selected[line] = match.groups()
        if not projection_lines or set(species) - {item[0] for item in selected.values()}:
            errors.append('请为结构中的每种元素明确选择至少一个投影轨道')
        orbital_count = sum(sum({'s':1,'p':3,'d':5,'f':7}[orbital] for element, orbital in selected.values() if element == atom['element']) for atom in geometry['atoms'])
        recommended_wann = orbital_count * (2 if noncollinear else 1)
        raw_wann = options.get('num_wann', '')
        if not isinstance(raw_wann, str) or not re.fullmatch(r'[1-9]\d*', raw_wann.strip()):
            errors.append(f'请明确填写 NUM_WANN 正整数；当前投影建议 {recommended_wann}，SOC／非共线按 spinor 乘 2')
            num_wann = None
        else:
            num_wann = int(raw_wann)
            try:
                too_many_bands = bool(values.get('NBANDS')) and num_wann > int(values['NBANDS'])
            except (TypeError, ValueError):
                too_many_bands = False  # NBANDS 的类型错误由参数审查单独报告
            if num_wann > 100000 or too_many_bands:
                errors.append('NUM_WANN 不得超过 NBANDS，且须为合理的正整数')
            elif num_wann != recommended_wann:
                warnings.append(f'您选择 NUM_WANN={num_wann}，投影计数建议 {recommended_wann}；请核对 .amn 维度')
        tags.update(ISYM='-1', NCORE='1', KPAR='1', LWANNIER90='.TRUE.', LWANNIER90_RUN='.FALSE.',
                    LWRITE_MMN_AMN='.TRUE.')
        if num_wann is not None:
            tags['NUM_WANN'] = str(num_wann)
        tags['WANNIER90_WIN'] = '"\nBegin Projections\n' + '\n'.join(selected) + '\nEnd Projections\n"'
        for tag in ('ISYM','NCORE','KPAR','LWANNIER90','LWANNIER90_RUN','LWRITE_MMN_AMN','NUM_WANN','WANNIER90_WIN'):
            origins[tag] = 'VASP-Wannier 节点合同'
    moments = dict(shared.get("moments", {}), **local.get("moments", {}))
    if noncollinear:
        try:
            vectors = []
            for atom in geometry['atoms']:
                parts = (moments.get(atom['id']) or '').split()
                if len(parts) != 3:
                    raise ValueError()
                vectors.extend(str(number(part)) for part in parts)
            tags['MAGMOM'] = ' '.join(vectors)
        except ValueError:
            errors.append('非共线/SOC 请按原子 ID 填写三个初始 MAGMOM 分量，例如 0 0 4')
    elif values["ISPIN"] == "2":
        try:
            tags["MAGMOM"] = " ".join(str(number(moments.get(a["id"]))) for a in geometry["atoms"])
        except ValueError:
            errors.append("请按原子 ID 填写全部初始 MAGMOM")
    hubbard = dict(shared.get("hubbard", {}), **local.get("hubbard", {}))
    if values["LDAU"] == "true":
        if values.get("LDAUTYPE") not in ("1", "2"):
            errors.append("请明确 LDAUTYPE")
        ls, us, js = [], [], []
        for element in species:
            row = hubbard.get(element, {})
            try:
                l = row.get("l")
                if l not in ("-1", "0", "1", "2", "3"):
                    raise ValueError()
                u, j = (0, 0) if l == "-1" else (number(row.get("u")), number(row.get("j")))
                if min(u, j) < 0:
                    raise ValueError()
                ls.append(l); us.append(str(u)); js.append(str(j))
            except ValueError:
                errors.append(f"{element} 的 L / U / J 未明确")
        tags.update(LDAUL=" ".join(ls), LDAUU=" ".join(us), LDAUJ=" ".join(js))
        needed = max([2 * int(l) for l in ls] + [0])
        if number(values.get("LMAXMIX") or "0") < needed:
            errors.append(f"当前 +U 电荷交接需要显式 LMAXMIX ≥ {needed}")
    try:
        if kind == "bands":
            count = number(options.get("line_points", ""))
            if not count.is_integer() or not 2 <= count <= 10000:
                raise ValueError("每段点数需为 2–10000 整数")
            lines = [line.split() for line in options.get("path", "").splitlines() if line.strip()]
            if not 2 <= len(lines) <= 200 or len(lines) % 2:
                raise ValueError("路径需成对端点，每行三个倒空间分数坐标和可选标签")
            rendered = []
            for i, line in enumerate(lines):
                if len(line) not in (3, 4):
                    raise ValueError("路径端点格式无效")
                rendered.append(" ".join(str(number(x)) for x in line[:3]) + (" ! " + line[3] if len(line) == 4 else ""))
                if i % 2:
                    rendered.append("")
            files["KPOINTS"] = f"Explicit path\n{int(count)}\nLine-mode\nReciprocal\n" + "\n".join(rendered) + "\n"
            if values["ISMEAR"] == "-5":
                errors.append("能带路径不支持四面体积分")
        else:
            grid = [number(x) for x in options.get("grid", "").split()]
            if len(grid) != 3 or any(not x.is_integer() or not 1 <= x <= 1000 for x in grid):
                raise ValueError("网格需三个 1–1000 的整数")
            if values.get('KPAR') is not None and number(values['KPAR']) > math.prod(grid):
                errors.append('KPAR 大于规则网格总 k 点数，不可并行分组')
            center = options.get("center")
            if center not in ("Gamma", "Monkhorst-Pack"):
                raise ValueError("请明确网格中心")
            files["KPOINTS"] = "Regular mesh\n0\n" + center + "\n" + " ".join(str(int(x)) for x in grid) + "\n0 0 0\n"
            if values["ISMEAR"] == "-5":
                warnings.append("四面体积分仍需实际 VASP 网格和适用性验证；网格尺寸检查不证明四面体可用")
                if min(grid) < 2:
                    errors.append("当前四面体模式要求三个方向至少各 2 个网格点")
    except ValueError as exc:
        errors.append(f"KPOINTS: {exc}")
    potcar = None
    try:
        raw = potcar_bytes(effective_potcar_id)
        from .file_cache import potcar as cached_potcar
        potcar, issues = cached_potcar(ROOT / "potcars" / effective_potcar_id, species, contract._parse_potcar)
        errors.extend(str(e) for e in issues)
        receipt_id = effective_potcar_provenance
        if receipt_id:
            if not re.fullmatch('[a-f0-9]{64}', receipt_id):
                raise ValueError('赝势来源记录 ID 无效')
            receipt_raw = (ROOT / "potcars" / (receipt_id + ".library.json")).read_bytes()
            if digest(receipt_raw) != receipt_id:
                raise ValueError('赝势来源记录校验失败')
            receipt = json.loads(receipt_raw)
            if receipt['sha256'] != effective_potcar_id or receipt['functional'] != values['XC_FUNCTIONAL']:
                raise ValueError('赝势选择来源与当前配置不同，请重新从库生成')
            potcar["library_provenance"] = receipt
        records = potcar["records"]
        if raw.count(b"End of Dataset") != len(species) or any("lexch" not in r or "enmax" not in r for r in records):
            errors.append("POTCAR 数据集数或 LEXCH / ENMAX 不完整")
        expected = "CA" if values["XC_FUNCTIONAL"] == "LDA" else "PE"
        if any(r.get("lexch", "").upper() != expected for r in records):
            errors.append("POTCAR 泛函元数据与所选泛函不匹配（PBEsol 使用 PBE 势）")
        if records and number(values["ENCUT"]) < max(r.get("enmax", 0) for r in records):
            warnings.append("ENCUT 低于 POTCAR 最大 ENMAX，请给出收敛依据")
    except (ValueError, OSError) as exc:
        errors.append(f"POTCAR: {exc}")
    files["INCAR"] = "".join(f"{k} = {v}\n" for k, v in tags.items())
    warnings.append("输入格式与兼容性审查不代替能量、力、k 点和磁态收敛验证")
    incar_tags = {tag: {'value': value,
                        'source': origins[tag] if tag in origins else
                                  'XC_FUNCTIONAL' if tag == 'GGA' else
                                  '磁矩表' if tag == 'MAGMOM' else
                                  'Hubbard 元素表' if tag in ('LDAUL','LDAUU','LDAUJ') else '工作台执行合同',
                        'validation': validation_levels.get(tag, 'generated-contract')}
                  for tag, value in tags.items()}
    return {"errors": errors, "warnings": warnings, "dependencies": dependencies, "files": files,
            "ready": not errors and not dependencies, "potcar": potcar, "species": species,
            "potcar_source": "节点覆盖" if options.get('potcar_id') else "项目共享",
            "geometry_sha256": geometry_hash(geometry), "source_sha256": source["source"]["sha256"],
            "parameters": {k: {"value": v, "source": origins[k], "validation": validation_levels.get(k, 'unset')} for k, v in values.items()},
            "incar_tags": incar_tags,
            "parameter_catalog": {"revision": parameter_catalog.REVISION, "source": parameter_catalog.CATALOG['source'],
                                  "retrieved": parameter_catalog.CATALOG['retrieved'], "confirmed_custom_tags": sorted(confirmations)},
            "generated_tags": {k: v for k, v in tags.items() if k not in values}, "stage": kind,
            "restart_required": restart_required, "source_draft": source_draft,
            "band_path_source_sha256": options.get('path_structure_hash') if kind == 'bands' else None,
            "band_path_source_attempt_id": options.get('path_source_attempt_id') if kind == 'bands' else None}


def freeze(project, node_id, reason, *, context=None, report=None, verification_mode='lightweight', defer_sources=False, execution_scope=None):
    from . import recycle
    recycle.assert_available('projects',project['id'])
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("请填写本次输入选择 / 修改理由（最多 2000 字）")
    if verification_mode not in ('lightweight', 'full'):
        raise ValueError('文件核验策略无效')
    report = report if report is not None else generate(project, node_id, context)
    if report['errors']:raise ValueError('请先修正输入错误：'+'; '.join(report['errors']))
    source_contract = None
    node = next(n for n in project['nodes'] if n['id'] == node_id)
    if node['data'].get('sourceDraft') and (not defer_sources or node['data']['sourceDraft'].get('mode') in ('self','manual')):
        from . import source_contracts
        source_contract = source_contracts.freeze(project,node_id,report,verification_mode,execution_scope)
    elif defer_sources:
        source_contract = {'schema':'fpilot.source-contract.v1','mode':'planned','purpose':'charge','files':'charge','structure':'planned','roles':{},'plan_nodes':report.get('dependencies',[]),'verification_mode':verification_mode}
    from . import branch_bindings
    branch_bindings.check(project,node_id,source_contract=source_contract,scope=execution_scope)
    if report["errors"]:
        raise ValueError("请先修正输入错误：" + "; ".join(report["errors"]))
    contents = {name: text.encode() for name, text in report["files"].items()}
    contents["POTCAR"] = potcar_bytes(report["potcar"]["sha256"])
    contents["project.json"] = json.dumps(project, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    manifest = dict(report, files={k: {"sha256": digest(v), "size_bytes": len(v)} for k, v in contents.items()},
                    execution_scope=execution_scope or node_id,
                    schema="fpilot.web-input-bundle.v1", node_id=node_id, reason=reason.strip(), verification_mode=verification_mode,
                    status="pending-restart" if report['restart_required'] else "ready-for-execution-review" if report["ready"] else "pending-dependencies")
    if source_contract is not None:
        manifest["source_contract"] = source_contract
        manifest["status"] = "ready-for-execution-review"
        if source_contract["structure"] == "configured" and "POSCAR.reference" in contents:
            contents["POSCAR"] = contents.pop("POSCAR.reference")
            manifest["files"]["POSCAR"] = manifest["files"].pop("POSCAR.reference")
    contents["manifest.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    raw = stream.getvalue(); key = digest(raw)
    put_file(ROOT / "inputs" / (key + ".zip"), raw)
    recycle.reactivate('inputs',key)
    return {"id": key, "manifest": manifest, "download": f"/api/inputs/{key}/download"}


def history(project_id=None):
    result = []
    from .scans import case_scopes
    scan_scopes=case_scopes()
    paths = sorted((ROOT / "inputs").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        raw = path.read_bytes()
        if digest(raw) != path.stem:
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            try:
                bundle_project_id = json.loads(archive.read('project.json'))['id']
            except (KeyError, ValueError):
                bundle_project_id = manifest.get('project_id')
            if project_id is not None and bundle_project_id != project_id:
                continue
            if (ROOT / 'recycle' / 'inputs' / path.stem).exists():
                continue
            result.append({"id": path.stem, "project_id": bundle_project_id, "node_id": manifest["node_id"], "stage": manifest.get("stage"),
                           "execution_scope":manifest.get('execution_scope') or (scan_scopes[path.stem][0] if len(scan_scopes.get(path.stem,[]))==1 else 'scan-case' if path.stem in scan_scopes else None),
                           "status": manifest["status"], "dependencies": manifest.get("dependencies", []),
                           "source_contract":manifest.get('source_contract'), "created_at":datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
                           "band_path_source_attempt_id": manifest.get('band_path_source_attempt_id'),
                           "requires_ncl": any(str(manifest.get('incar_tags', {}).get(tag, {}).get('value', '')).strip().upper() in ('.TRUE.', 'TRUE', 'T') for tag in ('LSORBIT', 'LNONCOLLINEAR')),
                           "reason": manifest["reason"], "download": f"/api/inputs/{path.stem}/download"})
            if len(result) >= 100:
                break
    return result
