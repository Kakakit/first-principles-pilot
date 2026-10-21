"""Scientific projection checks with local sources; no remote tools or SSH."""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend import analysis_queue, compact_plots, execution, interactive_plots as plots


def fixture(root, spins=1, soc=False, explicit=False, repeat_headers=False):
    """Two k points, two bands, three ions; first and last ions share an element."""
    nk = nb = 2
    names = 's py pz px dxy dyz dz2 dxz x2-y2'
    header = 'ion ' + names + ' tot\n'
    source = 'PROCAR\n# of k-points: 2 # of bands: 2 # of ions: 3\n'
    energy = np.empty((spins, nk, nb))
    weights = np.empty((spins, nk, nb, 3, 9))
    eig = f'1 1 1 {spins}\n' + 'header\n' * 4 + '4 2 2\n'
    for spin in range(spins):
        if explicit: source += f'spin component {spin + 1}\n'
        for k in range(nk):
            source += f'k-point {k + 1} : {k * .5} 0 0 weight = 0.5\n'
            for band in range(nb):
                energy[spin, k, band] = -1 + 2 * band + .2 * k + .1 * spin
                source += f'band {band + 1} # energy {energy[spin, k, band]:.8f} # occ. 1\n'
                source += header
                for component in range(4 if soc else 1):
                    if component and repeat_headers: source += header
                    for ion in range(3):
                        values = np.array([(.01 * (1 + spin + k + band + ion + orb)) for orb in range(9)])
                        if component == 0: weights[spin, k, band, ion] = values
                        else: values = np.full(9, -component * 3.0)
                        source += str(ion + 1) + ' ' + ' '.join(f'{value:.6f}' for value in values) + f' {values.sum():.6f}\n'
                    source += 'tot ' + ' '.join(['0'] * 10) + '\n'
    for k in range(nk):
        eig += f'\n{k * .5} 0 0 0.5\n'
        for band in range(nb):
            eig += str(band + 1) + ' ' + ' '.join(str(value) for value in energy[:, k, band]) + ' 1' * spins + '\n'
    (root / 'PROCAR').write_text(source)
    (root / 'EIGENVAL').write_text(eig)
    (root / 'POSCAR').write_text('test\n1\n1 0 0\n0 1 0\n0 0 1\nFe O Fe\n1 1 1\nDirect\n0 0 0\n.5 .5 .5\n.25 .25 .25\n')
    (root / 'KPOINTS').write_text('path\n2\nLine-mode\nReciprocal\n0 0 0 ! G\n.5 0 0 ! X\n')
    (root / 'OUTCAR').write_text('E-fermi : 0.25\nLNONCOLLINEAR = ' + ('T' if soc else 'F') + '\n')
    return energy, weights


