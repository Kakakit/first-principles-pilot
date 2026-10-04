# LCALCPOL 极化量子扣除：代码与数学过程

## 1. 这段代码到底在扣除什么

代码处理的是 VASP `OUTCAR` 中最后一组完整的 LCALCPOL 记录：

```text
p[ion]   —— 离子偶极矩
p[elc]   —— 电子偶极矩
```

两者的单位都是 `e Å`。代码先相加得到总偶极矩

$$
\mathbf p=\mathbf p_{\rm ion}+\mathbf p_{\rm elc}.
$$

这里的 `p` 不是极化强度；它是偶极矩。极化由总偶极矩除以晶胞体积得到：

$$
\mathbf P_{\rm raw}
=\frac{\mathbf p}{\Omega}\times 16.021766,
$$

其中：

- $\mathbf p$：`e Å`；
- $\Omega$：`Å³`；
- `16.021766`：代码中使用的单位换算因子；
- 输出单位：`C/m²`。

代码对应的是：

```python
total = p_ion + p_elc
raw = total / volume * EANG_TO_C_M2
```

所以，代码使用的原始极化是

$$
\mathbf P_{\rm raw}
=\frac{16.021766}{\Omega}
\left(\mathbf p_{\rm ion}+\mathbf p_{\rm elc}\right).
$$

---

## 2. 为什么极化可以扣除“极化量子”

周期性晶体中的 Berry-phase 极化不是唯一的单值，而是满足

$$
\mathbf P\equiv \mathbf P+\sum_{i=1}^{3}n_i\mathbf Q_i,
\qquad n_i\in\mathbb Z.
$$

对晶格矢量 $\mathbf a_i$，对应的极化量子为

$$
\mathbf Q_i=\frac{e\mathbf a_i}{\Omega}.
$$

在代码的单位中，若晶格矢量用 `Å`、体积用 `Å³`，则

$$
\mathbf Q_i
=\frac{16.021766}{\Omega}\mathbf a_i
\quad {\rm C/m^2}.
$$

因此允许的等价极化为

$$
\mathbf P'
=\mathbf P_{\rm raw}
-\sum_{i=1}^{3}n_i\mathbf Q_i.
$$

注意：这里扣除的不是任意的笛卡尔坐标分量，而是整数倍的完整晶格极化量子。

---

## 3. 代码的实际分支扣除算法

设 POSCAR 的晶格矩阵按“每一行一个晶格矢量”写成

$$
A=
\begin{pmatrix}
\mathbf a_1\\
\mathbf a_2\\
\mathbf a_3
\end{pmatrix}.
$$

代码先求总偶极矩在晶格矢量基底中的坐标：

$$
\mathbf p=c_1\mathbf a_1+c_2\mathbf a_2+c_3\mathbf a_3.
$$

因为代码把晶格矢量放在矩阵的行中，这等价于求解

$$
A^{\mathsf T}\mathbf c=\mathbf p^{\mathsf T},
\qquad
\mathbf c=
\begin{pmatrix}c_1\\c_2\\c_3\end{pmatrix}.
$$

对应代码：

```python
coeff = np.linalg.solve(lattice.T, p)
```

然后对每个晶格坐标分别取最近整数：

$$
n_i={\rm round}(c_i).
$$

对应代码：

```python
integer = np.round(coeff)
```

接着保留每个晶格坐标的小数部分：

$$
c_i'=c_i-n_i.
$$

对应代码：

```python
coeff_red = coeff - integer
```

最后用扣除后的晶格坐标重建偶极矩：

$$
\mathbf p_{\rm red}
=c_1'\mathbf a_1+c_2'\mathbf a_2+c_3'\mathbf a_3
=\mathbf p-\sum_{i=1}^{3}n_i\mathbf a_i.
$$

对应代码：

```python
p_red = (
    coeff_red[0] * lattice[0]
    + coeff_red[1] * lattice[1]
    + coeff_red[2] * lattice[2]
)
```

最后再把扣除后的偶极矩换算成极化：

$$
\mathbf P_{\rm red}
=\frac{16.021766}{\Omega}\mathbf p_{\rm red}
=\mathbf P_{\rm raw}-\sum_{i=1}^{3}n_i\mathbf Q_i.
$$

---

## 4. 一个与单元测试一致的例子

取一个正交晶格

$$
A={\rm diag}(2,3,4)\ {\rm Å},
$$

并令总偶极矩为

$$
\mathbf p=(2.7,-3.6,6.5)\ {\rm e\ Å}.
$$

晶格坐标为

$$
\mathbf c=(1.35,-1.20,1.625).
$$

代码取最近整数：

$$
\mathbf n=(1,-1,2).
$$

所以

$$
\mathbf c'=\mathbf c-\mathbf n
=(0.35,-0.20,-0.375),
$$

重建得到

$$
\mathbf p_{\rm red}
=(0.35)(2,0,0)
 +(-0.20)(0,3,0)
 +(-0.375)(0,0,4)
=(0.70,-0.60,-1.50)\ {\rm e\ Å}.
$$

也就是

$$
\mathbf p_{\rm red}
=\mathbf p-1\mathbf a_1-(-1)\mathbf a_2-2\mathbf a_3.
$$

这个例子说明：整数是在晶格基底 $(\mathbf a_1,\mathbf a_2,\mathbf a_3)$ 中扣除的。

对于非正交晶格，不能先对笛卡尔坐标的三个分量分别取整；必须先解晶格坐标。

