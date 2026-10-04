# First Principles Pilot

个人第一性原理计算工作台：结构、完整流程画布、计算配置、运行监控与结果后处理。支持 VASP、Wannier90、链与参数迭代，保留五套主题和扩展。程序完成、数值收敛和科学结论分别记录。

## 源码与更新

源码保存在私有仓库 [Kakakit/first-principles-pilot](https://github.com/Kakakit/first-principles-pilot)。授权账号可克隆；后续修复按提交更新。[克隆、打包与同步说明](docs/GITHUB.md)。GitHub 源码需要 Node 构建，分享给普通使用者继续采用下方运行 ZIP。

## 分享给别人

**分享运行 ZIP，不要压缩整个开发文件夹。**

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

开发时从机器配置或 `FPILOT_PYTHON` 读取已有解释器，执行 `npm start`，默认网页 5173、API 8000。启动器管理自己的 API/Vite/worker，不抢占已有端口；worker 只推进已明确授权的计划。Python 环境按项目登记复用，不创建新环境。

已有前端 `dist/` 时可直接打包；只有改了前端才重新 `npm run build`。以下命令中的 `python` 应替换为登记解释器：

```sh
python packaging/build.py --target macos --output /你的输出目录/Mac
python packaging/build.py --target windows --output /你的输出目录/Windows
```

输出目录须为空，工具以白名单选择运行文件，生成 ZIP、文件清单和 SHA256。日常测试只检查改动范围；测试选择与精简交接规则见 [开发指南](docs/DEVELOPMENT.md)。

## 运行约定与说明

提交顺序是选择冻结输入 → 检测空闲节点 → 修改基础资源 → 准备并确认一次。计算来源在配置中冻结，UNKNOWN 核查原 job ID，不自动重投。同一远端文件依赖分支保持服务器绑定；独立分支可分别选择服务器。

新建项目时填写“远端项目目录名”：默认从项目名称生成，也可手动修改，窗口直接预览默认服务器的完整项目路径。导入副本自动使用独立目录名，保留原项目和历史 attempt。已有远端派发或 UNKNOWN 记录后目录名锁定；仅本地准备且确认未派发的记录不永久锁定目录名。

本机数据默认在 `workspace-data/`，也可由设置或 `FPILOT_WORKSPACE` 指定。远端唯一布局仍为：

```text
$HOME/230269265lht/material-projects/<project-slug>/flows/main/stages/<NN>-<stage-slug>/attempts/<timestamp>-<suffix>/
```

已有 attempt 的准确来源、路径与冻结策略保持；小文件使用受控入口，大于 10 MiB 的结果留在服务器处理。回收在本机隐藏对象并暂停后续派发，恢复后须手动继续。原科研文件和服务器任务不随本机回收删除。

详细使用与证据按需查阅：[提交流程](docs/submission/ACCEPTANCE.md)、[能窗迭代](docs/wannier-window/README.md)、[扩展协议](docs/extensions-b/PROTOCOL.md)、[主题格式](docs/appearance/THEME_FORMAT.md)、[回收站](docs/trash-flow/ACCEPTANCE.md)、[远端性能](docs/remote-speed/ACCEPTANCE.md)、[D 包验收](docs/release-d/ACCEPTANCE.md)。历史长版说明保存在 history（原始验收资料保留在本机）。
