import sys,unittest
from pathlib import Path
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from cli import config
class RowTypesTests(unittest.TestCase):
 def test_rows_match_legacy_geometry_configuration(self):
  self.assertEqual(config({'options':{'polyhedron_types':['CrO6','FeO4']}}),config({'options':{'polyhedra':'CrO6,FeO4'}}))
 def test_each_row_one_type_and_conflicts(self):
  for entries in [['CrO6, FeO4'],['CrO6，FeO4'],[''],[],['CrO6','CrO4'],['FeO4','FeO4'],['XxO4']]:
   with self.assertRaises(ValueError):config({'options':{'polyhedron_types':entries}})
if __name__=='__main__':unittest.main()
