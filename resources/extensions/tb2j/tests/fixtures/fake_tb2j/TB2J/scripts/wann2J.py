"""Simulates native CLI and eV SpinIO output; no numerical TB2J calculation."""
import argparse
import os
import pickle
from pathlib import Path
import numpy as np
from ase.io import read

def main():
 p=argparse.ArgumentParser();p.add_argument('--spinor',action='store_true');p.add_argument('--orb_decomposition',action='store_true')
 for key in ['path','posfile','efermi','groupby','prefix_spinor','prefix_up','prefix_down','emin','emax','nz','np','rcut','description','output_path']:p.add_argument('--'+key)
 for key in ['kmesh','elements','index_magnetic_atoms']:p.add_argument('--'+key,nargs='+')
 a=p.parse_args();output=Path(a.output_path);output.mkdir(exist_ok=True)
 (output/'exchange.out').write_text('SIMULATION: native-format reader test only\n')
 if os.environ.get('TB2J_TEST_FORMAT')=='unknown':return
 atoms=read(Path(a.path)/a.posfile);key=((1,0,0),0,1)
 values={'atoms':atoms,'colinear':not a.spinor,'ind_atoms':{0:0,1:2},'index_spin':[0,-1,1],'exchange_Jdict':{key:0.0012345678901234567},'distance_dict':{key:([8,0,0],8.0)},'dmi_ddict':{key:np.array([0.0001,0.0002,0.0003])} if a.spinor else None,'Jani_dict':{key:np.eye(3)*0.00001} if a.spinor else None,'Jiso_orb':{key:np.array([[0.0005,0.0001],[0.0002,0.0004]])} if a.orb_decomposition else None,'DMI_orb':{key:np.ones((3,2,2))*0.00001} if a.spinor and a.orb_decomposition else None,'Jani_orb':{key:np.ones((3,3,2,2))*0.000001} if a.spinor and a.orb_decomposition else None}
 if os.environ.get('TB2J_TEST_FORMAT')=='missing-soc':values['dmi_ddict']=None
 (output/'TB2J.pickle').write_bytes(pickle.dumps(values));(output/'assigned_basis.txt').write_text('SIMULATION: labels are not physical orbital assignments\n');print('SIMULATION finished')
if __name__=='__main__':main()
