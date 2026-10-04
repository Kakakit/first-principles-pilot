#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calc_local_crystal_field.py
===========================

从 Wannier90 输出的 <seed>_hr.dat 提取过渡金属原位的局域 d 轨道哈密顿量

    H_loc,mn = <w_m|H|w_n>          (hr.dat 中 R = (0,0,0) 的原位 5x5 块)

并在由实际 M-O 键方向确定的局域配位坐标架中计算：

  1. 平均配位晶场劈裂（子空间重心差，迹公式）
       CrO6 八面体:  Delta_oct = <eg> - <t2g> = Tr(P_eg H)/2 - Tr(P_t2g H)/3
       FeO4 四面体:  Delta_tet = <t2> - <e>   = Tr(P_t2 H)/3 - Tr(P_e H)/2
  2. 子空间内部低对称劈裂（投影子块 H_A = P_A H P_A 的本征值差）
       delta_t2g / delta_eg （八面体）,  delta_e / delta_t2 （四面体）
  3. 跨子空间混合强度 ||P_A H P_B||_F，判断 3+2 分类是否可靠
  4. 完整 5 个本征值及其子空间权重（直接对角化方案的对照）
  5. （可选）Wannier 基占据矩阵 rho_mn = (1/Nk) Sum_k U f U^dagger，
     局域自然轨道占据数，以及 Fe e 子空间占据极化 P_e。

轨道顺序约定（Wannier90 v3.1 User Guide, Table 3.1）：
  l=2 -> [dz2, dxz, dyz, dx2-y2, dxy]     （注意：与 VASP OUTCAR 占据矩阵
                                           顺序 [dxy,dyz,dz2,dxz,dx2-y2] 不同）
  l=1 -> [pz, px, py]
局域配位架中的子空间（轴沿 M-O 键方向）：
  八面体: t2g = {dxy,dxz,dyz}, eg = {dz2,dx2-y2}
  四面体: t2  = {dxy,dxz,dyz}, e  = {dz2,dx2-y2}

前提：
  * 每个自旋通道单独运行（磁性体系），即对 wannier90.1_hr.dat /
    wannier90.2_hr.dat 分别调用本脚本；
  * .win 中已加 write_hr = true（User Guide 2.9.24；hr_plot 已废弃）；
  * WF 顺序 = .win projections 块中按 POSCAR 物种顺序展开，
    每个原子 5 个 d（l=2）或 3 个 p（l=1）。可用 write_xyz=true 输出的
    seedname_centres.xyz 核对。

用法示例：
  python3 calc_local_crystal_field.py wannier90.1_hr.dat \
      --poscar POSCAR_primitive --sites "Cr:l=2,Fe:l=2,O:l=1" \
      --coord "Cr:6,Fe:4" --mp-grid 8 7 7 --occ 60 --spin up
