"""Exercise the installed PyW90 window methods with valid VASP band blocks."""
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import pyw90_adapter as adapter

try:
    import numpy as np
    from pymatgen.electronic_structure.core import Spin
    from pyw90.lib.w90 import W90
    from pyw90.lib.dos import DOS
    from pyw90 import pre_w90_tool as pre
except ImportError:
    W90 = None


@unittest.skipIf(W90 is None, 'requires the registered PyW90/pymatgen runtime')
class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(W90, 'read_eigenval', W90.read_eigenval))
        self.stack.enter_context(patch.object(DOS, 'get_dos_from_selected', DOS.__dict__['get_dos_from_selected']))
        self.stack.enter_context(patch.object(pre, 'main_features', pre.main_features))
        self.stack.enter_context(patch.object(pre, 'Vasprun', pre.Vasprun))

    def eigenval(self, ispin=2):
        header = f' 2 2 1 {ispin}\n 1 1 1\n 0\n CAR\n test\n 2 2 3\n'
        blocks = ''
        for k in range(2):
            blocks += f'\n {k / 2} 0.0 0.0 0.5\n'
            for b in range(1, 4):
                up = b + k / 10
                blocks += (f' {b} {up} {up + 10} 1.0 0.0\n' if ispin == 2 else
                           f' {b} {up} 1.0\n')
        p = self.path / 'EIGENVAL'
        p.write_text(header + blocks)
        return p

    def read(self, down=False):
        adapter.install(down)
        with redirect_stdout(StringIO()) as output:
            obj = W90(eig='EIGENVAL', path=str(self.path), nwann=2)
        return obj, output.getvalue()

    def test_collinear_channels_and_band_extrema_are_not_scrambled(self):
        self.eigenval()
        up, _ = self.read()
        np.testing.assert_allclose(up.ir_ebands[0], [[1, 2, 3], [1.1, 2.1, 3.1]])
        # Restore before installing another process-local adapter in this test.
        self.stack.close()
        self.setUp()
        self.eigenval()
        down, receipt = self.read(True)
        np.testing.assert_allclose(down.ir_ebands[0], [[11, 12, 13], [11.1, 12.1, 13.1]])
        np.testing.assert_allclose(down.eband_max, [11.1, 12.1, 13.1])
        self.assertIn('"channel": "down"', receipt)
        self.assertEqual(down.count_states_most((11, 12.5)), 2)
        with redirect_stdout(StringIO()):
            candidates = down.get_dis_froz_df((10, 14))
        self.assertGreater(len(candidates), 0)
        self.assertTrue((candidates['dis_froz_min'] >= 10).all())

    def test_single_channel_including_soc_does_not_split_bands(self):
        self.eigenval(1)
        obj, receipt = self.read()
        self.assertEqual(obj.ir_ebands.shape, (1, 2, 3))
        self.assertIn('"channel": "single"', receipt)

    def test_single_channel_rejects_down(self):
        self.eigenval(1)
        with self.assertRaisesRegex(ValueError, '单通道'):
            self.read(True)

    def test_truncated_wrong_order_and_nonfinite_are_rejected(self):
        p = self.eigenval()
        valid = p.read_text()
        for malformed in [valid.rsplit('\n', 2)[0], valid.replace(' 2 2.0', ' 3 2.0'),
                          valid.replace(' 1 1.0', ' 1 nan')]:
            with self.subTest(malformed=malformed[-30:]):
                p.write_text(malformed)
                with self.assertRaises((ValueError, IndexError)):
                    self.read()

    def test_selected_pdos_uses_requested_down_channel(self):
        adapter.install(True)
        with patch.object(DOS, 'get_dos_given_site_orb', side_effect=[2, 3]) as get:
            self.assertEqual(DOS.get_dos_from_selected(object(), (-1, 1), {'a', 'b'}), 5)
        self.assertTrue(all(c.kwargs['spin'] == Spin.down for c in get.call_args_list))

    def test_soc_projection_count_and_actual_band_channel(self):
        self.eigenval(1)
        parsed = SimpleNamespace(parameters={'LSORBIT': True}, tdos=SimpleNamespace(densities={Spin.up: []}))
        args = SimpleNamespace(deg=1)
        def feature(actual):
            pre.Vasprun('vasprun.xml')
            obj = W90(eig='EIGENVAL', path=str(self.path), nwann=2 * actual.deg)
            self.assertEqual(obj.nwann, 4)
            self.assertEqual(obj.nbnds, 3)
        with patch.object(pre, 'Vasprun', return_value=parsed), patch.object(pre, 'main_features', side_effect=feature):
            adapter.install()
            with redirect_stdout(StringIO()) as output:
                pre.main_features(args)
        self.assertEqual(args.deg, 2)
        self.assertIn('"spinors": true', output.getvalue())

    def test_eig_reader_is_preserved(self):
        (self.path / 'test.eig').write_text('1 1 1.0\n2 1 2.0\n1 2 1.1\n2 2 2.1\n')
        adapter.install()
        obj = W90(seedname='test', path=str(self.path))
        np.testing.assert_allclose(obj.ir_ebands[0], [[1, 2], [1.1, 2.1]])


if __name__ == '__main__':
    unittest.main()
