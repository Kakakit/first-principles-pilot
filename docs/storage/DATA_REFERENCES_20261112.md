**数据传递整改第一阶段 · 2026-11-12**

按 Astra《数据传递与保存整改》第一阶段交付批次清单和只读产物引用。VASP 迭代的 Case 表、Wannier 能窗迭代的运行状态提供“导出数据清单”。计算结果的文件页和独立扩展的原始结果提供“导出产物引用”。

清单包包含 `batch.json`、`cases.csv`、`README.txt`。JSON 保存固定的 case 编号、扫描参数、当前 attempt 与历史 attempt、来源 case、实际服务器与冻结路径、输入摘要、收敛证据、已提取指标和产物引用。CSV 每个 attempt 一行，未派发的 case 保留一行，缺失的历史记录带错误说明。负数能量保持数值，可能被表格软件解释为公式的文本加单引号。

Wannier 批次导出完整 `source` 与 `source_frozen_id`，包含费米能参考、seed、来源 attempt 和自旋模式。每个 case 保存完整 `relative_windows`、`absolute_windows`。相对窗口以费米能为零点，绝对窗口沿用原始能量零点，单位均为 eV。JSON 的批次来源和 case 能窗也写入 CSV 同名结构化列，未记录的结构化值为 `null`。派生 case 保存 `derived_from_case_id` 和同批的 `derived_from_batch_id`，CSV 同步带出这两个标量列。

清单生成使用本机保存的任务、摘要和文件列表。ZIP 内三份文件由同一份清单生成。`collected_at` 是清单生成时间，`files_observed_at` 是文件列表原读取时间，`metrics_checked_at` 是指标提取时间。`null` 或 CSV 空单元格表示尚无相应证据。程序结束、电子/离子收敛、Wannier 解纠缠/局域化收敛和科学验证各自保存。VASP 指标以原始单位导出，使用与当前记录身份相符的摘要缓存。Wannier 指标使用同一 attempt、bundle 和回执版本的能窗摘要。

| 接口 | 用途 |
|---|---|
| `GET /api/scans/{batch_id}/manifest?project_id=...&format=json` | 返回完整 JSON 清单 |
| 同一接口的 `format=csv`、`format=zip` | 下载表格或清单包 |
| `GET /api/artifact-refs?project_id=...&producer_kind=attempt&producer_id=...` | 查询指定计算的已记录文件 |
| 同一接口的 `producer_kind=analysis` | 查询独立扩展的本机及远端产物 |
| `POST /api/artifact-refs/resolve` | 请求体提供 `project_id` 与 `reference`，由宿主解析位置 |

`fpilot.artifact-ref.v1` 包含项目、生产者类型/ID、产物相对标识、原生产者的 batch/case、数据类型、格式版本及元数据版本。未知文件格式版本写为 `null`。实际位置来自宿主记录，引用中的生产者固定为指定 attempt 或分析 ID，上游重试和批次排序不改变已有引用。

版本由已记录大小、修改时间和来源位置身份组成，位置指纹只哈希元数据。本机原生文件还记录纳秒修改时间。产物没有已记录修改时间时保留 `null`。解析会拒绝项目不符、路径越界、已回收/清理、文件记录缺失或版本不符的引用。远端大产物可在本机没有下载副本时列出。

接口的 `remote_checked=false`、`requires_verification=true` 明确记录本轮读取边界。过期、未读取和不完整的文件清单会附带状态与说明。消费方实际准备文件时仍须实时核验来源。消费引用登记、文件准备与消费期间的生命周期保护随后续消费者接口接入。

后续阶段按原规划衔接逐 case 的 SCF 后续批次与新目录外层、持久化传输任务、独立扩展接入。历史路径由已冻结记录解析，后续目录改造继续沿用这一入口。

验收使用隔离临时工作区，所有 SSH 调用均被禁止。专项覆盖多主机、固定编号、重试历史、缺失记录、来源 case、过期指标、回收/清理、版本变化、大产物元数据、Wannier 复用，以及真实 ASGI 请求的 ZIP 下载和引用解析。运行中的工作台需正常重启加载后端接口，旧版 API 返回 404 时页面给出重启提示。

12 项新增后端专项、6 项既有结果摘要测试、25 项相关前端测试、类型检查和生产构建通过。隔离 Chrome 实际下载 ZIP 与引用 JSON，解包核对 3 个 case、5 行运行记录及 CONTCAR 引用。三个按钮各发出一次 GET，运行时异常为 0，旧版后端的 404 提示通过。浏览器工具连接超时后采用独立临时配置的无界面 Chrome 完成验收。短回执、截图和一次性脚本归档在 `workspace-data/development-records/data-references-20261112/`。
