#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calc_local_crystal_field.py 的合成数据自测。

构造已知晶场劈裂的八面体 FeO6 模型：
  t2g (dxy,dxz,dyz) = (-0.05, 0.00, 0.10) eV   -> delta_t2g(max) = 0.15
  eg  (dz2,dx2-y2)  = ( 1.20, 1.28) eV         -> delta_eg      = 0.08
  Delta_oct = 1.24 - 0.016667 = 1.223333 eV
将哈密顿量用随机正交矩阵转到"全局"架，验证脚本能否从畸变键方向
恢复局域架并重现全部标量结果（这些量在立方对称操作下不变）。
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import calc_local_crystal_field as cf

RNG = np.random.default_rng(42)

# w90 顺序 [dz2, dxz, dyz, dx2-y2, dxy]
H_LOC = np.diag([1.20, 0.00, 0.10, 1.28, -0.05])
DELTA_OCT = (1.20 + 1.28) / 2 - (-0.05 + 0.00 + 0.10) / 3
N_OCC = 20  # 18 个 O p (全占) + 2 个最低 d


def random_q():
    a = RNG.normal(size=(3, 3))
    q, r = np.linalg.qr(a)
    q *= np.sign(np.diag(r))
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1
    return q


def write_poscar(path, q0):
    """八面体键方向 = q0 的轴（结构与哈密顿量必须一起旋转）。"""
    lat = 12.0
    fe = np.array([lat / 2] * 3)
    lines = ["synthetic FeO6", "1.0",
             f"{lat} 0 0", f"0 {lat} 0", f"0 0 {lat}",
             "Fe O", "1 6", "Direct",
             " ".join(f"{x:.12f}" for x in fe / lat)]
    for k, d in [(0, 1.95), (0, 2.05), (1, 1.98),
                 (1, 2.02), (2, 1.96), (2, 2.10)]:
        sign = 1.0 if d in (1.95, 1.98, 1.96) else -1.0
        o = fe + sign * d * q0[:, k]
        lines.append(" ".join(f"{x:.12f}" for x in o / lat))
    path.write_text("\n".join(lines) + "\n")


def write_hr(path, h_d):
    nw = 23  # Fe 5d + 6 O x 3p
    h0 = np.zeros((nw, nw))
    h0[:5, :5] = h_d
    h0[5:, 5:] = -6.0 * np.eye(18)
    rs = [(0, 0, 0), (1, 0, 0), (-1, 0, 0)]
    with open(path, "w") as f:
        f.write("synthetic\n%d\n%d\n" % (nw, len(rs)))
        f.write("1 1 1\n")
        for r in rs:
            h = h0 if r == (0, 0, 0) else np.zeros((nw, nw))
            for m in range(nw):
                for n in range(nw):
                    f.write("%d %d %d %d %d %.12f %.12f\n"
                            % (r[0], r[1], r[2], m + 1, n + 1,
                               h[m, n].real, h[m, n].imag))


def main():
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        pos, hr = tmp / "POSCAR", tmp / "test_hr.dat"

        q0 = random_q()
        write_poscar(pos, q0)
        rd0 = cf.d_rotation(q0)
        h_glob = rd0.T @ H_LOC @ rd0        # H_local = Rd H_global Rd^T 的反变换
        write_hr(hr, h_glob)

        # 1. 旋转矩阵正交性
        assert np.allclose(rd0 @ rd0.T, np.eye(5), atol=1e-12)

        # 2. 函数级验证：局域架恢复 + 子空间劈裂
        blocks, nw = cf.read_hr(hr)
        assert nw == 23
        h0 = [h for r, _, h in blocks if tuple(r) == (0, 0, 0)][0].real
        symbols, counts, lattice, cart = cf.read_poscar(pos)
        o_carts = cart[1:]
        bonds, _, gap = cf.bond_vectors(cart[0], o_carts, lattice, 6)
        assert gap > 1.5, f"近邻间隔异常: {gap}"
        q, rms = cf.kabsch_frame(bonds, cf.OCT_REFS)
        rd = cf.d_rotation(q)
        h5 = rd @ h0[:5, :5] @ rd.T
        t2, eg = cf.T2_SET, cf.E_SET
        delta = np.trace(h5[np.ix_(eg, eg)]) / 2 - np.trace(h5[np.ix_(t2, t2)]) / 3
        assert abs(delta - DELTA_OCT) < 1e-6, f"Delta_oct = {delta}"
        assert np.linalg.norm(h5[np.ix_(t2, eg)]) < 1e-6, "跨子空间混合应为 0"
        assert np.allclose(np.linalg.eigvalsh(h5[np.ix_(t2, t2)]),
                           [-0.05, 0.00, 0.10], atol=1e-6)
        assert np.allclose(np.linalg.eigvalsh(h5[np.ix_(eg, eg)]),
                           [1.20, 1.28], atol=1e-6)

        # 3. 占据矩阵：18 个 O p 全占 + 2 个最低 d
        rho = cf.occ_matrix(blocks, (2, 2, 2), N_OCC, nw)
        assert abs(np.trace(rho).real - N_OCC) < 1e-8
        r5 = rd @ rho[:5, :5].real @ rd.T
        occs = np.linalg.eigvalsh(r5)
        assert np.allclose(occs, [0, 0, 0, 1, 1], atol=1e-8), occs

        # 4. CLI 冒烟测试
        res = subprocess.run(
            [sys.executable, str(HERE / "scripts" / "calc_local_crystal_field.py"), str(hr),
             "--poscar", str(pos), "--sites", "Fe:l=2,O:l=1", "--coord", "Fe:6",
             "--mp-grid", "2", "2", "2", "--occ", str(N_OCC), "--spin", "up"],
            capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert "Delta_oct = <eg>-<t2g> =  1.2233 eV" in res.stdout, res.stdout
        assert "P_t2g" in res.stdout
        print(res.stdout)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
