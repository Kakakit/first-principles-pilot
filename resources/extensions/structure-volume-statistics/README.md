# 结构体积与元素统计 · 2.0.0

新版工作台扩展（`fpilot.extension.v2`），支持单个结构或已有计算结果。本机或来源服务器解析，只有 Python 标准库依赖。

从“扩展管理”导入 ZIP，选择 POSCAR/CONTCAR 后检查并运行。输出晶胞体积（Å³）、原子总数、元素数量、`statistics.json` 和 `elements.csv`；元素明细可关闭。支持 VASP5/6 元素标记和单一比例因子，包括负目标体积；不支持三分量比例因子，缺失或不完整坐标会拒绝。

这是几何统计，不确认 DFT 输入合理、计算结束或数值收敛。入口 `scripts/cli.py` 遵循 [doctor/check/run 协议](../../../docs/extensions-b/PROTOCOL.md)。最小样例在 `fixtures/`，测试运行 `python -m unittest discover -s tests`。下载及 SHA256 见 [`extensions/`](../../../extensions/README.md)。
