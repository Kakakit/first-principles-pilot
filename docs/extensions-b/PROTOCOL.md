# 扩展文件协议 v2

扩展是独立 Python CLI，只依赖自己的文件和第三方库。主体负责来源解析、参数表单、执行与报告展示，不按扩展 ID 选择科学算法。扩展不导入 `backend`、不读取工作台数据库，也不自己提交作业。v1 SDK/API 继续供历史兼容；新安装、结构分析、所选结果后处理和多个结果分析都使用 v2。

## 最短开发路径

1. 复制 `extension-templates/structure-statistics`，修改清单中的 ID、名称、版本和说明。
2. 修改 `scripts/cli.py` 中的 `check`、`analyze`，在 `options_schema` 声明参数；不要编辑主体。
3. 用已有 Python 执行 `python scripts/cli.py doctor` 和 `python tests/test_standalone.py`。模板仅用标准库，测试生成请求并检查体积、CSV 和原文件不变。
4. 在“扩展管理”填复制目录的绝对路径，点击“导入并启用”；也可导入包含唯一清单的 ZIP。
5. 在结构页或所选结果旁选择扩展并运行。缺依赖会保留安装记录并列出模块；主体不会重建环境或升级软件。

模板中的 `protocol/` 和主体 `resources/extension-protocol/` 提供 manifest、request、result 与可选 presentation 的 Draft 7 schema。当前包限普通文件、1000 项、20 MiB，至少含一个 fixture。模块依赖写 Python 导入名，如 `spglib`；版本约束和算法许可应由扩展自己的 README/依赖文件说明。

## 清单：`extension.manifest.json`

协议标识为 `fpilot.extension.v2`，完整示例见可运行模板。

| 字段 | 含义 |
|---|---|
| `id`、`name`、`version`、`description` | 独立包的身份、显示名、版本与用途 |
| `entrypoint` | 包内 CLI 相对路径；由选定 Python 执行 |
| `contexts`、`stages` | 来源类型 `structure`/`attempt` 与适用计算阶段；空阶段列表表示不限阶段 |
| `sources.min/max` | 1–64 个来源（包括当前任务）；每个扩展仍使用自己的最小/最大值，按用户选择顺序排列 |
| `inputs` | 每个来源需要的角色；包含 `role/title/names/max_bytes`，可选 `required`（默认 true） |
| `evidence` | 所需证据，如 `COMPLETED`、`vasp_timing`、`ediff_reached`；布尔证据必须严格为 true |
| `execution` | 新运行支持 `local`、`ssh`，以及 CLI 超时；旧 `scheduler` 声明只供历史解析，不赋予执行能力；可选 `preferred_location`（默认本机，须为支持位置） |
| `dependencies` | doctor 要检查的 Python 模块 |
| `options_schema`、`ui_schema` | object JSON Schema 与 RJSF 展示设置；只接受本 schema 内 `$ref` |
| `presentation`（可选） | `{entrypoint:"scripts/presentation.py"}`，包内独立展示整理 CLI；旧扩展缺省使用基础表格/曲线 |
| `outputs`、`fixtures` | table/curve/image/file 输出能力，以及可用于独立检查的包内输入 |

主体按清单筛选阶段和来源；缺文件、证据或环境时显示具体原因。“不适用”与“缺依赖”分别记录。默认角色要求每个来源提供候选文件；`required=false` 允许部分或全部来源缺少该角色，最终组合由扩展 check 判断。明确选择的无效文件不能回退；可选角色空字符串表示本次不提供。

计算结果后处理提供“批量选择已有任务”，搜索节点名称、作业号、备注或服务器并勾选。当前任务固定为来源 1；已选任务保留顺序，新任务按列表顺序加入。取消不改变来源；批量调整按来源身份保留已选输入文件；超过扩展上限不能全选，不截断选择。同一来源不能重复，回收记录与其他项目不参与选择。原逐个添加入口保留。来源列表包含本项目全部已知任务，不受执行页默认 100 条历史限制；读取列表不连接服务器。采用后继续按文件、证据和实际运行位置核验。参考 [EOS 等多来源扩展接入](MULTI_SOURCE_INTEGRATION.md)。

