# v1.1.0-beta.2 发布验收

- 发布时间：2026-10-15。发布页：https://github.com/Kakakit/first-principles-pilot/releases/tag/v1.1.0-beta.2
- 原生 CI：https://github.com/Kakakit/first-principles-pilot/actions/runs/38695529349 ，全部任务成功。
- 安装包源码：e80433bf8e8ad73ce6a782817d4139cf384318aa；699 个文件完整树核验，仅更新版本、安装说明与检查点。
- Windows x64：EXE 87,423,081 字节；实际中文路径安装、启动、原子 JSON 写入、停止/重启、卸载保留数据通过。
- Apple Silicon Mac：DMG 226,367,999 字节；原生应用启动、中文数据目录及 POSCAR 解析、单 worker、重复打开、停止/重启与数据保留通过。
- 两平台 native-startup 回执均为 PASS，real_ssh_requests=0。无研究任务提交或远端输入修改；本机正式服务未重启。
- 前端 136 项通过，类型检查与构建通过；安装器本机专项 8 项通过。
- 后端干净环境：基线 649 项／本版 699 项，70 个原有环境相关失败，新增失败 0。whole_suite_green=false；回归差异通过不等于所有测试通过。
- Release 共 10 个资产：两个安装包、各自 SHA256、两平台启动回执、三份回归 JSON 和安装说明，均为 uploaded。

本版包含轻量快照传输、服务器移除与恢复、能带 VBM/CBM、统一图标、GPU 诊断与可选卡数脚本、自由文本和矩阵生成前 pre dos。未完成的 PyW90 分析区间／能量注释编辑保留在开发目录，不进入发布树。

保留上次未签商业证书/Apple 公证的测试版范围，Windows 用户机器与其他 Mac 的安装及科学流程仍需实际验收。升级前保存草稿并退出旧工作台，覆盖安装保留用户数据。安装步骤见 [INSTALLATION.md](../INSTALLATION.md)。

私人发布回执与隔离源码副本：workspace-data/development-backups/release-beta2-20261015；不随软件发布。
