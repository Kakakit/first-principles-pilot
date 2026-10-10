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

class PreAdviceContractTest(unittest.TestCase):
    def recommend(self, changed=False, insufficient=False):
        from contextlib import ExitStack
        parent = {'id': 'parent'}
        hashes = {'vasprun.xml': 'xml', 'EIGENVAL': 'eig'}
        second = ('FPILOT_EIGENVAL={"channel":"down","nkpts":2,"nbands":3}\n'
                  'dis_froz_min dis_froz_max percent\n0 1.0 2.0 0.5\n'
                  'Lowest `dis_win_max` for 1: 4.0\n')
        if insufficient:
            second += 'There is no enough states for 1 with 9 WFs!\n'
        with ExitStack() as stack:
            stack.enter_context(patch.object(pyw90_remote.wannier_execution, '_parent', return_value=(parent, {})))
            stack.enter_context(patch.object(pyw90_remote.execution, 'attempt_path', return_value={'shell': '/parent'}))
            stack.enter_context(patch.object(pyw90_remote, '_remote_num_wann', return_value=('wannier90.win', 2)))
            stack.enter_context(patch.object(pyw90_remote, '_file_hashes', side_effect=[
                {'OUTCAR': 'outcar'}, hashes, {**hashes, 'EIGENVAL': 'changed'} if changed else hashes]))
            run = stack.enter_context(patch.object(pyw90_remote, '_run', side_effect=[
                'Wannier90 Projection:\nFe:d\npyw90 --extra input:\nFe,0,0\n', second]))
            result = pyw90_remote.recommend({'id': 'p'}, 'w', 'parent', 'jutai-1', 'wannier90.2',
                                            'pre', -2, 3, {'fermi_eV': 2.5, 'outcar_sha256': 'outcar'})
            args = run.call_args_list[0].args[2]
            self.assertNotIn('--rm-fermi', args)
            self.assertEqual(args[args.index('-e') + 1:args.index('-e') + 3], ['0.5', '5.5'])
            self.assertIn('--spin-down', args)
            return result

    def test_pre_uses_parent_absolute_reference_and_keeps_channel_receipt(self):
        result = self.recommend()
        self.assertEqual(result['advice']['frozen'][0]['dis_froz_min'], -1.5)
        self.assertEqual(result['advice']['eigenval']['channel'], 'down')

    def test_changed_analysis_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '输入发生变化'):
            self.recommend(changed=True)

    def test_insufficient_states_do_not_expose_placeholder_outer_limit(self):
        result = self.recommend(insufficient=True)
        self.assertNotIn('minimum_outer_max', result['advice'])


if __name__ == '__main__':
    unittest.main()
