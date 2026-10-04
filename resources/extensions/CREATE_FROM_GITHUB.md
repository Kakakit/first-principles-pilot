# 从 GitHub 项目创建扩展

这是未来 AI 创建新扩展时的唯一操作清单。目标是一个人能读懂、复制和运行
的薄适配器，不要把主插件改造成通用工作流引擎。

## 七件套

1. **调研来源**：确认项目主页、实际命令/API、输入输出、许可证和一个可复现的
   版本锚点（release、tag 或 PyPI artifact hash）。把短结论写入
   `references/<project>.md`，不要复制上游源码或大段手册。
2. **复制骨架**：复制 `_template/` 为小写连字符目录，保留
   `extension.json`、`README.md`、`references/`、`examples/`、`scripts/`、
   `tests/`、`tests/fixtures/` 七个区域。
3. **写薄适配**：用标准库提供 `doctor`、`check`、`run`。`doctor` 只检查外部
   命令是否可调用；`check` 只检查请求和本地输入；`run` 用 argv 列表调用用户
   已安装的工具并保存 stdout/stderr/回执。不要自动安装、下载、提交调度任务、
   重试或解释科学结论，除非另有明确扩展需求。
4. **写契约**：在 manifest 和 README 中说明 request schema、必需输入、输出
   文件、相对路径基准、单位/命名约定及成功条件。模块通过 `requires`/
   `provides` 声明 artifact role，不依赖另一个扩展的目录名。
5. **加最小测试**：使用小 fixture 和假的外部可执行文件覆盖正常检查、缺输入、
   外部命令失败/成功、回执和相对路径。优先标准库 `unittest`，不需要真实大型
   计算才能测试适配层。
6. **验证并登记**：在扩展根目录运行测试；再运行
   `fpilot_extensions.py validate <path>`。确认 source hash、测试路径和
   manifest 都通过后，计算 directory hash，才把 name/version/status/hash 加入
   `registry.json`。
7. **真实验收**：在目标环境用用户实际安装的上游版本跑一次小案例，保存可读的
   命令/版本/输入输出记录，再决定保持 `experimental` 还是升级为 `ready`。

## 最小 manifest 约定

命令型模块把 `module_type`、`requires`、`provides`、`command` 四项一起写入
`extension.json`。建议入口统一为：

```bash
python3 scripts/<name>.py doctor --executable <tool>
python3 scripts/<name>.py check --request request.json
python3 scripts/<name>.py run --request request.json
```

脚本只负责本地适配；VASP、Wannier90、TB2J 等大型计算或后处理仍由主体流程
和各自扩展负责。TB2J 以及本目录的 `pyw90` 都是可参考的完整例子。
