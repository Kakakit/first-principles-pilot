# 数据传递第一阶段独立 debug 验收

日期：2026-11-12。

## 结论与实际范围

当前交付是第一阶段：批次清单 ZIP、只读 ArtifactRef 查询/解析、前端下载入口。整体方案中的逐 case 后续 SCF、新批次实体目录、持久化传输和扩展消费尚未交付，不能列为测试通过。CHECKPOINT 与实施文档也明确保留了这些后续阶段。

已有专项全部通过，补充故障场景通过；另发现两个导出信息缺失问题。本轮仅新增本报告，不修改业务代码、安装包或科研数据，不连接服务器、不执行科研提交、不重启正式服务。

## 本轮检查

- 使用登记解释器，`PYTHONPATH=.:tests FPILOT_REMOTE_DISABLED=1`，运行 `tests.test_batch_manifest` 与 `tests.test_result_overview`，18 项通过。含真实 ASGI 请求、ZIP 内容与引用解析。
- `npx tsc --noEmit` 通过。
- `node --import tsx --test tests/scanCaseView.test.ts tests/scanDispatch.test.ts tests/windowScan.test.ts tests/extensionModel.test.ts`，25 项通过。
- 使用现有 BatchManifestTests 的 TemporaryDirectory 夹具，追加四项断言，全部通过：过期且不完整文件清单明确标记；当前 attempt 缺失仍导出其他 case；远端清理后本地副本仍可解析；最后一份副本消失后不再宣称可用。
- 另对以下两项缺口做隔离复现。原始测试均没有覆盖这两个字段合同。本轮未重跑构建或浏览器 UI，前端下载的本次证据来自源码核对与 ASGI 测试；历史 Chrome 验收不冒充本轮结果。

## P2：Wannier 清单丢失完整能窗与能量参考

位置：`backend/batch_manifest.py:91–104`。

`backend/wannier_scans.py` 的实际 case 同时保存 parameters、relative_windows、absolute_windows；parameters 只记录本轮扫描的那个边界。批次 source 保存 energy_reference、seed、parent_attempt_id。

导出只保存 parameters，没有复制完整 relative_windows/absolute_windows，也没有导出批次 source。用符合实际字段形状的 PENDING case 复现：输入包含四个能窗边界、费米能 5 eV，以及 parameters=[dis_win_min=-8]；输出只剩 dis_win_min=-8，完整能窗和费米参考都不存在。未派发 case 没有 attempt，不能通过 attempt 的冻结输入摘要补回这些内容。

影响：把 ZIP 交给 AI 后无法独立确定该 case 的完整冻结窗/外窗，也无法把相对费米值换算到绝对值。已执行 case 的 JSON 可能保留部分绝对输入参数，但不能替代批次的完整能窗定义与来源。

建议：JSON 原样导出完整相对/绝对能窗和明确的能量参考、seed、来源 frozen/attempt 身份；CSV 增加对应字段或完整结构化列。对旧记录缺失值保持 null，不能填 0 或推测费米值。覆盖 PENDING、已完成、跨批复用三种情况。

## P2：派生 case 的父 case 关系没有导出

位置：`backend/batch_manifest.py:91–98`。

`backend/task_recovery.py` 在调整扫描坐标后创建新的 case，并写 derived_from_case_id；已有 test_scan_axis_edit_creates_labeled_derived_case 验证这一关系。清单只导出 reused_from，没有导出 derived_from_case_id。

临时夹具创建 derived_from_case_id=case-a 的 PENDING case，导出后该字段消失，reused_from 仍为 null。此时尚无 attempt，不能从运行来源推回父 case。

影响：清单丢失“这组参数由哪个 case 修改而来”的关系，AI 会把它当作独立新点；原数据库关系仍在，不是历史数据损坏。

建议：JSON 与 CSV 明确保存派生父 case；同批来源补 batch_id。不要把派生关系和重用同一个 attempt 的 reuse 混为一谈。已有 input_versions/original_bundle_id 可按需随输入变更历史导出，但本轮必须优先补齐父 case 关系。

## 未交付功能

1. 选择一组优化 case，逐 case 使用各自 CONTCAR 创建后续 SCF 批次。
2. 新批次命名父目录与固定 case 子目录。
3. 大文件流式传输、断线续传、传输任务恢复。
4. 扩展直接消费 ArtifactRef、登记消费引用及相应生命周期保护。

目前导出的引用只是元数据与位置解析入口，不会触发文件传输，也不会自动为扩展准备输入。本阶段测试通过不能代表上面四项已具备。

## 2026-11-12 修复回执

两项 P2 已在 `backend/batch_manifest.py` 补修。批次 JSON 导出原始 `source`、`source_frozen_id`，各 case 导出完整的相对/绝对能窗，CSV 保存对应结构化列。`source` 包括保存的费米参考、seed、来源 attempt 与自旋模式。旧记录缺少元数据时保留 `null`，标量 CSV 空值保留为空。

派生 case 的 `derived_from_case_id` 和同批的 `derived_from_batch_id` 已接通 JSON 与 CSV。复用已有 attempt 的 `reused_from` 沿用独立字段。

三项新增回归在修复前均复现字段缺失，修复后通过。验证覆盖未派发、已完成、跨批复用、派生待派发及旧能窗记录，并对 ZIP 内 JSON/CSV 的完整窗口、来源与父 case 关系逐项核对。既有参数恢复用例追加了实际恢复产生派生 case 后的清单断言。

登记解释器下的清单专项 15 项、结果摘要 6 项、扫描参数修改并派生 case 的恢复用例 1 项，共 22 项通过。测试工作区隔离且禁止 SSH。运行中的后端通过正常重启加载修复。
