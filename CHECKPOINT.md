# 当前开发交接 · 2026-10-05

活动源为本仓库；复用本机已有 Python，通过 fpilot.local.json 或 FPILOT_PYTHON 登记。原开发 Git 历史与个人研究记录不入库。

首次配置、脚本选择与提交编辑已实施：默认 SSH 别名 + 导入脚本，独立连接测试发现已有 Python，支持 ncl 分类，提交页直接选脚本。基本字段编辑保留同服务器节点信息并清除旧确认；派发前实时复核。新记录冻结实际选择，旧记录保持合同，UNKNOWN 不重投。

新服务器默认 $HOME/fpilot，原根和旧 attempt 不迁移；只读检查不创建目录。原稿、副本来源、PBS 未编辑资源与精确路径/所有权保护保留。复杂 GPU、多节点和混合线程布局不扩展支持。

Python 360 项、前端 64 项、类型检查和生产构建通过。空白独立浏览器/API 模拟验收通过，编辑期间远端请求增量 0、繁忙未派发、唯一模拟 job 回执通过。验收不涉及真实集群或科学结论。正式服务按原配置加载更新，worker 开关保留。

方案和验收见 docs/submission/SETUP_AND_SCRIPT_PLAN.md、SETUP_AND_SCRIPT_ACCEPTANCE.md。运行 ZIP 未重发，新设备从最新源码构建/打包；Windows 原生与具体真实任务仍需相应验收和授权。

同步始终基于远端 main 的准确父提交，只发布筛选过的源码/说明。不上传工作区数据、POTCAR、SSH 配置/密钥、私人服务器脚本或旧本地 Git 历史，不强推。详细规则见 docs/GITHUB.md、docs/DEVELOPMENT.md。
