# 可运行扩展模板：结构体积与元素统计

> 扩展开发已移至同仓库的 [extensions 分支](https://github.com/Kakakit/first-principles-pilot/tree/extensions)。此处保留主程序兼容快照；新版本与 ZIP 请从该分支获取。

独立 Python CLI，仅依赖标准库，Python >=3.9。不导入主体模块、不访问数据库、不提交任务。

1. 复制本目录；修改 `extension.manifest.json` 中的 `id`、名称、版本与说明。
2. 根据输入角色修改 `scripts/cli.py` 的 `check` 和 `analyze`；参数由 `options_schema` 描述。
3. 用已有 Python 执行 `python scripts/cli.py doctor`。
4. 执行 `python tests/test_standalone.py`；它生成独立请求，调用 check/run 并核对 JSON/CSV、体积和原文件不变。
5. 工作台“扩展管理”填写本目录绝对路径，点击“导入并启用”；也可将目录压缩成 ZIP 导入。
6. 结构页“结构分析”选择扩展，填写必要参数后运行。停用/卸载不删除历史报告。

扩展管理按“本地运行 / 服务器运行”筛选；同一个包可以支持两边。在“Python 环境 · 默认或单独指定”选择位置，本地继承本地默认，服务器继承该主机默认，也可保存扩展自己的 Python 覆盖。恢复默认会清除对应覆盖；路径不写进包。未设置本地默认时沿用工作台解释器，扩展设置不会改变工作台启动环境。

保存后点击“检查所选环境”。远端检查只上传小扩展包并执行 doctor，不执行分析或提交作业。模板 doctor 实际导入 dependencies 中的模块，并分别返回解释器路径、Python 版本和可取得的依赖版本；缺依赖时仍保持启用与参数编辑。正式 doctor/check/run 使用同一选定 Python。修改环境须重新检查或预览，历史分析保留执行时的环境。直接调用已有 Python 的绝对路径，无需 activate，不创建环境。

CLI 用法：`python scripts/cli.py check --request /absolute/request.json`，`run` 同样接受请求文件。请求使用 `fpilot.extension-request.v2`；结果使用 `fpilot.extension-result.v2`，包括 summary、warnings、单位、表格/曲线、输出文件和来源。完整协议见主体 `docs/extensions-b/PROTOCOL.md`，其 schema 也随模板位于 `protocol/`。

修改输出时仅写入请求的 `output_dir`，结果文件写到 `result_file`，读取 `inputs.<role>[i].path`；不能猜测作者路径或读取工作台内部数据。远端运行使用用户保存的已有 Python 路径；缺依赖仅报告，不创建环境或自动升级。

输入角色可增加 `required:false`，默认仍为 true。可选角色的数组可能仅含部分来源，或整个键缺省；用每项 `source_id` 与 `sources[].id` 关联，不能直接把数组下标当作来源下标。执行配置可增加 `preferred_location`，缺省保持本机，显式值必须在 `execution.locations` 中。仅需服务器命令行的扩展可声明 `locations:["ssh","local"], preferred_location:"ssh"`，由宿主执行并等待，无需扩展自行 SSH 或提交作业。


可选交互展示：清单添加 `presentation:{"entrypoint":"scripts/presentation.py"}`，独立实现 `render --request`。读取 source_dir 已有结果，只向本次 output_dir 写入 HTML 与数据分片，返回 `fpilot.extension-presentation.v1` 文件清单；schema 位于 protocol/presentation.schema.json。HTML 内联所需资源，通过通用 postMessage 读取清单 JSON、保存独立设置或下载，不导入主体组件。详见主体协议“可选独立交互展示”；TB2J 的 scripts/presentation.py 与 viewer/ 是实际示例。无需交互的扩展继续使用现有 table/curve，不必实现此入口。

### 可选本机结果展示

支持在 presentation 声明 `location: "local"` 和 `inputs: [{path: "elements.csv"}]`。宿主只复制已回传的小文件，以本地扩展 Python 调用独立 render；缺失或超限给出错误，不自动连接服务器。缺省 source 保持旧行为。展示包、解释器及输入副本单独记录，原分析冻结配置不变。常用/高级分区可通过 ui_schema 的 ui:groups 声明，详见通用协议。
