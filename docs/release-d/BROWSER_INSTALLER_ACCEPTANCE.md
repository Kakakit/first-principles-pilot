# 浏览器安装测试版实施与验收

## 交付范围

使用同一份浏览器界面和本机 Python 后端，发布 `1.1.0-beta.1` 的 Windows x64 EXE 与 Apple Silicon Mac DMG。按用户取舍，Intel Mac 不进入首版发布矩阵。Windows 使用 Inno Setup，Mac 使用原生应用目录和 DMG；CPython 上游下载 URL、SHA256 与 Python 依赖版本固定，第三方原始许可证保留。

使用者安装后点击图标，默认浏览器打开工作台，无需 Node、Python 或 npm。控制页提供启动、停止、重启、退出和日志；重复打开复用当前实例。后台仅监听本机，停止只等待自己创建的进程和操作；更新/卸载需先退出，用户数据独立保留。新安装从空工作区开始，原开发工作区、冻结输入和远端任务不自动迁移。

## 已实施的跨平台修复

- 增加独立用户目录和安装运行时，避免依赖开发机绝对路径；Windows 中文和空格路径由实际安装与 API 导入检查。
- 关闭临时 JSON 文件后再原子替换，修复 Windows 文件句柄阻止 worker 状态和执行设置写入的问题；替换失败保留原记录并清理临时文件。
- POSIX 端口检查与 Uvicorn 使用相同的复用策略，支持干净停止后的立即重启；仍拒绝占用中的监听端口。
- 控制中心拒绝中途停止派发，停止时等待 worker 当前操作，禁止安装器自动强杀进程。控制页突变请求核对本机 Host、Origin 和一次实例凭据。
- Windows 中文安装文本来自 Inno Setup 官方语言文件，保留作者信息；应用、停止、诊断和卸载入口进入开始菜单。

## 验证及证据边界

本机控制/归档/真实端口专项 7 项和 JSON 替换专项 2 项通过。公开副本全量 655 项（3 跳过）已执行；两项沙箱监听限制在允许本机监听后复核通过，前端 126 项、类型检查和构建通过。实际 Mac 自包含运行时通过 API、页面资源、Si POSCAR 解析、中文用户目录、健康 worker、重复打开、停止/重启和数据保持。浏览器验证板块切换、控制页确认取消与重启。

最新 [原生发布流水线](https://github.com/Kakakit/first-principles-pilot/actions/runs/38400942242) 的 Windows 实际安装/启动/卸载、Apple Silicon 启动以及发布全部成功。Windows 捆绑 Python 实际执行 JSON 替换专项 2 项通过，真实 worker 健康、安全停止/重启与数据保持通过；卸载后用户数据仍存在。公开树 629 个文件核对通过，只增量发布安装器范围，研究数据、SSH 配置、POTCAR 和私人验收回执未上传。

干净 GitHub 克隆的历史测试依赖开发机登记、私人示例和额外扩展环境，因此采用安装改动前公开主分支作完整回归对照。任何新增失败阻止发布；已有失败保留为失败，`whole_suite_green` 不伪造为真。Release 的 `clean-regression.json` 与两个 `*-native-startup.json` 分别记录回归差异和原生启动结果。

所有安装验收使用一次性独立工作区，不发出 SSH 或调度提交。打包/API/worker 通过不等于完整远端计算验收。用户 Windows 10/11 实机、SSH 配置和实际科学流程继续按 [安装测试步骤](../INSTALLATION.md) 验收。首版未签商业证书或作 Apple 公证，安全提示及系统兼容限制在安装说明中明确保留。

## 当前结果

[v1.1.0-beta.1 预发行版](https://github.com/Kakakit/first-principles-pilot/releases/tag/v1.1.0-beta.1) 已发布：Windows EXE 约 83 MiB，Apple Silicon DMG 约 211 MiB。10 个发布附件状态均为 uploaded，包括两种安装器、对应 SHA256、两平台原生启动回执、三个回归 JSON 和安装说明。没有 Intel Mac 产物。

干净克隆完整对照为基线 649 项、当前 658 项，70 项既有环境相关失败保持，新增失败为零，`whole_suite_green=false` 如实保留。前端全量和构建通过；原生安装/启动回执均为 PASS，SSH 请求为零。用户 Windows 实机的完整使用验收尚待反馈。
