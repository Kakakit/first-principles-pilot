import hashlib
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import execution as ex, program_tasks, program_sources, remote_cleanup
import test_direct_execution as direct


class ProgramSourcesTests(unittest.TestCase):
    install = direct.DirectExecutionTests.install
    ssh = direct.DirectExecutionTests.ssh

    def setUp(self):
        direct.DirectExecutionTests.setUp(self)
        self.project['edges']=[]
        self.parent=direct.DirectExecutionTests.prepare(self)
        self.run_dir.mkdir(parents=True)
        self.poscar=Path('tests/fixtures/Si.POSCAR').read_bytes()
        (self.run_dir/'POSCAR').write_bytes(self.poscar)
        ex.save({**self.parent,'state':'COMPLETED'})
        self.host['scheduler']='pbs'
        self.install(patch.object(ex,'ssh',side_effect=self.batch_ssh))
        self.add('n',self.parent['id'],'POSCAR','exchange.csv')

    def batch_ssh(self, alias, command, data=None, **kwargs):
        shell='qsub(){ PBS_O_WORKDIR="$PWD" /bin/bash "$1"; echo 19.lab; }; qstat(){ echo "job_state = C"; echo "exit_status = 0"; };\n'
        result=subprocess.run(['/bin/bash','-c',shell+command],input=data,capture_output=True)
        return dict(exit_code=result.returncode,stdout=result.stdout.decode(),stderr=result.stderr.decode())

    def add(self,parent,key,source,target):
        node_id='child-'+parent
        ref=dict(source_attempt_id=key,node_id=parent,file=source,name='selected-input')
        data=dict(kind='program',label=node_id,stageSlug='program',stageSequence=9,executionMode='program',
                  programDraft=dict(command='head -n 1 selected-input > '+target,alias='lab',files=[],outputs=[target],sources=[ref]))
        self.project['edges'].append(dict(source=parent,target=node_id))
        self.project['nodes'].append(dict(id=node_id,data=data))
        return node_id

    def test_scf_analysis_consumer(self):
        current='child-n'
        for source,target in [('POSCAR','exchange.csv'),('exchange.csv','result.txt')]:
            record=program_tasks.prepare(self.project,current)
            ref=program_tasks.frozen(record)['sources'][0]
            self.assertEqual(ref['file'],source)
            self.assertEqual(ref['bundle_id'],ex.get(ref['source_attempt_id'])['bundle_id'])
            self.assertIn(record['id'],remote_cleanup._pending_children(ref['source_attempt_id']))
            self.assertEqual(ex.submit(record['id'])['state'],'SUBMITTED')
            self.assertEqual(ex.refresh(record['id'])['state'],'COMPLETED')
            content=(Path(record['remote_path_absolute'])/'selected-input').read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(),ref['sha256'])
            self.assertEqual(program_sources.editable_text(record['id'],'p',target)['text'],self.poscar.decode().splitlines()[0]+'\n')
            if source=='POSCAR':
                current=self.add(current,record['id'],target,'result.txt')
        self.assertEqual(len(program_sources.candidates('p','child-n','lab')),1)

    def test_direct_launch_stages_frozen_bytes(self):
        self.host['scheduler']='custom'
        record=program_tasks.prepare(self.project,'child-n')
        with patch.object(ex,'ssh',side_effect=self.ssh):
            self.assertEqual(ex.submit(record['id'])['state'],'DISPATCHING')
        self.assertEqual((Path(record['remote_path_absolute'])/'selected-input').read_bytes(),self.poscar)

    def test_cross_host_and_undeclared_output_rejected(self):
        draft=self.project['nodes'][1]['data']['programDraft']
        with self.assertRaisesRegex(ValueError,'同服务器'):
            program_sources.resolve(self.project,'child-n','other',draft,program_tasks.check_document(draft))
        child=program_tasks.prepare(self.project,'child-n')
        ex.submit(child['id'])
        ex.refresh(child['id'])
        target=self.add('child-n',child['id'],'selected-input','out.txt')
        with self.assertRaisesRegex(ValueError,'声明的输出'):
            program_tasks.prepare(self.project,target)

    def test_changed_source_fails_before_launch(self):
        record=program_tasks.prepare(self.project,'child-n')
        (self.run_dir/'POSCAR').write_text('changed')
        result=ex.submit(record['id'])
        self.assertTrue(result['not_dispatched'])
        self.assertFalse((Path(record['remote_path_absolute'])/'exchange.csv').exists())
        self.assertIsNone(result['job_id'])

    def test_refuses_disconnected_source_and_duplicate_name(self):
        self.project['nodes'][1]['data']['programDraft']['files']=[dict(name='selected-input',text='')]
        with self.assertRaisesRegex(ValueError,'重名'):
            program_tasks.prepare(self.project,'child-n')
        self.project['edges']=[]
        with self.assertRaisesRegex(ValueError,'已连接'):
            program_tasks.prepare(self.project,'child-n')
