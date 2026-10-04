"""5A-2 versioned AI recipe: strict validation and deterministic draft materialization."""
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import parameter_catalog
from .storage import ROOT, validate_workspace
from .structure_edit import source_bytes

SCHEMA = "fpilot.recipe.v1"
MAX_BYTES = 2 * 1024 * 1024
KINDS = ("relax", "scf", "bands", "dos")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class RecipeError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(issues[0]["message"] if issues else "配方无效")


def issue(path, message, code="invalid"):
    return {"path": path, "message": message, "code": code}


def _closed(value, allowed, path, issues):
    if not isinstance(value, dict):
        issues.append(issue(path, "必须是 JSON 对象", "type")); return False
    for key in value:
        if key not in allowed:
            issues.append(issue(f"{path}.{key}", "字段不在 fpilot.recipe.v1 规格中", "additional_property"))
    return True


def _text(value, path, issues, required=True, maximum=2000):
    if value is None and not required:
        return
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum or "\x00" in value:
        issues.append(issue(path, f"必须是{'非空' if required else ''}文本，最长 {maximum} 字符", "type"))


def _layer(value, path, issues):
    if not _closed(value, {"values", "preset", "moments", "hubbard", "confirmed_custom_tags"}, path, issues): return
    confirmations = value.get('confirmed_custom_tags', [])
    if not isinstance(confirmations, list) or len(confirmations) > 100 or any(not isinstance(tag, str) or not parameter_catalog.TAG.fullmatch(tag) for tag in confirmations):
        issues.append(issue(path + '.confirmed_custom_tags', '自定义 tag 确认列表格式无效', 'type'))
        confirmations = []
    values = value.get("values")
    if not isinstance(values, dict) or len(values) > 100:
        issues.append(issue(path + ".values", "必须是不超过 100 项的参数对象", "type"))
    else:
        for tag, raw in values.items():
            tag_path = f"{path}.values.{tag}"
            if raw is not None and (not isinstance(raw, str) or len(raw) > 200):
                issues.append(issue(tag_path, "参数值必须是文本或 null", "type"))
            elif raw is not None:
                try: parameter_catalog.validate_value(tag, raw, tag in confirmations)
                except ValueError as exc: issues.append(issue(tag_path, str(exc), 'parameter'))
    if value.get("preset") not in (None, "none", "basic"):
        issues.append(issue(path + ".preset", "仅支持 none 或 basic", "enum"))
    moments = value.get("moments", {})
    if not isinstance(moments, dict) or len(moments) > 1500:
        issues.append(issue(path + ".moments", "初始磁矩必须是不超过 1500 项的对象", "type"))
    else:
        for key, raw in moments.items():
            if not isinstance(key, str) or raw is not None and (not isinstance(raw, str) or len(raw) > 200):
                issues.append(issue(f"{path}.moments.{key}", "磁矩值必须是文本或 null", "type"))
    hubbard = value.get("hubbard", {})
    if not isinstance(hubbard, dict):
        issues.append(issue(path + ".hubbard", "DFT+U 必须是按元素索引的对象", "type"))
    else:
        for element, row in hubbard.items():
            row_path = f"{path}.hubbard.{element}"
            if not re.fullmatch(r"[A-Z][a-z]?", str(element)) or not _closed(row, {"l", "u", "j"}, row_path, issues): continue
            _text(row.get("l"), f"{row_path}.l", issues, maximum=200)
            for key in ("u", "j"):
                _text(row.get(key), f"{row_path}.{key}", issues, required=False, maximum=200)


def _input(value, path, issues):
    if not _closed(value, {"grid", "center", "path", "line_points", "potcar_id", "potcar_provenance_id", "reason"}, path, issues): return
    for key, raw in value.items(): _text(raw, f"{path}.{key}", issues, required=False, maximum=20000)


def _potcar(value, path, issues):
    if not _closed(value, {"functional", "variants"}, path, issues): return
    _text(value.get("functional"), path + ".functional", issues, maximum=40)
    variants = value.get("variants")
    if not isinstance(variants, dict) or not variants:
        issues.append(issue(path + ".variants", "必须按元素指定 POTCAR 变体，不包含 POTCAR 正文", "required"))
    else:
        for element, variant in variants.items():
            if not re.fullmatch(r"[A-Z][a-z]?", str(element)): issues.append(issue(f"{path}.variants.{element}", "元素符号无效", "pattern"))
            _text(variant, f"{path}.variants.{element}", issues, maximum=120)


