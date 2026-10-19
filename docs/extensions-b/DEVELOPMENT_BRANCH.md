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

## 现有兼容资源

main 现有 `resources/extensions/`、`extensions/` 和模板保留为固定兼容快照，不再作为扩展开发入口。旧内置能带和结构分析仍有直接引用，这次不删除目录或改写其算法，避免破坏现有功能和安装包。新的扩展版本从 extensions 分支独立安装，不要求主程序跟随更新。

因此本次完成的是开发、验证与分发分离；旧内置功能的运行依赖尚未全部移除，不能将分支拆分描述成已完成全部历史代码解耦。协议的权威版本在 main，扩展分支保存便于开发的协议快照。

本次从 GitHub 已发布内容提取，未夹带本机尚未发布的其他扩展修改；保留原有许可、已安装包、环境、历史分析与研究数据。不连接服务器、不运行研究任务、不发布工作台安装器。
