"""Package-local shorthand parser. Each modern array entry is one polyhedron."""
import re
ELEMENTS=set('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og'.split())

def parse_types(options):
    modern=options.get('polyhedron_types')
    if modern==[] and options.get('polyhedra'):modern=None
    if modern is not None:
        if not isinstance(modern,list):raise ValueError('多面体类型须为列表，每个输入框填写一种')
        if len(modern)>16:raise ValueError('最多填写16种多面体')
        tokens=modern
    elif options.get('polyhedra'):
        tokens=re.split(r'[,，;；\s]+',options['polyhedra'].strip())
        tokens=[t for t in tokens if t]
    else:return {}
    specs={};seen=set()
    for i,token in enumerate(tokens):
        if not isinstance(token,str):raise ValueError(f'第{i+1}项须填写一种多面体，例如 CrO6')
        token=token.strip();match=re.fullmatch(r'([A-Z][a-z]?)([A-Z][a-z]?)([46])',token)
        if not match:raise ValueError(f'第{i+1}项只填写一种，例如 CrO6；不要在同一输入框填写逗号或多种类型')
        center,ligand,cn=match.groups()
        if center not in ELEMENTS or ligand not in ELEMENTS:raise ValueError('多面体含无效元素：'+token)
        if center==ligand:raise ValueError('中心元素与配体须不同：'+token)
        value={'ligand':ligand,'coordination':int(cn)}
        if center in specs and specs[center]!=value:raise ValueError('同一中心元素不能指定冲突配位：'+center)
        if modern is not None and token in seen:raise ValueError('多面体类型重复：'+token)
        seen.add(token);specs[center]=value
    return specs

def label(center,spec):
    return center+spec['ligand']+str(spec['coordination'])