def validate(recipe):
    issues = []
    if not _closed(recipe, {"schema", "name", "description", "structure", "shared", "stages", "runtime"}, "$", issues):
        return issues
    if recipe.get("schema") != SCHEMA: issues.append(issue("$.schema", f"必须是 {SCHEMA}", "const"))
    _text(recipe.get("name"), "$.name", issues, maximum=120)
    if "description" in recipe: _text(recipe.get("description"), "$.description", issues, required=False, maximum=2000)
    structure = recipe.get("structure")
    if _closed(structure, {"id", "label", "document"}, "$.structure", issues):
        _text(structure.get("id"), "$.structure.id", issues, maximum=128)
        _text(structure.get("label"), "$.structure.label", issues, maximum=200)
        if not ID_RE.fullmatch(str(structure.get("id", ""))): issues.append(issue("$.structure.id", "ID 仅允许字母、数字、点、下划线和短横线", "pattern"))
        try: source_bytes(structure.get("document"))
        except Exception as exc: issues.append(issue("$.structure.document", f"结构文档无效：{str(exc)[:240]}", "structure"))
    shared = recipe.get("shared", {"values": {}})
    _layer(shared, "$.shared", issues)
    stages = recipe.get("stages")
    if not isinstance(stages, list) or not 1 <= len(stages) <= 100:
        issues.append(issue("$.stages", "必须包含 1–100 个计算阶段", "size")); stages = []
    ids, dependencies = set(), {}
    structure_id = structure.get("id") if isinstance(structure, dict) and isinstance(structure.get("id"), str) else None
    for index, stage in enumerate(stages):
        path = f"$.stages[{index}]"
        if not _closed(stage, {"id", "kind", "label", "description", "depends_on", "parameters", "input", "potcar"}, path, issues): continue
        stage_id = stage.get("id")
        _text(stage_id, path + ".id", issues, maximum=128); _text(stage.get("label"), path + ".label", issues, maximum=200)
        valid_id = isinstance(stage_id, str) and bool(ID_RE.fullmatch(stage_id))
        if not valid_id: issues.append(issue(path + ".id", "ID 格式无效", "pattern"))
        elif stage_id in ids or stage_id == structure_id: issues.append(issue(path + ".id", "阶段 ID 重复", "duplicate"))
        else: ids.add(stage_id)
        if stage.get("kind") not in KINDS: issues.append(issue(path + ".kind", "仅支持 relax/scf/bands/dos", "enum"))
        if "description" in stage: _text(stage.get("description"), path + ".description", issues, required=False, maximum=2000)
        deps = stage.get("depends_on")
        if not isinstance(deps, list) or not deps or len(deps) > 20 or len(set(map(str, deps))) != len(deps):
            issues.append(issue(path + ".depends_on", "必须是非空、无重复的上游 ID 列表", "dependencies")); deps = []
        else:
            for offset, dep in enumerate(deps): _text(dep, f"{path}.depends_on[{offset}]", issues, maximum=128)
            deps = [dep for dep in deps if isinstance(dep, str)]
        if valid_id: dependencies[stage_id] = deps
        _layer(stage.get("parameters", {"values": {}}), path + ".parameters", issues)
        if "input" in stage: _input(stage["input"], path + ".input", issues)
        if "potcar" in stage: _potcar(stage["potcar"], path + ".potcar", issues)
    all_ids = ids | ({structure_id} if structure_id else set())
    for stage_id, deps in dependencies.items():
        for dep in deps:
            if dep not in all_ids: issues.append(issue(f"$.stages[{next((i for i,s in enumerate(stages) if s.get('id') == stage_id), 0)}].depends_on", f"上游 {dep} 不存在", "reference"))
    visiting, visited = set(), set()
    def walk(key):
        if key == structure_id: return False
        if key in visiting: return True
        if key in visited: return False
        visiting.add(key)
        if any(walk(parent) for parent in dependencies.get(key, [])): return True
        visiting.remove(key); visited.add(key); return False
    if any(walk(key) for key in dependencies): issues.append(issue("$.stages", "阶段依赖存在循环", "cycle"))
    runtime = recipe.get("runtime")
    if runtime is not None and _closed(runtime, {"alias", "template_id", "remote_workspace_root", "limits"}, "$.runtime", issues):
        for key in ("alias", "template_id", "remote_workspace_root"):
            if key in runtime: _text(runtime[key], f"$.runtime.{key}", issues, required=False, maximum=500)
        limits = runtime.get("limits")
        if limits is not None and _closed(limits, {"max_inflight", "max_per_host", "max_cores_per_host"}, "$.runtime.limits", issues):
            for key, raw in limits.items():
                if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0: issues.append(issue(f"$.runtime.limits.{key}", "必须是非负整数", "type"))
    return issues


