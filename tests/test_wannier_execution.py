import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from backend import wannier_execution as w90


def fixture_win():
    original = Path('/Users/kafka/Documents/FeCrO论文/06_工具/hpc_smoke_tests/wannier90_parallel/example01/gaas.win').read_text()
    return original.replace(' num_wann    =  4', ' num_bands = 4\n num_wann    =  4\n spinors = .true.', 1)


class WannierExecutionTests(unittest.TestCase):
    def test_soc_win_keeps_vasp_owned_fields_but_allows_localization_settings(self):
        source = fixture_win()
        self.assertEqual(w90.win_contract.validate_win(source), [])
        w90._checked_win(source, source.replace('num_iter    = 20', 'num_iter = 60'))
        with self.assertRaisesRegex(ValueError, 'spinors'):
            w90._checked_win(source, source.replace('spinors = .true.', 'spinors = .false.'))
        with self.assertRaisesRegex(ValueError, 'kpoints'):
            w90._checked_win(source, source.replace('0.0 0.0 0.5', '0.1 0.0 0.5', 1))
        with self.assertRaisesRegex(ValueError, '未登记'):
            w90._checked_win(source, source + '\nmade_up_wannier_tag = 1\n')

    def test_structured_parameters_are_official_and_keep_channel_identity(self):
        names={item['tag'] for item in w90.parameter_catalog()}
        self.assertTrue({'num_iter','dis_win_min','dis_froz_max','write_hr'} <= names)
        result=w90.edit_win(fixture_win(), {'num_iter':'300', 'write_hr':'true'})
        self.assertEqual(result['parameters']['num_iter'],'300')
        self.assertEqual(result['parameters']['write_hr'],'.true.')
        with self.assertRaisesRegex(ValueError, '未登记'):
            w90.edit_win(fixture_win(), {'fictional_option':'1'})
        with self.assertRaisesRegex(ValueError, '允许范围'):
            w90.edit_win(fixture_win(), {'dis_mix_ratio':'1.5'})
        self.assertEqual(w90.source_names('wannier90.1')[0],'wannier90.1.win')
        self.assertEqual(w90.source_names('wannier90.2')[-1],'wannier90.2.eig')
        with self.assertRaises(ValueError):w90.source_names('../wannier90')

    def test_common_tags_restart_enum_and_missing_checkpoint_gate(self):
        changes={'restart':'plot','num_cg_steps':'5','precond':'true','guiding_centres':'false','trial_step':'1.5','num_dump_cycles':'100','num_print_cycles':'1','write_tb':'true','write_u_matrices':'true','use_ws_distance':'true'}
        result=w90.edit_win(fixture_win(),changes)
        self.assertEqual(result['parameters']['restart'],'plot')
        with self.assertRaisesRegex(ValueError,'restart 需要'):
            w90.require_fresh_interface_win(result['win'])
        w90.require_fresh_interface_win(fixture_win())
        with self.assertRaisesRegex(ValueError,'只能是'):
            w90.edit_win(fixture_win(),{'restart':'invalid'})
        with self.assertRaisesRegex(ValueError,'超出允许范围'):
            w90.edit_win(fixture_win(),{'num_dump_cycles':'0'})
        with self.assertRaisesRegex(ValueError,'fixed_step'):
            w90.edit_win(fixture_win(),{'trial_step':'1','fixed_step':'1'})

    def test_frozen_restart_is_checked_after_frozen_win_is_restored(self):
        project={'id':'p'}
        frozen={'project_id':'p','node_id':'w','alias':'jutai-1','project':project,'parent_attempt_id':'v','win':fixture_win()+'\nrestart=plot\n','seed':'wannier90','input_reason':'frozen','energy_reference':None,'relative_windows':{},'band_path':None}
        with patch('backend.recycle.assert_available'),patch.object(w90,'configuration',return_value=frozen):
            with self.assertRaisesRegex(ValueError,'restart 需要'):
                w90.prepare.__wrapped__(project,'w','v',{'alias':'jutai-1'},'probe','node',win=fixture_win(),frozen_id='frozen')

    def test_relative_windows_convert_once_to_absolute_win_values(self):
        win=fixture_win()
        changed=w90.edit_win(win,{'dis_win_min':'-3','dis_win_max':'4','dis_froz_min':'-2','dis_froz_max':'1'},fermi_eV=6.179381397)
        self.assertAlmostEqual(float(changed['parameters']['dis_win_min']),3.179381397)
        self.assertAlmostEqual(float(changed['parameters']['dis_win_max']),10.179381397)
        self.assertAlmostEqual(float(changed['parameters']['dis_froz_min']),4.179381397)
        self.assertAlmostEqual(float(changed['parameters']['dis_froz_max']),7.179381397)

    def test_remove_parameters_preserves_comments_and_owned_fields(self):
        source=fixture_win()+'\nwrite_hr = true # keep explanation\nrestart=plot\n'
        changed=w90.edit_win(source,{},remove=['write_hr','restart','num_iter'])
        self.assertEqual(changed['parameters']['write_hr'],'')
        self.assertEqual(changed['parameters']['num_iter'],'')
        self.assertIn('# keep explanation',changed['win'])
        w90.require_fresh_interface_win(changed['win'])
        for tags in (['num_wann'],['spinors'],['kpoints'],['write_hr','write_hr']):
            with self.assertRaises(ValueError):w90.edit_win(source,{},remove=tags)
        with self.assertRaises(ValueError):w90.edit_win(source,{'write_hr':'true'},remove=['write_hr'])
        self.assertEqual(w90.edit_win(changed['win'],{})['parameters']['write_hr'],'')

    def test_final_raw_input_is_authoritative_and_checks_scalar_types(self):
        source=fixture_win()
        final=w90.edit_win(source,{'dis_win_min':'-3','dis_win_max':'4'},fermi_eV=6)['win']
        final=final.replace('dis_win_max = 10','dis_win_max = 12')
        self.assertEqual(w90.relative_window_values(final,6),{'dis_win_min':-3,'dis_win_max':6})
        with self.assertRaisesRegex(ValueError,'最终 .win'):w90.relative_window_values(final,6,{'dis_win_max':4})
        with self.assertRaisesRegex(ValueError,'整数'):w90.edit_win(source.replace('num_iter    = 20','num_iter = nonsense'),{},source_win=source)
        with self.assertRaisesRegex(ValueError,'spinors'):w90.edit_win(source.replace('spinors = .true.','spinors = .false.'),{},source_win=source)
        deleted=w90.edit_win(final,{},remove=['dis_win_max'])['win']
        self.assertEqual(w90.relative_window_values(deleted,6),{'dis_win_min':-3})

    def test_all_supplied_scripts_render_without_body_whitelist(self):
        choices = w90.script_choices()
        self.assertEqual({item['alias'] for item in choices},
                         {'gengzi-187', *(f'jutai-{index}' for index in range(1, 6))})
        for item in choices:
            scheduler = 'slurm' if item['alias'] == 'gengzi-187' else 'pbs'
            config = {'alias': item['alias'], 'scheduler': scheduler}
            profile = {'name': 'w90', 'queue': 'batch', 'node': 'node02', 'ppn': 2, 'walltime': '48:00:00'}
            script, source = w90._render_script(config, profile, None)
            self.assertIn('wannier90.x', script)
            self.assertIn('node02', script)
            self.assertEqual(source['sha256'], item['sha256'])
            self.assertIn('gpus=1', script) if item['alias'] == 'jutai-4' else None

    def test_inspect_two_channel_seed_uses_safe_pull_only_for_small_win(self):
        from backend import safe_files,remote_cache
        seed='wannier90.1';win=fixture_win();names=w90.source_names(seed)
        rows=''.join(f'FPILOT_W90_FILE={name}|{hashlib.sha256(win.encode()).hexdigest() if name.endswith(".win") else "-"}|{len(win.encode()) if name.endswith(".win") else 50_000_000}|stamp\n' for name in names)
        parent={'id':'p','job_id':'23.node01','config':{'alias':'jutai-1'}}
        def pull(alias,files,command):
            self.assertEqual(len(files),1);self.assertTrue(files[0]['remote_path'].endswith(seed+'.win'))
            Path(files[0]['destination']).write_text(win)
            return {'companion_stdout':rows,'files':[]}
        with tempfile.TemporaryDirectory() as temp,patch.object(remote_cache,'ROOT',Path(temp)),patch.object(w90,'_parent',return_value=(parent,{})),patch.object(w90.ex,'attempt_path',return_value={'shell':'/home/user/230269265lht/source'}),patch.object(w90.ex,'ssh') as ssh,patch.object(safe_files,'pull_many',side_effect=pull) as transfer:
            result=w90.inspect({},'n','p','jutai-1',seed)
            self.assertEqual(result['seed'],seed);self.assertEqual(set(result['files']),set(names))
            ssh.assert_not_called();self.assertEqual(transfer.call_count,1)
            w90.inspect({},'n','p','jutai-1',seed);self.assertEqual(transfer.call_count,1)

    def test_shared_win_is_detected_and_loaded_for_each_spin_channel(self):
        from backend import safe_files,remote_cache
        win=fixture_win().replace('spinors = .true.','');digest=hashlib.sha256(win.encode()).hexdigest()
        parent={'id':'p','job_id':'23.node01','config':{'alias':'jutai-1'}}
        discovery='FPILOT_W90_SEED=wannier90.1\nFPILOT_W90_SEED=wannier90.2\n'
        with tempfile.TemporaryDirectory() as temp,patch.object(remote_cache,'ROOT',Path(temp)),patch.object(w90,'_parent',return_value=(parent,{})),patch.object(w90.ex,'attempt_path',return_value={'shell':'/home/user/230269265lht/source'}),patch.object(w90.ex,'ssh',return_value={'exit_code':0,'stdout':discovery,'stderr':''}) as ssh:
            result=w90.discover({},'n','p','jutai-1')
            self.assertEqual(result['seeds'],['wannier90.1','wannier90.2'])
            self.assertIn('test -s wannier90.win',ssh.call_args.args[1])
            for seed in result['seeds']:
                names=w90.source_names(seed,'wannier90.win')
                rows=''.join(f'FPILOT_W90_FILE={name}|{digest if name.endswith(".win") else "-"}|{len(win) if name.endswith(".win") else 50_000_000}|stamp\n' for name in names)
                def pull(alias,files,command):
                    self.assertTrue(files[0]['fallback_path'].endswith('/wannier90.win'))
                    Path(files[0]['destination']).write_text(win)
                    return {'companion_stdout':rows,'files':[]}
                with patch.object(safe_files,'pull_many',side_effect=pull) as transfer:
                    loaded=w90.inspect({},'n','p','jutai-1',seed)
                self.assertEqual(loaded['win_source_name'],'wannier90.win');self.assertEqual(set(loaded['files']),set(names))
                self.assertEqual(transfer.call_count,1)

    def test_changed_large_interface_hash_stops_before_dispatch(self):
        source = {name: {'sha256': 'a' * 64, 'size_bytes': 12} for name in w90.SOURCE_NAMES}
        record = {'id': 'a', 'bundle_id':'d'*64, 'project_id':'project', 'kind': 'wannier90', 'state': 'PREPARED', 'config': {'alias': 'jutai-1', 'scheduler': 'pbs'},
                  'profile': {'name':'w90','queue':'batch','node':'node02','ppn':2,'walltime':'48:00:00'},
                  'node':'node02','cores':2,'reason':'',
                  'script_source': {'id':None,'sha256':'b'*64,'source':'script'},
                  'script_sha256':hashlib.sha256(w90.ex.with_exit_trap('script').encode()).hexdigest()}
        bundle = {'parent_attempt_id':'parent','energy_reference':{'outcar_sha256':'c'*64,'fermi_eV':6.0},'source_path':'/home/user/230269265lht/source','source_files':source,'seed':'wannier90'}
        fake = {'exit_code':0, 'stdout': ''.join(f'FPILOT_W90_FILE={name}|{"b"*64}\n' for name in w90.SOURCE_NAMES), 'stderr':''}
        with patch.object(w90.ex,'get',return_value=record), patch.object(w90,'_bundle',return_value=bundle), \
             patch('backend.interactive_plots.energy_reference',return_value={'outcar_sha256':'c'*64,'fermi_eV':6.0}), \
             patch.object(w90.servers,'probe',return_value={'id':'p'}), \
             patch.object(w90.servers,'preview',return_value={'resource_ready':True,'profile':record['profile']}), \
             patch.object(w90,'_render_script',return_value=('script',record['script_source'])), \
             patch.object(w90.ex,'ssh',return_value=fake) as ssh, patch.object(w90.ex,'save') as save:
            with self.assertRaisesRegex(ValueError, '内容已变化'):
                w90.submit('a')
            self.assertEqual(ssh.call_count, 1)
            save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