## CLI 与请求

```text
python scripts/cli.py doctor
python scripts/cli.py check --request /absolute/request.json
python scripts/cli.py run --request /absolute/request.json
```

doctor 在 stdout 返回单个 JSON：`schema=fpilot.extension-doctor.v2`、布尔 `ready`、`missing` 模块列表和可选原因。推荐同时返回 `python`（实际解释器路径）、`python_version`、`versions`（模块名称到版本的映射）；科学程序版本也可用 `version` 返回。检查实际模块导入，不能把残留安装元数据当作可用。旧 helper 将 Python 版本放在 python 字段的回执仍兼容。check 返回 `schema=fpilot.extension-check.v2`、`status=APPLICABLE/NOT_APPLICABLE`、`reason`。stdout 不混入其他文字，诊断写 stderr。 仅已知输入不适用返回 NOT_APPLICABLE。check 侧重来源、参数和文件元信息，完整解析在 run 执行。环境、读取和内部失败以非零退出及阶段诊断报告。run 将结果写到请求的 `result_file`，正常结束返回 0；非零退出不能视为完成。

请求 `fpilot.extension-request.v2` 包含 `analysis_id`、`inputs`、`sources`、`options`、`output_dir`、`result_file`。`inputs.<role>` 按来源顺序排列，每项给出 `path/name/source_id/size_bytes`，可能附小文件 sha256。可选角色可能只有部分来源，或整个键缺省；扩展须按 source_id 关联，不能按数组下标配对。path 均为本次独立副本，扩展只向 output_dir 写文件。

sources 是宿主冻结的来源摘要，包含具体 attempt、节点、主机、阶段、程序/收敛证据和实际冻结输入摘要。Wannier 摘要补充 seed、DFT 父 attempt、可读父运行名称、自旋模式、num_wann、冻结结构的 elements（已记录时）及本次冻结 energy_reference；不抓取其他计算或最新草稿的能量参考。结构草稿包含当前几何内容身份及原始文件身份提示。来源、选项、包 revision 和运行时创建时冻结，报告与下载 JSON 沿用冻结来源。

数组选项可以在 `ui_schema[field]["ui:options"].source_enum` 声明 `"elements"`。宿主把冻结来源的元素并集作为该字段的字符串选项；不自动勾选或判断磁性。来源没有元素摘要时保持手工数组输入。独立 CLI 仍接收普通字符串数组，不依赖界面服务。缺少输入或运行时只阻止执行操作，允许先编辑参数。

顶层文件选项可声明为字符串 `format: "data-url"`，表单把附件编码后保存于 `options`。附件遵循字段的 `maxLength`，全部附件的编码内容合计最多 16 MiB。其他选项合计最多 64 KiB，并须为有限 JSON。

扩展可在 `ui_schema["ui:projectArtifact"]` 声明一个可复用文件选项，字段为 `field`、`producer`、`path`、`title`、`match`。宿主通过 `GET /project-artifacts` 列出同项目已完成且本机可用的产物。 `match: "project"` 由用户选择，`match: "same_sources"` 按完整 attempt 集合匹配，仅唯一候选自动选入。可用 `source_count` 限制来源个数，`auto_when` 指定开启自动选入的布尔选项。手动导入优先，实际字节冻结进同一个选项；CLI 仍负责核对数据语义和输入绑定。

## 结果与通用展示

结果 `fpilot.extension-result.v2` 必须是有限 JSON，包含：

- `status`：COMPLETED、FAILED 或 NOT_APPLICABLE；`summary`、`warnings`、`scientific_validation`、`sources`。
- `tables`：title、columns（key/label/unit）、rows；无量纲可写 `1` 或空单位。
- `curves`：title、x_label/x_unit、y_label/y_unit、series（label 和 `[x,y]` points）。
- `artifacts`：输出目录内相对 path、label、media_type；图片直接展示，其他文件提供下载。

