# TB2J v2 接入验收 · 2026-10-06

按用户追加要求，首版改为来源服务器直接命令行执行并等待。TB2J 清单只支持 `ssh/local`，默认 `ssh`；不填写调度核数，不探测节点，不提交 PBS/Slurm。原调度适配器未修改，其他扩展原行为保留。

## 实现与来源

- 独立包位于 `resources/extensions/tb2j`，使用 v2 manifest 和 `doctor/check/run`，旧 CLI、请求与 fixture 保留。包不导入主体 backend、不访问主体数据库、不自行 SSH。
- 表单选项冻结为 JSON；扩展检查后构造 argv，使用选定 Python 执行 `-m TB2J.scripts.wann2J`。执行器负责同服务器独立复制、等待和读取回执。原子编号沿用官方 CLI 的 1 起编号；没有调用其他环境的 PATH 命令。
- SOC 绑定一个完成的 spinor attempt；共线绑定同服务器、同 DFT 父来源的 `.1/.2` 两个完成 attempt。读取 win/wout 与 hr+centres 或 tb，核对前缀、晶胞、原子顺序、num_wann、自旋模式与通道兼容性。规范文件名和 POSCAR 只在独立分析目录生成。
- 通用协议新增可选 `inputs.required`（默认 true）及 `execution.preferred_location`（缺省沿用本机）。可选输入按 source_id 配对；主体补充冻结 Wannier 通道、父来源、num_wann 和能量参考，没有按 TB2J ID 分派算法的分支。模板 schema 与说明同步。
- 费米能仅使用本来源冻结参考，或用户填写的数值与来源说明。SOC groupby 必须选择并确认。轨道映射使用原子内 TB2J 矩阵轴的 1 起序号；没有自动推断 t2g/eg。
- 使用本次原始产物及官方 SpinIO 读取接口，导出高精度 J、DMI、各向异性及可选轨道矩阵。eV 转 meV，距离 Å，保留原子编号与晶格平移 R；CSV/JSON 完整，页面有行数和 10 MiB 限制。SOC 缺少张量、未知格式或映射越界明确失败，原始产物保留。

## 检查结果

| 验证 | 结果与边界 |
|---|---|
| 主体 Python 全量回归 | 383 项通过，包含 15 项新独立 TB2J 测试及 32 项扩展宿主测试 |
| 独立包回归 | 24 项通过：15 项 v2、9 项旧 CLI 兼容；共线/SOC × hr/tb 作为子场景覆盖 |
| 前端 | 66 项用例通过；类型检查、生产构建通过；已有 chunk 大小提示保留 |
| 输入及科学合同 | 错配父来源/通道/服务器、自旋/晶胞/原子顺序/num_wann、缺依赖、冻结/手动能量参考、SOC 基底顺序、索引/单位/精度、轨道映射和原文件保护通过 |
| 通用执行恢复 | SSH 唯一执行、UNKNOWN 只核对原记录不重执行；既有 PBS/Slurm 等待恢复与回执测试通过。TB2J 本身不走调度 |
| 隔离 UI/API | 实际页面完成安装、启停、两次分析、下载、卸载后历史保留；默认来源服务器、参数冻结、tb 第一来源省略 centres 后按 source_id 关联通过 |

隔离页面使用独立数据库与合成 Wannier 输入；本机 fake TB2J 只模拟官方 CLI/SpinIO 形状，版本标记 `0.0.0-simulation`。SOC hr 与共线 tb 各执行一次；`direct=2, stage=2, capacity_probe=0, scheduler_submit=0, real_ssh=0`。原输入 hash 均保持；下载的 CSV 保留 `1.2345678901234567` meV，页面显示精度与完整下载分开。模拟计算不是 TB2J 数值正确性或真实服务器可运行的证据。

## 安装与未完成的真实验收

正式本机注册表已安装并启用 TB2J 2.0.0，默认服务器命令行。实际本机 doctor 为不可用：所选 Python 无法导入 TB2J；残留安装元数据不视为依赖满足。正式扩展运行时尚未登记，使用前须在扩展管理登记来源服务器已有 Python 与工作区；本轮没有安装/升级本机或服务器环境，也未声称远端 TB2J doctor 已通过。

安装前后正式 attempt/链/扫描状态、其他扩展注册与本机配置保持一致。开发期间原后台监控更新过既有记录；安装以当时当前快照核对，没有恢复队列或新建研究 attempt。正式服务收尾时处于停止状态，保持停止，worker 配置保留。没有提交真实研究任务、远端计算或新的运行 ZIP。

程序完成、数值收敛和物理验证独立记录；单方向 SOC 保持方向验证未完成。真实 TB2J、积分收敛、DFT–Wannier 一致性和多方向物理验证仍须按明确来源和单次授权另验收。长运行/断线沿用宿主 UNKNOWN 与原目录回执恢复，不重复执行。

使用步骤与官方来源见 [扩展指南](../../resources/extensions/tb2j/README.md)，通用字段见 [v2 协议](PROTOCOL.md)。本机备份、日志、一次性验收、计数与截图归档于 `workspace-data/development-backups/tb2j-v2-20261005/`，不上传 GitHub。
