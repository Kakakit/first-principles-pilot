import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import handoffs
from backend.app import HandoffText, handoff_import


def sample():
    return {
        "schema": "fpilot.handoff.v1",
        "binding": {
            "attempt": "/home/user/230269265lht/material-projects/si/flows/single/stages/scf/attempts/attempt-0001",
            "job_id": "123.node01",
            "profile": "/home/user/fpilot/jobs/profiles/jutai-1.json",
            "contract": "/home/user/230269265lht/contracts/scf-output.json",
        },
        "created_at": "2026-09-10T08:03:47.140122+00:00",
        "scheduler": {"status": "TERMINAL", "job": {"qstat": {"stdout": "Job Id: 123.node01\n Exit_status = 0\n"}}},
        "review_receipt": {"status": "REVIEWED", "decision": "REVISE", "program_status": "COMPLETE", "calculation_status": "INCOMPLETE", "promotion_performed": False},
        "output_summary": {"schema": "fpilot.output-summary.v1", "input_manifest_sha256": "a" * 64, "timing_found": True},
        "audit": {"decision": "REVISE", "program_status": "COMPLETE", "calculation_status": "INCOMPLETE"},
        "limits": "终态与审核建议不是物理结论；原始大文件留在服务器。",
    }


class HandoffTests(unittest.TestCase):
    def test_import_normalizes_read_only_record_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            store = handoffs.HandoffStore(Path(folder))
            text = json.dumps(sample(), ensure_ascii=False, indent=2)
            value = store.import_text(text, "jutai-1")
            self.assertTrue(value["read_only"])
            self.assertFalse(value["submission_authorized"])
            self.assertEqual(value["scheduler_exit_status"], 0)
            self.assertEqual(value["decision"], "REVISE")
            self.assertEqual(value["source"]["host"], "jutai-1")
            self.assertEqual(value["input_hashes"][0]["sha256"], "a" * 64)
            self.assertEqual(store.original(value["id"]).decode(), text)

    def test_repeat_import_is_idempotent_and_different_original_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            store = handoffs.HandoffStore(Path(folder)); text = json.dumps(sample())
            first = store.import_text(text, "jutai-1"); second = store.import_text(text, "jutai-1")
            self.assertEqual(first["id"], second["id"]); self.assertTrue(second["duplicate"])
            self.assertEqual(len(store.list()), 1)
            changed = sample(); changed["limits"] = "changed"
            with self.assertRaisesRegex(handoffs.HandoffError, "未覆盖"):
                store.import_text(json.dumps(changed), "jutai-1")
            self.assertEqual(store.original(first["id"]).decode(), text)

    def test_same_report_on_different_source_host_is_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            store = handoffs.HandoffStore(Path(folder)); text = json.dumps(sample())
            self.assertNotEqual(store.import_text(text, "jutai-1")["id"], store.import_text(text, "archive-host")["id"])
            self.assertEqual(len(store.list()), 2)

    def test_invalid_report_returns_field_issues_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = handoffs.HandoffStore(Path(folder)); value = sample(); value["binding"]["attempt"] = "relative/path"; value.pop("audit")
            with self.assertRaises(handoffs.HandoffError) as caught:
                store.import_text(json.dumps(value), "1.2.3.4")
            paths = {row["path"] for row in caught.exception.issues}
            self.assertIn("$.source_host", paths); self.assertIn("$.binding.attempt", paths); self.assertIn("$.audit", paths)
            self.assertEqual(store.list(), [])

    def test_api_uses_same_store_and_never_authorizes_submission(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(handoffs, "ROOT", Path(folder)):
            value = handoff_import(HandoffText(text=json.dumps(sample()), source_host="jutai-1"))
            self.assertFalse(value["submission_authorized"])
            self.assertTrue(value["read_only"])


if __name__ == "__main__":
    unittest.main()
