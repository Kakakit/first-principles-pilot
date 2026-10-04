import csv
import json
import tempfile
import unittest
from pathlib import Path

from backend import result_datasets as results


class ResultDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.old_root,self.old_research=results.ROOT,results.RESEARCH_ROOT
        results.ROOT=self.root/'store';results.RESEARCH_ROOT=self.root.resolve()
        self.dataset=self.root/'dataset';self.dataset.mkdir()
        for name in ('bands_spin_up.csv','pdos_spin_up.csv'):
            pass
        with (self.dataset/'bands_spin_up.csv').open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['k_index','path_x_A^-1','band','energy_eV'])
            writer.writerows([[1,0,1,-1],[2,1,1,-0.5],[1,0,2,1],[2,1,2,1.5]])
        with (self.dataset/'pdos_spin_up.csv').open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['energy_eV','total']);writer.writerows([[-1,0],[0,0],[1,1]])
        self.manifest={'schema':results.DATASET_SCHEMA,'extension':'electronic-structure-characterization',
            'energy_reference':{'kind':'explicit','value_eV':5.0},
            'inputs':[{'role':'PROCAR','path':str(self.root/'PROCAR'),'sha256':'a'*64}],
            'band_path':{'nkpoints':2,'nbands':2,'ticks_A^-1':[0.0,1.0],'labels':['G','X']},
            'spin_order':['spin_up'],'spins':{'spin_up':{'label':'Spin up','band_table':'bands_spin_up.csv','pdos_table':'pdos_spin_up.csv'}}}
        self.manifest_path=self.dataset/'manifest.json';self.write(self.manifest_path,self.manifest)
        self.characterization={'schema':results.CHARACTERIZATION_SCHEMA,'dataset_manifest':str(self.manifest_path),
            'energy_reference':{'kind':'explicit','value_eV':5.0},'spins':{'spin_up':{
                'sampled_path_band_edges':{'classification':'gapped_on_sampled_path','vbm_relative_eV':-0.5,'cbm_relative_eV':1.0,'sampled_path_indirect_gap_eV':1.5},
                'density_of_states':{'total_dos_at_reference_states_per_eV':0.0}}},
            'magnetization':{'cell_magnetization_muB':2.0},'scientific_boundaries':['sampled path only']}
        self.write(self.dataset/'characterization.json',self.characterization)

    def tearDown(self):
        results.ROOT,results.RESEARCH_ROOT=self.old_root,self.old_research;self.temp.cleanup()

    @staticmethod
    def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')

    def test_import_checks_numbers_units_reference_and_is_idempotent(self):
        first=results.import_dataset(str(self.manifest_path));second=results.import_dataset(str(self.manifest_path))
        self.assertFalse(first['duplicate']);self.assertTrue(second['duplicate']);self.assertEqual(first['id'],second['id'])
        self.assertEqual(first['units']['energy'],'eV');self.assertEqual(first['units']['dos'],'states/eV')
        self.assertEqual(first['spins']['spin_up']['sampled_path_band_edges']['sampled_path_indirect_gap_eV'],1.5)
        self.assertEqual(len(first['spins']['spin_up']['band_curves']),2)
        self.assertEqual(results.get(first['id'])['source']['producer_input_hash_verification'],'RECORDED_NOT_REHASHED')
        self.assertEqual(len(results.list_records()),1)

    def test_mismatched_reference_or_curve_summary_is_rejected_without_record(self):
        changed=dict(self.characterization);changed['energy_reference']={'kind':'explicit','value_eV':4.0}
        self.write(self.dataset/'characterization.json',changed)
        with self.assertRaisesRegex(results.ResultDatasetError,'能量参考不一致'):
            results.import_dataset(str(self.manifest_path))
        self.assertEqual(results.list_records(),[])
        self.write(self.dataset/'characterization.json',self.characterization)
        changed=json.loads(json.dumps(self.characterization));changed['spins']['spin_up']['sampled_path_band_edges']['vbm_relative_eV']=-0.4
        self.write(self.dataset/'characterization.json',changed)
        with self.assertRaisesRegex(results.ResultDatasetError,'数值不一致'):
            results.import_dataset(str(self.manifest_path))

    def test_paths_outside_research_root_and_table_traversal_are_rejected(self):
        outside=Path('/private/tmp/outside-result-manifest.json')
        with self.assertRaisesRegex(results.ResultDatasetError,'科研工作区'):
            results.import_dataset(str(outside))
        changed=json.loads(json.dumps(self.manifest));changed['spins']['spin_up']['band_table']='../outside.csv'
        self.write(self.manifest_path,changed)
        with self.assertRaisesRegex(results.ResultDatasetError,'数据集目录内'):
            results.import_dataset(str(self.manifest_path))


if __name__=='__main__':unittest.main()
