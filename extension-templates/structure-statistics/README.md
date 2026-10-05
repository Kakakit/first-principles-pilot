# 可运行扩展模板：结构体积与元素统计

独立 Python CLI，仅依赖标准库，Python >=3.9。不导入主体模块、不访问数据库、不提交任务。

1. 复制本目录；修改 `extension.manifest.json` 中的 `id`、名称、版本与说明。
2. 根据输入角色修改 `scripts/cli.py` 的 `check` 和 `analyze`；参数由 `options_schema` 描述。
3. 用已有 Python 执行 `python scripts/cli.py doctor`。
4. 执行 `python tests/test_standalone.py`；它生成独立请求，调用 check/run 并核对 JSON/CSV、体积和原文件不变。
5. 工作台“扩展管理”填写本目录绝对路径，点击“导入并启用”；也可将目录压缩成 ZIP 导入。
6. 结构页“结构分析”选择扩展，填写必要参数后运行。停用/卸载不删除历史报告。

CLI 用法：`python scripts/cli.py check --request /absolute/request.json`，`run` 同样接受请求文件。请求使用 `fpilot.extension-request.v2`；结果使用 `fpilot.extension-result.v2`，包括 summary、warnings、单位、表格/曲线、输出文件和来源。完整协议见主体 `docs/extensions-b/PROTOCOL.md`，其 schema 也随模板位于 `protocol/`。

修改输出时仅写入请求的 `output_dir`，结果文件写到 `result_file`，读取 `inputs.<role>[i].path`；不能猜测作者路径或读取工作台内部数据。远端运行使用用户保存的已有 Python 路径；缺依赖仅报告，不创建环境或自动升级。

输入角色可增加 `required:false`，默认仍为 true。可选角色的数组可能仅含部分来源，或整个键缺省；用每项 `source_id` 与 `sources[].id` 关联，不能直接把数组下标当作来源下标。执行配置可增加 `preferred_location`，缺省保持本机，显式值必须在 `execution.locations` 中。仅需服务器命令行的扩展可声明 `locations:["ssh","local"], preferred_location:"ssh"`，由宿主执行并等待，无需扩展自行 SSH 或提交作业。