结果 JSON 最多 10 MiB；路径必须指向独立输出目录中的普通文件。表格界面显示约 8 位有效数字，JSON/CSV 保留算法原始精度。程序完成、数值证据和物理结论分别表达；宿主不因扩展返回成功就升级原计算的科学审核状态。

首批提供体积/元素统计、普通空间对称性、配位多面体畸变、LCALCPOL 单结果及双结果分析。后三者薄封装包内既有算法。对称性保留三个 symprec 容差，不分析磁空间群。极化比较按来源 2−来源 1 计算，保留原始差和极化量子约化；它不能单凭两个端点证明同分支或绝缘路径，科学验证保持 INSUFFICIENT。

## 本机与服务器直接命令行

界面按“本地运行 / 服务器运行”组织，一个扩展可支持两边；仍由 execution.locations 和 preferred_location 声明能力和默认位置，不新增互斥类型。本地使用“本地默认 Python”，未配置时沿用工作台解释器。每台服务器使用原 runtimes 登记作为服务器默认环境。扩展可按本地或具体服务器单独指定 Python；服务器直接命令行使用该服务器覆盖。覆盖优先于默认，恢复默认会清除覆盖。环境配置保存在本机数据的 extensions-v2/runtime-bindings.json，不进入扩展包或 GitHub。

