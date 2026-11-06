# 扩展统一发布目录

这里集中管理独立扩展的版本和 ZIP。源码只在本仓库 `extensions` 分支的 [`resources/extensions/`](../resources/extensions/) 维护，主体宿主位于 main。本目录由 `scripts/package-extensions.py` 生成。

| 扩展 | 版本 | 接口 | 下载 | 源码与说明 |
|---|---|---|---|---|
| 配位多面体畸变 | 2.2.0 | 工作台 ZIP 导入 | [ZIP](packages/crystal-structure-distortion-analysis-2.2.0.zip) | [README](../resources/extensions/crystal-structure-distortion-analysis/README.md) |
| electronic-structure-characterization | 0.3.0 | 旧版 CLI | [ZIP](packages/electronic-structure-characterization-0.3.0.zip) | [README](../resources/extensions/electronic-structure-characterization/README.md) |
| 形式极化与双结果比较 | 2.2.0 | 工作台 ZIP 导入 | [ZIP](packages/lcalcpol-polarization-analysis-2.2.0.zip) | [README](../resources/extensions/lcalcpol-polarization-analysis/README.md) |
| 轨道占据、能级与劈裂分析 | 2.4.3 | 工作台 ZIP 导入 | [ZIP](packages/local-crystal-field-analysis-2.4.3.zip) | [README](../resources/extensions/local-crystal-field-analysis/README.md) |
| 压力结构与 EOS 分析 | 1.0.1 | 工作台 ZIP 导入 | [ZIP](packages/pressure-structure-eos-1.0.1.zip) | [README](../resources/extensions/pressure-structure-eos/README.md) |
| pyw90 | 0.5.1 | 旧版 CLI | [ZIP](packages/pyw90-0.5.1.zip) | [README](../resources/extensions/pyw90/README.md) |
| 空间对称性 | 2.0.0 | 工作台 ZIP 导入 | [ZIP](packages/structure-symmetry-2.0.0.zip) | [README](../resources/extensions/structure-symmetry/README.md) |
| 结构体积与元素统计 | 2.0.0 | 工作台 ZIP 导入 | [ZIP](packages/structure-volume-statistics-2.0.0.zip) | [README](../resources/extensions/structure-volume-statistics/README.md) |
| TB2J 交换参数与轨道分解 | 2.4.1 | 工作台 ZIP 导入 | [ZIP](packages/tb2j-2.4.1.zip) | [README](../resources/extensions/tb2j/README.md) |

## 安装与更新

- 标记“工作台 ZIP 导入”的 7 个包：打开工作台“扩展管理” → 导入 ZIP，选择所需 ZIP；登记已有 Python 环境并检查依赖。GitHub 的 ZIP 页面选择 Download raw file 下载原文件。ZIP 不带 Python 或第三方运行环境。
- 标记“旧版 CLI”的 2 个包尚未提供新版工作台安装清单；解压后按各自 README 使用，不承诺可以从新版扩展管理直接导入。
- EOS 的冻结重新拟合需要宿主支持 `refresh-view` 和 `view_state`；本次源码同步包含该通用接口。旧安装器不会因下载 ZIP 自动升级宿主，请使用包含本次提交的工作台源码版本。其他依赖和科学边界见各包 README。
- 轨道分析 2.4.3 需要宿主支持可选的 `analysis_modes` 清单字段。安装前请确认工作台包含该协议支持。
- 更新已有分析的交互界面时使用“更新结果展示”；不改原始研究输入，不自动重新运行分析或研究计算。

## 可复现管理

从仓库根目录使用已有 Python 3.9+ 执行 `python scripts/package-extensions.py --extension tb2j`，只重建选中包；省略选项重建全部。生成固定排序、时间戳的 ZIP，导出说明和旧清单中的开发机路径改为可移植占位；原开发源保持。不执行扩展、不连接服务器、不安装依赖。`catalog.json` 保存接口、版本、下载路径、ZIP SHA256 及包内各文件 SHA256，校验字段仅供机器核对，不作为界面标识。

包内包括代码、说明、第三方许可和最小测试样例；不包括研究任务目录、POTCAR、SSH 配置、密钥、虚拟环境或运行缓存。保留各第三方许可；本目录不是对上游代码重新授权。
