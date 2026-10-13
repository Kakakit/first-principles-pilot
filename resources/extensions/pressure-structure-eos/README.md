# 压力结构与 EOS 分析 · 1.0.1

独立 `fpilot.extension.v2` 包；支持 1–64 个 scf/relax 结果，不运行 VASP、不提交任务、不修改研究来源。依赖已登记 Python >=3.9、numpy、scipy、pymatgen。拟合调用 pymatgen EOS（MIT），离线展示采用 ECharts/zrender（包内附许可）；源码目录的 Node 构建只供开发，安装包无需 Node。

## 使用

在项目“结果与后处理”打开一个已完成任务，选择本扩展，通过“批量选择已有任务”加入扫描点。刷新来源文件清单，检查后运行。大 OUTCAR 选择来源服务器 SSH 和该服务器已登记 Python；所有来源必须同主机。本机路径只接受现有小文件回传限额。

默认展示 **a/b/c–V**，三条曲线在同一图中，可点击图例分别隐藏。提供 E–V、P–V、V–P、a/b/c–P、c/a–P、最大力–V/–P。任意已有数值列可选 X，Y 下拉菜单支持多选，已选项显示 ✓；不同单位项及全部来源未提供项置灰并注明原因，清空选择后可换单位类别。多条 Y 必须共享单位；不同单位分别绘图。图例纵向排列，窄屏放在图下。散点/连线、范围、压力 GPa/kbar、每晶胞/原子/化学式单元可调整。

表格的“批量选择”列可勾选多点，输入“批量组名”后一次应用，支持全选和清空。批量选择与拟合勾选互不影响。修改组名或排除点后，点击“应用分组并重新拟合”：保存展示选择，用原分析 JSON 重新计算并生成新展示版本。不会再次读取远端 OUTCAR。旧分析和旧展示保留。仅改绘图设置可点“保存展示设置”。PNG 为当前图；CSV 为当前归一化的数据表；JSON 含原值、证据、当前选择、拟合参数/曲线/残差和显示单位。拟合选择变动未应用时，CSV/JSON 导出会要求先重新拟合，防止导出过期结果。

## 取值与物理边界

- 流式读取 OUTCAR，POSITION/TOTAL-FORCE 块作为离子步边界，后续最终能量关联该步；保留最后完整步。末尾截断、未记录该步 EDIFF 或未结束时，原值可看，但不用于拟合。
- 保存实际计算晶格、能量/压力/应力/力原文和行号。体积用于计算的是晶格行列式，同时保存 OUTCAR 打印体积及其舍入精度。CONTCAR 核对最终晶格；POSCAR 仅为初始结构。不自动转换常规晶胞，所以原胞 a/b/c 可能不同于文献晶格参数。
- 能量口径为 `energy(sigma->0)`（默认）、`energy without entropy`、`free energy TOTEN`；保留存在的 `enthalpy is TOTEN`，但不用焓拟合 E–V。缺字段为 null，不代零、不自动切换能量。
- 输入 PSTRESS、OUTCAR external pressure、VASP 六应力原符号和 EOS 求导压力独立列出。1 kbar = 0.1 GPa；1 eV/Å³ = 160.2176634 GPa。非零/未确认 PSTRESS 阻止能量 EOS，防止压力项混入 E(V)。无目标压力时，不凭空算压力残差。
- 最大力/RMS 是原子力向量模长诊断，单位 eV/Å。力阈值不代替电子/离子停止证据。静态计算没有离子弛豫要求；relax 需 required accuracy 停止标记。
- 按组成、赝势、ENCUT、k 点、展宽、U/SOC、初始 MAGMOM 等设置分组。跨组强行改成同名会阻止混合拟合；磁态/结构相也应手动拆组。初始 MAGMOM 不同并不证明最终磁态不同，本版采取保守分组。
- 三阶 Birch–Murnaghan/Vinet/Murnaghan 每组至少 6 个不同体积点，最低采样能量两侧均需点；拟合 V0 必须在范围内、B0>0。先按每原子体积和能量拟合，显示按所选口径换算，B0 不受原子数归一化影响。推荐检查 9–15 个点及模型敏感性。
- B0 是 EOS 体模量，不等于完整弹性张量；不计算焓差、有限温自由能、声子稳定性或相变机制。程序完成、数值门禁通过、拟合成功和物理验证分别报告。

## 输出协议

`pressure-eos.json` (`fpilot.pressure-eos.v1`) 保存 points（来源/步骤/结构/values/evidence/settings_signature/validation/fit_blockers）、fields（标签/单位/类型）、options、fits、conversion。`result.json` 保持通用 v2。展示入口 `scripts/presentation.py render --request ...` 仅读取冻结 JSON，并接受通用展示请求 `view_state` 快照。

## 验证

开发环境运行包内 `tests/test_core.py`，覆盖完整/截断离子步配对、缺字段、打印精度、力与压力单位、PSTRESS 门禁、模型恢复/求导、排除/分组与冻结展示重算。宿主测试见 `tests/test_extension_presentations.py`，页面模型测试见 `tests/pressureEosViewer.test.ts`。`fixtures` 是明确标注的合成解析样例，不是研究结果。