"""

from __future__ import annotations

import argparse
import itertools
import sys

import numpy as np

W90_D_ORDER = ["dz2", "dxz", "dyz", "dx2-y2", "dxy"]  # l=2, mr = 1..5 (UG T3.1)
W90_P_ORDER = ["pz", "px", "py"]                     # l=1

# 局域架中的子空间索引（相对 W90_D_ORDER = [dz2,dxz,dyz,dx2-y2,dxy]）
E_SET = [0, 3]       # dz2, dx2-y2
T2_SET = [4, 1, 2]   # dxy, dxz, dyz

# 理想参考键方向
OCT_REFS = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0],
                     [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)
TET_REFS = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1],
                     [-1, -1, 1]], dtype=float) / np.sqrt(3.0)


# ---------------------------------------------------------------- hr.dat
def read_hr(path):
    """解析 seedname_hr.dat（格式：Wannier90 v3.1 User Guide Sec. 8.19）。

    返回 HR[(iR)] = (R_vec, ndeg, num_wann x num_wann 复矩阵)，保持文件顺序。
    """
    with open(path) as f:
        f.readline()                                   # 日期行
        num_wann = int(f.readline())
        nrpts = int(f.readline())
        degen = []
        while len(degen) < nrpts:
            degen.extend(int(x) for x in f.readline().split())
        degen = degen[:nrpts]
        blocks = []
        current_r, current_h = None, None
        count = 0
        for _ in range(num_wann * num_wann * nrpts):
            p = f.readline().split()
            r = (int(p[0]), int(p[1]), int(p[2]))
            m, n = int(p[3]) - 1, int(p[4]) - 1
            if r != current_r:
                if current_h is not None:
                    blocks.append((np.array(current_r), current_h))
                current_r, current_h = r, np.zeros((num_wann, num_wann), complex)
                count += 1
            current_h[m, n] = float(p[5]) + 1j * float(p[6])
        blocks.append((np.array(current_r), current_h))
    if len(blocks) != nrpts:
        raise RuntimeError(f"R 点数目 {len(blocks)} 与 nrpts={nrpts} 不一致")
    hr = []
    for i, (r, h) in enumerate(blocks):
        hr.append((r, degen[i], h))
    return hr, num_wann


# ------------------------------------------------- 实 d 轨道旋转 (5x5 正交)
def _d_basis_matrices():
    """w90 顺序 [dz2,dxz,dyz,dx2-y2,dxy] 的无迹对称矩阵表示 p(r)=r^T B r。"""
    ms = []
    ms.append(np.diag([-1.0, -1.0, 2.0]))                 # dz2  ~ 2z2-x2-y2
    m = np.zeros((3, 3)); m[0, 2] = m[2, 0] = 1.0; ms.append(m)   # dxz
    m = np.zeros((3, 3)); m[1, 2] = m[2, 1] = 1.0; ms.append(m)   # dyz
    ms.append(np.diag([1.0, -1.0, 0.0]))                  # dx2-y2
    m = np.zeros((3, 3)); m[0, 1] = m[1, 0] = 1.0; ms.append(m)   # dxy
    return [m / np.sqrt(np.trace(m @ m)) for m in ms]     # Frobenius 归一


_D_BASIS = _d_basis_matrices()


def d_rotation(q):
    """q 的列 = 局域 x,y,z 轴在全局坐标中的方向。

    返回 5x5 正交矩阵 Rd，使 H_local = Rd @ H_global @ Rd.T
    （实 d 轨道，w90 顺序）。
    """
    rd = np.zeros((5, 5))
    for i in range(5):
        qi = q @ _D_BASIS[i] @ q.T
        for j in range(5):
            rd[i, j] = np.trace(qi @ _D_BASIS[j])
    return rd


# ------------------------------------------------------------- 局域坐标架
def kabsch_frame(bonds, refs):
    """找真旋转 q（列=局域轴），使局域架中的键方向 q^T b_i 最贴近 refs 的某排列。"""
    b = np.array(bonds)
    best = (np.inf, None, None)
    for perm in itertools.permutations(range(len(refs))):
        t = refs[list(perm)]
        c = b.T @ t
        v, _, wt = np.linalg.svd(c)
        d = np.sign(np.linalg.det(v @ wt))
        q = v @ np.diag([1.0, 1.0, d]) @ wt
        rms = np.sqrt(np.mean(np.sum((b @ q - t) ** 2, axis=1)))
        if rms < best[0]:
            best = (rms, q, perm)
    return best[1], best[0]


# ---------------------------------------------------------------- POSCAR
def read_poscar(path):
    lines = open(path).read().splitlines()
    scale = float(lines[1])
    lattice = np.array([[float(x) for x in lines[i].split()[:3]]
                        for i in range(2, 5)]) * scale
    symbols = lines[5].split()
    counts = [int(x) for x in lines[6].split()]
    idx = 7
    if lines[idx].strip().lower().startswith("s"):
        idx += 1
    mode = lines[idx].strip().lower()
    idx += 1
    natoms = sum(counts)
    coords = np.array([[float(x) for x in lines[idx + i].split()[:3]]
                       for i in range(natoms)])
    if mode.startswith("d"):
        cart = coords @ lattice
    else:
        cart = coords * scale
    return symbols, counts, lattice, cart


def bond_vectors(m_cart, o_carts, lattice, n_bond):
    """金属原子 m_cart 的最近 n_bond 个 O 的单位键向量（最小镜像约定）。"""
    trans = np.array(list(itertools.product([-1, 0, 1], repeat=3)))
    cand = []
    for oc in o_carts:
        for t in trans:
            v = oc + t @ lattice - m_cart
            cand.append((np.linalg.norm(v), v))
    cand.sort(key=lambda x: x[0])
    bonds = [cand[i] for i in range(n_bond)]
    gap = cand[n_bond][0] / cand[n_bond - 1][0]
    return [v / d for d, v in bonds], [d for d, _ in bonds], gap


# ------------------------------------------------------------- 占据矩阵
def occ_matrix(hr, mp_grid, n_occ, num_wann):
    n1, n2, n3 = mp_grid
    ks = [(i / n1, j / n2, k / n3)
          for i in range(n1) for j in range(n2) for k in range(n3)]
    rho = np.zeros((num_wann, num_wann), complex)
    for k in ks:
        hk = np.zeros((num_wann, num_wann), complex)
        for r, ndeg, h in hr:
            hk += h * np.exp(2j * np.pi * np.dot(k, r)) / ndeg
        hk = 0.5 * (hk + hk.conj().T)
        w, u = np.linalg.eigh(hk)
        f = np.zeros(num_wann)
        f[:n_occ] = 1.0
        # 用 einsum 而非 (u*f) @ u.conj().T：
        # numpy2 + macOS Accelerate 对退化本征矢的复数 matmul 有虚假告警
        rho += np.einsum("ij,j,kj->ik", u, f, u.conj())
    return rho / len(ks)


# ---------------------------------------------------------------- 分析
def subspace_report(h5, coord, label, out):
    """h5: 局域架 5x5 H_loc（实对称）。coord: 6=八面体, 4=四面体。"""
    if coord == 6:
        set_a, set_b = T2_SET, E_SET
        name_a, name_b = "t2g", "eg"
        delta_name = "Delta_oct = <eg>-<t2g>"
    else:
        set_a, set_b = E_SET, T2_SET
        name_a, name_b = "e", "t2"
        delta_name = "Delta_tet = <t2>-<e>"

    pa = np.zeros((5, 5)); pa[np.ix_(set_a, set_a)] = np.eye(len(set_a))
    pb = np.eye(5) - pa
    ea = np.trace(h5[np.ix_(set_a, set_a)]) / len(set_a)
    eb = np.trace(h5[np.ix_(set_b, set_b)]) / len(set_b)
    delta = eb - ea
    mixing = np.linalg.norm(h5[np.ix_(set_a, set_b)])

    out.append(f"  [{label}] {name_a} = {[W90_D_ORDER[i] for i in set_a]}, "
               f"{name_b} = {[W90_D_ORDER[i] for i in set_b]}")
    out.append(f"    <{name_a}> = {ea: .4f} eV   <{name_b}> = {eb: .4f} eV")
    out.append(f"    {delta_name} = {delta: .4f} eV")
    out.append(f"    ||P_{name_a} H P_{name_b}||_F = {mixing:.4f} eV   "
               f"(<< |{delta:.3f}| 时 3+2 分类可靠)")

    # 子空间内部劈裂
    for sset, sname in ((set_a, name_a), (set_b, name_b)):
        block = h5[np.ix_(sset, sset)]
        ev = np.linalg.eigvalsh(block)
        centroid = ev.mean()
        d_max = ev.max() - ev.min()
        d_rms = np.sqrt(np.mean((ev - centroid) ** 2))
        out.append(f"    {sname} 子块本征值 (eV): {np.array2string(ev, precision=4)}")
        out.append(f"      delta_{sname}(max) = {d_max:.4f} eV, "
                   f"delta_{sname}(RMS) = {d_rms:.4f} eV, "
                   f"相对重心: {np.array2string(ev - centroid, precision=4)}")

    # 直接对角化对照：完整 5 本征值 + 子空间权重
    ev, u = np.linalg.eigh(h5)
    wa = np.sum(u[set_a, :] ** 2, axis=0)
    out.append(f"    完整对角化本征值 (eV): {np.array2string(ev, precision=4)}")
    out.append(f"    对应 {name_a} 子空间权重: {np.array2string(wa, precision=3)}")
    return delta


def occ_report(rho5, coord, label, out):
    if coord == 6:
        set_lo, name_lo = T2_SET, "t2g"
    else:
        set_lo, name_lo = E_SET, "e"
    o = np.linalg.eigvalsh(rho5)[::-1]          # 降序：高占据在前
    out.append(f"    自然轨道占据数 (降序): {np.array2string(o, precision=4)}"
               f"   trace = {np.trace(rho5).real:.4f}")
    block = rho5[np.ix_(set_lo, set_lo)]
    ob = np.sort(np.linalg.eigvalsh(block))[::-1]
    pol = (ob[0] - ob[1]) / (ob[0] + ob[1]) if ob[0] + ob[1] > 1e-12 else 0.0
    out.append(f"    {name_lo} 子空间占据: {np.array2string(ob, precision=4)}, "
               f"极化 P_{name_lo} = (o1-o2)/(o1+o2) = {pol: .4f}")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(
        description="从 wannier90 hr.dat 计算局域 d 哈密顿量与晶场劈裂")
    ap.add_argument("hr", help="seedname_hr.dat（单个自旋通道）")
    ap.add_argument("--poscar", required=True, help="wannier 所用原胞 POSCAR")
    ap.add_argument("--sites", required=True,
                    help='投影与物种对应，如 "Cr:l=2,Fe:l=2,O:l=1"，'
                         "顺序须与 POSCAR 物种行一致")
    ap.add_argument("--coord", default="Cr:6,Fe:4",
                    help='配位数，如 "Cr:6,Fe:4"（6=八面体, 4=四面体）')
    ap.add_argument("--global-frame", action="store_true",
                    help="不做键方向拟合，直接以全局轴为局域架（测试/理想结构用）")
    ap.add_argument("--mp-grid", type=int, nargs=3, default=None,
                    help="ab initio k 网格（.win 的 mp_grid），用于占据矩阵")
    ap.add_argument("--occ", type=int, default=None,
                    help="该自旋通道每 k 点占据带数（绝缘体整数占据）")
    ap.add_argument("--spin", default="", help="自旋标签（仅用于报告标题）")
    args = ap.parse_args()

    np.set_printoptions(precision=4, suppress=True, linewidth=140)

    sites = []
    for item in args.sites.split(","):
        el, ls = item.split(":")
        sites.append((el, int(ls.split("=")[1])))
    coord_map = {}
    for item in args.coord.split(","):
        el, c = item.split(":")
        coord_map[el] = int(c)

    symbols, counts, lattice, cart = read_poscar(args.poscar)
    if [s for s, _ in sites] != symbols:
        raise SystemExit(f"--sites 物种顺序 {sites} 与 POSCAR {symbols} 不一致")

    hr, num_wann = read_hr(args.hr)

    # WF 索引映射：按 sites 顺序，每个原子 per_atom 个 WF
    per_atom = {2: 5, 1: 3}
    wf_map, offset = {}, 0
    for (el, l), n_at in zip(sites, counts):
        nw = per_atom[l]
        wf_map[el] = [(offset + i * nw, nw) for i in range(n_at)]
        offset += n_at * nw
    if offset != num_wann:
        raise SystemExit(f"WF 总数不匹配：映射 {offset} vs hr.dat num_wann={num_wann}，"
                         "请检查 --sites 顺序与投影是否一致")

    h0 = None
    for r, ndeg, h in hr:
        if tuple(r) == (0, 0, 0):
            h0 = h / ndeg
    if h0 is None:
        raise SystemExit("hr.dat 中未找到 R=(0,0,0) 块")

    # 占据矩阵（可选）
    rho_full = None
    if args.occ is not None:
        if args.mp_grid is None:
            raise SystemExit("计算占据矩阵需要 --mp-grid n1 n2 n3")
        rho_full = occ_matrix(hr, tuple(args.mp_grid), args.occ, num_wann)

    title = f"== {args.hr}  spin={args.spin or '?'} =="
    out = [title, f"轨道顺序 (w90 l=2): {W90_D_ORDER}"]

    species_of_atom = []
    for si, el in enumerate(symbols):
        species_of_atom += [el] * counts[si]
    o_carts = [cart[i] for i in range(len(cart)) if species_of_atom[i] == "O"]
    atom_offset = {}
    acc = 0
    for el, n_at in zip(symbols, counts):
        atom_offset[el] = acc
        acc += n_at

    for (el, l), n_at in zip(sites, counts):
        if l != 2:
            continue
        cn = coord_map.get(el)
        if cn is None:
            continue
        refs = OCT_REFS if cn == 6 else TET_REFS
        for ia, (i0, nw) in enumerate(wf_map[el]):
            gi = atom_offset[el] + ia
            label = f"{el}{ia + 1} (WF {i0 + 1}-{i0 + nw})"
            h5 = 0.5 * (h0[i0:i0 + nw, i0:i0 + nw]
                        + h0[i0:i0 + nw, i0:i0 + nw].conj().T).real
            if args.global_frame:
                q, rms = np.eye(3), 0.0
            else:
                bonds, blen, gap = bond_vectors(cart[int(gi)], o_carts,
                                                lattice, cn)
                q, rms = kabsch_frame(bonds, refs)
                out.append(f"  [{label}] 键长 (A): "
                           f"{np.array2string(np.array(blen), precision=3)}"
                           f"  第{cn}与次近邻比 = {gap:.2f}")
                out.append(f"    局域架拟合 RMSD = {rms:.4f} "
                           f"(0=理想配位多面体取向)")
            rd = d_rotation(q)
            h5l = rd @ h5 @ rd.T
            subspace_report(h5l, cn, label, out)
            if rho_full is not None:
                r5 = 0.5 * (rho_full[i0:i0 + nw, i0:i0 + nw]
                            + rho_full[i0:i0 + nw, i0:i0 + nw].conj().T).real
                r5l = rd @ r5 @ rd.T
                occ_report(r5l, cn, label, out)
            out.append("")

    print("\n".join(out))


if __name__ == "__main__":
    main()
