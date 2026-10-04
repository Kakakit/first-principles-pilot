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
RESOURCES = {'name': 'FP_Test', 'queue': 'batch', 'node': 'node02', 'ppn': 16, 'walltime': '00:10:00'}


class JobTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = patch.object(job_templates, 'ROOT', Path(self.temp.name))
        self.recycle_root = patch.object(recycle, 'ROOT', Path(self.temp.name))
        self.root.start(); self.recycle_root.start()

    def tearDown(self):
        self.recycle_root.stop(); self.root.stop(); self.temp.cleanup()

    def test_user_body_is_preserved_and_not_an_eligibility_gate(self):
        record = job_templates.import_template(PBS, 'jutai-1', 'vasp_std', 'user', 'PBS')
        self.assertTrue(record['submission_supported'])
        self.assertEqual(record['original_text'], PBS)
        self.assertEqual(record['original_hash'], hashlib.sha256(PBS.encode()).hexdigest())
        preview = job_templates.render_template(record['id'], RESOURCES, alias='jutai-1')
        self.assertTrue(preview['submission_supported'], preview['execution_blockers'])
        self.assertIn('#PBS -l nodes=node02:ppn=16', preview['rendered_text'])
        self.assertIn('cd /scratch/user-choice\ntrap', preview['rendered_text'])
        ncl = job_templates.render_template(record['id'], RESOURCES, alias='jutai-1',
                   config={'scheduler':'pbs','vasp_executable':'/opt/vasp/vasp_ncl'})
        self.assertIn('/opt/vasp/vasp_ncl > vasp.out', ncl['rendered_text'])
        self.assertEqual(job_templates.get_template(record['id'])['original_text'], PBS)

    def test_slurm_script_can_be_selected_and_resources_are_explicit(self):
        record = job_templates.import_template(SLURM, 'gengzi-187', 'gpu_vasp', 'user', 'Slurm')
        preview = job_templates.render_template(record['id'], dict(RESOURCES, queue='master', node='cn1'), alias='gengzi-187', config={'scheduler':'slurm'})
        self.assertTrue(preview['submission_supported'], preview['execution_blockers'])
        self.assertIn('#SBATCH --nodelist=cn1', preview['rendered_text'])
        self.assertIn('#SBATCH --ntasks=16', preview['rendered_text'])
        self.assertIn('module load VASP\nmpirun', preview['rendered_text'])
        self.assertEqual(preview['original_text'], SLURM)

    def test_windows_scripts_keep_original_bytes_and_render_linux_copy(self):
        for body, alias, program, scheduler in ((PBS, 'jutai-1', 'vasp_std', 'pbs'),
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
        with self.assertRaisesRegex(ValueError, '调度器声明'):
            job_templates.import_template(PBS, 'jutai-1', 'vasp_std', 'user', scheduler='slurm')

    def test_recycle_hides_list_without_changing_original(self):
        record = job_templates.import_template(PBS, 'jutai-1', 'vasp_std', 'user')
        recycle.move('job-templates', record['id'])
        self.assertEqual(job_templates.list_templates(), [])
        self.assertEqual(job_templates.get_template(record['id'])['original_text'], PBS)
        recycle.restore('job-templates', record['id'])
        self.assertEqual(len(job_templates.list_templates()), 1)

    def test_source_size_and_scheduler_identity(self):
        with self.assertRaises(ValueError):
            job_templates.import_template('echo hello', 'jutai-1', 'vasp_std', 'user')
        with self.assertRaises(ValueError):
            job_templates.import_template(PBS, 'unknown-host', 'vasp_std', 'user')
        self.assertEqual(job_templates.list_templates(), [])


if __name__ == '__main__': unittest.main()