class ProcarReaderTests(unittest.TestCase):
    def test_scalar_spin_matches_pymatgen_and_soc_matches_known_charge_projection(self):
        from pymatgen.io.vasp.outputs import Procar
        from pymatgen.electronic_structure.core import Spin
        for spins, soc, explicit, repeated in ((1, False, False, False), (2, False, False, False),
                                              (2, False, True, False), (1, True, False, False),
                                              (1, True, False, True)):
            with self.subTest(spins=spins, soc=soc, explicit=explicit, repeated=repeated), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                energy, weights = fixture(root, spins, soc, explicit, repeated)
                info = plots._procar(root / 'PROCAR', ['Fe', 'O', 'Fe'], root, soc, root / 'EIGENVAL')
                actual = np.load(root / 'band_weight.npy', mmap_mode='r')[:spins]
                np.testing.assert_allclose(np.load(root / 'band_energy.npy')[:spins], energy, atol=1e-6)
                self.assertEqual(info['spins'], spins)
                self.assertEqual(info['orbitals'][-1], 'dx2-y2')
                self.assertEqual(info['procar_sha256'], hashlib.sha256((root / 'PROCAR').read_bytes()).hexdigest())
                for spin, key in enumerate((Spin.up, Spin.down)[:spins]):
                    # This installed pymatgen release cannot parse the SOC tables.
                    reference = weights[spin] if soc else Procar(root / 'PROCAR').data[key]
                    np.testing.assert_allclose(reference, weights[spin], atol=1e-6)
                    np.testing.assert_allclose(actual[spin, :, :, 0], reference[:, :, [0, 2]].sum(axis=2), atol=1e-6)
                    np.testing.assert_allclose(actual[spin, :, :, 1], reference[:, :, 1], atol=1e-6)

    def test_truncated_duplicate_nonfinite_and_mismatched_sources_are_rejected(self):
        mutations = {
            'missing atom': lambda text: text.replace('3 0.030000', '4 0.030000', 1),
            'duplicate atom': lambda text: text.replace('2 0.020000', '1 0.020000', 1),
            'truncated': lambda text: text.rsplit('tot ', 1)[0],
            'nonfinite': lambda text: text.replace('1 0.010000', '1 nan', 1),
            'duplicate band': lambda text: text.replace('band 2 #', 'band 1 #', 1),
            'missing k': lambda text: text.replace('k-point 2 :', 'k-point 3 :', 1),
            'dimension': lambda text: text.replace('of ions: 3', 'of ions: 2', 1),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); fixture(root)
                (root / 'PROCAR').write_text(mutate((root / 'PROCAR').read_text()))
                with self.assertRaises(ValueError):
                    plots._procar(root / 'PROCAR', ['Fe', 'O', 'Fe'], root, False, root / 'EIGENVAL')
        for replacement in ('1 1 1 2', '4 3 2', '.6 0 0 0.5', '1 9.0 1'):
            with self.subTest(eigenval=replacement), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); fixture(root)
                text = (root / 'EIGENVAL').read_text()
                old = ('1 1 1 1' if replacement.startswith('1 1 1') else '4 2 2' if replacement.startswith('4 ') else
                       '0.5 0 0 0.5' if replacement.startswith('.6') else '1 -1.0 1')
                self.assertIn(old, text)
                (root / 'EIGENVAL').write_text(text.replace(old, replacement, 1))
                with self.assertRaises(ValueError):
                    plots._procar(root / 'PROCAR', ['Fe', 'O', 'Fe'], root, False, root / 'EIGENVAL')

    def test_soc_missing_magnetization_table_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root, soc=True)
            text = (root / 'PROCAR').read_text()
            (root / 'PROCAR').write_text(text.rsplit('1 -9.000000', 1)[0])
            with self.assertRaisesRegex(ValueError, '不完整'):
                plots._procar(root / 'PROCAR', ['Fe', 'O', 'Fe'], root, True)

    def test_compact_script_uses_builtin_reader_without_incar_doscar_or_external_program(self):
        for spins, soc in ((1, False), (2, False), (1, True)):
            with self.subTest(spins=spins, soc=soc), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'source'; root.mkdir()
                energy, _ = fixture(root, spins, soc)
                # Native source takes priority over unrelated/stale legacy files.
                (root / 'PBAND_Fe_SOC.dat').write_text('stale projection')
                (root / 'PBAND_O_SOC.dat').write_text('stale projection')
                before = {path.name: path.read_bytes() for path in root.iterdir()}
                out = Path(tmp) / 'derived/data.npz'
                with patch.object(execution, 'attempt_path', return_value={'shell': str(root)}), \
                     patch('backend.extension_remote.runtimes', return_value={}), \
                     patch.object(analysis_queue, 'interpreter', return_value=sys.executable):
                    command = compact_plots.script({'id': 'fixture', 'config': {'alias': 'fixture'}}, 'band', str(out))
                self.assertNotIn('subprocess', command)
                result = subprocess.run(shlex.split(command), env={**os.environ, 'PATH': ''}, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})
                with np.load(out, allow_pickle=False) as data:
                    receipt = json.loads(str(data['metadata']))
                    self.assertEqual(receipt['generation']['tool'], 'fpilot.procar-reader')
                    self.assertEqual(receipt['metadata']['spins'], spins)
                    self.assertEqual(receipt['metadata']['source_format'], 'procar')
                    self.assertEqual(receipt['metadata']['kpath_labels'], [{'index': 0, 'label': 'G'}, {'index': 1, 'label': 'X'}])
                    np.testing.assert_allclose(data['band_energy'][:spins], energy, atol=1e-6)
                    source = next(row for row in receipt['sources'] if row['name'] == 'PROCAR')
                    self.assertEqual(source['verification_mode'], 'content')
                    self.assertEqual(data['band_weight'].shape[0], spins)

    def test_existing_fixture_streams_without_loading_procar_text_or_inventing_orbitals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Path(__file__).parent / 'fixtures/banddos/PROCAR'
            with patch.object(Path, 'read_text', side_effect=AssertionError('stream PROCAR')):
                info = plots._procar(source, ['Fe'], root, False)
            self.assertEqual((info['nk'], info['nb'], info['spins']), (3, 2, 1))
            self.assertEqual(info['orbitals'], plots.PBAND_COLUMNS)
            self.assertAlmostEqual(float(np.load(root / 'band_weight.npy')[0, 0, 0, 0].sum()), .5)

    def test_phase_factors_are_ignored_and_l_resolved_headers_stay_l_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = 'PROCAR\n# of k-points: 1 # of bands: 1 # of ions: 1\n'
            source += 'k-point 1 : 0 0 0 weight = 1\nband 1 # energy 0 # occ. 1\n'
            source += 'ion s p d tot\n1 .1 .2 .3 .6\ntot .1 .2 .3 .6\n'
            source += 'ion s p d\n1 9 8 7 6 5 4\n'
            (root / 'PROCAR').write_text(source)
            info = plots._procar(root / 'PROCAR', ['Fe'], root, False)
            self.assertEqual(info['orbitals'], ['s', 'p', 'd'])
            np.testing.assert_allclose(np.load(root / 'band_weight.npy')[0, 0, 0, 0], [.1, .2, .3])

    def test_legacy_pband_remains_readable_when_procar_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'; root.mkdir()
            energy, _ = fixture(root)
            (root / 'PROCAR').unlink()
            for element in ('Fe', 'O'):
                text = '#K-Path Energy s py pz px dxy dyz dz2 dxz x2-y2 tot\n# NKPTS & NBANDS: 2 2\n'
                for band in range(2):
                    text += f'# Band-Index {band + 1}\n'
                    for k in (range(2) if band % 2 == 0 else reversed(range(2))):
                        text += f'{k * .5} {energy[0, k, band]} .1 .1 .1 .1 .1 .1 .1 .1 .1 .9\n'
                (root / f'PBAND_{element}.dat').write_text(text)
            out = Path(tmp) / 'derived/data.npz'
            with patch.object(execution, 'attempt_path', return_value={'shell': str(root)}), \
                 patch('backend.extension_remote.runtimes', return_value={}), \
                 patch.object(analysis_queue, 'interpreter', return_value=sys.executable):
                command = compact_plots.script({'id': 'fixture', 'config': {'alias': 'fixture'}}, 'band', str(out))
            result = subprocess.run(shlex.split(command), env={**os.environ, 'PATH': ''}, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            with np.load(out, allow_pickle=False) as data:
                receipt = json.loads(str(data['metadata']))
                self.assertEqual(receipt['metadata']['source_format'], 'vaspkit-pband')
                self.assertIsNone(receipt['generation'])

    def test_existing_cache_does_not_launch_remote_extraction(self):
        record = {'id': 'fixture', 'config': {'alias': 'fixture'}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(compact_plots, 'CACHE', Path(tmp)), \
             patch.object(execution, 'attempt_path', return_value={'shell': '/source'}), \
             patch.object(compact_plots.recycle, 'assert_available'), \
             patch.object(compact_plots.safe_files, 'pull_many') as transfer:
            directory = Path(tmp) / compact_plots.identity(record, 'band'); directory.mkdir()
            (directory / 'receipt.json').write_text(json.dumps({'metadata': {'source_format': 'vaspkit-pband'}}))
            np.savez_compressed(directory / 'data.npz', band_energy=np.zeros((1, 2, 2)))
            value = compact_plots.fetch(record, 'band')
            self.assertTrue(value['cached'])
            transfer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
