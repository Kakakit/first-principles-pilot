import unittest

from backend.wannier_bands import path_lines_from_run


class PathReceiptTests(unittest.TestCase):
    def test_shared_execution_receipt_is_parsed_from_stdout(self):
        line = 'GAMMA 0.00000 0.00000 0.00000 X -0.00000 0.50000 -0.50000'
        run = {'arguments': ['pre', 'kpath', '--path', '.'], 'stdout': line + '\n',
               'stderr': 'nonfatal warning', 'exit_code': 0}
        self.assertEqual(path_lines_from_run(run), [line])
        self.assertEqual(path_lines_from_run({**run, 'stdout': 'begin kpoint_path\n' + line + '\nend kpoint_path\n'}), [line])
        self.assertEqual(run['stderr'], 'nonfatal warning')

    def test_invalid_receipts_raise_actionable_errors(self):
        for run, message in (({}, 'stdout'),
                             ({'stdout': '', 'exit_code': 0}, '未输出'),
                             ({'stdout': 'x', 'exit_code': 1, 'stderr': 'failed'}, '执行失败'),
                             ({'stdout': 'G 0 0 0 X nan 0 0', 'exit_code': 0}, '坐标'),
                             ({'stdout': 'G 0 0 0 X 3 0 0', 'exit_code': 0}, '坐标'),
                             ({'stdout': 'no path produced', 'exit_code': 0}, '为空')):
            with self.subTest(run=run), self.assertRaisesRegex(ValueError, message):
                path_lines_from_run(run)


if __name__ == '__main__':
    unittest.main()