环境选择只影响扩展子进程，不改变工作台启动环境。直接调用 Python 绝对路径，[无需 activate](https://docs.python.org/3/library/venv.html#how-venvs-work)。不自动安装、升级或创建环境。不存在的覆盖路径明确失败，不能静默回退。来源 attempt 的单个文件仅经受控工具回传，必须是已知大小、非空、普通文件且不超过 10 MiB；大文件应选择远端运行。草稿结构保存独立副本。本机 run 超时停止整个独立子进程组，失败保留日志。

远端运行时登记已有 SSH 别名、Python 绝对路径和工作区，不创建环境。SSH 直接执行扩展，新入口不提供调度、节点、核数、时限资源表单。大型计算由计算节点与脚本管理。TB2J 默认 SSH + 来源服务器已有 Python，无调度节点检测或调度核数预算。预览绑定来源、选项、包版本和运行时，10 分钟内点击“按此来源与脚本运行”。新调度预览与派发请求拒绝，未启动的旧调度预览失效；历史已启动分析保留只读核查。

远端独立目录位于 `<remote_workspace_root>/analyses/<analysis_id>`。原计算文件只读，使用普通独立复制并核对源文件大小/修改时间和目标大小，不建立可写硬链接、不扫描大文件 SHA。此策略不声称内容完全一致。来源元数据变化使准备失败。

“检查所选环境”使用指定解释器只执行扩展 doctor，远端小包保存在 `<remote_workspace_root>/extension-checks/<check_id>`，不读取计算来源、不运行分析、不探测容量或提交作业。检查结果与启用状态分开。一个环境缺依赖只阻止该环境执行，参数仍可编辑。

doctor → check → run 全程使用同一解释器。创建分析时冻结实际 Python、服务器配置、参数、扩展包；实际执行的 doctor 版本回执保存到 runtime_doctor。修改默认或覆盖使旧预览不再匹配，须重新检查或预览；已创建记录继续使用自己的冻结配置。旧本地历史未记录解释器时界面显示“未记录”，不按当前默认补填。UNKNOWN 仍只核查原记录，不重执行。

## 环境 API 与兼容

- `GET /api/extension-host/runtimes` 保留原 `runtimes`、`aliases`，新增 `local:{python,inherited,scope}` 和 `overrides:{extension_id:{local?,remote?:{alias:python}}}`。local.inherited 表示是否沿用工作台解释器；执行解析后 inherited 表示是否继承默认环境。
- `PUT /api/extension-host/runtimes/local` 接收 `{python:"/absolute/python"}`；`python:null` 恢复工作台解释器。
- 原 `PUT /api/extension-host/runtimes` 的服务器登记字段保持不变。
- `PUT /api/extension-host/packages/{id}/runtime` 接收 `{scope:"local",python:"/absolute/python"}`，或 `{scope:"remote",alias:"source-server",python:"/absolute/python"}`；python:null 清除此范围的覆盖。
- `POST /api/extension-host/packages/{id}/doctor` 可接收 `{location:"local"}` 或 `{location:"ssh",alias:"source-server"}`（新运行仅 local/ssh）。返回 runtime 和 doctor；无请求体的旧调用仍检查所选本地默认/覆盖环境。
- check、preview、analyses 可携带 `runtime_python`；与当前解析路径不符时拒绝旧选择。远端预览继续绑定整个运行配置；本地预览也可返回 preview_id，提交时若携带则核对冻结绑定。旧本地直接运行请求保持兼容。

脚本顺序为 doctor→check→run，记录 doctor.json/check.json、日志和原子 exit.json；doctor/check 最多 30/60 秒，run 使用清单超时，超时停止子进程组并写退出码 124。SSH 直接运行先在原分析目录独占保留 launch.once，再由选定 Python 启动脱离连接的持久监督进程；仅短时等待启动回执，后续按 PID、启动时钟与系统启动标记核对精确进程，排除 PID 复用。超时停止该监督树并写退出回执。连接中断不会重跑。旧分析缺少 exit.json 时，仅在成功 CLI 回执的 result_file 精确对应原目录且结果协议有效时恢复结果，记录 wrapper_exit_recorded=false，不补造退出码；其他情况保留 UNKNOWN。调度器提前终止且没有足够结束证据也保持 UNKNOWN，只核对原目录/精确 job ID，不重投。缺依赖、输入不适用、非零退出分别记 FAILED、NOT_APPLICABLE、FAILED。

每个分析返回 analysis_id，通过独立 API 读取状态/历史/产物。文件锁和派发前持久状态防止 API/worker 重复执行；历史 scheduler 在途与 UNKNOWN 继续按原记录保留额度，不能重投。恢复读取原 job.id、exit.json 和精确队列状态；qstat/squeue 消失或 exit 0 单独都不能证明分析产物有效。小产物经受控脚本逐文件回传，超过 10 MiB 的产物留在远端并显示路径。

## 生命周期与依赖选择

安装复制包并按小包内容形成 revision；清单和 doctor 按版本/所选解释器缓存。“导入并启用”在包验证通过后启用，依赖检查单独保存，缺依赖不自动停用；旧安装/启用状态保留。重新检查只刷新所选环境，停用立即移出可运行动作。卸载取消登记并移入 extensions-v2/recycle，历史 analyses-v2 中的包、请求、日志、报告和产物保留。已创建的分析继续使用自己的冻结包与 Python。

前端薄接 [react-jsonschema-form 6.11.0](https://rjsf-team.github.io/react-jsonschema-form/docs/quickstart/)（Apache-2.0），按需加载表单；后端使用 [jsonschema 4.25.1](https://github.com/python-jsonschema/jsonschema/blob/v4.25.1/pyproject.toml)（MIT，Python 3.9）。两者按锁定版本使用，不自建表单/JSON Schema 引擎。用户扩展自己的算法许可须随包保存。

TB2J 独立包见 [使用指南](../../resources/extensions/tb2j/README.md)，接入证据见 [TB2J 验收](TB2J_ACCEPTANCE.md)。

## 可选独立交互展示

旧扩展不用添加字段。新扩展可以声明 `presentation.entrypoint` 并实现：

```text
/path/to/frozen/python scripts/presentation.py render --request /absolute/presentation-request.json
```

请求标识 `fpilot.extension-presentation-request.v1`，包含 analysis_id、source_dir（已有输出只读）、output_dir（本次独立展示版本），以及可选 view_state（已保存的展示状态 revision/value 快照；缺省为空）。重新生成展示可读取该快照，仅处理冻结输出，不读取研究来源。stdout 返回 `fpilot.extension-presentation.v1`、entry_html、assets（path/media_type），可附 data_schema。assets 必须是输出目录中的普通文件，最多 1000 项，每个不超过 10 MiB；扩展自行分片。数据含义与科学算法由包负责，宿主不解析扩展格式。

首次完成可用冻结包自动生成；旧记录或手动更新采用当前启用包，保存新的展示包副本和版本，缺省沿用原分析冻结 Python 与服务器。远端原始大数据留在原主机，宿主仅上传展示包、执行整理 CLI 并受控回传小文件，不调用分析 run。失败保留原结果及下载；并发请求共用同一生成记录。停用/卸载不能破坏已保存展示。上版保存的展示设置随更新继承，旧版本仍保留。

可选 `presentation.location` 为 `source`（缺省）或 `local`。`local` 必须声明 `presentation.inputs`：最多 64 项 `{path, required?}`，required 缺省 true。路径相对原分析 outputs，只接受安全的普通文件；宿主仅从已回传本机文件独立复制，每个不超过 10 MiB，绝不因缺失自动连接服务器。整理使用当前本地扩展 Python，展示快照单独冻结实际位置、解释器、包版本和输入副本；原分析的 Python、服务器、参数和产物不变。新分析按清单指定位置生成展示。迁移至 source 的扩展可保留本机 inputs 清单，声明新版 renderer 仍能读取历史 local 快照。已按 local 生成的展示或冻结为 local 的历史分析继续从本机准备输入。

通用表单支持包自定义 `ui:groups`（title/fields/collapsed），按声明顺序展示常用区和折叠区；`ui:visibility` 按同表单字段值条件显示，source_spinors 仅在 auto 模式兼容冻结来源的 spinors。`ui:preview` 指定预览需突出显示的参数；预览 API 返回对应冻结 `options`。这些只改变展示，不跳过原 schema 校验；未声明的扩展保持旧表单。数组操作使用可辨认文字，固定长度数组可在原 ui:options 禁用添加/删除/排序。

HTML 内联脚本、样式和绘图库，以无同源权的 iframe sandbox 隔离，禁止外部连接；普通运行不依赖网络或主体组件。页面与宿主使用 `fpilot.extension-view.v1` postMessage：read-file 只读清单内 JSON，read-state/save-state 保存最大 64 KiB 有限 JSON，resize 调整高度，failed 回退基础结果，download 交付本机生成的 CSV/JSON/文本/PNG（有限内容、简单文件名，最多 64 MiB）。新增 refresh-view RPC 仅供原分析可更新展示：保存状态后调用既有展示生成接口并刷新记录，不重新运行分析或读取来源；归档/数据集只读展示拒绝更新。每条 RPC 带字符串 id，宿主验证消息来源为当前 iframe，返回 kind=response、同 id 和 value/error。扩展不调用工作台 API，也不读取宿主 DOM；原生单位、映射与计算规则不进入宿主。

通用 API：POST analyses/{id}/presentation 生成；GET presentation/{version}/files/{path} 只读声明文件；GET/PUT presentation/{version}/state 读写独立展示设置。均位于 `/api/extension-host/`。设置每次保存写独立版本，不改原分析来源、选项、产物或科学状态。分析可返回 presentation、presentation_supported、last_checked_at、refresh_message、refresh_busy；界面显示核对进度、完成/错误反馈和最后核对时间。
# 可选的阶段分析模式（2026-10-20）

v2 清单可追加 `analysis_modes`：非空列表，每项包含 `id`、`title`、`stages`、`input_roles`、`evidence`。模式 ID 唯一，各模式阶段不重叠且共同覆盖清单 stages；输入角色引用清单 inputs 中的角色。无此字段时完全沿用原合同。

宿主按本次来源的 stage 选择同一模式；混合模式来源报错。模式筛选输入角色，保留角色原 required 和大小限制；模式证据与清单公共 evidence 合并。上下文返回该模式的有效输入及阶段，检查、预览、冻结和执行采用同一规则。原始完整清单冻结在分析记录，来源、大小/mtime、解释器和预览身份核验不变。请求 schema 不增加业务字段，独立 CLI 仍可从 sources.stage 确定自己的算法。宿主不认识任何扩展 ID 或物理量。

兼容性：旧包不需要更新；声明 analysis_modes 的新包需要此版宿主。协议源码/校验/测试在 main，扩展分支只维护兼容文档与 schema 快照，不整体合并分支。
