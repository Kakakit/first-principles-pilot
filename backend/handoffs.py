"""Read-only fpilot.handoff.v1 imports with immutable source retention."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

from .storage import ROOT

SCHEMA = "fpilot.handoff.v1"
MAX_BYTES = 10 * 1024 * 1024
HOST = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
HEX64 = re.compile(r"[a-fA-F0-9]{64}")


class HandoffError(ValueError):
    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = issues or []


def _issue(path, message, code="invalid"):
    return {"path": path, "message": message, "code": code}


def _text(value, path, issues, maximum=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        issues.append(_issue(path, f"必须是 1–{maximum} 字符的字符串"))


def validate(report, source_host):
    issues = []
    if not isinstance(report, dict):
        return [_issue("$", "交接报告必须是 JSON 对象", "type")]
    if report.get("schema") != SCHEMA:
        issues.append(_issue("$.schema", f"必须是 {SCHEMA}", "const"))
    if not isinstance(source_host, str) or not HOST.fullmatch(source_host):
        issues.append(_issue("$.source_host", "必须是已登记风格的 SSH 别名，不填写地址或端口"))
    binding = report.get("binding")
    if not isinstance(binding, dict):
        issues.append(_issue("$.binding", "必须是对象", "type"))
    else:
        for key in ("attempt", "job_id", "profile", "contract"):
            _text(binding.get(key), f"$.binding.{key}", issues)
        attempt = binding.get("attempt")
        if isinstance(attempt, str) and (not PurePosixPath(attempt).is_absolute() or ".." in PurePosixPath(attempt).parts):
            issues.append(_issue("$.binding.attempt", "必须是服务器上的规范绝对路径", "path"))
        job_id = binding.get("job_id")
        if isinstance(job_id, str) and not JOB_ID.fullmatch(job_id):
            issues.append(_issue("$.binding.job_id", "不是可展示的准确调度器 job ID", "format"))
    _text(report.get("created_at"), "$.created_at", issues, 100)
    for key in ("scheduler", "review_receipt", "output_summary", "audit"):
        if not isinstance(report.get(key), dict):
            issues.append(_issue(f"$.{key}", "必须是对象；缺失时不能猜测历史证据", "type"))
    _text(report.get("limits"), "$.limits", issues, 2000)
    return issues


def _walk_hashes(value, path="$", output=None):
    output = output if output is not None else []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(child, str) and HEX64.fullmatch(child) and any(token in key.lower() for token in ("sha", "hash", "digest")):
                output.append({"path": child_path, "sha256": child.lower()})
            else:
                _walk_hashes(child, child_path, output)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_hashes(child, f"{path}[{index}]", output)
    return output


def _first(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def normalize(report, source_host, original_sha256):
    binding = report["binding"]
    review = report["review_receipt"]
    audit = report["audit"]
    scheduler = report["scheduler"]
    identity_raw = json.dumps({"host": source_host, "attempt": binding["attempt"], "job_id": binding["job_id"]}, sort_keys=True, separators=(",", ":")).encode()
    identity = hashlib.sha256(identity_raw).hexdigest()
    decision = _first(review, "decision") or _first(audit, "decision") or "UNKNOWN"
    program_status = _first(review, "program_status") or _first(audit, "program_status") or "UNKNOWN"
    calculation_status = _first(review, "calculation_status") or _first(audit, "calculation_status") or "UNKNOWN"
    scheduler_status = _first(scheduler, "status", "scheduler_status") or "UNKNOWN"
    scheduler_exit_status = _first(scheduler, "scheduler_exit_status", "exit_status")
    if scheduler_exit_status is None:
        qstat = scheduler.get("job", {}).get("qstat", {}).get("stdout", "") if isinstance(scheduler.get("job"), dict) else ""
        match = re.search(r"\bExit_status\s*=\s*(-?\d+)", qstat, re.I)
        scheduler_exit_status = int(match.group(1)) if match else None
    hashes = _walk_hashes({"output_summary": report["output_summary"], "audit": audit, "review_receipt": review})
    return {
        "id": identity,
        "schema": SCHEMA,
        "read_only": True,
        "submission_authorized": False,
        "source": {"host": source_host, "attempt": binding["attempt"], "profile": binding["profile"], "contract": binding["contract"], "original_sha256": original_sha256},
        "job_id": binding["job_id"],
        "created_at": report["created_at"],
        "scheduler_status": scheduler_status,
        "scheduler_exit_status": scheduler_exit_status,
        "decision": decision,
        "program_status": program_status,
        "calculation_status": calculation_status,
        "input_hashes": hashes,
        "output_summary": report["output_summary"],
        "audit": audit,
        "review_receipt": review,
        "limits": report["limits"],
        "source_complete": bool(hashes),
    }


class HandoffStore:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else ROOT)
        self.originals = self.root / "handoffs" / "originals"
        self.originals.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS handoffs (
                id TEXT PRIMARY KEY, original_sha256 TEXT NOT NULL,
                source_host TEXT NOT NULL, attempt TEXT NOT NULL, job_id TEXT NOT NULL,
                imported_at TEXT NOT NULL, normalized_json TEXT NOT NULL)""")
            db.commit()

    def connect(self):
        return sqlite3.connect(str(self.root / "handoffs.sqlite3"), timeout=10)

    def _store_original(self, raw, digest):
        path = self.originals / digest
        if path.exists():
            if path.read_bytes() != raw:
                raise HandoffError("同一 hash 的交接原稿内容不一致；未覆盖")
            return path
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return path

    def import_text(self, text, source_host):
        raw = text.encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise HandoffError("交接报告不能超过 10 MiB", [_issue("$", "文件超过 10 MiB", "max_bytes")])
        try:
            report = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HandoffError("交接报告不是有效 JSON", [_issue("$", f"第 {exc.lineno} 行：{exc.msg}", "json")]) from exc
        issues = validate(report, source_host)
        if issues:
            raise HandoffError("交接报告校验失败", issues)
        digest = hashlib.sha256(raw).hexdigest()
        normalized = normalize(report, source_host, digest)
        packed = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        imported_at = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT original_sha256,normalized_json,imported_at FROM handoffs WHERE id=?", (normalized["id"],)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise HandoffError("同一主机、attempt 与 job ID 已有不同原稿；为保护来源未覆盖")
                value = json.loads(existing[1]); value["imported_at"] = existing[2]; value["duplicate"] = True
                return value
            self._store_original(raw, digest)
            db.execute("INSERT INTO handoffs VALUES (?,?,?,?,?,?,?)", (normalized["id"], digest, source_host, normalized["source"]["attempt"], normalized["job_id"], imported_at, packed))
            db.commit()
        normalized["imported_at"] = imported_at
        normalized["duplicate"] = False
        return normalized

    def list(self):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT normalized_json,imported_at FROM handoffs ORDER BY imported_at DESC,id").fetchall()
        output = []
        for packed, imported_at in rows:
            value = json.loads(packed); value["imported_at"] = imported_at
            output.append(value)
        return output

    def get(self, key):
        if not HEX64.fullmatch(key):
            raise HandoffError("交接记录不存在")
        with closing(self.connect()) as db:
            row = db.execute("SELECT normalized_json,imported_at FROM handoffs WHERE id=?", (key,)).fetchone()
        if not row:
            raise HandoffError("交接记录不存在")
        value = json.loads(row[0]); value["imported_at"] = row[1]
        return value

    def original(self, key):
        value = self.get(key)
        digest = value["source"]["original_sha256"]
        raw = (self.originals / digest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise HandoffError("交接原稿 hash 校验失败")
        return raw
