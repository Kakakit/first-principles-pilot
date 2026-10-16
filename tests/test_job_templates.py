import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import job_templates, recycle

PBS = '''#!/bin/bash
#PBS -N old
#PBS -q batch
#PBS -l nodes=node01:ppn=48
cd /scratch/user-choice
trap 'echo done' EXIT
mpirun -np 48 /opt/vasp/vasp_std > vasp.out
'''
SLURM = '''#!/bin/bash
#SBATCH -J old
#SBATCH -p normal
#SBATCH -n 48
module load VASP
mpirun vasp_std > vasp.out
'''
SAFE_PBS = PBS.replace('cd /scratch/user-choice','cd \"$PBS_O_WORKDIR\"').replace("trap 'echo done' EXIT\n",'').replace('-np 48','-np \"$NPROCS\"')
RESOURCES = {'name': 'FP_Test', 'queue': 'batch', 'node': 'node02', 'ppn': 16, 'walltime': '00:10:00'}


class JobTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = patch.object(job_templates, 'ROOT', Path(self.temp.name))
        self.recycle_root = patch.object(recycle, 'ROOT', Path(self.temp.name))
        self.root.start(); self.recycle_root.start()

    def tearDown(self):
        self.recycle_root.stop(); self.root.stop(); self.temp.cleanup()

    def test_original_is_preserved_and_conflicts_require_an_adapted_copy(self):
        record = job_templates.import_template(PBS, 'jutai-1', 'vasp_std', 'user', 'PBS')
        self.assertEqual(record['original_text'], PBS)
        self.assertEqual(record['original_hash'], hashlib.sha256(PBS.encode()).hexdigest())
        preview = job_templates.render_template(record['id'], RESOURCES, alias='jutai-1')
        self.assertTrue(preview['submission_supported'])
        for text in ('cd /scratch/user-choice',"trap 'echo done' EXIT",'mpirun -np 48'):self.assertIn(text,preview['rendered_text'])
        adapted=PBS.replace('cd /scratch/user-choice','cd "$PBS_O_WORKDIR"').replace("trap 'echo done' EXIT\n",'').replace('-np 48','-np 16')
        child=job_templates.import_template(adapted,'jutai-1','vasp_std','user','adapted',parent_id=record['id'])
        rendered=job_templates.render_template(child['id'],RESOURCES,alias='jutai-1',config={'runtime_source':'script','program':'vasp_std','scheduler':'pbs'})
        self.assertTrue(rendered['submission_supported'],rendered['execution_blockers'])
        self.assertEqual(rendered['parent_hash'],record['script_hash'])
        self.assertEqual(job_templates.get_template(record['id'])['original_text'],PBS)

    def test_mixed_pbs_resources_survive_and_ncl_reuses_original(self):
        original=SAFE_PBS.replace('nodes=node01:ppn=48','nodes=node01:ppn=48,mem=96gb,walltime=01:00:00')
        record=job_templates.import_template(original,'jutai-1','vasp_std','user')
        config={'scheduler':'pbs','runtime_source':'script','program':'vasp_std'}
        result=job_templates.render_template(record['id'],RESOURCES,alias='jutai-1',config=config)
        self.assertTrue(result['submission_supported'],result['execution_blockers'])
        self.assertIn('nodes=node02:ppn=16,mem=96gb,walltime=00:10:00',result['rendered_text'])
        converted=job_templates.render_template(record['id'],RESOURCES,alias='jutai-1',config={**config,'program':'vasp_ncl'})
        self.assertTrue(converted['submission_supported'],converted['execution_blockers'])
        self.assertIn('/opt/vasp/vasp_ncl',converted['rendered_text'])
        self.assertEqual(converted['original_text'],original)
        self.assertEqual(converted['program_adaptation']['target_program'],'vasp_ncl')
        self.assertIn('-mpirun',converted['program_diff'])
        self.assertEqual(job_templates.get_template(record['id'])['original_text'],original)
        ncl=job_templates.import_template(original.replace('vasp_std','vasp_ncl'),'jutai-1','vasp_ncl','user',parent_id=record['id'])
        self.assertTrue(job_templates.render_template(ncl['id'],RESOURCES,alias='jutai-1',config={**config,'program':'vasp_ncl'})['submission_supported'])

    def test_slurm_thread_layout_is_not_mapped_to_total_mpi_ranks(self):
        record=job_templates.import_template(SLURM.replace('#SBATCH -n 48','#SBATCH -n 12\n#SBATCH --cpus-per-task=4'),'gengzi-187','vasp_std','user')
        preview=job_templates.render_template(record['id'],RESOURCES,alias='gengzi-187',config={'scheduler':'slurm','runtime_source':'script','program':'vasp_std'})
        self.assertTrue(preview['submission_supported']);self.assertEqual(preview['rendered_text'],record['original_text'])

    def test_workdir_suffix_and_ambiguous_slurm_directives_are_blocked(self):
        for body,alias,scheduler in ((SAFE_PBS.replace('$PBS_O_WORKDIR','$PBS_O_WORKDIR/../old'),'jutai-1','pbs'),
                                     (SLURM.replace('#SBATCH -n 48','#SBATCH -n 48 --cpus-per-task=4'),'gengzi-187','slurm')):
            record=job_templates.import_template(body,alias,'vasp_std','user')
            rendered=job_templates.render_template(record['id'],RESOURCES,alias=alias,config={'scheduler':scheduler,'runtime_source':'script','program':'vasp_std'})
            self.assertTrue(rendered['submission_supported'])
        record=job_templates.import_template(SAFE_PBS+'echo -n 1\n','jutai-1','vasp_std','user')
        self.assertTrue(job_templates.render_template(record['id'],RESOURCES,alias='jutai-1')['submission_supported'])

    def test_slurm_script_can_be_selected_and_resources_are_explicit(self):
        record = job_templates.import_template(SLURM, 'gengzi-187', 'gpu_vasp', 'user', 'Slurm')
        preview = job_templates.render_template(record['id'], dict(RESOURCES, queue='master', node='cn1'), alias='gengzi-187', config={'scheduler':'slurm'})
        self.assertTrue(preview['submission_supported'], preview['execution_blockers'])
        self.assertIn('#SBATCH --nodelist=cn1', preview['rendered_text'])
        self.assertIn('#SBATCH --ntasks=16', preview['rendered_text'])
        self.assertIn('module load VASP\nmpirun', preview['rendered_text'])
        self.assertEqual(preview['original_text'], SLURM)

    def test_windows_scripts_keep_original_bytes_and_render_linux_copy(self):
        for body, alias, program, scheduler in ((SAFE_PBS, 'jutai-1', 'vasp_std', 'pbs'),
                                                (SLURM, 'gengzi-187', 'gpu_vasp', 'slurm')):
            original = '\ufeff' + body.replace('\n', '\r\n')
            record = job_templates.import_template(original, alias, program, 'windows')
            preview = job_templates.render_template(record['id'], RESOURCES, alias=alias,
                                                     config={'scheduler': scheduler})
            self.assertTrue(preview['submission_supported'])
            self.assertEqual(preview['original_text'], original)
            self.assertEqual(preview['original_hash'], hashlib.sha256(original.encode('utf-8')).hexdigest())
            self.assertTrue(preview['line_endings_normalized'])
            self.assertTrue(preview['rendered_text'].startswith('#!/bin/bash\n'))
            self.assertNotIn('\r', preview['rendered_text'])
            self.assertNotIn('\ufeff', preview['rendered_text'])
            self.assertIn('mpirun', preview['rendered_text'])
            self.assertEqual(job_templates.get_template(record['id'])['original_text'], original)

    def test_blank_registry_has_no_inherited_verified_scripts(self):
        from backend import verified_templates
        with patch.object(job_templates.servers, 'load_registry', return_value={'hosts': {}}):
            self.assertEqual(verified_templates.catalog(), [])

    def test_host_and_scheduler_binding_still_apply(self):
        record = job_templates.import_template(PBS, 'jutai-1', 'vasp_std', 'user')
        self.assertFalse(job_templates.render_template(record['id'], RESOURCES, alias='jutai-5')['submission_supported'])
        self.assertFalse(job_templates.render_template(record['id'], RESOURCES, config={'scheduler':'slurm'})['submission_supported'])
        selected=job_templates.import_template(PBS, 'jutai-1', 'vasp_std', 'user', scheduler='slurm')
        self.assertEqual(selected['scheduler'],'slurm');self.assertEqual(selected['original_text'],PBS)

    def test_recycle_hides_list_without_changing_original(self):
        record = job_templates.import_template(PBS, 'jutai-1', 'vasp_std', 'user')
        recycle.move('job-templates', record['id'])
        self.assertEqual(job_templates.list_templates(), [])
        self.assertEqual(job_templates.get_template(record['id'])['original_text'], PBS)
        recycle.restore('job-templates', record['id'])
        self.assertEqual(len(job_templates.list_templates()), 1)

    def test_source_size_and_scheduler_identity(self):
        custom=job_templates.import_template('echo hello', 'jutai-1', 'vasp_std', 'user')
        self.assertEqual(custom['scheduler'],'custom')
        with self.assertRaises(ValueError):
            job_templates.import_template(PBS, 'unknown-host', 'vasp_std', 'user')
        self.assertEqual(len(job_templates.list_templates()),1)


if __name__ == '__main__': unittest.main()
