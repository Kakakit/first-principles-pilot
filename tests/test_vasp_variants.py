import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import vasp_variants as variants, job_templates, chain_runs, servers, workbench_settings


class VaspVariantTests(unittest.TestCase):
    def test_only_launch_path_changes_and_variable_checks_follow_target(self):
        original = '''#!/bin/bash
source /environment/setvars.sh
MPI_RUN=/mpi/bin/mpirun
VASP_STD="/vasp/bin/vasp_std"
test -x "$VASP_STD"
echo "original vasp_std"
"$MPI_RUN" -np "$NPROCS" "$VASP_STD" > vasp_std.out 2> vasp.err
'''
        text, info, blockers = variants.adapt(original, 'vasp_ncl')
        self.assertEqual(blockers, [])
        self.assertIn('VASP_STD="/vasp/bin/vasp_ncl"', text)
        self.assertIn('source /environment/setvars.sh\nMPI_RUN=/mpi/bin/mpirun', text)
        self.assertIn('echo "original vasp_std"', text)
        self.assertIn('> vasp_std.out 2> vasp.err', text)
        self.assertEqual(info['strategy'], 'same_directory')
        self.assertIn('test -x "$VASP_STD"', text)
        self.assertEqual(variants.adapt(original, 'vasp_std'), (original, None, []))
        subprocess.run(['bash', '-n'], input=text, text=True, check=True)

    def test_wrappers_multiple_launches_and_reassignments_are_not_guessed(self):
        bodies = [
            'mpirun /bin/vasp_std\nmpirun /bin/vasp_std\n',
            'VASP=/bin/vasp_std\nVASP=$(choose)\nmpirun "$VASP"\n',
            'VASP=/bin/vasp_std\nsource runtime.sh\nmpirun "$VASP"\n',
            'mpirun echo /bin/vasp_std\n',
            'if true; then mpirun /bin/vasp_std; fi\n',
            'mpirun /bin/vasp_std\nbash -c "mpirun /bin/vasp_std > out"\n',
            'mpirun /bin/vasp_std\necho "$(mpirun /bin/vasp_std)"\n',
        ]
        for body in bodies:
            with self.subTest(body=body):
                text, info, blockers = variants.adapt(body, 'vasp_ncl')
                self.assertEqual(text, body)
                self.assertIsNone(info)
                self.assertTrue(blockers)

    def test_path_resolution_uses_same_directory_and_missing_target_never_runs_std(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            standard = root / 'vasp_std'
            standard.write_text('#!/bin/bash\necho WRONG_STANDARD\n'); standard.chmod(0o755)
            for command in ('vasp_std', 'VASP=vasp_std\n"$VASP"'):
                original = '#!/bin/bash\nPATH=' + str(root) + ':$PATH\n' + command + '\n'
                text, info, blockers = variants.adapt(original, 'vasp_ncl')
                self.assertFalse(blockers)
                missing = subprocess.run(['bash'], input=text, text=True, capture_output=True)
                self.assertEqual(missing.returncode, 127)
                self.assertNotIn('WRONG_STANDARD', missing.stdout)
                self.assertIn('vasp_ncl', missing.stderr)
                target = root / 'vasp_ncl'
                target.write_text('#!/bin/bash\necho RIGHT_NONCOLLINEAR\n'); target.chmod(0o755)
                success = subprocess.run(['bash'], input=text, text=True, capture_output=True)
                self.assertEqual(success.returncode, 0, success.stderr)
                self.assertEqual(success.stdout.strip(), 'RIGHT_NONCOLLINEAR')
                target.unlink()

    def test_same_script_handles_mixed_chain_and_gpu_requests_without_mutating_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(job_templates, 'ROOT', Path(tmp)), \
             patch.object(workbench_settings, 'ROOT', Path(tmp)), \
             patch.object(servers,'load_registry',return_value={'hosts':{'fixture':{}}}), \
             patch('backend.site_registration.ssh_aliases',return_value=['fixture']):
            raw = '#!/bin/bash\n#PBS -N gpu\n#PBS -q batch\n#PBS -l nodes=node01:ppn=10:gpus=2\n#PBS -l walltime=01:00:00\nmpirun -np 1 /vasp/vasp_std\n'
            record = job_templates.import_template(raw, 'fixture', 'gpu_vasp', 'fixture')
            config = {**servers.DEFAULT, 'alias':'fixture', 'runtime_source':'script', 'program':'vasp_std', 'template_id':record['id']}
            cpu = chain_runs._node_config(config, {'vasp_executable':'vasp_std'})
            soc = chain_runs._node_config(config, {'vasp_executable':'vasp_ncl'})
            self.assertEqual(cpu['template_id'],soc['template_id'])
            self.assertEqual(soc['program'],'vasp_ncl')
            self.assertEqual(servers.resource_request(cpu),servers.resource_request(soc))
            resources={'name':'case','queue':'batch','node':'node01','ppn':10,'gpus':2,'mpi_ranks':1,'walltime':'01:00:00'}
            preview=job_templates.render_template(record['id'],resources,'fixture',soc)
            self.assertTrue(preview['submission_supported'],preview['execution_blockers'])
            self.assertIn('mpirun -np 1 /vasp/vasp_ncl',preview['rendered_text'])
            self.assertEqual(preview['original_text'],raw)
            with patch.object(servers,'validate',side_effect=lambda x:x):
                saved=workbench_settings.save({'runtime':soc,'program':'vasp_ncl','template_id':record['id']})
            self.assertEqual(saved['defaults']['fixture:vasp_ncl'],record['id'])
            wrong=job_templates.import_template(raw,'fixture','wannier90','fixture')
            self.assertFalse(job_templates.fits_program(wrong,'vasp_ncl'))
            misleading=job_templates.import_template(raw.replace('/vasp/vasp_std','/vasp/vasp_ncl')+'echo vasp_std\n','fixture','vasp_std','fixture')
            self.assertNotIn('program_conflict',misleading)
            self.assertTrue(job_templates.fits_program(misleading,'vasp_std'))


if __name__ == '__main__': unittest.main()
