import pickle
from pathlib import Path
class SpinIO:
    @classmethod
    def load_pickle(cls,path='TB2J_results',fname='TB2J.pickle'):
        obj=cls();obj.__dict__.update(pickle.loads((Path(path)/fname).read_bytes()));return obj
