import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import recipes
from backend.app import RecipeDocument, RecipeProject, RecipeText, recipe_export, recipe_import, recipe_materialize, recipe_validate


FIXTURE = Path(__file__).parent / "fixtures" / "Si.structure.json"


def sample():
    structure = json.loads(FIXTURE.read_text())
    return {"schema": "fpilot.recipe.v1", "name": "Si 配方", "description": "导入为草稿，不自动提交",
            "structure": {"id": "si-structure", "label": "Si 结构", "document": structure},
            "shared": {"values": {"XC_FUNCTIONAL": "PBE", "ENCUT": "400", "EDIFF": "1e-5", "ISPIN": "1", "LDAU": "false"}},
            "stages": [
                {"id": "relax", "kind": "relax", "label": "优化", "depends_on": ["si-structure"], "parameters": {"preset": "basic", "values": {"NSW": "40", "EDIFFG": "-0.02"}}},
                {"id": "scf", "kind": "scf", "label": "SCF", "depends_on": ["relax"], "parameters": {"preset": "basic", "values": {"ISMEAR": "0", "SIGMA": "0.05"}}},
                {"id": "bands", "kind": "bands", "label": "能带", "depends_on": ["scf"], "parameters": {"preset": "basic", "values": {"ISMEAR": "0", "SIGMA": "0.05"}}},
            ], "runtime": {"alias": "jutai-1", "template_id": "reviewed-template", "remote_workspace_root": "$HOME/230269265lht", "limits": {"max_inflight": 2}}}


class RecipeTests(unittest.TestCase):
    def test_valid_recipe_has_deterministic_canvas_and_no_submission(self):
        value = sample(); first = recipes.materialize(value); second = recipes.materialize(copy.deepcopy(value))
        self.assertEqual(first, second)
        self.assertEqual([n["position"] for n in first["nodes"]], [{"x": 40, "y": 80}, {"x": 310, "y": 80}, {"x": 580, "y": 80}, {"x": 850, "y": 80}])
        self.assertNotIn("state", first)

    def test_export_and_recipe_materialization_match_project_content(self):
        project = recipes.materialize(sample())
        exported = recipes.export_project(project)
        self.assertEqual(recipes.materialize(exported), project)

    def test_field_errors_locate_invalid_assignment_reference_and_extra_field(self):
        value = sample(); value["extra"] = True; value["stages"][0]["parameters"]["values"]["MAGIC"] = "1; NSW=5"; value["stages"][1]["depends_on"] = ["missing"]
        issues = recipes.validate(value); paths = {row["path"] for row in issues}
        self.assertIn("$.extra", paths); self.assertIn("$.stages[0].parameters.values.MAGIC", paths); self.assertIn("$.stages[1].depends_on", paths)

    def test_cycle_is_rejected(self):
        value = sample(); value["stages"][0]["depends_on"] = ["scf"]
        self.assertTrue(any(row["code"] == "cycle" for row in recipes.validate(value)))

    def test_malformed_identity_and_dependency_types_return_issues_not_exceptions(self):
        value = sample(); value["structure"]["id"] = []; value["stages"][0]["id"] = {}; value["stages"][1]["depends_on"] = [[]]
        issues = recipes.validate(value)
        self.assertTrue(any(row["path"] == "$.structure.id" for row in issues))
        self.assertTrue(any(row["path"] == "$.stages[0].id" for row in issues))
        self.assertTrue(any(row["path"] == "$.stages[1].depends_on[0]" for row in issues))

    def test_import_preserves_exact_original_by_hash(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(recipes, "ROOT", Path(folder)):
            text = json.dumps(sample(), ensure_ascii=False, indent=2)
            result = recipes.import_text(text)
            path = Path(result["original"]["source_path"])
            self.assertEqual(path.read_text(), text)
            self.assertFalse(result["submission_authorized"])

    def test_gui_and_ai_api_use_same_materializer(self):
        value = sample()
        checked = recipe_validate(RecipeDocument(recipe=value))
        self.assertTrue(checked["valid"]); self.assertFalse(checked["submission_authorized"])
        ai_project = recipe_materialize(RecipeDocument(recipe=value))["project"]
        with tempfile.TemporaryDirectory() as folder, patch.object(recipes, "ROOT", Path(folder)):
            gui_project = recipe_import(RecipeText(text=json.dumps(value, ensure_ascii=False)))["project"]
        self.assertEqual(ai_project, gui_project)
        self.assertEqual(recipe_export(RecipeProject(project=gui_project))["recipe"], recipes.export_project(ai_project))


if __name__ == "__main__": unittest.main()