def _depths(recipe):
    structure_id = recipe["structure"]["id"]
    parents = {stage["id"]: stage["depends_on"] for stage in recipe["stages"]}
    memo = {structure_id: 0}
    def depth(key):
        if key not in memo: memo[key] = 1 + max(depth(parent) for parent in parents[key])
        return memo[key]
    for key in parents: depth(key)
    return memo


def materialize(recipe):
    issues = validate(recipe)
    if issues: raise RecipeError(issues)
    recipe = copy.deepcopy(recipe)
    depths = _depths(recipe); rows = {}
    structure = recipe["structure"]
    def position(key):
        column = depths[key]; row = rows.get(column, 0); rows[column] = row + 1
        return {"x": 40 + 270 * column, "y": 80 + 210 * row}
    nodes = [{"id": structure["id"], "type": "step", "position": position(structure["id"]), "data": {
        "kind": "structure", "label": structure["label"], "note": recipe.get("description", ""),
        "executionMode": "vasp", "stageSequence": 1, "stageSlug": "structure",
        "params": {"material": structure["document"].get("formula", "未指定")}, "structure": structure["document"]}}]
    edges = []
    for sequence, stage in enumerate(recipe["stages"], 2):
        stage_slug = re.sub(r"[^a-z0-9]+", "-", stage["id"].lower()).strip("-")[:48].rstrip("-") or stage["kind"]
        data = {"executionMode": "vasp", "stageSequence": sequence, "stageSlug": stage_slug,
                "kind": stage["kind"], "label": stage["label"], "note": stage.get("description", ""),
                "params": {"functional": "", "encut": "", "kmesh": "", "spin": ""},
                "parameters": stage.get("parameters", {"values": {}})}
        if stage.get("input"): data["inputDraft"] = stage["input"]
        if stage.get("potcar"): data["recipePotcar"] = stage["potcar"]
        nodes.append({"id": stage["id"], "type": "step", "position": position(stage["id"]), "data": data})
        edges.extend({"id": f"recipe:{parent}->{stage['id']}", "source": parent, "target": stage["id"]} for parent in stage["depends_on"])
    content = {"name": recipe["name"], "nodes": nodes, "edges": edges,
               "parameters": {"version": 1, "shared": recipe.get("shared", {"values": {}})}}
    if recipe.get("runtime") is not None: content["recipeRuntime"] = recipe["runtime"]
    identity = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    project = {"id": "recipe-" + identity[:24], "remoteSlug": "recipe-" + identity[:24], **content, "updatedAt": "1970-01-01T00:00:00Z"}
    validate_workspace({"activeId": project["id"], "projects": [project]})
    return project


def export_project(project):
    validate_workspace({"activeId": project["id"], "projects": [project]})
    structures = [node for node in project["nodes"] if node["data"]["kind"] == "structure"]
    if len(structures) != 1 or not structures[0]["data"].get("structure"):
        raise RecipeError([issue("$.structure", "导出配方需要唯一、已导入的结构节点", "structure")])
    root = structures[0]
    stages = []
    for node in project["nodes"]:
        if node is root: continue
        parents = [edge["source"] for edge in project["edges"] if edge["target"] == node["id"]]
        stage = {"id": node["id"], "kind": node["data"]["kind"], "label": node["data"]["label"],
                 "description": node["data"].get("note", ""), "depends_on": parents,
                 "parameters": node["data"].get("parameters", {"values": {}})}
        if node["data"].get("inputDraft"): stage["input"] = node["data"]["inputDraft"]
        if node["data"].get("recipePotcar"): stage["potcar"] = node["data"]["recipePotcar"]
        stages.append(stage)
    recipe = {"schema": SCHEMA, "name": project["name"], "description": root["data"].get("note", ""),
              "structure": {"id": root["id"], "label": root["data"]["label"], "document": root["data"]["structure"]},
              "shared": project.get("parameters", {}).get("shared", {"values": {}}), "stages": stages}
    if project.get("recipeRuntime") is not None: recipe["runtime"] = project["recipeRuntime"]
    issues = validate(recipe)
    if issues: raise RecipeError(issues)
    return recipe


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name); handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    try: os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()


