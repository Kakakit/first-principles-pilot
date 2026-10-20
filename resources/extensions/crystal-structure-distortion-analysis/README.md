# 配位多面体畸变 · 2.1.0

填写 `CrO6, FeO4`，选取具体中心原子，查看可旋转多面体、逐键长度、内部键角、共享配体桥角和中文公式。独立扩展不导入主体、不自行 SSH、不提交研究任务。

## 使用

1. 在工作台扩展管理导入本目录或 2.1.0 ZIP，选用已有 Python；doctor 实际导入 NumPy 和 SciPy。默认本地运行，不自动安装或升级环境。
2. 选择冻结 POSCAR/CONTCAR，多面体类型填 `CrO6, FeO4`（支持中文逗号）。它表示 Cr–O 六配位和 Fe–O 四配位，不是材料化学式或氧化态。
3. 高级搜索半径默认 3 Å。它用于寻找配体候选，不自动识别化学成键；邻居不足报错。第 n 与 n+1 个候选间隔小于 0.15 Å 提示检查；半径内没有下一候选时显示未知，不表示无限大。首版限单配体四/六配位，同一中心不能配置冲突配位。
4. 选择具体中心；拖动旋转、滚轮缩放，模型获得焦点后方向键旋转、+/- 缩放。三维不可用时明细仍可查看。点配体或键长行选中键，再点第二个配体显示夹角；也可直接点角度行。
5. 类型与位点排序只影响展示。键长图虚线为本位点平均值。四面体完整6角；八面体完整15角，分12顺式和3对向角，可按类型筛选、角度排序。

原子编号从1开始，镜像为整数晶格平移。`O 7 [0,-1,0]` 和 `O 7 [0,0,0]` 是不同空间顶点，不合并。桥角只取两端指定壳层共同包含的配体；反向重复只列一次，不同配体路径分别保留。点击桥角展示两个多面体、两条键长和中心间距。没有共享配体就显示空结果，不补猜配位；桥角没有统一理想值，不自动推断交换作用。

## 指标与公式

正常页面使用中文、符号与单位，机器字段留在 JSON/兼容 CSV。展开每个指标可看静态 SVG 公式、可复制文字、定义、理想参考，以及当前位点 n、m、平均键长和等体积理想键长。

- 键长畸变 Δd：Σ[(dᵢ−d̄)/d̄]²/n；Baur D：Σ|dᵢ−d̄|/(n d̄)。两者无量纲、等长时为0，平方偏差与绝对偏差不同。
- 二次伸长率 λ：Σ(dᵢ/d₀)²/n，d₀来自等体积规则多面体，不用实测平均值替代；规则多面体为1。
- 键角方差：Σ(θᵢ−θ₀)²/(m−1)，单位°²。四面体 m=6、θ₀≈109.471221°；八面体 m=12、θ₀=90°，只计顺式角。对向角另与180°比较；这不是围绕实测均值的方差。
- 八面体先最大化三组对向角之和，再将配对平均键长排序为 l₁≤l₂≤l₃。Q₂=√2(l₁−l₂)，Q₃=√(2/3)(2l₃−l₁−l₂)，QJT=√(Q₂²+Q₃²)，单位Å。排序编号不是晶体学方向；QJT只是几何幅度，不直接证明电子 Jahn–Teller 效应。
- 长度Å、凸包体积Å³、角度°；无统一的“畸变合格阈值”。程序完成、数值收敛与物理证据分别判断。

公式已随包提供，运行不需要 Matplotlib、LaTeX、在线字体或 CDN。3Dmol.js 2.5.3 原版浏览器构建、BSD与第三方声明见 viewer/vendor；不引用主体 JavaScript。

## 数据与历史兼容

原 distortion.json 的 schema_version=1.0、标量定义及来源保持，新增 detail_schema=fpilot.polyhedra-geometry.v1、逐位点坐标/向量/面片/全部内部角和 bridges。原 distortion.csv 列及数值不变；新增中文 bonds.csv、angles.csv、bridges.csv。全局原子号1起始；JSON的 faces 和 ligand_local_indices 是本位点配体数组的0起始索引，不能作为原子编号。

交互仅使用冻结结果。旧分析缺少配体坐标，不能凭旧 JSON 凭空生成三维和桥角；请从同一冻结结构新建本地几何分析，原记录保留，不重跑 VASP，也不使用最新草稿补历史坐标。

旧 center_element/ligand_element/coordination 参数及独立 CLI 保持兼容，新旧定义冲突时报错。独立旧命令可能覆盖调用方指定的同名输出；工作台每次使用独立目录：

```sh
python src/crystal_distortion.py POSCAR --config config/default_fecr2o4.json --output-prefix analysis/distortion
python scripts/cli.py doctor
python scripts/cli.py check --request request.json
python scripts/cli.py run --request request.json
```

## 开发和验收

在 extensions 分支根目录用已有环境：

```sh
python -m unittest discover -s resources/extensions/crystal-structure-distortion-analysis/tests -p test_details.py
node --import tsx --test tests/polyhedraViewer.test.ts
npm run build:polyhedra
python scripts/package-extensions.py --extension crystal-structure-distortion-analysis
```

原 test_geometry.py/test_cli.py 保留，可用已有 pytest 或直接调用独立测试函数，不创建环境。仅改公式时再运行 scripts/build_formulas.py，需要已有 Matplotlib；用户运行扩展不需要它。

本轮11项新增Python、5项原几何/CLI和3项JS通过。本机冻结样例4个CrO₆、2个FeO₄，共72内部角、48桥角（24 Cr–O–Cr、24 Cr–O–Fe），150个旧标量完全一致。数量仅属于该样例，不硬编码，不构成物理验证。独立内嵌页与通用表单完成输入、联动、键盘旋转、筛选排序、公式和导出动作检查。Safari有人操作时停止自动化，未声称独立Safari验收完成。不改主体、不跑全量回归、不连接服务器或恢复研究队列。
