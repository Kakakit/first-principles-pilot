# PyW90 reference card

这是扩展作者使用的短参考卡，不是 PyW90 手册的复制品。扩展把 PyW90 当作
用户环境中的外部命令调用，算法和参数解释以用户安装版本的上游文档为准。

## 上游来源

- GitHub 项目：<https://github.com/Cloudiiink/pyw90>
- PyPI 发布页：<https://pypi.org/project/pyw90/>
- 复现锚点：PyPI `1.0.7`，发布日期 `2023-06-06`。
- sdist SHA256：`71b9ed6ff856d1859fda44b7a80a50ee62a2121355f98b1412958ea84bbf0a63`
- wheel SHA256：`9ee387090b6f934280f81e62a7ea97a1db71e3507645ddcf77cd67e0a97e90f9`
- 许可证：`GPL-3.0-only`。本扩展不打包、修改或再分发 PyW90 源码。

PyW90 的公开命令入口为 `pyw90 eig|pre|auto|cmp`。低层适配器只接受
`eig`、`pre`、`cmp`，把原生参数放在 `command` 数组中透传。`auto` 涉及自动
Wannier90 循环和调度器管理，留给 `hpc-job` 或用户直接调用。

扩展内部的 `fpilot.pyw90-window-scan-request.v1` 不是对上游 `auto` 的透传。它先从
同一规则 k 网格的 `.amn` 投影矩阵，或 `electronic-structure-characterization` 处理
PROCAR 后的逐 `(k, band)` CSV，确定所选投影轨道主要权重所在的目标能区；也允许
用户直接指定任意目标能区。随后使用 seed `.eig` 施加四个约束：outer 下界必须位于
目标能区下方的完整全局带隙，outer 在每个 k 点至少包含 `num_wann` 个态，frozen
完整覆盖目标能区，且 frozen 在任一 k 点的态数不超过 `num_wann`。它只生成有限
候选，用明确的本地 `wannier90.x` argv 执行，并用完成/收敛/血缘门槛和目标能区的
MAE/RMSE/max 排名。只有人工审核写入 `review.quality=poor` 时，才能在保持 gap 下界
不变的前提下生成下一轮局部候选。

双自旋能带比较应优先使用 `fpilot.pyw90-compare-request.v1`：它将两个 seed、
`.win/.wout/checkpoint`、KPOINTS/labelinfo、显式费米源、显示能区和指标能区绑定在
一张请求中。这个高层入口只消费已准备好的 band/labelinfo，不运行 VASP、
Wannier90 或重新局域化。

## 文件边界

VASP/Wannier90 接口文件应由 VASP/Wannier90 主流程准备。VASP 生成的
`wannier90.win` 已包含晶格、k-mesh 等信息，PyW90 扩展不重复生成这些内容。
`eig`/`pre` 的能窗和投影建议是分析输入，`cmp` 的差值是拟合一致性指标；它们
都不自动证明 Wannier 模型已经收敛、适合拓扑、Berry 相位或磁交换结论。
`.wout` 中的实际 outer/inner window 代表建模历史；当前 `.win` 不一致时，不能把当前
`.win` 或新的 PyW90 建议窗归因给既有 checkpoint。

## 扩展契约

`doctor` 检查命令是否可以启动并尽量识别版本；若 CLI 不提供版本信息，会尝试
读取运行本适配器的 Python 环境中的 `pyw90` 包版本，仍可能报告
`version: null`。调用外部命令时，如果环境尚未设置 `MPLCONFIGDIR`，适配器会先
创建 `tempfile.gettempdir()/fpilot-matplotlib-cache` 作为 matplotlib 缓存；已有
`MPLCONFIGDIR` 会原样保留。这只是启动性能设置，不
改变 PyW90 输入输出。低层 `check` 只检查本地文件和 argv，而高层 compare
`check` 还检查自旋、费米来源、路径 hash、band 数和 lineage。指标限定为用户声明
能区内、分段插值后、共用一个保序 DFT band offset 的配对差。这里不包含安装、
下载、远程提交、无限自动重试或结果物理解释。能窗扫描只允许本地有限执行；
在集群上运行时，候选目录的提交和调度仍必须通过 `hpc-job`。
