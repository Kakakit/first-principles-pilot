import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from backend import servers, candidate_pool

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=servers.ROOT;servers.ROOT=Path(self.temp.name)
        (servers.ROOT/'.ssh').mkdir();(servers.ROOT/'.ssh/config').write_text('Host jutai-1\n')
        self.home=patch.object(Path,'home',return_value=servers.ROOT);self.home.start()
        self.config=copy.deepcopy(servers.DEFAULT)
    def tearDown(self):
        self.home.stop();servers.ROOT=self.old;self.temp.cleanup()
    def probe(self,status='FULLY_IDLE',free=48,alias=None,node='node01'):
        alias=alias or self.config['alias']
        capacity=f'===== {alias} =====\nSCHEDULER=PBS/Torque\nNODE={node} STATUS={status} CPUS_FREE={free} CPUS_TOTAL=48\n'
        details='CHECK_mpi_executable=OK\nCHECK_vasp_executable=OK\nCHECK_environment_script=OK\nQueue: batch\n enabled = True\n started = True\n resources_max.walltime = 48:00:00\n'
        with patch.object(servers,'run',return_value={'exit_code':0,'stdout':capacity+'FPILOT_DIAGNOSTICS_BEGIN\n'+details,'stderr':''}) as run:
            report=servers.probe(self.config)
            self.assertNotIn('\nqsub ',run.call_args_list[0].args[0][-1])
            return report
    def test_registered_hosts_are_selectable_with_live_runtime_checks(self):
        profiles={item['alias']:item for item in servers.registered_profiles()}
        self.assertTrue(profiles['jutai-1']['selectable'])
        self.assertEqual(profiles['jutai-1']['ncl_runtime_status'],'PROJECT_EVIDENCE')
        self.assertTrue(profiles['jutai-1']['ncl_executable'].endswith('/vasp_ncl'))
        self.assertTrue(profiles['jutai-5']['selectable'])
        self.assertTrue(profiles['jutai-2']['selectable'])
        self.assertEqual(profiles['jutai-2']['reason_code'],'runtime_incomplete')
        self.assertIn('mpi_executable',profiles['jutai-2']['reason'])
        self.assertTrue(profiles['gengzi-187']['selectable'])
        self.assertEqual(profiles['gengzi-187']['reason_code'],'ready')
        self.assertTrue(profiles['gengzi-187']['supports_slurm_preview'])
        self.assertEqual(servers.select_profile('gengzi-187')['scheduler'],'slurm')
        self.assertTrue(profiles['jutai-4']['selectable'])
        self.assertEqual(servers.select_profile('jutai-5')['alias'],'jutai-5')

    def test_read_config_exposes_registry_profiles(self):
        response=servers.read_config()
        self.assertIn('registered_profiles',response)
        self.assertEqual({item['alias'] for item in response['registered_profiles']},
                         {'gengzi-187','jutai-1','jutai-2','jutai-3','jutai-4','jutai-5'})

    def test_validate_rejects_incomplete_runtime_without_guessing(self):
        (servers.ROOT/'.ssh/config').write_text('Host jutai-1 jutai-2\n')
        self.config=servers.select_profile('jutai-2')
        with self.assertRaisesRegex(ValueError,'运行时路径'):
            servers.validate(self.config)

    def test_node06_exclusion_is_host_specific(self):
        (servers.ROOT/'.ssh/config').write_text('Host jutai-1 jutai-5\n')
        self.config=servers.select_profile('jutai-5')
        capacity='===== jutai-5 =====\nSCHEDULER=PBS/Torque\nNODE=node06 STATUS=FULLY_IDLE CPUS_FREE=48 CPUS_TOTAL=48\n'
        details='CHECK_mpi_executable=OK\nCHECK_vasp_executable=OK\nCHECK_environment_script=OK\nQueue: batch\n enabled = True\n started = True\n resources_max.walltime = 48:00:00\n'
        with patch.object(servers,'run',return_value={'exit_code':0,'stdout':capacity+'FPILOT_DIAGNOSTICS_BEGIN\n'+details,'stderr':''}) as run:
            report=servers.probe(self.config)
        self.assertNotIn('EXCLUDED_NODES=node06',run.call_args_list[0].args[0])
        self.assertTrue(servers.preview(self.config,report['id'],'node06')['resource_ready'])
    def test_save_and_read_preserves_versions(self):
        servers.save_config(self.config)
        self.config['walltime']='02:00:00';servers.save_config(self.config)
        self.assertEqual(servers.read_config()['config'],self.config)
        self.assertEqual(len(list((servers.ROOT/'server-configs').iterdir())),2)
    def test_workspace_root_is_saved_and_bad_root_rejected(self):
        self.config['remote_workspace_root']='$HOME/230269265lht/research'
        servers.save_config(self.config)
        self.assertEqual(servers.read_config()['config']['remote_workspace_root'],self.config['remote_workspace_root'])
        self.config['remote_workspace_root']='relative'
        with self.assertRaises(ValueError):servers.save_config(self.config)
        self.assertEqual(servers.read_config()['config']['remote_workspace_root'],'$HOME/230269265lht/research')
    def test_ready_preview_never_enables_submission(self):
        report=self.probe();r=servers.preview(self.config,report['id'],'node01')
        self.assertTrue(r['resource_ready']);self.assertFalse(r['submission_enabled'])
        self.assertIn('nodes=node01:ppn=48',r['script']);self.assertIn('wc -l < "$PBS_NODEFILE"',r['script'])
        self.assertLess(r['script'].index('cd '),r['script'].index('source '))
    def test_slurm_probe_and_preview_use_registered_runtime(self):
        (servers.ROOT/'.ssh/config').write_text('Host jutai-1 gengzi-187\n')
        config=servers.select_profile('gengzi-187')
        capacity='===== gengzi-187 =====\nSCHEDULER=Slurm\nNODE=cn1 STATUS=FULLY_IDLE STATE=IDLE CPUS_FREE=56 CPUS_TOTAL=56 PARTITIONS=master,high_priority\n'
        details='CHECK_vasp_executable=OK\nFPILOT_PARTITION=master*|up|infinite\n'
        with patch.object(servers,'run',return_value={'exit_code':0,'stdout':capacity+'FPILOT_DIAGNOSTICS_BEGIN\n'+details,'stderr':''}) as run:
            report=servers.probe(config)
        self.assertNotIn('\nsbatch ',run.call_args_list[0].args[0][-1])
        preview=servers.preview(config,report['id'],'cn1')
        self.assertTrue(preview['resource_ready'])
        self.assertEqual(preview['profile']['ntasks'],56)
        self.assertIn('#SBATCH --partition=master',preview['script'])
        self.assertIn('module load VASP/6.2.1-gzbuild',preview['script'])
        self.assertIn('mpirun -np "$NPROCS"',preview['script'])
        self.assertIn('I_MPI_FABRICS=shm',preview['script'])
    def test_busy_and_excluded_nodes(self):
        report=self.probe('BUSY',0);r=servers.preview(self.config,report['id'],'node01')
        self.assertFalse(r['resource_ready']);self.assertTrue(r['script'])
        with self.assertRaises(ValueError):servers.preview(self.config,report['id'],'node06')

    def test_pool_waits_for_residual_load_on_pbs_and_slurm_without_script_drift(self):
        (servers.ROOT/'.ssh/config').write_text('Host jutai-1 gengzi-187\n')
        for scheduler,alias,node,cores in [('PBS/Torque','jutai-1','node01',48),('Slurm','gengzi-187','cn1',56)]:
            with self.subTest(scheduler=scheduler):
                config=servers.select_profile(alias)
                details=('CHECK_mpi_executable=OK\nCHECK_vasp_executable=OK\nCHECK_environment_script=OK\nQueue: batch\n enabled = True\n started = True\n resources_max.walltime = 48:00:00\n'
                         if scheduler=='PBS/Torque' else 'CHECK_vasp_executable=OK\nFPILOT_PARTITION=master*|up|infinite\n')
                def probe(status,free):
                    capacity=f'===== {alias} =====\nSCHEDULER={scheduler}\nNODE={node} STATUS={status} STATE=IDLE CPUS_FREE={free} CPUS_TOTAL={cores} LOAD=1.23\n'
                    with patch.object(servers,'run',return_value={'exit_code':0,'stdout':capacity+'FPILOT_DIAGNOSTICS_BEGIN\n'+details,'stderr':''}):
                        return servers.probe(config,force=True)
                report=probe('FULLY_IDLE',cores)
                candidates=[{'node':node,'cores':cores,'reason':''}]
                reviewed=candidate_pool.previews(config,report,candidates)
                approved=candidate_pool.approve(candidates,reviewed,reviewed)
                for status,free in [('BUSY',0),('SUSPECT_LOAD',0),('PARTIAL',cores//2)]:
                    report=probe(status,free)
                    with patch.object(servers,'run') as remote:
                        value=servers.preview(config,report['id'],node,cores)
                        self.assertFalse(value['resource_ready'])
                        self.assertEqual(value['profile'],approved[0]['profile'])
                        self.assertEqual(servers.hashlib.sha256(value['script'].encode()).hexdigest(),approved[0]['script_sha256'])
                        selected,reason=candidate_pool.select(config,report,approved)
                        self.assertIsNone(selected)
                        self.assertIn(node,reason)
                        if status=='SUSPECT_LOAD':self.assertIn('负载未恢复',reason)
                        remote.assert_not_called()
                for status in ('UNAVAILABLE','SUSPECT_MOM'):
                    with self.assertRaisesRegex(ValueError,'资源检查未通过.*'+status):
                        candidate_pool.select(config,probe(status,0),approved)
                report=probe('FULLY_IDLE',cores)
                self.assertEqual(candidate_pool.select(config,report,approved),(approved[0],''))
                with self.assertRaisesRegex(ValueError,'偏离授权预览'):
                    candidate_pool.select({**config,'walltime':'00:30:00'},report,approved)

    def test_stale_and_changed_config(self):
        report=self.probe();report.pop('id');report['preflight']['observed_at']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        raw=json.dumps(report).encode();key=servers.hashlib.sha256(raw).hexdigest();servers.put_file(servers.ROOT/'server-probes'/key,raw)
        self.assertFalse(servers.preview(self.config,key,'node01')['resource_ready'])
        self.config['queue']='other'
        self.assertIn('未发现所选队列',servers.preview(self.config,key,'node01')['blockers'])
    def test_job_edits_keep_capacity_and_runtime_without_remote_requests(self):
        report=self.probe()
        changed={**self.config,'name':'Edited','walltime':'02:00:00'}
        with patch.object(servers,'run') as remote:
            result=servers.preview(changed,report['id'],'node01',16,'small test')
            remote.assert_not_called()
        self.assertTrue(result['resource_ready']);self.assertIn('#PBS -N Edited',result['script'])
        self.assertIn('ppn=16',result['script'])
        changed['walltime']='49:00:00'
        with patch.object(servers,'run') as remote:
            self.assertIn('walltime 超过队列上限',servers.preview(changed,report['id'],'node01')['blockers'])
            remote.assert_not_called()
        with patch('backend.site_registration.connection_identity',return_value={'hostname':'changed'}):
            with self.assertRaisesRegex(ValueError,'SSH 目标'):servers.preview(self.config,report['id'],'node01')

    def test_script_runtime_requires_no_program_environment_paths(self):
        from backend import job_templates
        with patch.object(job_templates,'ROOT',servers.ROOT):
            original='#!/bin/bash\n#PBS -q batch\n#PBS -l nodes=node01:ppn=48\nsource /unknown/setvars.sh\nmpirun -np "$NPROCS" /unknown/vasp_std > vasp.out\n'
            script=job_templates.import_template(original,'jutai-1','vasp_std','user')
            self.config={**self.config,'runtime_source':'script','program':'vasp_std','template_id':script['id'],'mpi_executable':'','environment_script':'','vasp_executable':'vasp_std'}
            report=self.probe()
            with patch.object(servers,'run') as remote:
                result=servers.preview({**self.config,'name':'New'},report['id'],'node01')
                remote.assert_not_called()
            self.assertTrue(result['resource_ready'],result['blockers'])
            self.assertIn('source /unknown/setvars.sh',result['script'])

    def test_lower_cores_requires_reason_and_queue_limit(self):
        report=self.probe()
        self.assertFalse(servers.preview(self.config,report['id'],'node01',16)['resource_ready'])
        good=servers.preview(self.config,report['id'],'node01',16,'用户限制测试规模')
        self.assertTrue(good['resource_ready']);self.assertIn('ppn=16',good['script'])
        self.config['walltime']='49:00:00';report=self.probe()
        self.assertIn('walltime 超过队列上限',servers.preview(self.config,report['id'],'node01')['blockers'])
    def test_invalid_and_timeout(self):
        self.config['walltime']='01:99:00'
        with self.assertRaises(ValueError):servers.validate(self.config)
        self.config['walltime']='01:00:00';self.config['name']='bad\nname'
        with self.assertRaises(ValueError):servers.validate(self.config)
