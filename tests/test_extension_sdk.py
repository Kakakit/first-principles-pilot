import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import extension_sdk, postprocessing


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'resources/extensions/structure-symmetry/scripts/symmetry.py'
SI = (ROOT / 'tests/fixtures/Si.POSCAR').read_bytes()


class ExtensionSDKTests(unittest.TestCase):
    def test_install_and_enable_are_separate_and_revision_bound(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(extension_sdk, 'ROOT', Path(temp)):
            receipt = extension_sdk.status('structure-symmetry')
            self.assertTrue(receipt['valid'])
            self.assertFalse(receipt['installed'])
            installed = extension_sdk.install('structure-symmetry')
            self.assertTrue(installed['installed'])
            self.assertFalse(installed['enabled'])
            self.assertTrue(extension_sdk.enable('structure-symmetry', True)['enabled'])
            self.assertFalse(extension_sdk.enable('structure-symmetry', False)['enabled'])
            with patch.object(extension_sdk.importlib.util, 'find_spec', return_value=None):
                self.assertFalse(extension_sdk.status('structure-symmetry', refresh=True)['valid'])
                with self.assertRaisesRegex(ValueError, '缺少运行依赖'):
                    extension_sdk.install('structure-symmetry')

    def test_validator_rejects_invalid_fixture_and_uncontrolled_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'test-extension'
            package.mkdir()
            source = json.loads((ROOT / 'resources/extensions/structure-symmetry/extension.sdk.json').read_text())
            source['id'] = package.name
            source['side_effects'] = ['local_read', 'remote_submit']
            (package / 'extension.sdk.json').write_text(json.dumps(source))
            with patch.object(extension_sdk, 'BUNDLES', Path(temp)):
                with self.assertRaisesRegex(ValueError, '副作用'):
                    extension_sdk.validate(package.name)
                source['side_effects'] = ['local_read', 'local_write']
                (package / 'extension.sdk.json').write_text(json.dumps(source))
                with self.assertRaisesRegex(ValueError, '不存在'):
                    extension_sdk.validate(package.name)

    def _run_symmetry(self, source):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'POSCAR').write_bytes(source)
            request = directory / 'request.json'
            request.write_text(json.dumps({'source_file': str(directory / 'POSCAR'), 'source_name': 'POSCAR',
                                           'options': {'structure_source': 'POSCAR', 'angle_tolerance_degree': 5},
                                           'output_file': str(directory / 'symmetry.json')}))
            result = subprocess.run([sys.executable, str(SCRIPT), 'run', '--request', str(request)],
                                    cwd=directory, capture_output=True, text=True, timeout=120)
            report = json.loads((directory / 'symmetry.json').read_text()) if (directory / 'symmetry.json').exists() else None
            self.assertEqual((directory / 'POSCAR').read_bytes(), source)
            return result, report

    def test_ideal_perturbed_and_truncated_structure(self):
        result, ideal = self._run_symmetry(SI)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row['space_group_number'] for row in ideal['results']], [227, 227, 227])
        self.assertTrue(ideal['results'][0]['equivalent_groups'])
        perturbed = SI.replace(b'0.25 0.25 0.25', b'0.255 0.25 0.25')
        result, report = self._run_symmetry(perturbed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(report['results'][0]['space_group_number'], report['results'][2]['space_group_number'])
        result, report = self._run_symmetry(b'Si\n1.0\n0 0 0\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(report)

    def test_displayed_structure_can_be_analyzed_without_attempt(self):
        report = postprocessing.local_structure_symmetry(SI.decode(), 5)
        self.assertEqual(report['results'][0]['space_group_number'], 227)
        self.assertEqual(report['input']['name'], 'POSCAR')
        with self.assertRaisesRegex(ValueError, '分析失败'):
            postprocessing.local_structure_symmetry('truncated', 5)


if __name__ == '__main__':
    unittest.main()
