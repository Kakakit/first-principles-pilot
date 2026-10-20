# 扩展独立开发分支

扩展的唯一开发入口是同仓库的 [extensions 分支](https://github.com/Kakakit/first-principles-pilot/tree/extensions)，[扩展源码](https://github.com/Kakakit/first-principles-pilot/tree/extensions/resources/extensions)、[ZIP 目录](https://github.com/Kakakit/first-principles-pilot/tree/extensions/extensions) 与 [创建模板](https://github.com/Kakakit/first-principles-pilot/tree/extensions/extension-templates/structure-statistics) 均在该分支。主体后端、前端和通用协议继续在 main 开发。

## 开发与安装

使用独立目录，保留当前主程序和已安装扩展：

```sh
git clone --single-branch --branch extensions https://github.com/Kakakit/first-principles-pilot.git fpilot-extensions
cd fpilot-extensions
```

扩展只通过普通 JSON 文件和独立 CLI 接口接入，不导入 backend，不读取主程序数据库，不自行 SSH。扩展自己的参数、科学处理、分组、排序、轨道标签、绘图及测试均随包维护。Python 使用已有环境；仅修改展示时使用该分支锁定的独立 Node 工具。

只测试修改的扩展，按分支 README 打包：`python scripts/package-extensions.py --extension tb2j`。ZIP 或源码目录从工作台“扩展管理”导入；已安装扩展是独立副本，源码更新后需要重新导入，历史分析仍使用冻结配置。标记“旧版 CLI”的包按各自说明运行，不当成 v2 ZIP 导入。

扩展提交只更新 extensions 分支，不把整个分支合并回 main。通用协议确需变化时，另在 main 单独修改并兼容旧包；主程序不按扩展 ID 分派科学算法。两个分支的提交、测试和分发分别进行。

## 主体运行边界

现有内置能带、结构和极化功能由 main 自有的 `backend/builtin_analysis/` 提供，保留固定算法和 v1 合同，正常运行不再读取 `resources/extensions/`。窗口误差比较直接调用主体的纯数值函数，不启动扩展扫描 CLI。

v2 宿主只枚举已导入的独立包，不扫描旧源码或从旧快照安装。已安装包、启停配置、冻结副本和历史结果保持；新工作区通过扩展管理导入 ZIP 或独立目录。旧客户端调用内置安装接口时，会得到当前导入方式的说明。

main 的 `resources/extensions/`、`extensions/` 和 `extension-templates/` 仍作为历史快照保留，移除它们不影响主体导入和内置运行；主体打包也不携带这些目录。main 的旧打包命令仅提示切换至 extensions 分支，避免分发过期快照。

宿主测试使用 main 自有的通用协议 fixture；扩展科学算法与展示测试只在 extensions 分支运行。主体的内置兼容算法有自己的回归，两边不自动同步。协议的权威版本在 main，扩展分支保存便于开发的协议快照。解耦验证和兼容细节见 [验收记录](HOST_DECOUPLING_ACCEPTANCE.md)。

本次从 GitHub 已发布内容提取，未夹带本机尚未发布的其他扩展修改；保留原有许可、已安装包、环境、历史分析与研究数据。不连接服务器、不运行研究任务、不发布工作台安装器。
