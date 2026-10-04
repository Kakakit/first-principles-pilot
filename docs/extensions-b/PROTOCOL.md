# 扩展文件协议 v2

扩展是独立 Python CLI，只依赖自己的文件和第三方库。主体负责来源解析、参数表单、执行与报告展示，不按扩展 ID 选择科学算法。扩展不导入 `backend`、不读取工作台数据库，也不自己提交作业。v1 SDK/API 继续供历史兼容；新安装、结构分析、所选结果后处理和多个结果分析都使用 v2。

## 最短开发路径

1. 复制 `extension-templates/structure-statistics`，修改清单中的 ID、名称、版本和说明。
2. 修改 `scripts/cli.py` 中的 `check`、`analyze`，在 `options_schema` 声明参数；不要编辑主体。
3. 用已有 Python 执行 `python scripts/cli.py doctor` 和 `python tests/test_standalone.py`。模板仅用标准库，测试生成请求并检查体积、CSV 和原文件不变。
4. 在“扩展管理”填复制目录的绝对路径，点击“导入并启用”；也可导入包含唯一清单的 ZIP。
5. 在结构页或所选结果旁选择扩展并运行。缺依赖会保留安装记录并列出模块；主体不会重建环境或升级软件。

模板中的 `protocol/` 和主体 `resources/extension-protocol/` 提供 manifest、request、result 的 Draft 7 schema。当前包限普通文件、1000 项、20 MiB，至少含一个 fixture。模块依赖写 Python 导入名，如 `spglib`；版本约束和算法许可应由扩展自己的 README/依赖文件说明。

## 清单：`extension.manifest.json`

协议标识为 `fpilot.extension.v2`，完整示例见可运行模板。

| 字段 | 含义 |
|---|---|
| `id`、`name`、`version`、`description` | 独立包的身份、显示名、版本与用途 |
| `entrypoint` | 包内 CLI 相对路径；由选定 Python 执行 |
| `contexts`、`stages` | 来源类型 `structure`/`attempt` 与适用计算阶段；空阶段列表表示不限阶段 |
| `sources.min/max` | 1–8 个来源，按用户选择顺序排列 |
| `inputs` | 每个来源需要的角色；每项包含 `role/title/names/max_bytes`，例如结构角色从 CONTCAR/POSCAR 中明确选择 |
| `evidence` | 所需证据，如 `COMPLETED`、`vasp_timing`、`ediff_reached`；布尔证据必须严格为 true |
| `execution` | 支持 `local`、`ssh`、`scheduler`，以及 CLI 运行超时秒数 |
| `dependencies` | doctor 要检查的 Python 模块 |
| `options_schema`、`ui_schema` | object JSON Schema 与 RJSF 展示设置；只接受本 schema 内 `$ref` |
| `outputs`、`fixtures` | table/curve/image/file 输出能力，以及可用于独立检查的包内输入 |

主体按清单筛选阶段和来源；缺文件、证据或环境时显示具体原因。“不适用”与“缺依赖”分别记录。每个输入角色当前要求所有来源均提供一个候选文件；不支持任意可选角色组合。

## CLI 与请求

```text
python scripts/cli.py doctor
python scripts/cli.py check --request /absolute/request.json
python scripts/cli.py run --request /absolute/request.json
```

doctor 在 stdout 返回单个 JSON：`schema=fpilot.extension-doctor.v2`、布尔 `ready`、`missing` 模块列表和可选原因。check 返回 `schema=fpilot.extension-check.v2`、`status=APPLICABLE/NOT_APPLICABLE`、`reason`。stdout 不混入其他文字，诊断写 stderr。run 将结果写到请求的 `result_file`，正常结束返回 0；非零退出不能视为完成。

请求 `fpilot.extension-request.v2` 包含 `analysis_id`、`inputs`、`sources`、`options`、`output_dir`、`result_file`。`inputs.<role>` 是按来源顺序排列的数组，每项给出 `path/name/source_id/size_bytes`，可能附小文件 sha256；所有 path 都是已解析的本次独立副本。扩展读取这些路径，只向 output_dir 写文件。不得推测工作区、服务器路径或 attempt 数据结构。

sources 是宿主冻结的来源摘要，包含具体 attempt、节点、主机、阶段、程序/收敛证据和实际冻结输入摘要；结构草稿包含当前几何内容身份及原始文件身份提示。来源、选项、包 revision 和运行时在创建分析时冻结，不在恢复时改成“最新结果”。宿主保存的报告与下载结果 JSON 使用冻结来源。