def import_text(text):
    if not isinstance(text, str): raise RecipeError([issue("$", "配方必须是 UTF-8 JSON 文本", "type")])
    raw = text.encode("utf-8")
    if not raw or len(raw) > MAX_BYTES: raise RecipeError([issue("$", "配方大小必须在 1 B–2 MiB 之间", "size")])
    try: recipe = json.loads(text)
    except json.JSONDecodeError as exc: raise RecipeError([issue("$", f"JSON 语法错误：第 {exc.lineno} 行第 {exc.colno} 列", "json")]) from exc
    project = materialize(recipe)
    digest = hashlib.sha256(raw).hexdigest(); path = ROOT / "recipes" / "originals" / digest
    if path.exists() and path.read_bytes() != raw: raise ValueError("配方原稿存储冲突")
    if not path.exists(): _atomic(path, raw)
    metadata = {"schema": SCHEMA, "sha256": digest, "bytes": len(raw), "imported_at": datetime.now(timezone.utc).isoformat(), "source_path": str(path)}
    return {"recipe": recipe, "project": project, "original": metadata, "submission_authorized": False}


def schema_document():
    text = {"type": "string", "maxLength": 2000}
    layer = {"type": "object", "additionalProperties": False, "required": ["values"], "properties": {
        "values": {"type": "object", "maxProperties": 100, "additionalProperties": {"type": ["string", "null"], "maxLength": 200}},
        "confirmed_custom_tags": {"type": "array", "maxItems": 100, "items": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"}},
        "preset": {"enum": ["none", "basic"]},
        "moments": {"type": "object", "maxProperties": 1500, "additionalProperties": {"type": ["string", "null"], "maxLength": 200}},
        "hubbard": {"type": "object", "propertyNames": {"pattern": "^[A-Z][a-z]?$"}, "additionalProperties": {"type": "object", "additionalProperties": False, "required": ["l", "u", "j"], "properties": {"l": {"type": "string"}, "u": {"type": "string"}, "j": {"type": "string"}}}}}}
    stage = {"type": "object", "additionalProperties": False, "required": ["id", "kind", "label", "depends_on", "parameters"], "properties": {
        "id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"}, "kind": {"enum": list(KINDS)},
        "label": {"type": "string", "minLength": 1, "maxLength": 200}, "description": text,
        "depends_on": {"type": "array", "minItems": 1, "maxItems": 20, "uniqueItems": True, "items": {"type": "string"}},
        "parameters": layer,
        "input": {"type": "object", "additionalProperties": False, "properties": {key: {"type": "string", "maxLength": 20000} for key in ("grid", "center", "path", "line_points", "potcar_id", "potcar_provenance_id", "reason")}},
        "potcar": {"type": "object", "additionalProperties": False, "required": ["functional", "variants"], "properties": {"functional": {"type": "string", "minLength": 1}, "variants": {"type": "object", "minProperties": 1, "propertyNames": {"pattern": "^[A-Z][a-z]?$"}, "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 120}}}}}}
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://local.fpilot/recipe/v1.schema.json",
            "title": "First Principles Pilot recipe v1", "type": "object", "additionalProperties": False,
            "required": ["schema", "name", "structure", "stages"],
            "properties": {"schema": {"const": SCHEMA}, "name": {"type": "string", "minLength": 1, "maxLength": 120}, "description": text,
                           "structure": {"type": "object", "additionalProperties": False, "required": ["id", "label", "document"], "properties": {"id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"}, "label": {"type": "string", "minLength": 1, "maxLength": 200}, "document": {"type": "object"}}},
                           "shared": layer, "stages": {"type": "array", "minItems": 1, "maxItems": 100, "items": stage},
                           "runtime": {"type": "object", "additionalProperties": False, "properties": {"alias": {"type": "string"}, "template_id": {"type": "string"}, "remote_workspace_root": {"type": "string"}, "limits": {"type": "object", "additionalProperties": False, "properties": {key: {"type": "integer", "minimum": 0} for key in ("max_inflight", "max_per_host", "max_cores_per_host")}}}}},
            "x-validator": "POST /api/recipes/validate returns authoritative field-level semantic issues"}
