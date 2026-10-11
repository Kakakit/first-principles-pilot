# Wannier90 单参数窗口测试

在画布连接 **Wannier90 → 参数迭代**。先在 Wannier 节点选择实际 VASP 接口、服务器和通道，载入 `.win` 与能量参考。fatband／PDOS、PyW90 辅助在参数之前；填写参数后在底部应用到 `.win`。参数迭代直接读取该通道的当前项目草稿，无需先手动冻结基础输入。

## 参数草稿与删除

四个窗口填写 `E−E_F`（eV），最终 `.win` 保存绝对能量；审核、冻结和准备重新解析最终原文。清空已有字段表示删除该 tag，布尔与 restart 可选择“删除／使用程序默认”。应用前显示新增、修改、删除；原文编辑后先解析再同步表格。未应用或解析失败时不能冻结。非窗口参数的删除不要求额外读取费米能。

默认展开窗口、restart、num_iter、dis_num_iter；高级收敛与插值、算法与日志折叠，输出文件单独分组。已有高级值保留。VASP 生成的晶胞、原子、维度、k 点和自旋信息受保护，其余内容和注释保留。`restart` 仍需要相同模型的检查点，新接口局域化应删除该项；已有模型保留结果页 `restart=plot` 重绘入口。

草稿按来源和通道保存在项目记录中，可随项目接续快照迁移。切换通道或重新载入时核对来源身份；已删除值不会从旧字段缓存恢复。运行审阅明确提示草稿与所选历史冻结输入不同，旧冻结和授权不改写。

## 一批测试如何运行

1. 在参数迭代中选择四个边界之一，填写起点／终点／步长（允许负步长），或显式数值列表，最多 64 个值。其他参数和矩阵来源固定。
2. 点击“预览候选”。来源服务器读取完整 `.eig`，返回每个 k 点的外窗／冻结窗态数、相对／绝对边界、错误和零余量提示。没有基础冻结输入选择，也不强制扫描顺序或上一轮基线。
3. 调整或移除错误候选后“准备这批测试”。后台保存不可变的批次基线和每个 case 的输入；之后进入资源、脚本审阅与明确授权。
4. 查看程序结束、解缠／局域化判据、spread（Å²）、耗时及人工叠图。误差比较按需运行。选择已完成结果采用到基础节点新草稿，不自动评选最佳窗口，不自动减半步长。

缺省遵循 Wannier90：未设外窗边界时使用完整 `.eig` 能量极值；没有 `dis_froz_max` 时不启用冻结窗，已有上界但未设下界时使用外窗下界。扫描冻窗下界前须先设置上界。每个 k 点须满足 `N_frozen ≤ num_wann ≤ N_outer`，等号允许但显示零余量；显式窗口关系错误逐候选报告。

检查完整均匀 mp_grid/kpoints、`.eig` 的 band/k 覆盖、有限值与能带顺序，矩阵头部维度与 num_bands/num_wann/num_kpts 一致。特殊 projectability 冻结和特殊 k 区域等现有不支持情况继续阻断。程序 All done 不等于数值收敛；spread 不单独证明物理拟合质量。

## 叠图与保存

结果页可填写叠图标题，再生成 PNG、SVG、CSV。新叠图和已有历史叠图均可“登记到数据集”，另填名称和备注。数据集独立保留图片、曲线、实际任务来源、窗口和费米参考；旧记录补读已核验冻结输入，缺证据时明确标记不可用。重复登记打开已有数据集，不重新分析。

DFT 比较须有同 SCF、同服务器、同路径和通道来源。连续能带的一一对应误差只是指定比较区间内的数值指标；纠缠交叉应结合叠图判断，不由误差自动排序。比较区间是可选项，不限制新候选的窗口资格。

## 合同、恢复与兼容

新请求使用 `mode=wannier90-window`、`source_draft`（parent_id、alias、seed、win、energy_reference、band_path、input_reason、pending）和 `window_search`（boundary＋values，或 start/end/step；可选 target）。`/api/scans/preview` 返回定义身份，`/freeze` 另需 reason、expected_definition_hash。新批次为 `fpilot.wannier90-window-scan.v2`。历史 v1 冻结包、分阶段接口和授权保留兼容，旧 next-round 仅服务历史调用。

每个 case 使用独立目录、`.win` 和 `.chk`，同服务器矩阵按既有文件合同核验与物化。来源变化要求重新预览；UNKNOWN 保留准确 attempt 并阻止重投。暂停、受阻链／批次不自动继续。准备输入不等于提交，提交仍经已有资源和脚本授权入口。

扩展的新运行入口仅有本地和服务器直接命令行；新扩展不能申请队列资源。历史已启动调度分析只能核查原作业与结果，未启动调度预览失效。大型计算由后续自定义节点处理。

依据：[官方窗口实现](https://wannier-developers.github.io/wannier90/proc/dis_windows.html)、[官方参数表](https://wannier90.readthedocs.io/en/latest/user_guide/wannier90/parameters/)、[SOC 拟合讨论](https://vasp.at/forum/viewtopic.php?p=18855)、[开发者关于投影与收敛](https://lists.quantum-espresso.org/pipermail/wannier/2006-November/000033.html)。本轮验收见 [记录](../submission/WANNIER_USABILITY_ACCEPTANCE.md)。
