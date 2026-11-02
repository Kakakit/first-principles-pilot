# 更新记录

## 1.1.0-beta.4 · 2026-11-02

本次发布 Apple Silicon macOS DMG。

- 整理导航与结构管理，可从任务 POSCAR / CONTCAR 提取独立结构，节点卡片列出实际继承文件。
- 自动保存草稿与检查点分离，支持命名检查点和恢复为新项目。
- 改进 INCAR 参数列表迭代与节点池等待 / 排队策略。
- 修复 Wannier 比较的起始带号、自动参考快照和部分带覆盖统计。
- 新远端扩展分析保存在来源计算项目的 analyses 目录；旧分析保留原位。
- 重写 README，增加贡献指南与 MIT License，并将许可证包含在发行包中。

安装与限制见 [安装指南](docs/INSTALLATION.md)，验证范围见 [beta.4 验收](docs/release-d/BETA4_ACCEPTANCE.md)。

## 1.1.0-beta.3 · 2026-10-23

- 发布 Apple Silicon DMG，内置 Python 与锁定依赖。
- 绿色工作台、融合流程画布、节点摘要、结构直达与冻结备注。
- 内置 PROCAR 投影解析，基础路径与能窗检查脱离 PyW90。
- 修复非有限证据 HTTP 500 与计算链画布遮挡。

历史版本详情见 [Releases](https://github.com/Kakakit/first-principles-pilot/releases)。