## 结果与通用展示

结果 `fpilot.extension-result.v2` 必须是有限 JSON，包含：

- `status`：COMPLETED、FAILED 或 NOT_APPLICABLE；`summary`、`warnings`、`scientific_validation`、`sources`。
- `tables`：title、columns（key/label/unit）、rows；无量纲可写 `1` 或空单位。
- `curves`：title、x_label/x_unit、y_label/y_unit、series（label 和 `[x,y]` points）。
- `artifacts`：输出目录内相对 path、label、media_type；图片直接展示，其他文件提供下载。

结果 JSON 最多 10 MiB；路径必须指向独立输出目录中的普通文件。表格界面显示约 8 位有效数字，JSON/CSV 保留算法原始精度。程序完成、数值证据和物理结论分别表达；宿主不因扩展返回成功就升级原计算的科学审核状态。

首批提供体积/元素统计、普通空间对称性、配位多面体畸变、LCALCPOL 单结果及双结果分析。后三者薄封装包内既有算法。对称性保留三个 symprec 容差，不分析磁空间群。极化比较按来源 2−来源 1 计算，保留原始差和极化量子约化；它不能单凭两个端点证明同分支或绝缘路径，科学验证保持 INSUFFICIENT。

## 本机、SSH 与调度运行

本机使用当前登记解释器。来源 attempt 的单个文件仅经受控工具回传，必须是已知大小、非空、普通文件且不超过 10 MiB；大文件应选择远端运行。草稿结构保存独立副本。本机 run 超时停止整个独立子进程组，失败保留日志。

远端运行时在扩展管理保存已有 SSH 别名、Python 绝对路径、队列、工作区、节点和核数。不创建环境。SSH 适于轻量读取；计算型扩展使用 PBS/Slurm。预览绑定当前来源、选项、包版本和运行时，10 分钟内点击“按此来源与脚本运行”。调度派发前重新核对实时容量和现有总量/主机/核数额度；资源不足等待，节点离线或配置错误报错。

远端独立目录位于 `<remote_workspace_root>/analyses/<analysis_id>`。原计算文件只读，使用普通独立复制并核对源文件大小/修改时间和目标大小，不建立可写硬链接、不扫描大文件 SHA。此策略不声称内容完全一致。来源元数据变化使准备失败。

脚本顺序为 doctor→check→run，记录 doctor.json/check.json、日志和原子 exit.json；doctor/check 最多 30/60 秒，run 使用清单超时，超时停止子进程组并写退出码 124。调度器另受用户 walltime 限制。SSH 连接等待最多 900 秒；连接中断或调度器提前终止而没有结束回执时记 UNKNOWN，只核对原目录/精确 job ID，不重投。缺依赖、输入不适用、非零退出分别记 FAILED、NOT_APPLICABLE、FAILED。

每个分析返回 analysis_id，通过独立 API 读取状态/历史/产物。文件锁和派发前持久状态防止 API/worker 重复执行；scheduler 在途与 UNKNOWN 占计算的同一额度。恢复读取原 job.id、exit.json 和精确队列状态；qstat/squeue 消失或 exit 0 单独都不能证明分析产物有效。小产物经受控脚本逐文件回传，超过 10 MiB 的产物留在远端并显示路径。

## 生命周期与依赖选择

安装复制包并按小包内容形成 revision；清单和 doctor 按版本/已有解释器缓存。首次安装本机环境满足即启用；缺本机依赖时保留停用记录。允许远端运行的包可显式启用，远端 doctor 将在独立目录核对。重新检查刷新环境状态；停用立即移出可运行动作。卸载取消登记并移入 extensions-v2/recycle，历史 analyses-v2 中的包、请求、日志、报告和产物保留。已创建的分析继续使用自己的冻结包。

前端薄接 [react-jsonschema-form 6.11.0](https://rjsf-team.github.io/react-jsonschema-form/docs/quickstart/)（Apache-2.0），按需加载表单；后端使用 [jsonschema 4.25.1](https://github.com/python-jsonschema/jsonschema/blob/v4.25.1/pyproject.toml)（MIT，Python 3.9）。两者按锁定版本使用，不自建表单/JSON Schema 引擎。用户扩展自己的算法许可须随包保存。
