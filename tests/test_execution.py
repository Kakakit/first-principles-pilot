import io
import json
import runpy
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from backend import execution as ex
from backend import remote_paths

class ExecutionTests(unittest.TestCase):
    def test_remote_restart_files_are_deferred_without_hiding_other_audit_errors(self):
        missing='CHGCAR required by INCAR restart settings'
        bad='invalid INCAR tag'
        source={'mode':'charge','source_attempt_id':'source'}
        reviewed=ex._review_input_audit({'status':'FAIL','errors':[missing]},restart=source,stage='scf')
        self.assertEqual(reviewed['status'],'PASS_WITH_REMOTE_DEPENDENCY')
        self.assertEqual(reviewed['deferred_errors'],[missing])
        self.assertEqual(reviewed['errors'],[])
        with self.assertRaisesRegex(ValueError,bad) as raised:
            ex._review_input_audit({'status':'FAIL','errors':[missing,bad],'inputs':{'POTCAR':{'sha256':'secret'}}},
                                   restart=source,stage='scf')
        self.assertNotIn('CHGCAR',str(raised.exception))
        self.assertNotIn('POTCAR',str(raised.exception))
        with self.assertRaisesRegex(ValueError,'CHGCAR'):
            ex._review_input_audit({'status':'FAIL','errors':[missing]},stage='scf')

    def test_lcalcpol_audit_does_not_impose_ncore_one(self):
        audit_script=Path(__file__).parents[1]/'resources/skills/calc-review/scripts/input_audit.py'
        audit_incar=runpy.run_path(str(audit_script))['_audit_incar']
        errors,_,_=audit_incar({'LCALCPOL':'.TRUE.','NCORE':'6'},[],[],'static','vasp_ncl')
        self.assertEqual(errors,[])

    def test_remote_root_policy_and_attempt_layout(self):
        self.assertEqual(remote_paths.normalize_workspace_root(), '$HOME/230269265lht')
        self.assertEqual(remote_paths.normalize_workspace_root('$HOME/230269265lht/scratch'),
                         '$HOME/230269265lht/scratch')
        self.assertEqual(remote_paths.normalize_workspace_root('/mnt/work/230269265lht'),
                         '/mnt/work/230269265lht')
        with self.assertRaises(ValueError):
            remote_paths.normalize_workspace_root('$HOME/other')
        with self.assertRaises(ValueError):
            remote_paths.normalize_workspace_root('/tmp/230269265lht/../outside')
        relative = remote_paths.attempt_relative_path('project-1', 3, 'scf', '20260921-120000-abcd')
        self.assertEqual(relative, 'material-projects/project-1/flows/main/stages/03-scf/attempts/20260921-120000-abcd')

    def test_prepare_binds_frozen_identity_and_records_remote_snapshot(self):
        config = {'alias': 'jutai-5', 'vasp_executable': '/opt/vasp_std',
                  'remote_workspace_root': '$HOME/230269265lht'}
        manifest = {'ready': True, 'dependencies': [], 'errors': [], 'stage': 'scf',
                    'node_id': 'node-1'}
        files = {name: b'test\n' for name in ('INCAR', 'POSCAR', 'KPOINTS', 'POTCAR')}
        project = {'id': 'project-1', 'name':'Project 1', 'remoteSlug':'project-1', 'nodes': [{'id': 'node-1','data':{'kind':'scf','executionMode':'vasp','stageSequence':3,'stageSlug':'scf'}}]}
        rendered = {'resource_ready': True, 'profile': {'node': 'node02', 'ppn': 4},
                    'script': '#!/bin/bash\nset -eo pipefail\ntrue\n'}
        probe_receipt = {'exit_code': 0,
                         'stdout': 'FPILOT_ROOT_ABS=/home/user/230269265lht\n'
                                   'FPILOT_ROOT_SNAPSHOT=1:2:3:4\n', 'stderr': ''}
        with patch.object(ex.servers, 'preview', return_value=rendered), \
             patch.object(ex, 'bundle', return_value=(manifest, files)), \
             patch.object(ex, 'frozen_project', return_value=project), \
             patch.object(ex.servers.pbs, 'audit_inputs', return_value={'status': 'PASS'}), \
             patch.object(ex, 'ssh', return_value=probe_receipt) as ssh:
            record = ex.prepare.__wrapped__('b' * 64, config, 'probe-1', 'node02', cores=4,
                                reason='path test')
        self.assertEqual(ssh.call_count, 1)
        self.assertEqual(record['project_id'], 'project-1')
        self.assertEqual(record['node_id'], 'node-1')
        self.assertEqual(record['flow_id'], 'single')
        self.assertEqual(record['remote_workspace_root_abs'], '/home/user/230269265lht')
        self.assertRegex(record['remote_path'], r'^\$HOME/230269265lht/material-projects/project-1/flows/main/stages/03-scf/attempts/\d{8}-\d{6}-[a-f0-9]{4}$')
        self.assertTrue(record['remote_path_absolute'].startswith('/home/user/230269265lht/material-projects/project-1/flows/main/stages/03-scf/attempts/'))
        self.assertEqual(record['path_schema'],'fpilot.remote-attempt-path')
        self.assertEqual(record['artifact_paths']['INCAR'], record['remote_path_absolute'] + '/INCAR')

    def test_manual_chgcar_restart_ignores_stale_graph_dependency_fields(self):
        config={'alias':'jutai-1','vasp_executable':'/opt/vasp_std','remote_workspace_root':'$HOME/230269265lht'}
        manifest={'ready':True,'dependencies':[],'errors':[],'stage':'scf','node_id':'node-1'}
        files={'INCAR':b'ISTART=0\nICHARG=1\nLCHARG=.FALSE.\n',
               'POSCAR':b'poscar\n','KPOINTS':b'grid\n','POTCAR':b'potcar\n'}
        project={'id':'project-1','name':'Project 1','remoteSlug':'project-1',
                 'nodes':[{'id':'node-1','data':{'kind':'scf','executionMode':'vasp','stageSequence':3,'stageSlug':'scf'}}]}
        restart={'source_attempt_id':'old','source_path':'$HOME/source','source_state':'COMPLETED',
                 'mode':'charge','reason':'','files':{'CHGCAR':{'sha256':'a'*64,'size_bytes':42}}}
        rendered={'resource_ready':True,'profile':{'node':'node01','ppn':4},'script':'#!/bin/bash\ntrue\n'}
        receipt={'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/user/230269265lht\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        with patch.object(ex.servers,'preview',return_value=rendered), \
             patch.object(ex,'bundle',return_value=(manifest,files)), \
             patch.object(ex,'frozen_project',return_value=project), \
             patch.object(ex,'restart_snapshot',return_value=restart), \
             patch.object(ex.servers.pbs,'audit_inputs',return_value={'status':'FAIL','errors':['CHGCAR required by INCAR restart settings']}), \
             patch.object(ex,'ssh',return_value=receipt):
            prepared=ex.prepare.__wrapped__('b'*64,config,'probe','node01',restart_attempt_id='old',
                                restart_mode='charge',restart_reason='',
                                dependency_attempt_id='stale',dependency_reason='stale note')
        self.assertEqual(prepared['state'],'PREPARED')
        self.assertEqual(prepared['restart_snapshot']['source_attempt_id'],'old')
        self.assertNotIn('dependency_snapshot',prepared)
        self.assertEqual(prepared['input_audit']['status'],'PASS_WITH_REMOTE_DEPENDENCY')

    def test_soc_prepare_requires_ncl_runtime_before_remote_mutation(self):
        config={'alias':'jutai-1','vasp_executable':'/opt/vasp/vasp_std'}
        manifest={'ready':True,'dependencies':[],'errors':[],'stage':'scf','node_id':'n'}
        files={name:b'test\n' for name in ('INCAR','POSCAR','KPOINTS','POTCAR')}
        files['INCAR']=b'ISPIN = 1\nLSORBIT = .TRUE.\nMAGMOM = 0 0 4\n'
        rendered={'resource_ready':True,'profile':{'node':'node01','ppn':4},'script':'#!/bin/bash\n'}
        with patch.object(ex.servers,'preview',return_value=rendered), \
             patch.object(ex,'bundle',return_value=(manifest,files)), \
             patch.object(ex,'frozen_project',return_value={'id':'p'}), \
             patch.object(ex,'ssh') as ssh:
            with self.assertRaisesRegex(ValueError,'vasp_ncl'):
                ex.prepare.__wrapped__('b'*64,config,'probe','node01',cores=4)
            ssh.assert_not_called()

    def test_capabilities_describe_actual_submission_scope(self):
        from backend.app import health, capabilities
        self.assertEqual(health()['phase'],'1.0')
        self.assertEqual(health()['version'],'1.0.0')
        self.assertTrue(health()['remote_execution'])
        self.assertTrue(capabilities()['job_submission'])
        self.assertTrue(capabilities()['dependent_vasp_chain'])
        self.assertTrue(capabilities()['result_curves'])
        self.assertEqual(capabilities()['submission_schedulers'],['pbs','slurm'])
        self.assertFalse(capabilities()['windows_support'])
        self.assertIn('relax→scf',capabilities()['submission_scope'])
    def test_terminal_evidence_survives_refresh_and_stale_save(self):
        r=self.record()
        for state in ('COMPLETED','FAILED'):
            r.update(state=state,evidence={'wrapper_exit':0 if state=='COMPLETED' else 1})
            with ex.db() as c:c.execute('UPDATE attempts SET state=?,body=? WHERE id=?',(state,json.dumps(r),'a'))
            with patch.object(ex,'ssh') as ssh:
                self.assertEqual(ex.refresh('a')['state'],state)
                ssh.assert_not_called()
            stale={**r,'state':'UNKNOWN','evidence':{}}
            self.assertEqual(ex.save(stale)['evidence'],r['evidence'])
            self.assertEqual(ex.get('a')['state'],state)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=ex.ROOT;ex.ROOT=Path(self.temp.name)
        self.config={'alias':'jutai-5'};self.profile={'node':'node02','ppn':64}
        self.files={k:b'test\n' for k in ('INCAR','POSCAR','KPOINTS','POTCAR')}
        self.manifest={'ready':True,'dependencies':[],'errors':[],'stage':'scf'}
    def tearDown(self):ex.ROOT=self.old;self.temp.cleanup()
    def record(self):
        r={'id':'a','state':'PREPARED','config':self.config,'node':'node02','cores':None,'reason':'',
           'profile':self.profile,'bundle_id':'b','path_schema':'fpilot.remote-attempt-path',
           'remote_workspace_root':'$HOME/230269265lht','remote_workspace_root_abs':'/home/user/230269265lht',
           'remote_relative_path':'material-projects/test/flows/main/stages/03-scf/attempts/20260921-120000-aaaa',
           'remote_path_absolute':'/home/user/230269265lht/material-projects/test/flows/main/stages/03-scf/attempts/20260921-120000-aaaa',
           'remote_project_path_absolute':'/home/user/230269265lht/material-projects/test','project_id':'project-1','project_slug':'test',
           'script':'#!/bin/bash\ntrue\n','script_sha256':'x','input_audit':{'status':'PASS'},'job_id':None}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('a','PREPARED',json.dumps(r)))
        return r
    def test_duplicate_dispatch_calls_qsub_only_once(self):
        self.record()
        with patch.object(ex.servers,'probe',return_value={'id':'fresh'}),patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':self.profile}),patch.object(ex,'bundle',return_value=(self.manifest,self.files)),patch.object(ex,'ssh',side_effect=[{'exit_code':0,'stdout':'FPILOT_JOB_ID=888.node01\n','stderr':''},{'exit_code':0,'stdout':'FPILOT_JOB_ID=888.node01\njob_state = R\n','stderr':''}]) as ssh,patch.object(ex,'start_watcher'):
            first=ex.submit('a');second=ex.submit('a')
            self.assertEqual(first['job_id'],'888.node01');self.assertEqual(second['state'],'RUNNING')
            self.assertEqual(ssh.call_count,2);self.assertIn('sha256sum -c',ssh.call_args_list[0].args[1])
    def test_uncertain_dispatch_is_not_retried(self):
        self.record()
        with patch.object(ex.servers,'probe',return_value={'id':'fresh'}),patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':self.profile}),patch.object(ex,'bundle',return_value=(self.manifest,self.files)),patch.object(ex,'ssh',return_value={'exit_code':None,'stdout':'','stderr':'timeout'}) as ssh,patch.object(ex,'start_watcher'):
            self.assertEqual(ex.submit('a')['state'],'UNKNOWN');ex.submit('a');self.assertEqual(ssh.call_count,1)
    def test_changed_capacity_no_dispatch(self):
        self.record()
        with patch.object(ex.servers,'probe',return_value={'id':'fresh'}),patch.object(ex.servers,'preview',return_value={'resource_ready':False,'profile':self.profile}),patch.object(ex,'ssh') as ssh:
            with self.assertRaises(ValueError):ex.submit('a')
            ssh.assert_not_called();self.assertEqual(ex.get('a')['state'],'PREPARED')
    def test_queue_disappearance_is_unknown_and_terminal_needs_timing(self):
        r=self.record();r['state']='SUBMITTED';r['job_id']='888.node01';ex.save(r)
        with patch.object(ex,'ssh',return_value={'exit_code':1,'stdout':'','stderr':'Unknown Job Id'}):
            self.assertEqual(ex.refresh('a')['state'],'UNKNOWN')
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'FPILOT_EXIT=0\nFPILOT_TIMING=1\nFPILOT_EDIFF=1\njob_state = C\n','stderr':''}):
            result=ex.refresh('a');self.assertEqual(result['state'],'COMPLETED');self.assertTrue(result['evidence']['ediff_reached'])
    def test_pbs_program_exit_waits_for_scheduler_terminal(self):
        record=self.record();record.update(state='RUNNING',job_id='123.node01')
        with ex.db() as c:c.execute('UPDATE attempts SET state=?,body=? WHERE id=?',('RUNNING',json.dumps(record),'a'))
        output='FPILOT_EXIT=0\nFPILOT_TIMING=1\nFPILOT_MPI_SINGLETON=0\njob_state = R\n'
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':output,'stderr':''}):
            self.assertEqual(ex.refresh('a')['state'],'EXITING')
    def test_slurm_dispatch_is_once_and_terminal_evidence_is_separate(self):
        record=self.record();record['config']={'alias':'gengzi-187','scheduler':'slurm'}
        record['profile']={'node':'cn1','ntasks':56};record['scheduler']='slurm'
        record['script']='#!/bin/bash\nset -euo pipefail\ntrue\n'
        record['script_sha256']=ex.hashlib.sha256(record['script'].encode()).hexdigest()
        with ex.db() as c:c.execute('UPDATE attempts SET body=? WHERE id=?',(json.dumps(record),'a'))
        receipts=[{'exit_code':0,'stdout':'FPILOT_JOB_ID=12345\n','stderr':''},
                  {'exit_code':0,'stdout':'FPILOT_JOB_ID=12345\nFPILOT_SLURM_LIVE=RUNNING\n','stderr':''}]
        with patch.object(ex.servers,'probe',return_value={'id':'fresh'}), \
             patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':record['profile']}), \
             patch.object(ex,'bundle',return_value=(self.manifest,self.files)), \
             patch.object(ex,'ssh',side_effect=receipts) as ssh,patch.object(ex,'start_watcher'):
            result=ex.submit('a')
        self.assertEqual(result['state'],'RUNNING');self.assertEqual(result['job_id'],'12345')
        self.assertEqual(ssh.call_args_list[0].args[1].count('sbatch --parsable run.slurm'),1)
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'FPILOT_EXIT=0\nFPILOT_TIMING=1\nFPILOT_EDIFF=1\nFPILOT_SLURM_ACCOUNT=COMPLETED|0:0\n','stderr':''}):
            result=ex.refresh('a')
        self.assertEqual(result['state'],'COMPLETED')
        self.assertEqual(result['evidence']['scheduler'],'slurm')
        self.assertEqual(result['evidence']['scheduler_exit_code'],'0:0')
    def test_slurm_program_exit_waits_for_accounting_terminal(self):
        record=self.record();record.update(state='RUNNING',job_id='12345',scheduler='slurm',config={'alias':'gengzi-187','scheduler':'slurm'})
        with ex.db() as c:c.execute('UPDATE attempts SET state=?,body=? WHERE id=?',('RUNNING',json.dumps(record),'a'))
        output='FPILOT_EXIT=0\nFPILOT_TIMING=1\nFPILOT_MPI_SINGLETON=0\nFPILOT_SLURM_LIVE=COMPLETING\nFPILOT_SLURM_ACCOUNT=RUNNING|0:0\n'
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':output,'stderr':''}):
            result=ex.refresh('a')
        self.assertEqual(result['state'],'EXITING')
        self.assertTrue(result['evidence']['output_sanity'])
    def test_pending_bundle_rejected(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('manifest.json',json.dumps({'ready':False,'dependencies':['CHGCAR']}))
        raw=stream.getvalue();key=ex.hashlib.sha256(raw).hexdigest();(ex.ROOT/'inputs').mkdir();(ex.ROOT/'inputs'/(key+'.zip')).write_bytes(raw)
        with self.assertRaises(ValueError):ex.bundle(key)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('manifest.json',json.dumps({'ready':False,'errors':[],'stage':'bands','dependencies':[{'node_id':'scf','file':'CONTCAR'},{'node_id':'scf','file':'CHGCAR'}]}))
        raw=stream.getvalue();key=ex.hashlib.sha256(raw).hexdigest();(ex.ROOT/'inputs'/(key+'.zip')).write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'缺少必须文件'):
            ex.bundle(key,allow_dependencies=True)

    def test_failed_upstream_never_probes_dependency_files(self):
        parent={'id':'parent','state':'FAILED','config':{'alias':'jutai-1'},'node_id':'relax-1',
                'path_schema':'fpilot.remote-attempt-path'}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('parent','FAILED',json.dumps(parent)))
        manifest={'stage':'scf','node_id':'scf-1','dependencies':[{'node_id':'relax-1','file':'CONTCAR'}]}
        with patch.object(ex,'ssh') as ssh:
            with self.assertRaisesRegex(ValueError,'不推进'):
                ex.dependency_snapshot('b'*64,manifest,{'INCAR':b'ISTART=0\nICHARG=2\n'},'parent','jutai-1','reuse')
            ssh.assert_not_called()

    def test_v1_attempt_cannot_be_reused_or_retried(self):
        parent={'id':'old','state':'FAILED','config':{'alias':'jutai-1'},'node_id':'relax-1',
                'path_schema':'fpilot.remote-attempt-path.v1','bundle_id':'b'}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('old','FAILED',json.dumps(parent)))
        manifest={'stage':'scf','node_id':'scf-1','dependencies':[{'node_id':'relax-1','file':'CONTCAR'}]}
        with patch.object(ex,'ssh') as ssh:
            with self.assertRaisesRegex(ValueError,'目录结构'):
                ex.dependency_snapshot('b'*64,manifest,{'INCAR':b'ISTART=0\nICHARG=2\n'},'old','jutai-1','reuse')
            with self.assertRaisesRegex(ValueError,'目录结构'):
                ex.retry('old','probe','node02')
            ssh.assert_not_called()

    def test_completed_upstream_creates_hash_bound_snapshot(self):
        parent={'id':'parent','state':'COMPLETED','config':{'alias':'jutai-1'},'node_id':'relax-1',
                'project_id':'project-1','flow_id':'single','bundle_id':'parent-bundle','job_id':'12.node01',
                'path_schema':'fpilot.remote-attempt-path','remote_workspace_root':'$HOME/230269265lht',
                'remote_relative_path':'material-projects/project-1/flows/main/stages/02-relax/attempts/20260921-120000-abcd',
                'remote_path_absolute':'/home/user/230269265lht/material-projects/project-1/flows/main/stages/02-relax/attempts/20260921-120000-abcd',
                'evidence':{'wrapper_exit':0,'vasp_timing':True,
                'scientific_validation':'NOT_PERFORMED'}}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('parent','COMPLETED',json.dumps(parent)))
        manifest={'stage':'scf','node_id':'scf-1','dependencies':[{'node_id':'relax-1','file':'CONTCAR'}]}
        digest='a'*64
        with patch.object(ex,'frozen_project',return_value={'id':'project-1','nodes':[{'id':'scf-1'}]}), \
             patch.object(ex,'bundle',return_value=({'stage':'relax'},{'POTCAR':b'potcar'})), \
             patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':f'FPILOT_DEP=CONTCAR|{digest}|42\n','stderr':''}):
            result=ex.dependency_snapshot('b'*64,manifest,{'INCAR':b'ISTART=0\nICHARG=2\n','POTCAR':b'potcar'},'parent','jutai-1','final geometry')
        self.assertEqual(result['files']['CONTCAR'],{'sha256':digest,'size_bytes':42})
        self.assertEqual(result['scientific_validation'],'NOT_PERFORMED')

    def test_restart_policy_is_fixed_and_never_uses_wavecar(self):
        manifest={'stage':'dos','dependencies':[{'node_id':'scf-1','file':'CONTCAR'},{'node_id':'scf-1','file':'CHGCAR'}]}
        policy=ex._restart_policy(manifest,{'INCAR':b'ISTART = 0\nICHARG = 11\n'})
        self.assertEqual(policy['required_files'],['CHGCAR','CONTCAR'])
        self.assertFalse(policy['uses_wavecar'])
        with self.assertRaisesRegex(ValueError,'ICHARG=11'):
            ex._restart_policy(manifest,{'INCAR':b'ISTART = 0\nICHARG = 2\n'})

    def test_terminal_same_node_restart_snapshots_both_files_without_submission(self):
        source={'id':'source','state':'FAILED','project_id':'project-1','flow_id':'single','node_id':'node-1',
                'config':{'alias':'jutai-1'},'bundle_id':'old','path_schema':'fpilot.remote-attempt-path',
                'remote_workspace_root':'$HOME/230269265lht',
                'remote_relative_path':'material-projects/test/flows/main/stages/03-scf/attempts/20260921-120000-aaaa',
                'remote_path_absolute':'/home/user/230269265lht/material-projects/test/flows/main/stages/03-scf/attempts/20260921-120000-aaaa',
                'evidence':{'ediff_reached':False}}
        manifest={'stage':'scf','node_id':'node-1','dependencies':[]}
        files={'POTCAR':b'potcar','KPOINTS':b'grid','POSCAR':b'title\n1\na\nb\nc\nSi\n2\nDirect\n0 0 0\n0.5 0.5 0.5\n',
               'INCAR':b'GGA=PE\nENCUT=400\nISPIN=2\nISTART=0\nICHARG=2\n'}
        receipt={'exit_code':0,'stdout':''.join(f'FPILOT_RESTART={name}|{"a"*64}|42\n' for name in ('CHGCAR','WAVECAR')),'stderr':''}
        with patch.object(ex,'get',return_value=source),patch.object(ex,'bundle',return_value=(manifest,files)), \
             patch.object(ex,'frozen_project',return_value={'id':'project-1'}), \
             patch.object(ex.remote_paths,'frozen_identity',return_value={'project_id':'project-1','flow_id':'single','node_id':'node-1'}), \
             patch.object(ex,'ssh',return_value=receipt) as ssh:
            snapshot=ex.restart_snapshot('b'*64,manifest,files,'source','jutai-1','both','continue SCF')
        self.assertEqual(set(snapshot['files']),{'CHGCAR','WAVECAR'})
        self.assertFalse(snapshot['source_converged'])
        self.assertEqual(ssh.call_count,1)
        self.assertIn(b'ISTART = 1\nICHARG = 1\n',ex._restart_incar(files['INCAR'],'both'))
        self.assertIn(b'ISTART = 0\nICHARG = 1\n',ex._restart_incar(files['INCAR'],'charge'))

        # Explicit CHGCAR continuation can originate at another node in the same project.
        other_node = dict(source, node_id='previous-scf')
        old_manifest = dict(manifest, node_id='previous-scf')
        with patch.object(ex,'get',return_value=other_node), \
             patch.object(ex,'bundle',return_value=(old_manifest,files)), \
             patch.object(ex,'frozen_project',return_value={'id':'project-1'}), \
             patch.object(ex.remote_paths,'frozen_identity',return_value={'project_id':'project-1','flow_id':'single','node_id':'node-1'}), \
             patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':f'FPILOT_RESTART=CHGCAR|{"a"*64}|42\n','stderr':''}):
            cross_node=ex.restart_snapshot('b'*64,manifest,files,'source','jutai-1','charge','reuse charge')
        self.assertEqual(cross_node['source_attempt_id'],'source')
        self.assertEqual(set(cross_node['files']),{'CHGCAR'})
        incompatible=dict(files,INCAR=files['INCAR']+b'PREC=Accurate\n')
        with patch.object(ex,'get',return_value=other_node), \
             patch.object(ex,'bundle',return_value=(old_manifest,files)), \
             patch.object(ex,'frozen_project',return_value={'id':'project-1'}), \
             patch.object(ex.remote_paths,'frozen_identity',return_value={'project_id':'project-1','flow_id':'single','node_id':'node-1'}), \
             patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':f'FPILOT_RESTART=CHGCAR|{"a"*64}|42\n','stderr':''}):
            mismatch=ex.restart_snapshot('b'*64,manifest,incompatible,'source','jutai-1','charge','')
        self.assertIn('PREC 不同：来源=未设置，本次=ACCURATE',mismatch['compatibility_warnings'])
        self.assertEqual(mismatch['reason'],'')

        # A charge-only SCF may intentionally rotate the SOC spinor basis.
        # WAVECAR reuse still requires the original SAXIS.
        soc_source=dict(files,INCAR=files['INCAR']+b'LSORBIT=.TRUE.\nLNONCOLLINEAR=.TRUE.\nSAXIS=0 0 1\nLDAUU=2.5 4.0 0\n')
        soc_target=dict(soc_source,INCAR=soc_source['INCAR'].replace(b'SAXIS=0 0 1',b'SAXIS=1 1 0'))
        with patch.object(ex,'get',return_value=other_node), \
             patch.object(ex,'bundle',return_value=(old_manifest,soc_source)), \
             patch.object(ex,'frozen_project',return_value={'id':'project-1'}), \
             patch.object(ex.remote_paths,'frozen_identity',return_value={'project_id':'project-1','flow_id':'single','node_id':'node-1'}), \
             patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':f'FPILOT_RESTART=CHGCAR|{"a"*64}|42\n','stderr':''}):
            rotated=ex.restart_snapshot('b'*64,manifest,soc_target,'source','jutai-1','charge','rotate to 110')
            self.assertEqual(rotated['saxis_change'],{'source':'0 0 1','target':'1 1 0'})
            changed_u=dict(soc_target,INCAR=soc_target['INCAR'].replace(b'LDAUU=2.5 4.0 0',b'LDAUU=2.5 5.0 0'))
            u_mismatch=ex.restart_snapshot('b'*64,manifest,changed_u,'source','jutai-1','charge','change U')
            self.assertIn('LDAUU 不同：来源=2.5 4.0 0，本次=2.5 5.0 0',u_mismatch['compatibility_warnings'])

    def test_changed_dependency_hash_blocks_before_dispatch(self):
        record=self.record()
        record['dependency_snapshot']={'upstream_attempt_id':'parent','upstream_job_id':'1.node01',
            'source_path':'$HOME/source','source_node_id':'relax-1','source_state':'COMPLETED',
            'files':{'CONTCAR':{'sha256':'a'*64,'size_bytes':42}},'reason':'reuse'}
        with ex.db() as c:c.execute('UPDATE attempts SET body=? WHERE id=?',(json.dumps(record),'a'))
        changed={**record['dependency_snapshot'],'files':{'CONTCAR':{'sha256':'b'*64,'size_bytes':42}}}
        with patch.object(ex.servers,'probe',return_value={'id':'fresh'}), \
             patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':self.profile}), \
             patch.object(ex,'bundle',return_value=(self.manifest,self.files)), \
             patch.object(ex,'dependency_snapshot',return_value=changed), patch.object(ex,'ssh') as ssh:
            with self.assertRaisesRegex(ValueError,'已变化'):
                ex.submit('a')
            ssh.assert_not_called()
        self.assertEqual(ex.get('a')['state'],'PREPARED')

    def test_large_source_files_are_linked_only_when_vasp_will_not_write_them(self):
        snapshot={'source_path':'/home/user/source','files':{
            name:{'sha256':'a'*64,'size_bytes':1_000_000_000}
            for name in ('CONTCAR','CHGCAR','WAVECAR')}}
        read_only=ex.source_file_plan(snapshot,b'LCHARG=.FALSE.\nLWAVE=.FALSE.\n')
        self.assertEqual(read_only['CONTCAR']['mode'],'copy')
        self.assertEqual(read_only['CHGCAR']['mode'],'hardlink')
        self.assertEqual(read_only['WAVECAR']['mode'],'hardlink')
        link_commands=ex.source_file_commands(snapshot,read_only)
        self.assertIn('rm -f -- POSCAR\ncp -- /home/user/source/CONTCAR POSCAR',link_commands)
        self.assertIn('ln /home/user/source/WAVECAR WAVECAR',link_commands)
        writable=ex.source_file_plan(snapshot,b'LCHARG=.TRUE.\nLWAVE=.TRUE.\n')
        self.assertEqual(writable['CONTCAR']['mode'],'copy')
        self.assertEqual(writable['CHGCAR']['mode'],'reflink-or-copy')
        self.assertEqual(writable['WAVECAR']['mode'],'reflink-or-copy')
        manual=ex.source_file_plan({**snapshot,'schema':'fpilot.restart-snapshot.v1'},
                                   b'LCHARG=.FALSE.\nLWAVE=.FALSE.\n')
        self.assertEqual(manual['CHGCAR']['mode'],'reflink-or-copy')
        self.assertEqual(manual['WAVECAR']['mode'],'reflink-or-copy')

    def test_dependency_submit_materializes_by_hash_then_qsub_once(self):
        record=self.record()
        snapshot={'upstream_attempt_id':'parent','upstream_job_id':'1.node01',
            'source_path':'/home/user/source','source_node_id':'relax-1','source_state':'COMPLETED',
            'files':{'CONTCAR':{'sha256':'a'*64,'size_bytes':42}},'reason':'reuse'}
        record['dependency_snapshot']=snapshot
        record['restart_policy']={'ISTART':0,'ICHARG':2,'required_files':['CONTCAR'],'uses_wavecar':False}
        dependent_manifest={'ready':False,'dependencies':[{'node_id':'relax-1','file':'CONTCAR'}],
                            'errors':[],'stage':'scf'}
        dependent_files={'INCAR':b'ISTART=0\nICHARG=2\n','POSCAR.reference':b'reference\n',
                         'KPOINTS':b'test\n','POTCAR':b'test\n'}
        record['source_file_plan']=ex.source_file_plan(snapshot,dependent_files['INCAR'])
        with ex.db() as c:c.execute('UPDATE attempts SET body=? WHERE id=?',(json.dumps(record),'a'))
        receipts=[{'exit_code':0,'stdout':'FPILOT_JOB_ID=2.node01\n','stderr':''},
                  {'exit_code':0,'stdout':'FPILOT_JOB_ID=2.node01\njob_state = R\n','stderr':''}]
        with patch.object(ex.servers,'probe',return_value={'id':'fresh'}), \
             patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':self.profile}), \
             patch.object(ex,'bundle',return_value=(dependent_manifest,dependent_files)), \
             patch.object(ex,'dependency_snapshot',return_value=snapshot), \
             patch.object(ex,'ssh',side_effect=receipts) as ssh, patch.object(ex,'start_watcher'):
            result=ex.submit('a')
        command=ssh.call_args_list[0].args[1]
        self.assertEqual(result['job_id'],'2.node01')
        self.assertIn('cp -- /home/user/source/CONTCAR POSCAR',command)
        self.assertIn('sha256sum POSCAR',command)
        self.assertEqual(command.count('qsub run.pbs'),1)
