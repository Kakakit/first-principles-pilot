# Wannier90 能窗分阶段迭代

## 从哪里开始

在计算流程画布连接 **Wannier90 → 参数迭代**。基础 Wannier90 节点先选择明确的 VASP 接口 attempt、通道与完整矩阵，配置投影、能窗和能带路径，审查并独立冻结。不要用 `restart=plot` 作为基线。

选择参数迭代节点，在“计算配置”中指定这份冻结输入和固定比较目标（均按 `E−E_F`、eV）。初始小冻窗继承该输入，应先在基础节点核对；界面不为材料猜步长和允许范围。

1. 填写本轮边界的允许范围、步长和测试目的，点击“预览并检查本轮窗口”。这时才读取源服务器 `.eig`，一次检查最多五组候选。
2. 核对候选、态数余量、过滤原因和新增任务数，冻结本轮。
3. 到“运行与监控”选择本轮，检测空闲节点，使用已有候选池设置并行上限；修改作业名、队列和时限，审阅最终脚本并确认一次。
4. 刷新状态，按需读取收敛、spread（Å²）和耗时。选择程序已完成的候选作为基线，再粗扫、减半步长或推进下一边界。新轮次仍须预览、冻结和授权。

固定顺序是外窗上沿 → 外窗下沿 → 冻窗上沿 → 冻窗下沿 → 外窗上沿局部复查 → 外窗下沿局部复查。每轮只修改一个边界的当前值、±一步、±两步，其他三个边界与所有其他参数固定。最后两次复查不能重复粗扫；没有合法新组合时给出原因，已完成的准确结果仍可复用并由用户选择。

“采用窗口，生成新草稿”把选定窗口写到基础节点的采用草稿卡片。在基础节点点击“载入扫描窗口并重新审查”，核对后生成新的冻结版本。旧冻结输入、旧轮次和授权不会改写。“开始新的窗口方案”只清空该节点的迭代配置，历史仍可访问。

## 科学检查与结果解释

- 检查完整均匀 `mp_grid/kpoints`，源 `.eig` 的每个 band/k 对须恰好一行，有限且按能带序递增；`.amn/.mmn` 头部须与有效 `num_bands/num_wann/num_kpts` 匹配。`exclude_bands` 应已由 DFT 接口体现在矩阵中，不在这里重复删带；维度不匹配时阻断。
- 在每个 k 点按包含上下边界的区间检查 `N_frozen ≤ num_wann ≤ N_outer`。表中外窗余量为 `min(N_outer)−num_wann`，解缠余量为 `num_wann−max(N_frozen)`。等号允许，但明确提示余量为零。
- 冻窗须位于外窗内且有正宽度；每个外窗候选必须覆盖固定比较目标。仅支持普通能量解缠；projectability 冻结、特殊解缠 k 区域及投影数不同于 `num_wann` 的来源会提示暂不支持，不误报通过。
- 程序结束与数值判据分开。明确 convergence 标记才显示“已证实”；`All done`、`Final State` 或达到最大迭代次数不作为收敛证据。未执行和未启用局域化判据分别显示。缺标记不会显示已证实，应结合完整 `.wout` 核查；不依 spread 自动终止阶段或判断物理拟合质量。
- spread 按 `.wout` 的长度单位换算为 Å²，包括 `length_unit=bohr`。来源 `.win` 内容须与本次冻结输入相符，才归属该输出证据。

## 按需比较 DFT 能带

无兼容 DFT 参考也可以运行窗口测试并人工评估。需要数值误差时，展开“按需比较 DFT 能带误差”，选择相同 SCF 来源、路径、通道与采样的 DFT 能带，明确共同的 DFT 起始能带。

本阶段支持明确的连续能带、按能量次序的一一对应：`num_wann` 条 Wannier 带对应从指定序号开始的同数目 DFT 带。接口内部序号从 0 计数，界面从 1 计数。子空间纠缠、交叉或混合不能建立这种对应时，使用叠图人工分析，不能把错误配对的指标解释成质量排序。

同一方案首次成功比较后固定 DFT attempt、目标范围和共同对应关系，各候选的取样掩码由 DFT 参考决定；拟合带移出目标范围不会被排除。返回 MAE、RMSE、最大绝对误差（meV）与叠图，不自动排序。路径、来源、通道或共同取样不一致则只报告阻断原因。没有分析 CLI 不影响核心窗口任务。

## 执行、恢复与回收

方案固定使用矩阵来源服务器，不能跨机搬运计算目录。每个新 case 拥有独立 `.win`、目录和新 `.chk`；完整矩阵复用现有同服务器来源文件合同，不复用旧 checkpoint。

候选池与 VASP 扫描/计算链共用容量、总量、主机和核数预算；批次还有限定的并行数。满载时等待。确定 attempt 身份持久保存；UNKNOWN 阻止继续派发并核查原回执，不能重投。失败可按现有规则明确重试一次。

回收项目、批次或准确来源会暂停后续派发；恢复后需手动继续。已永久删除的来源不会自动换成最新结果。单独删除 case 任务后，其窗口组合也不会静默重新提交。统一回收站只处理本地对象，服务器任务与文件不删除。

## 接口与独立工具

旧 `/api/scans/preview` 与 `/freeze` 请求默认 `mode=vasp`，行为保留。窗口模式用 `mode=wannier90-window`、`frozen_id`、`window_search`（phase、boundary、target、lower、upper、step、previous_batch_id、selected_case_id、refine）。冻结请求另需 reason 与 expected_definition_hash。

新增接口：`POST /api/scans/{id}/next-round` 只生成预览；`GET .../window-results` 按需读取；`POST .../window-compare` 使用 case_ids、band_attempt_id、dft_band_offset；`POST .../cases/{case}/adopt-window` 返回采用草稿。资源预览、授权、暂停、恢复和重试共用扫描入口。

`resources/sets/wannier_windows.py` 为 stdlib JSON CLI，支持 `check`/`summary`，在来源服务器读文件、只回传小摘要；批量检查按准确来源和大小/修改时间缓存。大矩阵不下载、不默认整文件哈希。小 `.win`/OUTCAR 与冻结合同内容身份保留。

能带指标通过 `resources/extensions/pyw90/scripts/pyw90_window_scan.py measure --request request.json` 的独立 JSON 文件协议获得，不调用主体内部模块或启动作业。请求包含 curves_csv、target_relative_eV、dft_band_offset，标准输出为 JSON；保留原扩展独立搜索能力。

依据：[官方窗口实现](https://wannier-developers.github.io/wannier90/proc/dis_windows.html)、[官方参数表](https://wannier90.readthedocs.io/en/latest/user_guide/wannier90/parameters/)、[官方 3.1 输出单位与收敛实现](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/wannierise.F90)、[能窗优化研究](https://greschd.ch/assets/pdf/gresch_automated_2018.pdf)。
