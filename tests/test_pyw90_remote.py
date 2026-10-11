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
                {'arguments':['pre', 'dos', '--path', '.'], 'stdout':'analysis complete','stderr':'','exit_code':0})
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
                  'dis_froz_min dis_froz_max N pdos tdos percent\n0 1.0 2.0 2 1.5 3.0 0.5\n'
                  'Lowest `dis_win_max` for 1: 4.0\n')
        if insufficient:
            second += 'There is no enough states for 1 with 9 WFs!\n'
        with ExitStack() as stack:
            stack.enter_context(patch.object(pyw90_remote.wannier_execution, '_parent', return_value=(parent, {})))
            stack.enter_context(patch.object(pyw90_remote.execution, 'attempt_path', return_value={'shell': '/parent'}))
            stack.enter_context(patch.object(pyw90_remote, '_remote_num_wann', return_value=('wannier90.win', 2)))
            stack.enter_context(patch.object(pyw90_remote, '_file_hashes', side_effect=[
                {'OUTCAR': 'outcar'}, hashes, {**hashes, 'EIGENVAL': 'changed'} if changed else hashes, {'OUTCAR':'outcar'}]))
            stack.enter_context(patch.object(pyw90_remote, '_interface_check', return_value={
                'basis':{'num_wann':2,'spinors':False}, 'candidates':{'outer':[],'frozen':[{'valid':True}]}}))
            run = stack.enter_context(patch.object(pyw90_remote, '_run', side_effect=[
                {'stdout':'FPILOT_DOS_SELECTION={"suggested_num_wann":3}\nWannier90 Projection:\nFe:d\npyw90 --extra input:\nFe,0,0\n','stderr':'warning'}, {'stdout':second,'stderr':''}]))
            result = pyw90_remote.recommend({'id': 'p'}, 'w', 'parent', 'jutai-1', 'wannier90.2',
                                            'pre', -2, 3, {'fermi_eV': 2.5, 'outcar_sha256': 'outcar'})
            args = run.call_args_list[0].args[2]
            self.assertNotIn('--rm-fermi', args)
            self.assertEqual(args[args.index('-e') + 1:args.index('-e') + 3], ['0.5', '5.5'])
            self.assertIn('--spin-down', args)
            self.assertIn('--fpilot-num-wann',run.call_args_list[1].args[2])
            return result

    def test_pre_uses_parent_absolute_reference_and_keeps_channel_receipt(self):
        result = self.recommend()
        self.assertEqual(result['advice']['frozen'][0]['dis_froz_min'], -1.5)
        self.assertEqual(result['advice']['eigenval']['channel'], 'down')
        self.assertEqual(result['advice']['frozen'][0]['N'], 2)
        self.assertEqual(result['advice']['frozen'][0]['pdos'], 1.5)
        self.assertEqual(result['runs'][0]['stderr'], 'warning')
        self.assertEqual(result['interface_source_hashes'], {'OUTCAR':'outcar'})

    def test_changed_analysis_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '输入发生变化'):
            self.recommend(changed=True)

    def test_insufficient_states_do_not_expose_placeholder_outer_limit(self):
        result = self.recommend(insufficient=True)
        self.assertNotIn('minimum_outer_max', result['advice'])

    def test_full_table_is_not_silently_cut_at_fifty(self):
        output='N pdos\n'+'\n'.join(f'{i} {i} {i/2}' for i in range(75))
        self.assertEqual(len(pyw90_remote._table(output,('N','pdos'))),75)

    def test_eig_absolute_cli_bounds_and_count_columns_survive_conversion(self):
        parent={'id':'parent'}
        hashes={'OUTCAR':'outcar','wannier90.win':'win','wannier90.eig':'eig'}
        output='dis_win_min dis_win_max i+1_min i_max Nleast Nmost\n0 1 8 2 0 1 2\ndis_froz_min dis_froz_max N\n0 2 4 2\n'
        with patch.object(pyw90_remote,'_source',return_value=(parent,'/parent','wannier90.win',3,5.5,hashes)), \
             patch.object(pyw90_remote,'_run',return_value={'stdout':output,'stderr':'warning'}) as run, \
             patch.object(pyw90_remote,'_interface_check',return_value={'basis':{'num_wann':3},'candidates':{'outer':[{}],'frozen':[{}]}}), \
             patch.object(pyw90_remote,'_file_hashes',return_value=hashes):
            result=pyw90_remote.recommend({'id':'p'},'w','parent','jutai-1','wannier90','eig',-2,3,{'fermi_eV':5.5})
        args=run.call_args.args[2]
        self.assertNotIn('--rm-fermi',args)
        self.assertEqual(args[args.index('-e')+1:args.index('-e')+3],['3.5','8.5'])
        row=result['advice']['outer'][0]
        self.assertEqual(row['Nmost'],2)
        self.assertEqual(row['i_max'],-5.5)
        self.assertEqual(result['advice']['frozen'][0]['N'],2)

    def test_preview_rejects_changed_source_instead_of_adopting_old_candidate(self):
        with patch.object(pyw90_remote,'_source',return_value=({},'/parent','wannier90.win',2,5,{'OUTCAR':'new'})), \
             patch.object(pyw90_remote,'_interface_check') as run:
            with self.assertRaisesRegex(ValueError,'来源已变化'):
                pyw90_remote.check({},'w','p','jutai-1','wannier90',-2,3,{},expected_source_hashes={'OUTCAR':'old'})
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
