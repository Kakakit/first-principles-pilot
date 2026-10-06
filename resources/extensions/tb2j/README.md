# TB2J v2：交换参数与轨道分解

独立扩展只做输入检查、选项到命令参数的转换和结果整理。数值计算使用[官方 TB2J](https://github.com/mailhexu/TB2J)（BSD-2-Clause），不导入工作台 backend，不自行 SSH，也不提交 PBS/Slurm。服务器执行由通用扩展宿主负责。

## 使用

1. 在扩展管理导入并启用 TB2J。在“服务器默认 Python”登记来源服务器已有 `bin/python` 绝对路径和分析工作区；也可在 TB2J 卡片的“Python 环境 · 默认或单独指定”中，对该主机单独指定 TB2J 环境。选择“使用默认环境”会清除这项覆盖；本地选择及其他服务器分别保存。保存后点击“检查所选环境”，它只运行 doctor，实际导入 TB2J、核对版本与 CLI 能力，不分析或提交任务。缺依赖不会自动停用扩展。无需 activate，不安装或升级环境；调度资源选项可保持折叠。
2. 打开完成的 Wannier attempt 结果页，选择本扩展。默认“服务器运行 · 直接命令行”，默认同来源主机；未登记该主机时先登记，不能自动换到其他服务器。结果页显示选定解释器，修改设置后点击“刷新环境设置”，重新检查或预览。小输入也可选择本地运行，继承本地默认或 TB2J 的本地覆盖；大文件会提示留在服务器运行。
3. SOC 选择一个 `spinors=true` 来源。共线追加同服务器、同 DFT 父 attempt 的另一通道，`.1` 为上自旋、`.2` 为下自旋。界面显示冻结的 seed、父 attempt 和费米能。
4. 每个来源选择 `.win`、`.wout` 和 `seed_hr.dat + seed_centres.xyz`，或 `seed_tb.dat`。`centres` 是可选角色，但选择 hr 时必须提供。文件前缀、完成记录、晶胞、原子顺序、num_wann、自旋模式和两通道兼容性由扩展检查。
5. 默认使用此来源冻结的能量参考。缺少时切换 `manual`，填写绝对费米能（eV）与来源说明；积分下/上界则是相对 E_F 的 eV。不会拿最新草稿或其他计算替代。
6. 磁性元素列出来源冻结 `.win` 中的元素，由用户勾选；缺少旧元数据时可手动填写。磁性元素与从 1 开始的原子序号二选一。填写 k 网格、积分范围/点数、可选截断距离（Å）和 TB2J 进程数（默认 1）。官方起步默认值不是收敛推荐。SOC 必须明确选择 `groupby` 并勾选基底确认。未登记环境或缺少文件时仍可编辑参数，检查和执行按钮会说明阻塞原因。
7. 检查选项，预览后点击“按此来源与脚本运行”。宿主把文件复制到独立分析目录，依次执行 doctor → check → run，等待服务器命令结束；不检测调度节点，不请求调度核数，不创建 job ID。

服务器等待沿用通用宿主的 900 秒连接上限。计算更久或连接中断时保留 UNKNOWN；核对/刷新原记录，读取原目录结束回执，不能重复执行。清单 run 超时为 86400 秒；实际结束、超时或断线均不代表数值收敛。

## 参数怎样变成命令

表单 → 冻结请求 JSON → `inputs_v2.argv()` → `subprocess.run(argv)`。
选定 Python 运行 `python -m TB2J.scripts.wann2J`，不调用 PATH 中另一个环境的 `wann2J.py`，不拼接可执行 shell 文本。示意：

```text
/path/to/python -m TB2J.scripts.wann2J --path /analysis/outputs/model --posfile POSCAR --efermi 2.3 --kmesh 5 5 5 --elements Fe --spinor --groupby spin --prefix_spinor spinor --emin -14 --emax 0 --nz 100 --np 1 --output_path /analysis/outputs/TB2J_results
```

标准文件名和 POSCAR 只在输出目录生成，原 Wannier 文件不修改、不重跑。原子顺序按 .win 保留。新 Wannier 配置可选择 `write_hr=true`、`write_xyz=true`；旧 attempt 缺产物会给出具体提示。

## 轨道标签和分组

开启轨道分解后，映射填写“原子序号 + 原子内 TB2J 轨道矩阵序号”（两者从 1 开始）、确认的标签与可选分组。序号是原子内矩阵轴的原始顺序，不是全局 Wannier 函数序号；对照本次 `assigned_basis.txt` 和原始产物确认物理含义。不自动推断 dxy、t2g/eg。

未映射项保持 `orb_1` 等原始序号。只汇总两端均填写分组的矩阵元素；报告完整矩阵和、已映射分组和、未分组元素和及各自与总 J 的差值。映射冻结到本次 command.json 和 exchange.json；重复、零序号、越界或超出实际输出矩阵会失败并保留原始产物。

## 结果和证据边界

本次官方 `SpinIO.load_pickle` 读取本次生成的 `TB2J.pickle`，从内部 eV 转为 meV；距离 Å，原子编号从 1 开始，保留原生晶格平移 R、符号和原子对计数，不自行倍增。不会读取用户导入的 pickle。未知输出格式明确失败，独立目录中的原始产物保留。

- `exchange.csv/json`：高精度 J、SOC DMI 和各向异性交换张量。
- `orbitals.csv`：完整原始轨道矩阵元素、确认标签、DMI/各向异性轨道贡献（实际产物提供时）。
- `groups.csv`：用户分组与总 J 差值。
- `command.json`、`environment.json`、日志、模型副本和所有原始 TB2J 产物：版本、实际 argv、选项、来源与运行证据。

页面每表最多 1000 行、距离曲线最多 2000 点，结果 JSON 不超过 10 MiB。完整 CSV/JSON 保留精度；远端单文件大于 10 MiB 时留在服务器并显示路径，不回传大 Hamiltonian。

程序完成、数值收敛、物理证据分别记录。单方向 SOC 的 DMI 平行分量及涉及该磁化方向的各向异性分量不能作为完整方向验证的张量；需要另外的多方向计算和合并。本版不提供磁振子、旋转方向或多方向合并。积分与 k 网格收敛、DFT–Wannier 一致性未检验时保留警告，科学验证为 NOT_PERFORMED。

## 独立 CLI 与兼容入口

```bash
/path/to/existing/python scripts/cli.py doctor
/path/to/existing/python scripts/cli.py check --request /absolute/request.json
/path/to/existing/python scripts/cli.py run --request /absolute/request.json
/path/to/existing/python -m unittest discover -s tests -p test_v2.py
```

请求/结果遵循 [通用 v2 协议](../../../docs/extensions-b/PROTOCOL.md)。整个目录可独立复制，CLI 不依赖工作台。doctor 实际导入 TB2J、读取实际版本，并用同一 Python 检查 CLI 参数能力；残留发行元数据不算环境可用。测试中的 `fake_tb2j` 只通过测试显式 PYTHONPATH 引入，模拟结果不构成真实计算验收。

原 `scripts/tb2j.py`、`extension.json`、v1 示例及原测试保留，供旧命令兼容；新管理入口以 `extension.manifest.json` 为准。

官方参考：[Wannier 输入](https://tb2j.readthedocs.io/en/latest/src/wannier.html)、[参数与默认值](https://tb2j.readthedocs.io/en/latest/src/parameters.html)、[轨道贡献](https://tb2j.readthedocs.io/en/latest/src/orbital_contribution.html)、[符号约定](https://tb2j.readthedocs.io/en/latest/src/convention.html)。
