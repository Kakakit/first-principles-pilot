# 独立扩展源码

本目录是 extensions 分支的扩展开发源码；[分支使用说明](../../README.md)、[ZIP 与版本目录](../../extensions/README.md)、[创建模板](../../extension-templates/structure-statistics/README.md)。

每个包自行维护清单、CLI、参数、科学处理、展示、测试和许可。工作台 v2 包使用 extension.manifest.json 和独立 doctor/check/run；旧版 CLI 的安装方式在目录中标明。没有主体源码或数据库依赖，不自行 SSH。

协议以 [main 的通用协议](https://github.com/Kakakit/first-principles-pilot/blob/main/docs/extensions-b/PROTOCOL.md) 为准，本分支提供可复制快照。安装通过 ZIP 或目录导入，更新需重新导入，不会修改已完成分析的冻结记录。
