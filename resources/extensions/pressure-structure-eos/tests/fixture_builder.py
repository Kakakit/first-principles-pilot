"""Synthetic OUTCAR snippets for parser contracts, not VASP research results."""
import math

def outcar(volume=40.0,energy=-5.0,pressure=0.0,pstress=0.0,step=1,ending=True,force=0.001):
 a,b=3.0,3.2;c=volume/(a*b)
 return f''' TITEL = PAW_PBE Ce 23Dec2003
 ions per type = 1
 NIONS = 1
 ENCUT = 600.0 eV
 PREC = Accurate
 EDIFF = 1E-7
 EDIFFG = -0.01
 ISMEAR = 0
 SIGMA = 0.05
 ISPIN = 2
 NKPTS = 64
 PSTRESS = {pstress}
 NSW = 100
 ISIF = 4
 Iteration {step}( 1)
 aborting loop because EDIFF is reached
 volume of cell : {volume:.8f}
 direct lattice vectors                 reciprocal lattice vectors
 {a:.9f} 0.0 0.0 0 0 0
 0.0 {b:.9f} 0.0 0 0 0
 0.0 0.0 {c:.9f} 0 0 0
 in kB {pressure} {pressure} {pressure} 0 0 0
 external pressure = {pressure} kB Pullay stress = 0 kB
 POSITION                                       TOTAL-FORCE (eV/Angst)
 -----------------------------------------------------------------------------------
 0.0 0.0 0.0 {force} 0.0 0.0
 -----------------------------------------------------------------------------------
 free  energy   TOTEN = {energy-0.001:.12f} eV
 energy  without entropy= {energy-0.0005:.12f} energy(sigma->0) = {energy:.12f}
 reached required accuracy - stopping structural energy minimisation
'''+(' General timing and accounting informations for this job:\n' if ending else '')
