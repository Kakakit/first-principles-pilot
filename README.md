# First Principles Pilot

个人第一性原理计算工作台：结构、完整流程画布、计算配置、运行监控与结果后处理。支持 VASP、Wannier90、链与参数迭代，保留五套主题和扩展。程序完成、数值收敛和科学结论分别记录。

## 源码与更新

源码保存在私有仓库 [Kakakit/first-principles-pilot](https://github.com/Kakakit/first-principles-pilot)。授权账号可克隆；后续修复按提交更新。[克隆、打包与同步说明](docs/GITHUB.md)。GitHub 源码需要 Node 构建，分享给普通使用者继续采用下方运行 ZIP。

## 安装使用

普通使用者下载 [GitHub Release](https://github.com/Kakakit/first-principles-pilot/releases) 的浏览器安装测试版：Windows x64 安装 EXE；Apple Silicon Mac 安装 DMG。首版暂不提供 Intel Mac。安装后点击应用图标，浏览器自动打开；自带 Python 与锁定依赖，无需 Node/Python/npm。首次仍须配置自己的 SSH、合法 POTCAR 与服务器脚本。详见 [安装与测试](docs/INSTALLATION.md)。

项目、配置和日志保存在独立用户数据目录，更新/卸载保留数据；旧开发目录不迁移。控制页提供停止、重启与退出，提交回执未完成时不强制停止。首版通过 Release 覆盖更新，安装测试版尚无商业签名证书，Windows/Mac 实机体验须进一步验收。

## 旧版运行 ZIP

已有 ZIP 保留供旧版本恢复。它们仍需要已有 Python，不属于新的自包含安装器。不要压缩整个开发文件夹分享。

- Mac：`FPilot-macOS-Test-20261004-r2.zip`（含理由输入卡顿修复）。解压后依次使用安装依赖、检查和启动三个 `.command`。[Mac 使用说明](packaging/macos/README.md)。
- Windows：`FPilot-Windows-Test-20261004-r3.zip`（含理由输入卡顿修复）；旧包保留。[Windows 使用说明](packaging/windows/README.md)。

运行包包含 `backend/`、预构建 `dist/`、`resources/`、示例与启动入口，不需要 Node。没有项目数据、POTCAR、私人服务器配置、开发测试、历史验收资料或 node_modules。

新设备使用自己的已有 Python、合法 POTCAR 库、SSH 配置和服务器脚本。不要复制开发机的 `fpilot.local.json`；在软件“设置”中填写这台机器的路径与服务器。分享软件和迁移项目数据是两个操作，运行包从空工作区开始。

## 开发目录怎么找

| 目录 | 用途 | 分享软件时复制？ |
|---|---|---|
| `src/` | 前端源码 | 否，包里使用构建结果 |
| `backend/` | 后端源码 | 打包工具自动选取 |
| `resources/` | 运行合同、解析器、扩展资源 | 打包工具自动选取 |
| `packaging/` | Mac/Windows 入口、依赖锁、打包工具 | 使用生成的 ZIP |
| `tests/` | 长期回归与模拟 fixture | 否 |
| `docs/` | 功能说明、验收证据；旧交接在 history | 否 |
| `scripts/` | 开发启动、基准、监控与诊断辅助工具 | 打包工具选取必要部分 |
| `workspace-data/` | 当前设备的项目、历史、冻结输入与缓存 | 软件分享时不复制 |
| `node_modules/`、`dist/` | 开发依赖、前端构建结果 | 不手动复制 |

`launch.py` 是共用运行入口，`fpilot.local.json` 是本机配置。Python 环境、POTCAR 和外部研究文件继续保留原位。

## 开发与打包

Mac 开发目录首次运行 `npm run dev:launcher` 生成唯一的 **`First Principles 工作台.app`**，以后双击即可打开控制页。入口源码放在 `packaging/macos/dev-launcher/`，生成的应用不进入 Git 或开发副本；可在 Finder 中创建替身放到桌面。备用入口是 `打开工作台.command`。

控制页提供“打开工作台、启动、停止、重启、日志”。重复打开复用原实例；关闭网页继续保留后台服务。停止和重启前确认项目已保存；停止只暂停本机监控与后续链推进，远端作业继续运行。正在上传／提交时等待回执完成，控制中心不强杀其他程序。默认控制页 `http://127.0.0.1:5174`，网页 5173、API 8000；`fpilot.local.json` 可配置独立的 `control_port`。

命令行仍可使用 `npm start`、`npm stop`、`npm run restart`、`npm run status`。启动器从本机配置或 `FPILOT_PYTHON` 读取已有解释器，worker 只推进已明确授权的计划。Python 环境按项目登记复用，不创建新环境。控制中心日志与身份保存在忽略的 `.fpilot-runtime/`，Vite 每个目录使用独立 `.fpilot-cache/`；这两项不包含在项目同步包内。这里的新入口适用于源码开发目录，已有无需 Node 的运行 ZIP 继续使用原 `start.command`／`start.cmd`。

已有前端 `dist/` 时可直接打包；只有改了前端才重新 `npm run build`。以下命令中的 `python` 应替换为登记解释器：

```sh
python packaging/build.py --target macos --output /你的输出目录/Mac
python packaging/build.py --target windows --output /你的输出目录/Windows
```

输出目录须为空，工具以白名单选择运行文件，生成 ZIP、文件清单和 SHA256。日常测试只检查改动范围；测试选择与精简交接规则见 [开发指南](docs/DEVELOPMENT.md)。

## 运行约定与说明

扩展管理按本地/服务器运行分类，同一扩展可支持两边。设置本地默认 Python、每台服务器默认 Python，或在扩展中单独指定已有环境；保存后检查依赖，运行时保留实际环境和历史。工作台启动环境不受影响。见 [使用与验收](docs/extensions-b/ENVIRONMENTS_ACCEPTANCE.md)。

首次配置按“SSH 别名 → 测试免交互连接、发现已有 Python → 导入任务脚本”完成。默认使用脚本中的环境、MPI 与程序命令，无需重新填写 setvars/MPI 路径；完整运行时登记仍可在高级方式中使用。

提交顺序是选择冻结输入 → 选服务器与导入脚本、编辑基础字段 → 检测节点并选择资源 → 准备并确认一次。检测后修改名称、walltime、核数或同服务器脚本会保留节点选择，更新最终预览；派发前自动实时复核。计算来源在配置中冻结，UNKNOWN 核查原 job ID，不自动重投。同一远端文件依赖分支保持服务器绑定；独立分支可分别选择服务器。

新建项目时填写“远端项目目录名”：默认从项目名称生成，也可手动修改，窗口直接预览默认服务器的完整项目路径。导入副本自动使用独立目录名，保留原项目和历史 attempt。已有远端派发或 UNKNOWN 记录后目录名锁定；仅本地准备且确认未派发的记录不永久锁定目录名。

本机数据默认在 `workspace-data/`，也可由设置或 `FPILOT_WORKSPACE` 指定。远端唯一布局仍为：

```text
$HOME/fpilot/material-projects/<project-slug>/flows/main/stages/<NN>-<stage-slug>/attempts/<timestamp>-<suffix>/
```

新服务器默认使用 `$HOME/fpilot`；可选择包含 `fpilot` 的专用绝对目录，原服务器的登记根目录与旧 attempt 不迁移。只读检查不创建目录，正式确认后才创建本次运行目录。已有 attempt 的准确来源、路径与冻结策略保持；小文件使用受控入口，大于 10 MiB 的结果留在服务器处理。回收在本机隐藏对象并暂停后续派发，恢复后须手动继续。原科研文件和服务器任务不随本机回收删除。

详细使用与证据按需查阅：[首次配置与脚本使用](docs/submission/SETUP_AND_SCRIPT_ACCEPTANCE.md)、[提交流程](docs/submission/ACCEPTANCE.md)、[能窗迭代](docs/wannier-window/README.md)、[扩展协议](docs/extensions-b/PROTOCOL.md)、[主题格式](docs/appearance/THEME_FORMAT.md)、[回收站](docs/trash-flow/ACCEPTANCE.md)、[远端性能](docs/remote-speed/ACCEPTANCE.md)、[D 包验收](docs/release-d/ACCEPTANCE.md)。历史长版说明保存在 [history](docs/history/README_through_20261004.md)。
