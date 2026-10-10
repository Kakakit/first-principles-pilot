# Wannier 输入与同源能带选项修复 · 2026-10-10

## 实际问题与操作方式

Cr2FeO4 的已完成 noSOC 能带 `25279.node01` 已有 LORBIT=11，远端 KPOINTS 为 line-mode，OUTCAR 有程序正常结束证据。它的 SCF 绑定在旧版 chain_source_snapshot（并保存在 input_audit.dependency_snapshot），界面及路径后端只读取 dependency_snapshot.upstream_attempt_id，造成漏选。画布上的 Wannier90 节点直接连接 noSOC 接口 `25308.node01`；两者实际 POSCAR 按原子顺序及周期坐标核对相同。修复后该能带已出现在选择器，真实远端 PyW90 提取成功：9 段路径，首段采样数 40。以上是路径及来源验收，不是局域化或物理模型验证。

SOC 接口 `25309.node01` 绑定另一 SCF，实际结构也不同，不作为当前 noSOC 接口的同源能带。未修改图连接、原研究输入或历史记录，未提交新计算。

## 实施

- vasp-wannier 与 bands/dos 一样，在共享及节点均没有显式 LORBIT 时，生成输入和前端最终值默认 LORBIT=11。显式覆盖（包括清空）继续优先，不回写旧冻结输入。
- 只读适配当前 dependency_snapshot、旧 chain_source_snapshot、旧输入审核的依赖记录；绑定冲突拒绝，缺少来源时不推断。能带、DOS 辅助、比较及重绘使用同一 SCF 适配器。
- 新路径合同比较两次实际 POSCAR；文件哈希不同则只读取受限小结构文件，允许晶格绝对差 1e-6 Å、周期分数坐标差 1e-6，元素顺序必须一致。冻结/准备/派发核验绑定和两侧文件身份。旧路径合同仍按原 CONTCAR 哈希只读核验。
- 无可选能带时显示缺少上游、缺少可确认绑定、同源能带尚未完成等具体原因。切换来源/主机后清除失效选择，LORBIT 不参与路径筛选。

## 参数目录

由 13 项扩展到 24 项，新增 restart、num_cg_steps、precond、guiding_centres、trial_step、fixed_step、num_dump_cycles、num_print_cycles、write_tb、write_u_matrices、use_ws_distance。按官方 v3.1 注册表及 User Guide 核对类型、语法和条件；不写入新的优化默认数值。restart 提供 default/wannierise/plot/transport 枚举；trial_step 与 fixed_step 仍由 .win 审核阻止共存。

restart 要求同一 Wannier90 模型的 seedname.chk。普通 VASP 接口四文件不含 .chk，该入口在冻结和准备阶段明确阻止 restart（含旧冻结输入）；用户可从 .win 中删除 restart 后开始新局域化。已有模型的“修改路径并重绘”入口继续独立复制检查点、使用 restart=plot。此次新增标签不扩展 default/wannierise/transport 的检查点选择及派发入口。

guiding_centres 需要显式 projections；界面提示此条件。VASP 所有字段保护、.1/.2 通道隔离、能窗相对费米参考保留。空白仍为沿用；删除已有 tag 使用 .win 原文编辑。

官方依据：[VASP LORBIT](https://vasp.at/wiki/LORBIT)、[Wannier90 parameters](https://wannier90.readthedocs.io/en/latest/user_guide/wannier90/parameters/)。现有官方 tag 注册表与解析器可复用，因此只扩展薄参数目录与历史来源适配，不引入新依赖。

## 验收

- 全量后端 530 项：3 跳过；两项 localhost 端口用例受沙箱限制，独立工作区补跑 portable_release 共 11 项通过。
- 最终 Wannier 专项 51 项（1 跳过），输入生成 20 项；前端全量 101 项，类型检查与构建通过。
- 覆盖旧链/审核字段、来源冲突、v1 CONTCAR 与 v2 POSCAR、打印舍入、错序、结构不同、父结构被替换、旧路径回读、枚举错误、步长互斥、冻结 restart 在恢复后阻止。
- 真实三个 attempt 的 INCAR、POSCAR、KPOINTS 和结束证据只读核查；真实同源路径提取成功。浏览器确认 25279 能带选项与 24 个标签，未冻结或提交研究任务。
- 正式服务按原配置重载，worker 周期 300 秒；62 条原 attempt 的身份、冻结包、来源、执行输入和授权字段核对保持。私有项目证据、截图、测试日志与一次性脚本只存本机开发备份。

发布副本由精确 GitHub 基线及本轮 18 项增量构建。旧测试依赖未公开的主机登记和脚本，使用本机私有离线夹具（六主机的合成解释器登记与脚本副本，SSH 仍被测试 mock）补齐，未发布该夹具。副本前端 98 项及类型/构建通过。
