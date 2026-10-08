"""GPU reservations and MPI launches are different contracts; no remote calls."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import servers, job_templates, candidate_pool, gpu_requests, execution, fast_dispatch

PBS = '''#!/bin/bash
#PBS -N GPU_TEST
#PBS -q batch
#PBS -l nodes=node01:ppn=1:gpus=1
#PBS -l walltime=1000:00:00
cd $PBS_O_WORKDIR
NPROCS=`cat $PBS_NODEFILE| wc -l`
export CUDA_VISIBLE_DEVICES=1
source /existing/env.sh
mpirun -np 1 /existing/vasp_std > vasp.out
'''
SLURM = '''#!/bin/bash
#SBATCH --job-name=GPU_TEST
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --gres=gpu:a100:2
#SBATCH --time=02:00:00
cd $SLURM_SUBMIT_DIR
srun --ntasks="$NPROCS" /existing/vasp_std > vasp.out
'''.replace('srun --ntasks=', 'NPROCS=$SLURM_NTASKS\nsrun --ntasks=')

class GpuSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(servers,'ROOT',self.root),patch.object(job_templates,'ROOT',self.root),
                      patch.object(servers,'load_registry',return_value={'hosts':{'gpu':{}}}),
                      patch.object(servers,'select_profile',side_effect=lambda alias:{**servers.DEFAULT,'alias':alias,'scheduler':self.scheduler}),
                      patch('backend.site_registration.ssh_aliases',return_value=['gpu']),
                      patch('backend.site_registration.connection_identity',return_value={'hostname':'node01','user':'test','port':'22'})]
        self.scheduler='pbs'
        for p in self.patches:p.start()
        self.config={**servers.DEFAULT,'alias':'gpu','program':'vasp_std','runtime_source':'script','walltime':'1000:00:00'}
        self.record=job_templates.import_template(PBS,'gpu','gpu_vasp','fixture','GPU script')
        self.config['template_id']=self.record['id']
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def probe(self,active=0,status='FULLY_IDLE',allocated=0,declared=True,direct=True,total=2):
        facts=f'NODE=node01 STATUS={status} CPUS_TOTAL=10 CPUS_FREE={10 if status=="FULLY_IDLE" else 0} CPUS_ALLOC=0 JOBS=none'
        if declared:facts+=f' GPUS_TOTAL={total} GPUS_ALLOC={allocated} GRES=gres/gpu={total},gres/gpu:a100={total}'
        text='===== gpu =====\nSCHEDULER='+('Slurm' if self.scheduler=='slurm' else 'PBS/Torque')+'\n'+facts+'\n'
        if direct:text+=f'GPU_NODE=node01 GPU_QUERY=OK GPUS_PHYSICAL_TOTAL={total} GPU_INDICES='+','.join(str(i) for i in range(total))+f' GPU_ACTIVE_PROCESSES={active}\n'
        return servers._store_probe({'config':copy.deepcopy(self.config),'preflight':servers.parser.parse_preflight(text),
            'queues':[{'name':'batch','enabled':'True','started':'True','available':'up','time_limit':'infinite'}],
            'diagnostics':{'exit_code':0,'stdout':'','stderr':''},'availability':{'exit_code':0,'stdout':text,'stderr':''}})
    def render(self,probe):return servers.preview(self.config,probe['id'],'node01')
    def test_pbs_full_reservation_keeps_one_rank_and_original_mask(self):
        result=self.render(self.probe())
        self.assertTrue(result['resource_ready'],result['blockers'])
        self.assertIn('nodes=node01:ppn=10:gpus=2',result['script'])
        self.assertIn('mpirun -np 1 /existing/vasp_std',result['script'])
        self.assertIn('CUDA_VISIBLE_DEVICES=1',result['script'])
        self.assertEqual(result['profile']['mpi_ranks'],1)
        self.assertEqual(result['profile']['gpus'],2)
        self.assertEqual(job_templates.get_template(self.record['id'])['original_text'],PBS)
    def test_free_scheduler_node_with_gpu_processes_waits_without_contract_drift(self):
        idle=self.render(self.probe());busy=self.render(self.probe(active=2))
        self.assertFalse(busy['resource_ready']);self.assertTrue(candidate_pool.capacity_wait(busy))
        self.assertEqual(idle['profile'],busy['profile']);self.assertEqual(idle['script'],busy['script'])
        candidates=candidate_pool.approve([{'node':'node01','cores':10,'reason':''}],{'node01':busy},{'node01':idle})
        choice,reason=candidate_pool.select(self.config,self.probe(active=2),candidates)
        self.assertIsNone(choice);self.assertIn('暂时占用',reason)
        self.assertEqual(candidate_pool.select(self.config,self.probe(),candidates)[0],candidates[0])
    def test_unregistered_gpu_fallback_requires_direct_inventory_and_process_check(self):
        text=PBS.replace(':gpus=1','').replace('CUDA_VISIBLE_DEVICES=1','CUDA_VISIBLE_DEVICES=0,1,2').replace('mpirun -np 1','mpirun -np $NPROCS').replace('ppn=1','ppn=3')
        self.record=job_templates.import_template(text,'gpu','gpu_vasp','fixture','unregistered GPU');self.config['template_id']=self.record['id']
        result=self.render(self.probe(declared=False,total=3))
        self.assertTrue(result['resource_ready'],result['blockers']);self.assertIn('NPROCS=3\nexport CUDA',result['script'])
        self.assertIn('nodes=node01:ppn=10',result['script']);self.assertNotIn(':gpus=',result['script'])
        unknown=self.render(self.probe(declared=False,direct=False,total=3))
        self.assertFalse(unknown['resource_ready']);self.assertFalse(candidate_pool.capacity_wait(unknown))
        self.assertTrue(any('GPU 资源未知' in item for item in unknown['blockers']))
    def test_gpu_allocation_and_fixed_devices_are_not_silently_reinterpreted(self):
        self.assertTrue(candidate_pool.capacity_wait(self.render(self.probe(allocated=1))))
        self.config['resource_request']={'mode':'gpu','mpi_ranks':2,'gpus':1,'allocation':'whole_node'}
        result=self.render(self.probe());self.assertFalse(result['resource_ready']);self.assertTrue(any('固定 MPI' in b for b in result['blockers']))
        self.config['resource_request']={'mode':'gpu','mpi_ranks':1,'gpus':2,'allocation':'whole_node'}
        result=self.render(self.probe());self.assertFalse(candidate_pool.capacity_wait(result));self.assertTrue(any('固定设备' in b for b in result['blockers']))
    def test_slurm_gpu_typed_whole_node_uses_script_mpi_ranks(self):
        self.scheduler='slurm';self.config.update(scheduler='slurm',walltime='02:00:00')
        record=job_templates.import_template(SLURM,'gpu','gpu_vasp','fixture','Slurm GPU');self.config['template_id']=record['id']
        result=self.render(self.probe(direct=False))
        self.assertTrue(result['resource_ready'],result['blockers']);self.assertIn('--exclusive',result['script']);self.assertIn('--gres=gpu:a100:2',result['script']);self.assertIn('--ntasks=2',result['script']);self.assertIn('NPROCS=2\nsrun',result['script'])
    def test_by_program_requests_do_not_leak_into_soc_steps(self):
        self.config['resource_request']=json.dumps({'by_program':{'vasp_std':{'mode':'gpu','mpi_ranks':1,'gpus':1}}})
        self.assertEqual(servers.resource_request(self.config)['mpi_ranks'],1)
        self.assertIsNone(gpu_requests.resolve({**self.config,'program':'vasp_ncl'},None))
    def test_walltime_four_digit_hours_is_validated_without_truncation(self):
        self.assertEqual(servers.validate(self.config)['walltime'],'1000:00:00')
    def test_final_remote_guard_rejects_gpu_busy_before_any_dispatch(self):
        idle=self.render(self.probe());busy=self.probe(active=1)
        import sys,io
        namespace={'sys':sys,'json':json,'p':{'parse_preflight':servers.parser.parse_preflight},'s':vars(servers.selector),
                   'contract':{'config':self.config,'node':'node01','cores':None,'reason':'','profile':idle['profile']}}
        output,error=io.StringIO(),io.StringIO()
        with patch.object(sys,'argv',['guard',busy['availability']['stdout']+'FPILOT_QUEUE_BEGIN\nQueue: batch\n enabled = True\n started = True\n']),patch.object(sys,'stdout',output),patch.object(sys,'stderr',error):
            with self.assertRaises(SystemExit) as result:exec(fast_dispatch.CAPACITY,namespace)
        self.assertEqual(result.exception.code,75)
        self.assertIn('FPILOT_CAPACITY_WAIT=1',output.getvalue())
        self.assertIn('GPU compute processes',error.getvalue())
    def test_unknown_or_mismatched_gpu_identity_does_not_become_waiting(self):
        report=self.probe();report['preflight']['hosts'][0]['nodes'][0]['GPU_INDICES']='0';report.pop('id');report=servers._store_probe(report)
        result=self.render(report);self.assertFalse(result['resource_ready']);self.assertFalse(candidate_pool.capacity_wait(result))
    def test_wrong_wannier_classification_is_rejected_and_source_preserved(self):
        record=job_templates.import_template(PBS,'gpu','wannier90','fixture','old wrong classification')
        config={**self.config,'program':'wannier90','template_id':record['id']}
        preview=job_templates.render_template(record['id'],{'name':'test','queue':'batch','node':'node01','ppn':1,'walltime':'01:00:00'},'gpu',config)
        self.assertFalse(preview['submission_supported']);self.assertIn('Wannier90',';'.join(preview['execution_blockers']))
        self.assertEqual(job_templates.get_template(record['id'])['original_text'],PBS)

    def fixture(self, kind):
        # Reuse real Si input/freeze fixtures; all paths and receipts stay isolated.
        if kind == 'chain':
            from test_chain_runs import ChainRunTests
            fixture=ChainRunTests()
        else:
            from test_scans import ScanTests
            fixture=ScanTests()
        fixture.setUp();self.addCleanup(fixture.tearDown)
        return fixture

    def test_gpu_single_prepare_freezes_full_allocation_and_mpi_separately(self):
        from backend import inputs
        fixture=self.fixture('chain')
        frozen=inputs.freeze(fixture.project,'r','GPU isolated acceptance')
        receipt={'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/test/fpilot\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        self.config['remote_workspace_root']='$HOME/fpilot'
        probe=self.probe()
        with patch.object(execution,'prepare',execution.prepare.__wrapped__),patch.object(execution,'ssh',return_value=receipt) as remote:
            prepared=execution.prepare(frozen['id'],self.config,probe['id'],'node01',project_id='p')
        self.assertEqual(prepared['state'],'PREPARED');self.assertIsNone(prepared['job_id'])
        self.assertEqual(prepared['profile']['ppn'],10);self.assertEqual(prepared['profile']['gpus'],2)
        self.assertEqual(prepared['profile']['mpi_ranks'],1)
        self.assertIn('mpirun -np 1',prepared['template_snapshot']['rendered_text'])
        self.assertEqual(prepared['template_snapshot']['original_text'],PBS)
        self.assertNotIn('qsub',remote.call_args.args[1])

    def test_gpu_scan_busy_pending_then_dispatch_once_and_config_drift_blocks(self):
        from backend import scans
        fixture=self.fixture('scan')
        batch=scans.freeze(fixture.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'400','step':'1'}],'GPU simulation')
        candidate={'node':'node01','cores':10,'reason':''};idle=self.render(self.probe())
        with patch.object(servers,'probe',return_value=self.probe(active=2)):
            batch=scans.authorize(batch['id'],'p',batch['case_table_hash'],self.config,'node01',10,'','',{},candidates=[candidate],approved_previews={'node01':idle},max_parallel=1)
        def prepare(*args,**kwargs):
            record={'id':kwargs['attempt_id'],'state':'PREPARED','config':args[1],'bundle_id':args[0],
                    'project_id':'p','profile':idle['profile'],'node':'node01','cores':10,'reason':'','execution_scope':'scan'}
            with execution.db() as db: db.execute('INSERT INTO attempts VALUES(?,?,?)',(record['id'],record['state'],json.dumps(record)))
            return record
        def submit(key):return execution.save({**execution.get(key),'state':'SUBMITTED','job_id':'SIMULATED.GPU'})
        with patch.object(servers,'capacity_probe',return_value=self.probe(active=2)),patch.object(execution,'prepare',side_effect=prepare) as prep,patch.object(execution,'submit',side_effect=submit) as dispatch:
            scans.tick();prep.assert_not_called();dispatch.assert_not_called()
        queued=scans.get(batch['id']);self.assertEqual(queued['state'],'ACTIVE');self.assertEqual(queued['cases'][0]['state'],'PENDING');self.assertIn('GPU',queued['waiting_reason'])
        with patch.object(servers,'capacity_probe',return_value=self.probe()),patch.object(execution,'prepare',side_effect=prepare) as prep,patch.object(execution,'submit',side_effect=submit) as dispatch:
            scans.tick();scans.tick();prep.assert_called_once();dispatch.assert_called_once()
        self.assertEqual(scans.get(batch['id'])['cases'][0]['state'],'SUBMITTED')
        # A real request edit is distinct from transient activity and cannot reuse authorization.
        altered={**self.config,'name':'CHANGED'};current=servers.preview(altered,self.probe()['id'],'node01')
        self.assertNotEqual(idle['script'],current['script'])
        with self.assertRaisesRegex(ValueError,'偏离授权预览'):
            candidate_pool.select(altered,self.probe(),batch['authorization']['candidates'])

    def test_gpu_chain_busy_pending_then_dispatch_exact_one_step(self):
        from backend import chain_runs as chains
        fixture=self.fixture('chain');project=fixture.project
        report=chains.preview(project,['r']);run=chains.freeze(project,['r'],'GPU simulation',report['definition_hash'])
        idle=self.render(self.probe());resource={'vasp_executable':'vasp_std','candidates':[{'node':'node01','cores':10,'reason':''}]}
        with patch.object(servers,'probe',return_value=self.probe(active=2)):
            run=chains.authorize(run['id'],'p',run['plan_hash'],run['mismatch_hash'],self.config,{'r':resource},{'r':{'node01':idle}})
        def prepare(*args,**kwargs):
            record={'id':kwargs['attempt_id'],'state':'PREPARED','config':args[1],'bundle_id':args[0],
                'project_id':'p','profile':idle['profile'],'node':'node01','cores':10,'reason':''}
            with execution.db() as db: db.execute('INSERT INTO attempts VALUES(?,?,?)',(record['id'],record['state'],json.dumps(record)))
            return record
        def submit(key):return execution.save({**execution.get(key),'state':'SUBMITTED','job_id':'SIMULATED.CHAIN_GPU'})
        with patch.object(chains,'_quota_available',return_value=True),patch.object(servers,'capacity_probe',return_value=self.probe(active=2)),patch.object(execution,'prepare',side_effect=prepare) as prep,patch.object(execution,'submit',side_effect=submit) as dispatch:
            chains.tick();prep.assert_not_called();dispatch.assert_not_called()
        queued=chains.get(run['id']);self.assertEqual(queued['state'],'ACTIVE');self.assertEqual(queued['steps'][0]['state'],'PENDING');self.assertIn('GPU',queued['steps'][0]['waiting_reason'])
        with patch.object(chains,'_quota_available',return_value=True),patch.object(servers,'capacity_probe',return_value=self.probe()),patch.object(execution,'prepare',side_effect=prepare) as prep,patch.object(execution,'submit',side_effect=submit) as dispatch:
            chains.tick();chains.tick();prep.assert_called_once();dispatch.assert_called_once()
        self.assertEqual(chains.get(run['id'])['steps'][0]['state'],'SUBMITTED')
        self.assertEqual(len(chains.get(run['id'])['steps'][0]['attempts']),1)

    def test_gpu_parallel_checks_effective_ncore_without_changing_inputs(self):
        incar='KPAR = 1\nNCORE = 8\n'
        execution.validate_parallel(incar,1,gpu=True)
        with self.assertRaises(ValueError):execution.validate_parallel(incar,1)
        with self.assertRaises(ValueError):execution.validate_parallel('KPAR = 2\n',1,gpu=True)
        self.assertEqual(incar,'KPAR = 1\nNCORE = 8\n')

    def test_gpu_implicit_mpirun_count_requires_explicit_copy(self):
        record=job_templates.import_template(PBS.replace('mpirun -np 1','mpirun'),'gpu','gpu_vasp','fixture')
        self.config['template_id']=record['id'];result=self.render(self.probe())
        self.assertFalse(result['resource_ready']);self.assertTrue(any('未明确进程数' in b for b in result['blockers']))

    def test_local_default_repair_preserves_legacy_snapshot_but_normal_edits_do_not(self):
        from backend import workbench_settings as settings
        with patch.object(settings,'ROOT',self.root):
            before=settings.snapshot('gpu','vasp_std')
            settings.repair_new_draft_defaults([{'runtime':self.config,'program':'vasp_std','template_id':self.record['id']}])
            settings.assert_current(before)
            settings.save({'runtime':{**self.config,'name':'EDITED'},'program':'vasp_std','template_id':self.record['id']})
            with self.assertRaisesRegex(ValueError,'设置已变化'):settings.assert_current(before)
            self.assertEqual(before['template_id'],None)

    def test_registered_and_frozen_chain_runtime_ignore_new_script_defaults(self):
        from backend import workbench_settings as settings,chain_runs as chains
        with patch.object(settings,'ROOT',self.root):
            settings.repair_new_draft_defaults([{'runtime':self.config,'program':'vasp_std','template_id':self.record['id']}])
            config={**servers.DEFAULT,'alias':'gpu','vasp_executable':'/original/vasp_std'}
            result=chains._node_config(config,{'vasp_executable':'/original/vasp_std'})
            self.assertEqual(result['vasp_executable'],'/original/vasp_std');self.assertEqual(result['runtime_source'],'registered')
            self.assertIsNone(servers.selected_template(result))
            self.assertEqual(chains._node_config(result,{'vasp_executable':'vasp_std'}),result)
            self.assertNotIn('template_id',result)

    def test_gpu_unknown_braced_launcher_rank_is_not_assumed_from_cpu_header(self):
        record=job_templates.import_template(PBS.replace('mpirun -np 1','mpirun -np "${OTHER_RANKS}"'),'gpu','gpu_vasp','fixture')
        self.config['template_id']=record['id'];result=self.render(self.probe())
        self.assertFalse(result['resource_ready']);self.assertTrue(any('动态 MPI' in b for b in result['blockers']))
        record=job_templates.import_template(PBS.replace('mpirun -np 1','mpirun -np "${NPROCS}"'),'gpu','gpu_vasp','fixture')
        self.config['template_id']=record['id'];self.assertTrue(self.render(self.probe())['resource_ready'])
