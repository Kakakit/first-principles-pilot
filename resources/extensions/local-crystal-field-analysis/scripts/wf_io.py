"""Text readers adapted from wannier90io (MIT, Jamal I. Mustafa 2022).
See licenses/wannier90io-MIT.txt. Added index, finite and allocation validation.
"""
import numpy as np
MAX_COMPLEX=16_000_000

def dims(line,n):
    try:values=[int(v) for v in line.split()]
    except ValueError:raise ValueError('Wannier矩阵维度行无效') from None
    if len(values)!=n or min(values)<1 or np.prod(values,dtype=object)>MAX_COMPLEX:raise ValueError('Wannier矩阵维度无效或超出首版256 MiB单数组限制')
    return values

def numbers(line,n):
    try:a=np.array([float(v.replace('D','E').replace('d','e')) for v in line.split()])
    except ValueError:raise ValueError('Wannier矩阵数值行无效') from None
    if len(a)!=n or not np.isfinite(a).all():raise ValueError('Wannier矩阵截断或含非有限值')
    return a

def read_u(stream):
    stream.readline();nk,nw,nb=dims(stream.readline(),3);u=np.zeros((nk,nb,nw),complex);ks=[]
    for k in range(nk):
        if stream.readline().strip():raise ValueError('U矩阵块前需有空行')
        ks.append(numbers(stream.readline(),3))
        for j in range(nw):
            for i in range(nb):
                v=numbers(stream.readline(),2);u[k,i,j]=v[0]+1j*v[1]
    if any(line.strip() for line in stream):raise ValueError('U矩阵标称记录后有额外数据')
    return np.array(ks),u

def read_amn(stream):
    stream.readline();nb,nk,np_=dims(stream.readline(),3);a=np.zeros((nk,nb,np_),complex);seen=np.zeros(a.shape,bool)
    for _ in range(nk*nb*np_):
        parts=stream.readline().split()
        if len(parts)!=5:raise ValueError('amn矩阵行截断')
        try:i,j,k=[int(v)-1 for v in parts[:3]]
        except ValueError:raise ValueError('amn索引无效') from None
        if not(0<=i<nb and 0<=j<np_ and 0<=k<nk) or seen[k,i,j]:raise ValueError('amn索引重复或越界')
        seen[k,i,j]=True;v=numbers(' '.join(parts[3:]),2);a[k,i,j]=v[0]+1j*v[1]
    if any(line.strip() for line in stream):raise ValueError('amn标称记录后有额外数据')
    return a
