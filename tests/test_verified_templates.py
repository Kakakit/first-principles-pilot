import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import job_templates, verified_templates, servers


class VerifiedTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = patch.object(job_templates, 'ROOT', Path(self.temp.name))
        self.root.start()

    def tearDown(self):
        self.root.stop()
        self.temp.cleanup()

    def test_peer_source_is_preserved_and_only_resources_are_mapped(self):
        entry, text = verified_templates.source('jutai-1')
        result = verified_templates.import_verified('jutai-1')
        record = result['template']
        self.assertEqual(record['original_text'], text)
        self.assertEqual(record['original_hash'], entry['source_hash'])
        preview = job_templates.render_template(record['id'], dict(node='node03', ppn=48, name='FP_C4C2', walltime='00:10:00'))
        self.assertTrue(preview['submission_supported'], preview['execution_blockers'])
        self.assertEqual(job_templates.code_lines(text), job_templates.code_lines(preview['rendered_text']))
        self.assertIn('#PBS -l nodes=node03:ppn=48', preview['rendered_text'])
        self.assertFalse(job_templates.render_template(record['id'], {}, alias='jutai-5')['submission_supported'])
        changed = text.replace('ulimit -s unlimited', 'ulimit -s unlimited\nexit 0')
        copy = job_templates.import_template(changed, 'jutai-1', 'vasp_std', 'edited')
        self.assertTrue(job_templates.render_template(copy['id'])['submission_supported'])

    def test_changed_registry_runtime_is_not_covered_by_peer_receipt(self):
        _, text = verified_templates.source('jutai-1')
        config = dict(servers.select_profile('jutai-1'), vasp_executable='/other/vasp_std')
        with patch.object(servers, 'select_profile', return_value=config):
            self.assertFalse(verified_templates.matches(text, 'jutai-1'))

    def test_source_drift_blocks_import_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'jutai-1'
            path.mkdir()
            (path / verified_templates.SOURCES[0][1]).write_text('changed')
            with patch.object(verified_templates, 'SOURCE_ROOT', Path(temp)):
                self.assertFalse(verified_templates.catalog()[0]['executable'])
                with self.assertRaises(ValueError):
                    verified_templates.import_verified('jutai-1')
                self.assertEqual(job_templates.list_templates(), [])

    def test_all_intact_sources_can_be_imported_with_exact_bytes(self):
        for entry in verified_templates.catalog():
            if entry['alias'] == 'jutai-1':
                continue
            self.assertTrue(entry['executable'])
            result = verified_templates.import_verified(entry['key'])
            self.assertEqual(result['original']['original_hash'], entry['source_hash'])
            self.assertTrue(result['template']['submission_supported'])

    def test_static_catalog_route_is_not_shadowed_by_id_route(self):
        from backend.app import app
        from starlette.routing import Match
        route = next(route for route in app.routes if route.matches(dict(type='http', path='/api/job-templates/verified', method='GET'))[0] == Match.FULL)
        self.assertEqual(route.name, 'verified_template_list')
        self.assertEqual(len(route.endpoint()), 5)
        route = next(route for route in app.routes if route.matches(dict(type='http', path='/api/job-templates/verified/jutai-1/import', method='POST'))[0] == Match.FULL)
        self.assertEqual(route.endpoint('jutai-1')['template']['alias'], 'jutai-1')
