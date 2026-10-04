# 当前开发交接 · 2026-10-04

活动源：`/Users/kafka/Documents/dft可视化软件开发`。原插件只作历史参考。复用登记 Python；不创建环境，不启动子代理。

## 当前状态

- 后端 A、扩展 B、界面 C、Wannier 能窗迭代及远端性能改造已交付。完整画布、五主题、来源合同、候选池、服务器绑定、回收与 UNKNOWN 不重投保留。
- D 已交付首次设置与 Windows 测试包。本轮新增 Mac 运行 ZIP，平台入口集中在 `packaging/`，根目录 README 说明如何分享与定位源码。
- 12 个一次性脚本与派生缓存已在 D 包中可恢复归档；本轮只整理交付文件和文档，不改正式数据与科学执行逻辑。
- 本轮没有修改前端/后端业务代码，没有新增测试文件，不重跑无关全量测试或重新构建未变的前端。两平台打包清单通过；Mac 原生解压、检查、启动、空工作区、Ctrl+C 退出和诊断通过。业务、构建、测试与 2498 个正式数据文件按整理前后逐字节核对。
- 正式服务原为停止，保持停止；机器配置继续使用已有解释器和原外部受控回传脚本。没有新增远端操作。

## 2026-10-04：Wannier 理由输入卡顿修复

理由输入曾重复重建同页 fatband 图表。绘图组件隔离父表单更新，图表选项按绘图输入复用；草稿连续输入合并保存，离开页面/失焦保存。23 KB 真实冻结 .win + 模拟三图，输入间隔从约 240–270 ms 降到 6–8 ms；输入无网络请求。最新文字冻结、重新载入、绘图调节与 Safari 中英文输入通过。类型检查、生产构建、既有 plotAppearance 用例通过；Mac r2 / Windows r3 分享包已同步。只改两个前端组件；源冻结与远端数据未改，正式服务保持原状态。

## 接下来

1. 其他 Mac 使用 `07_其他项目/fpilot-releases/FPilot-macOS-Test-20261004-r2.zip`；Windows 使用已交付包或输入卡顿修复版 `FPilot-Windows-Test-20261004-r3.zip`。新设备配置自己的 Python、SSH、POTCAR 与程序路径，从空工作区开始。
2. Windows 原生和其他 Mac 的架构/SSH/实际集群流程仍待对应设备测试。模拟、调度结束、数值收敛和物理质量是不同证据。
3. 收到具体缺陷再修相关模块和用例；不按测试数量扩充验证。当前工作规范见 `docs/DEVELOPMENT.md`。

## 按问题读取

- 最近交付/清理：`docs/release-d/ACCEPTANCE.md`。
- 提交与来源：`docs/submission/ACCEPTANCE.md`；迭代：`docs/wannier-window/README.md`。
- SSH/绘图：`docs/remote-speed/ACCEPTANCE.md`。
- 完整历史与旧 job ID：`docs/history/CHECKPOINT_through_20261004.md`。仅在需要该问题证据时读取相应段落，不默认全文载入。
