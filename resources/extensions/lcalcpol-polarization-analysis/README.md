# 形式极化与双结果比较 · 2.2.0

## 本版使用方法

页面先显示量子扣除计算器和 x/y/z 极化（μC/cm²）、向量模长；来源、OUTCAR 原文、晶格、基底与换算过程折叠查看。同一 OUTCAR 电子/离子记录只显示一次，不再同时重列表格。新分析的通用回退表只保留原始/整数参考概览，不复制整套端点明细。

- n1/n2/n3 可输入 4.5、−0.5、0.125 等有限小数（绝对值不超过 1000000）。点击“应用并计算”立即计算 P′ = P_raw − ΣnᵢQᵢ；双结果先计算 B−A，再扣除共同基底中的量子倍数。电子与离子不分别约化。
- 自动选择默认“最近整数或半整数”（0.5 步长），也可选择“最近整数”（1 步长），再点击“搜索并应用”。逐个基底系数取最近格点，恰好等距使用 ties-to-even；不是对非正交晶格做最短笛卡尔向量优化。
- 半整数及任意小数均按计算器原样执行，标为“小数系数试算”；整数标为“整数量子分支候选”。半量子形式极化可以出现，但扣除半量子一般不是同一极化等价类的分支变换，不能自动解释为去掉数值误差或获得自发极化。参见 [VASP 极化说明](https://vasp.at/wiki/index.php/Berry_phases_and_finite_electric_fields)。
- 选择及搜索模式单独保存，兼容旧整数选择。JSON/CSV 导出实际系数、数值试算类型、原值、单位与计算结果；原始分析保持不变。旧的自动整数量子结果仍保存在完整数据中，不被新搜索替换。

在工作台扩展管理导入新版 ZIP/源码目录，然后对已有结果点击“更新结果展示”，无需重跑 VASP 或重新读取服务器文件。历史通用回退表保持原样，新展示优先使用冻结 JSON。

仅验证本扩展：`node --import tsx --test tests/polarizationViewer.test.ts`；`python -m unittest discover -s tests -p test_polarization_presentation.py`；`python -m unittest discover -s resources/extensions/lcalcpol-polarization-analysis/tests`。旧解析、晶格核对及 CLI 算法保持。

## 本轮验收

5 项 JS 数值检查、2 项新旧/双端点展示检查与 17 项原解析/CLI 回归通过。覆盖任意小数、负数、非正交重建、半整数/整数搜索、等距处理、诊断小数差及 CSV 标识。内嵌浏览器在独立本机夹具页面实测 4.5 应用、刷新恢复、两种搜索、CSV 生成与折叠展示；未声称真实材料的物理验证或原生 Safari 验收。

本机通过宿主标准导入接口安装启用 2.2.0，沿用现有依赖环境及已安装版本支持的三种运行位置；不修改宿主、不连接服务器、不重跑分析或 VASP。仅更新交互展示时使用原冻结数据。GitHub 增量只包含本扩展、独立测试、分发包与相应索引/交接，不带入其他扩展未发布工作。

## 历史算法及 CLI 说明

# LCALCPOL polarization analysis v0.3.0

This local-only extension migrates `archived-development-source/calc_lcalcpol.py` while preserving its POSCAR/OUTCAR parsing, constants, unit conversion, and lattice-coordinate branch algorithm. The migration initially added polarization magnitudes; v0.3.0 also corrects the comparison order to subtract first and reduce second. The original source file remains unmodified.

## Inputs, outputs, and dependencies

Run from a directory containing the matching files:

```bash
python \
  resources/extensions/lcalcpol-polarization-analysis/scripts/calc_lcalcpol.py
```

The parser uses `pymatgen` to read the POSCAR lattice and volume, `numpy` for vector, magnitude, and lattice-coordinate operations, and the fixed conversion `16.021766` from `(e Ang)/Angstrom^3` to `C/m^2`. It reads the last complete `p[ion]` and `p[elc]` records and accepts ordinary decimal and scientific notation (`E/e/D/d`).

The extension performs only `local_read`. It does not execute VASP, SSH, PBS, scheduler operations, or remote services. It does not impose an `NCORE` value on the calculation.

## Scientific boundary

Interpret the result only when the OUTCAR is from a successful, converged calculation and contains complete `p[ion]` and `p[elc]` records. A single-structure output is a **formal polarization branch**. Subtracting an integer polarization quantum in lattice coordinates only selects a representative of that equivalence class; it cannot be identified directly with spontaneous polarization or switchable ferroelectricity.

To obtain a genuine spontaneous polarization, compare two structures on the same branch along an adiabatic connection: a polar structure and its reference structure, and report the same-branch `Delta P`. The migrated branch reduction retains the source's `lattice_coordinates` and `reduce_branch` algorithm and does not determine that physical comparison. The reported `|P_red|` is only the magnitude of the selected formal representative; it is not spontaneous polarization and is not a uniquely minimal value.

## Explicit compare mode

Compare noSOC and SOC endpoints with explicit paths:

```bash
python \
  scripts/calc_lcalcpol.py compare \
  --nosoc-outcar /path/noSOC/OUTCAR --nosoc-poscar /path/noSOC/POSCAR \
  --soc-outcar /path/SOC/OUTCAR --soc-poscar /path/SOC/POSCAR
```

The JSON output includes each endpoint's raw/formal/branch-representative values, lattice polarization-quantum vectors, input SHA-256 hashes, and raw/candidate branch-reduced ΔP in `C/m^2`. A lattice mismatch rejects the comparison. Endpoint files alone cannot prove an adiabatic same-branch connection, so `same_branch_delta_p.status` is `INSUFFICIENT`; the extension does not auto-minimize branches or claim spontaneous polarization or experimental agreement.

### v0.3.0 comparison change

The comparison now subtracts raw total polarizations **SOC minus noSOC first**, then reduces that difference in the shared quantum basis. Quantum vectors are stored as rows: `reduce_branch(delta_raw, quantum_vectors)` solves `quantum_vectors.T @ coefficients = delta_raw`. The noSOC quantum basis is the reference after the existing absolute lattice-match check (default `1e-8 Angstrom`).

`comparison.delta_p_branch_reduced_candidate_c_m2` and `same_branch_delta_p.branch_reduced_candidate_c_m2` now mean this difference-first representative. Their v0.2.0 meaning (independently reduced endpoints subtracted without further reduction) is retained only as `comparison.endpoint_reduced_difference_c_m2`. Consumers can identify the change via top-level `schema_version: 0.3.0`.

`difference_reduction` records the coefficients, integer quanta removed, residual coefficients, removed polarization, quantum basis, and distance to each half-integer boundary. Both raw and reduced differences are available in `C/m^2` and `uC/m^2`, with reduced-vector magnitudes. Exact half-integers retain NumPy ties-to-even rounding and have two equivalent representatives; non-orthogonal componentwise reduction does not guarantee the shortest Cartesian vector. These arithmetic operations do not establish physical branch continuity.

Regression tests cover endpoint boundary crossing, independent integer branch shifts away from ties, non-orthogonal reconstruction, unit conversion, zero/half-quantum differences, invalid tolerances, and mismatched cells. Calculation-completion, insulating-gap, and structure-lineage checks remain external audits; this release does not claim to automate them.

## Known limitations

The parser accepts the known LCALCPOL labels and takes the last complete record. Alternative output layouts and physical convergence are not inferred. Branch reduction is a formal representative operation, not a proof of physical branch continuity.

## Tests and provenance

From this extension directory, run the tests with the existing project environment:

```bash
python tests/test_calc_lcalcpol.py
```

The tests cover POSCAR lattice/volume parsing, `p[ion]`/`p[elc]` parsing, `dipole_to_p`, `polarization_magnitude`, orthogonal and non-orthogonal lattice branch reduction, missing-output errors, and numeric/label checks for the CLI magnitude report. The original source remains untouched at `archived-development-source/calc_lcalcpol.py`.

## v2.1.0：完整过程与手动量子选择

在“绘图与后处理”选择“形式极化与双结果比较”。检查/预览会刷新来源，亦可点击“刷新来源文件清单”；变化时重新准备，不重试旧失败记录。双结果按选择顺序显示 A、B，主结果 B−A 先相减再约化。电子、离子仅显示原值和原始差，不能把端点分别约化的差当作主要物理结果。

展示包括 OUTCAR 原文/行号、晶格和体积、量子公式和向量、基底系数、整数选择、扣除向量、约化后 dipole 与极化。修改 n1/n2/n3 后点击“应用并重新计算”，或“恢复自动选择”；选择独立保存，原分析不变。JSON/CSV 导出本次计算过程。单结果允许调整总量，双结果只调整总端点差。主极化单位为 C/m²，另显示 μC/cm²（乘以 100）。

OUTCAR 与结构晶格/体积不一致会阻止新分析，检查会考虑 OUTCAR 打印精度。旧分析点击“更新结果展示”只读取旧冻结 JSON；缺失原文标为未保存，旧产物不自动接受新晶格审核。量子代表仍不证明绝缘同分支路径或自发极化。
