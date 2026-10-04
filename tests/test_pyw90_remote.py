"""Remote PyW90 advice stays in an isolated scratch directory."""
import unittest
from unittest.mock import patch

from backend import pyw90_remote


class Pyw90RemoteTest(unittest.TestCase):
    def test_analysis_uses_temporary_directory_and_only_links_selected_inputs(self):
        receipt = {'exit_code': 0, 'stdout': 'analysis complete', 'stderr': ''}
        with patch.object(pyw90_remote.execution, 'ssh', return_value=receipt) as ssh:
            self.assertEqual(pyw90_remote._run(
                'jutai-1', '$HOME/230269265lht/example',
                ['pre', 'dos', '--path', '.'], ('vasprun.xml', 'EIGENVAL')),
                'analysis complete')
        command = ssh.call_args.args[1]
        self.assertIn('scratch=$(mktemp -d', command)
        self.assertIn('trap \'rm -rf -- "$scratch"\' EXIT', command)
        self.assertIn('ln -s "$source_dir"/vasprun.xml "$scratch"/vasprun.xml', command)
        self.assertIn('ln -s "$source_dir"/EIGENVAL "$scratch"/EIGENVAL', command)
        self.assertLess(command.index('cd "$scratch"'), command.index('MPLBACKEND=Agg timeout'))
        self.assertNotIn('PROCAR', command)


if __name__ == '__main__':
    unittest.main()
