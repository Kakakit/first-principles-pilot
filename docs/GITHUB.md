# GitHub 源码与更新

私有仓库：https://github.com/Kakakit/first-principles-pilot 。仅获授权账号可访问。首个源码提交包含当前完整业务代码、长期回归用例、扩展、必要说明和跨平台打包入口；没有导入旧开发 Git 历史。

2026-10-05 的源码更新包含默认脚本方式服务器配置、提交页脚本选择和编辑保留节点；使用与验收见 [本轮记录](submission/SETUP_AND_SCRIPT_ACCEPTANCE.md)。已有运行 ZIP 不会自动随源码更新，其他设备更新后按下面步骤重新构建/打包。

## 普通使用者下载安装器

打开 [GitHub Releases](https://github.com/Kakakit/first-principles-pilot/releases)，下载 Windows x64 EXE 或适配 Mac 架构的 DMG。新安装器内置 Python 和锁定依赖，使用者无需克隆仓库、安装 Node 或运行 npm。源码 ZIP 不等于安装包；旧便携 ZIP 保留原流程。[安装与验收](INSTALLATION.md)。

`.github/workflows/browser-release.yml` 用同一份源码完成 Linux 回归、Windows 和两种 Mac 架构的原生构建/启动检查；全部通过才发布预发行版。修改 `packaging/release.json` 的版本后运行，既有同名 Release 不覆盖。Python 上游包锁定下载 URL 和 SHA256；发布资源不包含项目、SSH 配置、POTCAR 或私人脚本。首版安装器未作商业签名/公证。

## 新设备开发或打包

安装 Git、Node.js 20.19+，并选择已有 Python（推荐 3.11）。使用已登录的 GitHub Desktop 克隆，或在已配置 GitHub 认证的终端执行：

```sh
git clone https://github.com/Kakakit/first-principles-pilot.git
cd first-principles-pilot
npm ci
npm run build
python -m pip install -r packaging/requirements-runtime.txt
python launch.py --check
```

`python` 必须替换为该机器选定的已有解释器。不复制开发机的配置、环境和数据。直接运行使用 `python launch.py`；前后端开发使用 `npm start`。源码中的平台启动脚本供打包工具选取，运行 ZIP 内才是根目录双击入口。

已有 `dist/` 后生成供普通使用者运行的包，无需使用者安装 Node：

```sh
python packaging/build.py --target macos --output /你的空输出目录/Mac
python packaging/build.py --target windows --output C:/你的空输出目录/Windows
```

按所在平台选一条。Mac/Windows 使用步骤见 packaging/ 下各自 README。Windows 原生验收仍待测试，不把本机检查当成跨平台验收。

## 更新与本机数据

新的干净克隆可以用 `git pull --ff-only`，前端改动后执行 `npm ci` 和 `npm run build`；依赖变化时按锁文件向已有 Python 安装。更新前正常停止工作台，保留原 worker 开关，随后按原设置启动。`workspace-data/`、`fpilot.local.json` 和本机环境不随代码更新覆盖。

服务器在软件设置中登记，保存在本机数据目录。可选的内置登记表 `resources/preflight/hpc_cluster_registry.json` 不入库；缺少时读取空白 `.example.json`。私人 PBS/Slurm 原稿从设置导入，不提交到仓库。POTCAR、SSH 密钥、实际研究结果、缓存、node_modules、dist、运行 ZIP 和旧验收截图不入库。

当前开发机仍保留旧本地 Git 基线与未提交改动。此次通过 GitHub 插件发布经过筛选的当前源码；后续插件同步以远端 main 的准确提交为父提交，使用相同排除规则，不强推旧本地历史。若改用 Git CLI 日常开发，请从此私有仓库新克隆开始，不在旧开发目录直接 push/pull 两套不同历史。

仅修改需要的模块，运行对应检查。正式来源和远端 attempt 不随代码同步迁移。新设备的 Python/SSH/POTCAR 路径在设置中单独配置。
