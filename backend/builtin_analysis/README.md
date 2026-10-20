# 主体内置分析

本目录属于 `main` 的主体代码，为现有内置页面和历史 v1 接口提供独立运行能力，不是可选扩展的源码或安装目录。

- `banddos_core/`、`banddos.py`、`extract_bounded.py`：内置 DFT/Wannier 绘图与有界提取。
- `comparison_metrics.py`：主体内带段误差，扫描比较直接调用纯数值函数，不加载扩展 CLI。
- `legacy/`：对称性、局域畸变、极化的 v1 兼容实现及其最小清单／样例。算法及合同字节保留，v1 revision 仍按原规则计算；旧登记、输入路径和历史结果不迁移。

提取基于拆分前 `main` 的固定实现，文件来源见 `origin.json`；保留原文件头。此目录不自动从 `extensions` 分支同步。主体功能修复在这里独立验证；可选扩展的算法、CLI、展示和 ZIP 均只在 `extensions` 分支维护，通过通用 JSON 协议导入。

不要从本目录生成 v2 扩展，不让 v2 宿主按扩展 ID 选择科学算法。删除或更新旧 `resources/extensions/` 不应改变主体行为。
