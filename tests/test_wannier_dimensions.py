import subprocess
import tempfile
import unittest
from pathlib import Path

from backend import wannier_dimensions as dims, wannier_execution as w90


class MatrixTests(unittest.TestCase):
    seed = 'wannier90'
    win = '''num_bands=2
num_wann=1
mp_grid=2 1 1
begin kpoints
-.25 0 0
.25 0 0
end kpoints
'''
    def validate(self, win=None, seed=None, **changes):
        data = {'amn':[2,2,1], 'mmn':[2,2,2], 'eig':[2,2], **changes}
        dims.validate(w90.win_contract.parse_win(win or self.win), w90.win_contract, data, seed or self.seed)

    def test_projection_subset_shifted_grid_and_spin(self):
        self.validate()
        self.validate(self.win+'select_projections=3', amn=[2,2,3])
        for win, changes, message in [
            (self.win, dict(mmn=[3,2,4]), 'mmn'),
            (self.win, dict(eig=[2,1]), 'eig'),
            (self.win, dict(amn=[2,2,3]), 'select_projections'),
            (self.win+'select_projections=1-2', dict(amn=[2,2,3]), 'num_wann'),
            (self.win.replace('\n.25', '\n.75'), {}, '重复'),
            (self.win.replace('\n.25', '\n.26'), {}, '均匀网格'),
            (self.win+'spinors=true', dict(seed='wannier90.1'), 'spinors')]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError,message):
                self.validate(win, **changes)

    def test_parent_incar_and_spinor_flag(self):
        w90._check_parent_spin({'final_incar':'LSORBIT = T'}, self.win+'spinors=true')
        w90._check_parent_spin({}, self.win)
        with self.assertRaisesRegex(ValueError,'LSORBIT'):
            w90._check_parent_spin({'final_incar':'LNONCOLLINEAR=.TRUE.'}, self.win)

    def test_remote_script_checks_eigenvalue_indices_and_header(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for ext, header in [('amn','2 2 1'),('mmn','2 2 4')]:
                (root/('wannier90.'+ext)).write_text('comment\n'+header+'\n')
            eig = root/'wannier90.eig'
            def read():
                result = subprocess.run(['sh','-ec',dims.probe(self.seed)], cwd=root, capture_output=True, text=True, check=True)
                return dims.receipt(result.stdout, self.seed)
            eig.write_text('1 1 -2D-1\n2 1 0.1\n1 2 -0.3\n2 2 0.5\n')
            self.assertEqual(read(), {'amn':[2,2,1], 'mmn':[2,2,4], 'eig':[2,2]})
            from unittest.mock import patch
            def local_receipt(alias, command, timeout):
                process = subprocess.run(['sh','-c',command], capture_output=True, text=True, timeout=timeout)
                return {'exit_code':process.returncode, 'stdout':process.stdout}
            with patch.object(w90.ex,'ssh',side_effect=local_receipt):
                legacy = {'seed':self.seed, 'source_path':folder}
                self.assertEqual(w90.frozen_dimensions(legacy,'test'),read())
                self.assertNotIn('matrix_dimensions',legacy)
            for bad in ['1 1 NaN', '1 1 0\n2 1 0\n1 2 0', '1 1 0\n1 1 0', '1 2 0']:
                eig.write_text(bad)
                with self.assertRaisesRegex(ValueError,'eig'):
                    read()
