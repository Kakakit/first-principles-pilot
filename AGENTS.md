# First Principles Pilot 开发约定

- 先读短 CHECKPOINT.md，再用 rg 定位当前任务；按需读取相关文档，不默认载入所有源码、测试和历史记录。
- 直接完成已授权工作，不启动子代理，不自动扩展工作包。中文沟通，保持完整画布、主题与既有功能。
- 复用用户指定且实际存在的已有 Python，通过 fpilot.local.json 或 FPILOT_PYTHON 配置；不创建环境。存在机器的 Python/HPC 登记文档时遵循其约定。
- 私人服务器配置、脚本、POTCAR、研究数据、工作区和缓存不入库。服务器在设置中登记，空白集群示例不提供可提交的账号、路径或成功证据。
- 远端任务先核对准确输入、资源和最终脚本；明确授权一次提交一个 attempt。UNKNOWN 核查原 job ID，不自动重投。完成、收敛和物理结论分别记录。
- 科研原件保留原位；清理应可恢复。大文件留在服务器处理，只通过受控入口回传每个明确、普通、已知大小且不超过 10 MiB 的文件。
- 检查与改动成比例。普通文案/布局不新增镜像用例；只运行相关验证，不重复已通过且未变化的检查或构建。核心来源、派发和数据清理需要针对性审核。
- 一次性验收脚本与旧截图归档在开发区之外。源码结构、同步排除及平台打包见 docs/DEVELOPMENT.md 和 docs/GITHUB.md。

- New attempts use the configured project/workspace root and flows/main/stages/.../attempts/... layout. Keep historical paths unchanged.