---

## 5. 代码输出中的几个量分别代表什么

| 输出字段 | 数学含义 |
|---|---|
| `total_dipole_e_ang` | $\mathbf p_{\rm ion}+\mathbf p_{\rm elc}$ |
| `raw.polarization_c_m2` | $\mathbf P_{\rm raw}=16.021766\mathbf p/\Omega$ |
| `formal.lattice_coefficients` | $\mathbf c$，满足 $\mathbf p=\sum_i c_i\mathbf a_i$ |
| `branch_reduced.integer_quanta_removed` | $\mathbf n=(n_1,n_2,n_3)$ |
| `branch_reduced.dipole_e_ang` | $\mathbf p_{\rm red}=\mathbf p-\sum_i n_i\mathbf a_i$ |
| `branch_reduced.polarization_c_m2` | $\mathbf P_{\rm red}=\mathbf P_{\rm raw}-\sum_i n_i\mathbf Q_i$ |
| `polarization_quantum.vectors_c_m2` | 三个 $\mathbf Q_i=16.021766\mathbf a_i/\Omega$ |

---

## 6. v0.3.0：同晶胞先相减，再约化

首先检查两端晶格在绝对容差 $10^{-8}$ Å 内一致，使用 noSOC 晶格定义共同量子基底。不同晶胞仍拒绝直接比较。

$$
\Delta\mathbf P_{\rm raw}=\mathbf P_{\rm raw}^{\rm SOC}-\mathbf P_{\rm raw}^{\rm noSOC}.
$$

令 $B$ 的每一行是一条极化量子 $\mathbf Q_i$，求解

$$
B^{\mathsf T}\mathbf d=\Delta\mathbf P_{\rm raw},\qquad
\mathbf n=\operatorname{round}(\mathbf d),\qquad
\Delta\mathbf P_{\rm red}=B^{\mathsf T}(\mathbf d-\mathbf n).
$$

对应实际实现：

```python
delta_raw = P_SOC_raw - P_noSOC_raw
quantum = np.asarray(no_soc["polarization_quantum"]["vectors_c_m2"])
delta_branch, coeff, coeff_red, integer = reduce_branch(delta_raw, quantum)
```

当晶格及体积完全相同时，这等价于先对总偶极矩作差、在晶格基底中约化、最后乘以 $16.021766/\Omega$。代码直接在极化空间计算，避免把单位为 C/m² 的向量误放进单位为 Å 的晶格基底。

`comparison.delta_p_branch_reduced_candidate_c_m2` 现为上述 $\Delta\mathbf P_{\rm red}$。旧版“端点分别约化后直接相减”保留为 `endpoint_reduced_difference_c_m2`；两端单独约化值仍可用于诊断。`difference_reduction` 保存 $B,\mathbf d,\mathbf n,\mathbf d-\mathbf n$ 和实际扣除向量，便于逐项复算。

例如 $P_A=0.499Q$、$P_B=0.501Q$，旧诊断值为 $-0.998Q$，先相减再约化得到 $0.002Q$。远离半整数并列边界时，独立改变任一端点的整数分支不改变此代表；恰好半整数时，NumPy ties-to-even 可能选择相反边界，但二者仍量子等价。

`same_branch_delta_p.status` 仍为 `INSUFFICIENT`：约化差是端点差的一个代表，不能单凭端点确定绝缘连续演化所经过的整数分支。

---

## 7. 历史不同晶胞 CrFeS 结果（不是本次同晶胞数据）

此前得到的

$$
\Delta\mathbf P_{\rm raw}
=(2.790810,\ 2.789656,\ 0.002491)\ {\rm C/m^2}
$$

是两个端点的**原始形式极化差**。它可以包含若干个晶格极化量子，因此不能直接当作唯一的物理极化变化。

此前按约九个第三晶格矢量极化量子进行的对齐，是后续物理分析中的一个分支候选；它不是 `reduce_branch()` 自动通过路径识别出来的“真实扣除数”。当前代码内部只执行：

1. 求 $\mathbf c$；
2. 对每个 $c_i$ 独立执行 `np.round`；
3. 扣除对应的 $n_i\mathbf Q_i$。

因此，代码的 `P_red` 应理解为“整数晶格量子等价类中的一个规范化代表”，而不是自动确定的自发极化或 SOC 诱导极化。

---

## 8. 需要特别注意的数值与物理限制

1. `np.round` 是逐分量最近整数；恰好位于半整数时遵循 NumPy 的 ties-to-even 规则。
2. 该算法不是在笛卡尔空间中全局最小化 $|\mathbf P|$。对于非正交晶格，逐个晶格坐标取整不一定给出欧氏范数最小的等价代表。
3. `|P_red|` 只是所选代表的模长，不是唯一的“最小极化”。
4. 端点单独扣除量子不能证明同分支；自发极化需要参考结构—极性结构之间的绝缘绝热路径。
5. 因此，出现很大的 `ΔP_raw` 时，正确顺序是：先查看晶格坐标系数和极化量子，再做对称性与分支连续性检查，最后才讨论物理极化。

## 9. 对应源代码位置

- 偶极到极化换算：`scripts/calc_lcalcpol.py` 中 `dipole_to_p()`。
- 晶格坐标求解：`lattice_coordinates()`。
- 极化量子扣除：`reduce_branch()`。
- 极化量子输出：`polarization_quantum()`。
- noSOC/SOC 诊断比较：`compare_polarizations()`。
