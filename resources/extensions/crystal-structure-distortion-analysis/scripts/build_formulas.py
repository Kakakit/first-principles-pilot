"""Development only: freeze static, font-independent SVG equations in the package."""
import io,json,re
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from metrics import METRICS

def build():
    matplotlib.rcParams['svg.fonttype']='path'
    matplotlib.rcParams['svg.hashsalt']='fpilot-polyhedra-formulas-v1'
    formulas={}
    for key,item in METRICS.items():
        figure=plt.figure(figsize=(7,0.8));figure.text(.02,.45,'$'+item['tex']+'$',fontsize=17,color='#18344b')
        stream=io.StringIO();figure.savefig(stream,format='svg',bbox_inches='tight',transparent=True);plt.close(figure)
        svg=stream.getvalue();svg=svg[svg.index('<svg'):]
        # Exclude Matplotlib timestamps; formula artifacts have no runtime dependency.
        svg=re.sub(r'<metadata>.*?</metadata>','',svg,flags=re.S)
        formulas[key]=svg
    (Path(__file__).resolve().parents[1]/'viewer/formulas.json').write_text(json.dumps(formulas,ensure_ascii=False))
    print('Static formula SVGs built')

if __name__=='__main__':build()
