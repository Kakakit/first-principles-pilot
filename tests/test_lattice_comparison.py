import hashlib
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import execution as ex, scan_results


def lattice_block(matrix):
    return 'direct lattice vectors                 reciprocal lattice vectors\n' + ''.join(
        ' '.join(str(value) for value in row) + ' 9 8 7\n' for row in matrix)


class LatticeComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.temp.name)
        self.outcar = self.root / 'OUTCAR'
        self.record = {'id': 'ab' * 16, 'project_id': 'p', 'bundle_id': 'cd' * 32,
                       'state': 'COMPLETED', 'config': {'alias': 'fixture-host'},
                       'evidence': {'vasp_timing': True, 'ediff_reached': True}}
        self.manifest = {'stage': 'relax', 'species': ['Si']}
        self.files = {'POSCAR': b'Si\n1\n2 0 0\n0 3 0\n0 0 4\nSi\n1\nDirect\n0 0 0\n',
                      'INCAR': b'ENCUT = 400\n', 'POTCAR': b'fixture'}
        patch.object(ex, 'ROOT', self.root).start()
        patch('backend.remote_transport._interpreter',return_value=sys.executable).start()
        patch.object(ex, 'bundle', side_effect=lambda *a, **kw: (self.manifest, self.files)).start()
        patch.object(ex, 'attempt_path', return_value={'shell': str(self.root)}).start()
        patch.object(ex, 'get', return_value=self.record).start()
        self.calls = []
        patch.object(ex, 'ssh', side_effect=self.local_summary).start()

    def local_summary(self, alias, command, **options):
        self.assertEqual(alias, 'fixture-host')
        self.assertEqual(options['stdout_limit'], 16384)
        self.calls.append(command)
        before = self.outcar.read_bytes()
        process = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        self.assertEqual(self.outcar.read_bytes(), before)
        return {'exit_code': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}

    def write(self, body):
        self.outcar.write_text(body + '\n free  energy   TOTEN = -12.5 eV\n reached required accuracy\n')

    def test_last_actual_lattice_uses_direct_vectors_and_retains_provenance(self):
        self.write(lattice_block([[2, 0, 0], [0, 3, 0], [0, 0, 4]]) +
                   lattice_block([[3, 0, 0], [1, 4, 0], [0, 0, 5]]))
        metrics = scan_results._extract(self.record)
        self.assertEqual(metrics['lattice_a']['value'], 3)
        self.assertAlmostEqual(metrics['lattice_b']['value'], math.sqrt(17))
        self.assertEqual(metrics['lattice_c']['value'], 5)
        self.assertAlmostEqual(metrics['lattice_alpha']['value'], 90)
        self.assertAlmostEqual(metrics['lattice_beta']['value'], 90)
        self.assertAlmostEqual(metrics['lattice_gamma']['value'], math.degrees(math.acos(1 / math.sqrt(17))))
        self.assertEqual(metrics['volume']['value'], 60)
        self.assertEqual([metrics[key]['unit'] for key in scan_results.LATTICE_KEYS], ['Å'] * 3 + ['°'] * 3 + ['Å³'])
        source = metrics['lattice_a']['source']
        self.assertEqual(source['file'], 'OUTCAR')
        self.assertEqual(source['sha256'], hashlib.sha256(self.outcar.read_bytes()).hexdigest())
        self.assertEqual(source['line'], 5)
        self.assertEqual(metrics['energy']['value'], -12.5)
        self.assertTrue(metrics['ionic_converged'])
        self.assertEqual(len(self.calls), 1)

    def test_missing_truncated_or_degenerate_last_lattice_never_uses_an_earlier_cell(self):
        good = lattice_block([[2, 0, 0], [0, 3, 0], [0, 0, 4]])
        for body in ('no lattice', good + 'direct lattice vectors\n2 0 0 0 0 0\n',
                     lattice_block([[2, 0, 0], [4, 0, 0], [0, 0, 4]]),
                     lattice_block([[float('nan'), 0, 0], [0, 3, 0], [0, 0, 4]])):
            with self.subTest(body=body):
                self.write(body)
                metrics = scan_results._extract(self.record)
                self.assertTrue(all(metrics[key] is None for key in scan_results.LATTICE_KEYS))
                self.assertIn('未用项目原始结构代替', metrics['lattice_error'])
                self.assertEqual(metrics['energy']['value'], -12.5)

    def test_hash_changes_or_missing_post_read_hash_reject_mixed_metrics(self):
        before = 'a' * 64
        for after in ('', '\nFPILOT_SHA_AFTER=' + 'b' * 64):
            with patch.object(ex, 'ssh', return_value={'exit_code': 0, 'stdout': 'FPILOT_SHA=' + before + after}):
                with self.assertRaisesRegex(ValueError, '读取过程中'):
                    scan_results._extract(self.record)

    def test_fixed_cell_and_incomplete_program_evidence_are_distinct(self):
        self.manifest['stage'] = 'scf'
        self.write(lattice_block([[2, 0, 0], [0, 3, 0], [0, 0, 4]]))
        row = scan_results.compare('p', [self.record['id']])['rows'][0]
        self.assertEqual(row['metrics']['volume']['value'], 24)
        self.assertIsNone(row['metrics']['ionic_converged'])
        self.assertIsNotNone(row['cell_composition_sha256'])
        self.record['evidence']['vasp_timing'] = False
        row = scan_results.compare('p', [self.record['id']])['rows'][0]
        self.assertIsNone(row['metrics'])
        self.assertEqual(len(self.calls), 1)

    def test_cell_composition_distinguishes_stoichiometry_and_supercells(self):
        def fingerprint(species, counts):
            raw = '\n'.join(['cell', '1', '2 0 0', '0 3 0', '0 0 4', species, counts, 'Direct'])
            return scan_results._cell_composition({}, {'POSCAR': raw.encode()})
        self.assertEqual(fingerprint('Fe Cr O', '1 2 4'), fingerprint('O Cr Fe', '4 2 1'))
        self.assertNotEqual(fingerprint('Fe Cr O', '1 2 4'), fingerprint('Fe Cr O', '2 1 4'))
        self.assertNotEqual(fingerprint('Fe Cr O', '1 2 4'), fingerprint('Fe Cr O', '2 4 8'))
        self.assertIsNone(scan_results._cell_composition({}, {}))


if __name__ == '__main__':
    unittest.main()
