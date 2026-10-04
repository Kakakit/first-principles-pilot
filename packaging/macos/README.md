# Mac 运行包

只需把 `FPilot-macOS-Test-20261004.zip` 传给另一台 Mac，完整解压后使用。它包含已经构建好的页面、后端和运行资源，不需要 Node，也不需要开发文件夹。

## 第一次使用

1. 使用已有的 64 位 Python，推荐 3.11；启动入口接受 3.9–3.12。不创建 Python 环境。没有合适解释器时先安装 Python 官方发行版。
2. 双击 `install-dependencies.command`。依赖可导入时直接跳过；缺失时向所选已有解释器安装锁定依赖，需要联网。
3. 双击 `check.command`。`ready: true` 表示运行依赖齐全；`port_available: true` 表示端口可用。
4. 双击 `start.command`。浏览器自动打开工作台，保留终端窗口；结束时按 Ctrl+C。
5. 在“设置”填写这台 Mac 的数据/POTCAR/研究目录，登记本机 SSH 别名和服务器程序路径，导入自己的脚本。

默认端口 8765。冲突时可在终端执行 `./start.command --port 8766`，进入设置保存新端口。启动失败保留错误信息，不关闭其他程序。

有多个 Python 时，在终端中指定已有解释器：

```sh
export FPILOT_PYTHON="/你的已有环境/bin/python"
./check.command
./start.command
```

保存设置后启动器优先使用机器配置中的 Python；`FPILOT_PYTHON` 可以覆盖。不要复制开发机的 `fpilot.local.json`，里面有该机器的绝对路径。

## 包内有什么

- 四个 `.command`：安装依赖、检查、启动、诊断。
- `backend/`、`dist/`、`resources/`：软件运行所需内容。
- `examples/` 和 `extension-templates/`：Si 结构示例与可复制的独立扩展模板。
- 首次启动后生成 `workspace-data/` 和 `fpilot.local.json`，分别保存这台机器的数据与设置。

开发测试、历史验收文档、node_modules、私人服务器脚本、项目数据库、POTCAR 和科研数据没有随包提供。软件分享与项目数据迁移分别处理；不要为了分享软件复制整个开发目录。

## 服务器与问题反馈

先在这台 Mac 的终端配置并验证 `ssh 你的别名 exit`，核对主机指纹并完成认证；工作台不填写密码、不复制密钥。服务器程序路径仍是 Linux `/...`，本机路径才是 Mac 路径。服务器登记仅保存用户配置，不继承开发机成功证据；执行前核对准确来源、资源与最终脚本。

同一远端依赖分支保持服务器绑定，UNKNOWN 核查原任务而不重投。POTCAR 由用户提供合法库；大型结果留在服务器分析，受控回传保持单文件 10 MiB 规则。新运行包从空工作区开始，不自动导入旧数据。

当前 Mac 已完成隔离启动与目录检查；其他 Mac 的 Python/架构、SSH 和真实集群流程仍需实测。遇到问题运行 `diagnostics.command`，将 `release-diagnostics.json` 与错误文本一起提供；诊断不读取密钥或项目内容、不连接计算服务器。
