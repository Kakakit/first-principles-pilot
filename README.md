# First Principles Pilot 扩展开发

本分支 `extensions` 是独立扩展的唯一开发入口；主程序在 [main 分支](https://github.com/Kakakit/first-principles-pilot/tree/main)。本分支不包含主体 backend、React 应用、数据库、服务器配置或研究数据。

- [扩展目录与 ZIP 下载](extensions/README.md)：优先选择“工作台 ZIP 导入”的包。
- [扩展源码](resources/extensions/)：独立 CLI、参数、科学处理、展示、测试和许可证。
- [可复制模板](extension-templates/structure-statistics/) 与 [协议参考](docs/extensions-b/PROTOCOL.md)。协议以 main 的通用宿主文档为准，此处保留兼容快照。

## 独立开发

```sh
git clone --single-branch --branch extensions https://github.com/Kakakit/first-principles-pilot.git fpilot-extensions
cd fpilot-extensions
```

使用已有 Python。只运行所改扩展的独立测试，例如 TB2J：

```sh
python -m unittest discover -s resources/extensions/tb2j/tests -p test_v2.py
python -m unittest discover -s tests -p test_tb2j_display.py
```

仅在修改交互展示时需要 Node。`package-lock.json` 锁定 ECharts、esbuild、tsx 及所需依赖，不依赖主程序的 React/Vite：

```sh
npm ci
npm test
npm run build:tb2j
```

无需修改展示时直接使用包内已有 viewer.html；用户安装扩展不需要 Node。Python 第三方依赖由每个扩展的 README 和 doctor 说明，不自动安装或升级环境。

## 打包与安装

```sh
python scripts/package-extensions.py --extension tb2j
```

仅重建选中扩展的 ZIP，同时更新目录索引；不执行扩展、不发 SSH、不提交任务。省略 --extension 才重建全部扩展。ZIP 位于 extensions/packages，导入工作台“扩展管理”即可；源码目录也可直接导入。导入会生成独立安装副本，修改本分支不改变已安装版本，更新时重新导入。

源码、模板和测试均不导入 backend，不读取工作台数据库，不自行 SSH；宿主只负责普通 JSON 协议、环境、传输与执行。只改扩展时，不构建或回归整套主程序。

## 与 main 的边界

本分支独立提交，不合并整个分支到 main。main 中原有内置资源和发布包保留为兼容快照；旧能带、结构分析仍有直接引用，完整移除前需要专项解耦。本次不删除这些文件，不改变原来的安装入口。

新扩展和后续功能更新只维护本分支，通过 ZIP 或目录导入接入主程序。只有确实需要通用协议能力时，才单独修改 main 的宿主接口，并保留旧扩展兼容；不得添加按扩展 ID 分派的科学算法。

源码从已发布 main 提取，未纳入开发机尚未发布的其他扩展改动。许可遵循各扩展和第三方文件中的原有声明，不重新授权上游项目。
