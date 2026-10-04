import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from backend import execution as ex, result_overview as overview, scan_results


class ResultOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(ex,'ROOT',self.root).start()
        self.key='ab'*32
        self.record={'id':'cd'*16,'project_id':'p','bundle_id':self.key,'state':'COMPLETED','kind':'vasp',
                     'evidence':{'vasp_timing':True,'ediff_reached':True,'scientific_validation':'NOT_PERFORMED'}}
        patch.object(ex,'get',side_effect=lambda key:self.record).start()
        patch.object(ex,'ssh',side_effect=AssertionError('summary must never contact a cluster')).start()
        (self.root/'inputs').mkdir()
        with zipfile.ZipFile(self.root/'inputs'/(self.key+'.zip'),'w') as z:
            z.writestr('manifest.json',json.dumps({'stage':'scf','species':['Si'],'geometry_sha256':'geometry',
                         'files':{'POTCAR':{'sha256':'potcar'},'INCAR':{'sha256':'incar'}}}))
            z.writestr('INCAR','GGA = PE\nENCUT = 400; ISPIN = 1 # comment\n')
            z.writestr('KPOINTS','mesh\n0\nGamma\n4 4 4\n0 0 0\n')
            z.writestr('POTCAR',b'large data are deliberately not read')

    def test_page_open_is_local_bounded_and_keeps_evidence_separate(self):
        read=zipfile.ZipFile.read;names=[]
        def tracked(archive,name,*a,**kw):names.append(name);return read(archive,name,*a,**kw)
        with patch.object(zipfile.ZipFile,'read',tracked),patch.object(ex,'bundle',side_effect=AssertionError('no full bundle hashing')):
            data=overview.summary('p',self.record['id'])
        self.assertEqual(names,['manifest.json','INCAR','KPOINTS'])
        self.assertFalse(data['remote_checked']);self.assertIsNone(data['metrics'])
        self.assertEqual(data['input']['parameters']['ENCUT'],'400')
        self.assertEqual(data['evidence']['scientific_validation'],'NOT_PERFORMED')

    def test_actual_prepared_input_and_exact_historical_parent(self):
        self.record['final_incar']='GGA = PE\nENCUT = 500\nSAXIS = 1 1 0\n'
        self.record['chain_source_snapshot']={'source_attempt_id':'older-attempt','source_path':'old/path',
                                             'files':{'CHGCAR':{'size_bytes':20}},'reason':'明确选用历史结果'}
        data=overview.summary('p',self.record['id'])
        self.assertEqual(data['input']['parameters']['ENCUT'],'500')
        self.assertEqual(data['input']['parameter_origin'],'实际准备 INCAR')
        self.assertEqual(data['sources'][0]['attempt_id'],'older-attempt')
        self.assertEqual(data['sources'][0]['files'],['CHGCAR'])

    def test_metrics_reuse_and_invalidation_without_remote_refresh(self):
        metric={'value':-10,'unit':'eV/cell','source':{'file':'OUTCAR','sha256':'source'}}
        overview.cache(self.record,{'metrics':{'energy':metric},'error':None})
        self.assertEqual(overview.summary('p',self.record['id'])['metrics']['energy'],metric)
        self.record['notes']='notes do not alter results'
        self.assertEqual(overview.summary('p',self.record['id'])['cache_status'],'available')
        self.record['config']={'alias':'changed-host'}
        self.assertEqual(overview.summary('p',self.record['id'])['cache_status'],'not-read')
        overview.cache(self.record,{'metrics':{'energy':metric},'error':None})
        self.record['evidence']['ediff_reached']=False
        self.assertIsNone(overview.summary('p',self.record['id'])['metrics'])
        self.assertIsNone(overview.summary('p',self.record['id'])['metrics_checked_at'])

    def test_other_project_and_unreadable_bundle_do_not_fallback(self):
        with self.assertRaisesRegex(ValueError,'不属于'):overview.summary('another',self.record['id'])
        (self.root/'inputs'/(self.key+'.zip')).write_bytes(b'broken')
        data=overview.summary('p',self.record['id'])
        self.assertIn('不可读',data['input']['warning'])
        self.assertIsNone(data['metrics'])

    def test_wannier_uses_frozen_win_not_current_draft(self):
        self.record.update(kind='wannier90',parent_attempt_id='exact-parent')
        (self.root/'wannier-bundles').mkdir()
        (self.root/'wannier-bundles'/(self.key+'.json')).write_text(json.dumps({'win':'num_wann = 8\nspinors = true\n',
             'parent_attempt_id':'exact-parent','seed':'wannier90','win_sha256':'win'}))
        data=overview.summary('p',self.record['id'])
        self.assertEqual(data['input']['parameters']['NUM_WANN'],'8')
        self.assertEqual(data['input']['parameter_origin'],'冻结 .win')
        self.assertEqual(data['sources'][0]['attempt_id'],'exact-parent')

    def test_comparison_caches_and_requires_boolean_program_evidence(self):
        manifest={'stage':'scf','geometry_sha256':'geometry'}
        with patch.object(ex,'bundle',return_value=(manifest,{'INCAR':b'GGA = PE\n','POTCAR':b'potential'})),patch.object(scan_results,'_extract',return_value={'energy':None}) as extract:
            scan_results.compare('p',[self.record['id']])
            self.assertEqual(extract.call_count,1)
            self.assertEqual(overview.summary('p',self.record['id'])['metrics'],{'energy':None})
            self.record['evidence']['vasp_timing']='not checked'
            rows=scan_results.compare('p',[self.record['id']])['rows']
            self.assertFalse(rows[0]['program_done']);self.assertEqual(extract.call_count,1)
