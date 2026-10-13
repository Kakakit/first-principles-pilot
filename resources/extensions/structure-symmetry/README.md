# 空间对称性 · 2.0.0

新版工作台扩展（`fpilot.extension.v2`），支持单个结构或已有计算结果。本机与来源服务器运行均可；依赖已有 Python 环境中的 pymatgen、spglib。

在“扩展管理”导入对应 ZIP 并检查环境，选择 POSCAR/CONTCAR 后检查和运行。比较三个几何容差下的普通空间群、Wyckoff 位与等价原子，角度容差默认 5°。输入几何应与研究目标对应，初始 POSCAR 与最终 CONTCAR 不能混称。

结果说明几何对称性，不分析磁空间群，不证明电子/离子收敛或物理稳定性。入口 `scripts/cli.py` 遵循 [doctor/check/run 协议](../../../docs/extensions-b/PROTOCOL.md)，最小测试几何在 `fixtures/`。下载及 SHA256 见 [`extensions/`](../../../extensions/README.md)。
