import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import artifacts, execution, postprocessing, extension_sdk


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.workspace=self.base/'230269265lht';self.attempt=self.workspace/'material-projects/p/flows/main/stages/03-scf/attempts/20260921-120000-abcd'
        self.attempt.mkdir(parents=True)
        self.old=execution.ROOT;execution.ROOT=self.base/'local'
        self.old_analysis=postprocessing.ROOT;postprocessing.ROOT=self.base/'local'
        self.old_sdk=extension_sdk.ROOT;extension_sdk.ROOT=self.base/'local'
        self.old_artifacts=artifacts.ROOT;artifacts.ROOT=self.base/'local'
        for extension_id in ('crystal-structure-distortion-analysis','lcalcpol-polarization-analysis'):
            extension_sdk.install(extension_id);extension_sdk.enable(extension_id,True)
        record={'id':'a','state':'COMPLETED','created_at':'2026-09-21T12:00:00+00:00','job_id':'1.node01',
                'config':{'alias':'jutai-1'},'project_id':'p-id','node_id':'n-id','flow_id':'single',
                'path_schema':'fpilot.remote-attempt-path','remote_workspace_root':str(self.workspace),
                'remote_relative_path':str(self.attempt.relative_to(self.workspace)),'remote_path_absolute':str(self.attempt),
                'evidence':{'vasp_timing':True,'scientific_validation':'NOT_PERFORMED'}}
        with execution.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',('a','COMPLETED',json.dumps(record)))
    def tearDown(self):execution.ROOT=self.old;postprocessing.ROOT=self.old_analysis;extension_sdk.ROOT=self.old_sdk;artifacts.ROOT=self.old_artifacts;self.temp.cleanup()
    def local_ssh(self,alias,command,**kwargs):
        result=subprocess.run(['bash','-c',command],capture_output=True)
        return {'exit_code':result.returncode,'stdout':result.stdout.decode(errors='replace'),'stderr':result.stderr.decode(errors='replace')}
    def test_lists_all_regular_files_and_previews_oszicar(self):
        (self.attempt/'OSZICAR').write_text('  1 F= -.100E+01 E0= -.110E+01  d E =-.20E-02 mag= 4.0\n')
        (self.attempt/'sub').mkdir();(self.attempt/'sub'/'name with space.txt').write_text('hello')
        (self.attempt/'escape').symlink_to(self.base/'outside')
        with patch.object(execution,'ssh',side_effect=self.local_ssh):
            listing=artifacts.refresh('a');view=artifacts.preview('a','OSZICAR')
        self.assertEqual({item['path'] for item in listing['files']},{'OSZICAR','sub/name with space.txt'})
        self.assertEqual(view['oszicar'][0]['kind'],'ionic')
    def test_rejects_traversal_large_and_v1(self):
        (self.attempt/'large').write_bytes(b'x'*(artifacts.MAX_PULL_BYTES+1))
        with patch.object(execution,'ssh',side_effect=self.local_ssh):
            with self.assertRaises(ValueError):artifacts.preview('a','../large')
            with self.assertRaisesRegex(ValueError,'10 MiB'):artifacts.preview('a','large')
        old=execution.get('a');old['id']='old';old['path_schema']='fpilot.remote-attempt-path.v1'
        with execution.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',('old','COMPLETED',json.dumps(old)))
        with self.assertRaisesRegex(ValueError,'目录结构'):artifacts.refresh('old')
    def test_extension_catalog_explains_readiness(self):
        (self.attempt/'POSCAR').write_text('small')
        (self.attempt/'OUTCAR').write_text('General timing and accounting informations')
        with patch.object(execution,'ssh',side_effect=self.local_ssh):
            artifacts.refresh('a');rows=postprocessing.catalog('a')
        status={item['id']:item for item in rows}
        self.assertTrue(status['crystal-structure-distortion-analysis']['ready'])
        self.assertTrue(status['lcalcpol-polarization-analysis']['ready'])
        self.assertFalse(status['pyw90']['ready'])

    def test_distortion_requires_explicit_applicability_and_keeps_source(self):
        options={'structure_source':'POSCAR','center_element':'Cr','ligand_element':'O',
                 'coordination':6,'search_radius_angstrom':3.0}
        (self.attempt/'POSCAR').write_bytes((Path(__file__).parent/'fixtures/Si.POSCAR').read_bytes())
        before=(self.attempt/'POSCAR').read_bytes()
        local_pull=lambda _attempt, path: ((self.attempt/path).read_bytes(), path)
        with patch.object(execution,'ssh',side_effect=self.local_ssh), patch.object(artifacts,'pull',side_effect=local_pull):
            check=postprocessing.check_applicability('a','crystal-structure-distortion-analysis',options)
            self.assertEqual(check['applicability_state'],'NOT_APPLICABLE')
            with self.assertRaisesRegex(ValueError,'不适用'):postprocessing.run('a','crystal-structure-distortion-analysis',confirmed=True,options=options)
        self.assertEqual((self.attempt/'POSCAR').read_bytes(),before)
        self.assertFalse((self.base/'local/analyses').exists())
        source=Path(__file__).parents[1]/'resources/extensions/crystal-structure-distortion-analysis/fixtures/ideal_fecr_polyhedra/POSCAR'
        (self.attempt/'POSCAR').write_bytes(source.read_bytes());before=source.read_bytes()
        with patch.object(execution,'ssh',side_effect=self.local_ssh), patch.object(artifacts,'pull',side_effect=local_pull):
            check=postprocessing.check_applicability('a','crystal-structure-distortion-analysis',options)
            self.assertEqual(check['applicability_state'],'APPLICABLE')
            self.assertEqual(postprocessing.check_applicability('a','crystal-structure-distortion-analysis',
                             {**options,'ligand_element':'S'})['applicability_state'],'NOT_APPLICABLE')
            self.assertEqual(postprocessing.check_applicability('a','crystal-structure-distortion-analysis',
                             {**options,'search_radius_angstrom':0.1})['applicability_state'],'NOT_APPLICABLE')
            result=postprocessing.run('a','crystal-structure-distortion-analysis',confirmed=True,options=options)
        self.assertEqual(result['state'],'COMPLETED')
        self.assertEqual(result['applicability_state'],'APPLICABLE')
        self.assertEqual(result['scientific_validation'],'NOT_PERFORMED')
        for ext in ('csv','json','md'):
            self.assertTrue((self.base/'local/analyses'/result['id']/f'polyhedral_distortion.{ext}').exists())
            self.assertTrue(postprocessing.output_file('a',result['id'],f'polyhedral_distortion.{ext}'))
        with self.assertRaises(ValueError):postprocessing.output_file('other',result['id'],'polyhedral_distortion.csv')
        self.assertEqual((self.attempt/'POSCAR').read_bytes(),before)


    def test_symmetry_is_read_only_and_records_three_tolerances(self):
        import hashlib
        source=(Path(__file__).parent/'fixtures/Si.POSCAR').read_bytes()
        (self.attempt/'POSCAR').write_bytes(source)
        options={'structure_source':'POSCAR','angle_tolerance_degree':5}
        with patch.object(execution,'ssh',side_effect=self.local_ssh), patch.object(artifacts,'pull',side_effect=lambda _attempt,path: ((self.attempt/path).read_bytes(),path)):
            with self.assertRaisesRegex(ValueError,'安装并启用'):
                postprocessing.check_applicability('a','structure-symmetry',options)
            extension_sdk.install('structure-symmetry')
            with self.assertRaisesRegex(ValueError,'安装并启用'):
                postprocessing.check_applicability('a','structure-symmetry',options)
            extension_sdk.enable('structure-symmetry',True)
            check=postprocessing.check_applicability('a','structure-symmetry',options)
            self.assertEqual(check['applicability_state'],'APPLICABLE')
            result=postprocessing.run('a','structure-symmetry',options=options)
        self.assertEqual(result['state'],'COMPLETED')
        self.assertEqual(result['scientific_validation'],'NOT_PERFORMED')
        self.assertEqual(result['summary'][0]['space_group_number'],227)
        output=json.loads(postprocessing.output_file('a',result['id'],'symmetry.json'))
        self.assertEqual([row['symprec_angstrom'] for row in output['results']],[0.001,0.01,0.1])
        self.assertEqual(output['input']['sha256'],hashlib.sha256(source).hexdigest())
        self.assertEqual(output['parameters']['angle_tolerance_degree'],5)
        self.assertIn('pymatgen',output['libraries'])
        self.assertIn('spglib',output['libraries'])
        self.assertEqual((self.attempt/'POSCAR').read_bytes(),source)

    def test_lcalcpol_legacy_adapter_still_runs(self):
        fixture=Path(__file__).parents[1]/'resources/extensions/lcalcpol-polarization-analysis/tests/fixtures'
        for name in ('POSCAR','OUTCAR'):
            (self.attempt/name).write_bytes((fixture/name).read_bytes())
        before={name:(self.attempt/name).read_bytes() for name in ('POSCAR','OUTCAR')}
        with patch.object(execution,'ssh',side_effect=self.local_ssh), patch.object(artifacts,'pull',side_effect=lambda _attempt,path: ((self.attempt/path).read_bytes(),path)):
            artifacts.refresh('a')
            record=postprocessing.run('a','lcalcpol-polarization-analysis')
        self.assertEqual(record['state'],'COMPLETED',record.get('stderr'))
        self.assertEqual(record['scientific_validation'],'NOT_PERFORMED')
        self.assertIn('e Ang',record['stdout'])
        self.assertEqual({name:(self.attempt/name).read_bytes() for name in before},before)

    def test_extension_output_contract_failure_keeps_separate_record(self):
        from types import SimpleNamespace
        original_run=subprocess.run
        source=(Path(__file__).parent/'fixtures/Si.POSCAR').read_bytes()
        (self.attempt/'POSCAR').write_bytes(source)
        extension_sdk.install('structure-symmetry');extension_sdk.enable('structure-symmetry',True)
        options={'structure_source':'POSCAR','angle_tolerance_degree':5}
        with patch.object(execution,'ssh',side_effect=self.local_ssh), patch.object(artifacts,'pull',side_effect=lambda _attempt,path: ((self.attempt/path).read_bytes(),path)):
            with patch.object(postprocessing,'check_applicability',return_value={'applicability_state':'APPLICABLE','source_name':'POSCAR','source_bytes':source,'options':options}):
                with patch.object(postprocessing.subprocess,'run',side_effect=lambda argv,**kw: original_run(argv,**kw) if argv[0]=='bash' else SimpleNamespace(returncode=0,stdout='{}',stderr='')):
                    record=postprocessing.run('a','structure-symmetry',options=options)
        self.assertEqual(record['state'],'FAILED')
        self.assertIn('缺少',record['error'])
        self.assertEqual(postprocessing.history('a')[0]['id'],record['id'])
        self.assertEqual((self.attempt/'POSCAR').read_bytes(),source)


if __name__=='__main__':unittest.main()
