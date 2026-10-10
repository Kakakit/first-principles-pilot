# PyW90 pre dos：EIGENVAL 与 SOC 适配

## 原因与修复

PyW90 1.0.7 对共线 ISPIN=2 的 EIGENVAL 按 `2*NBANDS+1` 行拆 k 点块；实际每条能带行同时包含上下自旋，每块仍为 `NBANDS+1` 行。由此混入不同列数的行并触发 NumPy 的 inhomogeneous shape 错误。原 reshape 还会混排通道；窗口方法始终使用第零通道，选定轨道的 PDOS 积分也默认上自旋。

新增进程内适配器，复用现有 pymatgen Eigenval 解析器，先核对声明的 k 点、能带行、列数与顺序，再核对有限值和数组形状，防止旧版解析器将截断内容填为零。共线上下自旋显式选择对应能量与 PDOS，普通单通道及 SOC/非共线保持实际能带数量。DOS 和 EIGENVAL 通道不一致、单通道选择下自旋时明确停止。

SOC/非共线由实际 vasprun.xml 的 LSORBIT/LNONCOLLINEAR 判断。自动建议的空间轨道按默认 `(u,d)` spinor 投影计两个函数；这是投影数量，不将能带强制配对，也不假定 Kramers 简并。现有 `.eig` 模式仍使用原读取器。

分析范围先使用所选 VASP-Wannier OUTCAR 的 E_F 转为绝对能量，再传给 pre dos；返回值减同一个参考。单独 DOS attempt 的不同 E_F 不再暗中平移范围。源 XML/EIGENVAL 的分析前后完整哈希一致才返回建议。数值表保持单块输出；PyW90 报态数不足时不展示其占位外窗上界。空冻结窗明确说明分析已完成但没有候选。

所有改动只在临时分析进程生效；远端安装包、研究输入输出和既有 `.win` 保留。临时目录沿用原有清理流程，不提交 VASP/Wannier90 计算。页面显示实际通道、k 点、能带、此次投影建议数及说明。

## 验收

- 62 项相关后端检查通过（1 项既有跳过）：真实格式的双通道、单通道、SOC spinor 投影数、下自旋 PDOS、截断/错序/非有限值、原 `.eig` 读取、冻结窗通道、统一能量参考、输入变化拒绝及态数不足占位值排除。
- 5 项相关前端检查、TypeScript 检查和生产构建通过。
- 真实已完成 DOS：402 个 k 点、每通道 69 条能带，上下自旋两次完整分析成功；输入前后身份一致。
- 真实已完成 SOC 接口：216 个 k 点、144 条单通道能带，实际 spinor 标记及 54 个空间轨道→108 个默认投影函数通过；输入前后身份一致。
- 正式服务页面验证来源选择、分析结束、实际通道及“没有冻结窗候选”说明，不再返回原 NumPy 异常，也不展示态数不足的伪外窗上界。未采用候选或写入 `.win`。
- 真实验收范围为 −8～5 eV，未生成冻结窗候选；该结果仅验证解析和分析流程，不构成能窗推荐、局域化验收或物理结论。

本次改动限于按需分析适配与显示，相关域回归完成；未改派发、来源冻结或数据删除合同，不重复全量回归。私人运行回执和截图保留本机 `workspace-data/development-backups/pyw90-predos-20261010/`，不随源码上传。

## 官方依据

- [PyW90 源码](https://github.com/Cloudiiink/pyw90/blob/master/pyw90/lib/w90.py)
- [pymatgen Eigenval API](https://pymatgen.org/pymatgen.io.vasp.html)
- [Wannier90 spinor 投影与默认 u,d](https://wannier90.readthedocs.io/en/latest/user_guide/wannier90/projections/#spinor-projections)
