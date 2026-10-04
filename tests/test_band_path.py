import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.band_path import generate
from backend import app
from fastapi import HTTPException


class BandPathTests(unittest.TestCase):
    def test_original_cell_path_is_editable_pairs_with_source_hash(self):
        structure=json.loads((Path(__file__).parent/'fixtures/Si.structure.json').read_text())
        result=generate(structure)
        lines=result['path'].splitlines()
        self.assertGreaterEqual(len(lines),2)
        self.assertEqual(len(lines)%2,0)
        self.assertEqual(result['structure_hash'],structure['source']['sha256'])
        self.assertEqual(result['algorithm'],'seekpath.get_path_orig_cell')
        for line in lines:
            parts=line.split()
            self.assertEqual(len(parts),4)
            for value in parts[:3]:float(value)

    def test_attempt_path_uses_completed_scf_contcar_and_project(self):
        structure=json.loads((Path(__file__).parent/'fixtures/Si.structure.json').read_text())
        record={'project_id':'p','state':'COMPLETED','bundle_id':'frozen',
                'config':{'alias':'jutai-1'},'job_id':'123.node01',
                'evidence':{'ediff_reached':True}}
        with patch('backend.execution.get',return_value=record), \
             patch('backend.execution.bundle',return_value=({'stage':'scf'},{})), \
             patch('backend.artifacts.preview',return_value={'partial':False,'structure':structure}) as preview:
            result=app.attempt_band_path('attempt',app.AttemptBandPathRequest(project_id='p'))
            self.assertEqual(result['source_attempt_id'],'attempt')
            self.assertEqual(result['structure_hash'],structure['source']['sha256'])
            preview.assert_called_once_with('attempt','CONTCAR','head',262144)
            with self.assertRaises(HTTPException):
                app.attempt_band_path('attempt',app.AttemptBandPathRequest(project_id='other'))
