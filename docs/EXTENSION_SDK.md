# 本地扩展合同 v1

扩展位于 `resources/extensions/<id>/`，以 `extension.sdk.json` 为运行合同。`_template` 只供复制，不会被发现或运行。旧 `extension.json` 保留供原两项扩展使用；两者分别通过 `legacy-distortion` 和 `legacy-lcalcpol` 适配器接入。`structure-symmetry` 展示 `cli-v1` 用法。

## 合同与验证

- `input_selectors` 声明文件名和每个文件的最大字节数，上限 10 MiB。当前 `cli-v1` 只接收一个选择器，结构文件来自所选 attempt 根目录的 POSCAR 或 CONTCAR。
- `applicability.result_schema` 标识适用性回执；`outputs.schema` 和唯一的 `outputs.files` 标识 JSON 输出。扩展成功退出但缺少文件或 schema 不符时，analysis record 为 `FAILED`。
- `dependencies.python_modules` 由已登记 Python 环境检查；`runtime` 仅接受 `registered-python` 和 1–600 秒超时；`side_effects` 仅接受本地读写。网络操作和远程提交不属于此合同。
- `entrypoint` 与 `fixtures` 必须是扩展目录内的普通文件。validator 计算合同、入口、fixture 和旧清单的版本摘要。文件改变后需重新安装、重新启用。
- 使用已登记解释器调用 `backend.extension_sdk.validate('<id>')`，并运行对应 fixture 测试。安装接口 `POST /api/extensions/<id>/install` 只登记当前版本；启用接口 `PUT /api/extensions/<id>/enabled` 的请求体为 `{"enabled":true}`。安装本身不启动分析。

## CLI v1

宿主在独立的本地 analysis 目录复制经 attempt 路径与大小检查的输入，再执行 `python <entrypoint> check --request <absolute-request.json>`。适用时 stdout 为一个 JSON 对象，含合同指定 schema 与 `applicability_state: "APPLICABLE"`。不适用时非零退出，stderr 给出原因。只有启用、文件就绪、适用性通过后可运行 `run --request`。

请求 JSON 包含 `source_file`、`source_name`、`options`、`output_file`。路径均由宿主生成；扩展应自行检查选项与输入内容。`run` 成功时在指定路径写入合同指定 schema 的 JSON，并在 stdout 返回含该 schema 的 JSON 回执；可附 `summary` 供页面展示。失败退出、超时或输出合同不符均写入独立 analysis record。原计算文件保持原位。

界面分别标识安装/启用状态、适用性、程序成功/失败和科学审核状态。对称性扩展的 `NOT_PERFORMED` 表示没有完成材料物理结论审核。
