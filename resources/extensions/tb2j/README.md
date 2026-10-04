# TB2J Wannier90 后处理扩展

这个扩展只做三件事：检查 Wannier90 产物、调用用户环境中的
`wann2J.py`、保存运行记录。VASP 和 Wannier90 的生成仍由主体流程负责，
TB2J 的数值结果也不在这里解释。

## 输入约定

`input_dir` 是包含 `POSCAR` 的目录，`prefix` 是 Wannier90 文件前缀，因而
父模型位置可直接理解为：`parent = input_dir + "/" + prefix`（实际文件是
`input_dir/prefix_hr.dat`、`input_dir/prefix.win` 等）。不需要复制或重建
VASP 生成的 `.win` 文件。

spinor 示例要求 `wannier90_hr.dat` 与 `wannier90_centres.xyz`，也接受
`wannier90_tb.dat`。collinear 示例分别使用 up/down 两个前缀；每个前缀
都需要 `_hr.dat` + `_centres.xyz`，或一个 `_tb.dat`。两个 HR 同时存在时
会检查 `num_wann` 相同。

可直接复制的完整请求在 `examples/spinor.request.json` 和
`examples/collinear.request.json`。常用选项 `emin_ev`/`emax_ev`/`nz`/
`nproc`/`orb_decomposition` 会直接映射为 TB2J 参数；其他少用参数
仍可直接使用原生 `wann2J.py`。

保存为 `request.json`：

```json
{
  "schema": "fpilot.tb2j-request.v1",
  "input_dir": "/path/to/wannier",
  "mode": "spinor",
  "prefix": "wannier90",
  "efermi_ev": 5.0182,
  "kmesh": [5, 5, 5],
  "elements": ["Fe", "Cr"],
  "output_dir": "TB2J_results",
  "executable": "wann2J.py"
}
```

也可以用 `index_magnetic_atoms` 替代 `elements`，索引从 1 开始。collinear
只需改成：

```json
{
  "input_dir": "/path/to/wannier",
  "mode": "collinear",
  "prefix_up": "wannier90_up",
  "prefix_down": "wannier90_down",
  "efermi_ev": 5.0182,
  "kmesh": [5, 5, 5],
  "elements": ["Fe", "Cr"]
}
```

## 人工命令

```bash
cd /path/to/first-principles-pilot/extensions/tb2j
python3 scripts/tb2j.py doctor --executable wann2J.py
python3 scripts/tb2j.py check --request /path/to/request.json
python3 scripts/tb2j.py run --request /path/to/request.json
```

`doctor` 不设 TB2J 版本门槛，只报告命令是否能启动以及输出中是否识别到
版本。`check` 不运行 TB2J；存在 `.win` 时只核对其中的 `num_wann` 和
`spinors`。`run` 会把完整 argv、stdout、stderr 和返回码写入
`TB2J_results/tb2j_run.json`，同时保存两个文本日志；成功的唯一输出门槛是
`exchange.out` 非空，不要求 `exchange.xml` 或某个版本的其他文件。

## 来源卡

- TB2J 本地源码和参数入口：`TB2J-main/TB2J/exchange_params.py`、
  `TB2J-main/docs/src/wannier.rst`。
- 官方项目：<https://github.com/mailhexu/TB2J>。

以上只记录 `wann2J.py` 的公开参数和文件边界，不复制官方手册内容。
简短参考卡见 `references/tb2j-wannier90.md`。
