# 外观主题格式

从右上角「外观」或侧栏「外观设置」切换主题。默认青瓷使用明亮绿白配色；极夜、航线、星轨是深色主题，纸页使用温暖纸色和衬线标题。五套主题使用同一套完整布局，计算画布、节点设置、配置、提交、结果和扩展入口均保留。

外观选择与自定义主题仅保存于当前浏览器的 `fpilot.appearance.v1`，不会改变计算工作区、物理输入、冻结记录或远端任务。切换后即时生效，刷新后继续使用；跨电脑迁移可以用「查看 / 复制当前主题」取得完整 JSON，保存为 UTF-8 的 .json 文件再导入。浏览器清理网站数据会清除外观选择，可重新导入 JSON。

## 创建和导入

1. 点击「下载格式模板」，或复制仓库中的 `public/theme-templates/custom-theme.json`。
2. 修改 `id`、`name`，按需修改颜色、字体和尺寸。`id` 使用小写英文开头的英文、数字、短横线，最多 48 位；同 ID 再次导入会替换该自定义主题。
3. 点击「导入并应用」，选择 JSON 文件。无需修改源码、安装包或重启后端。
4. 错误会直接说明字段与修正方式，保留当前主题。最多保存 20 套自定义主题，单文件不超过 64 KiB。移除自定义主题不会删除任何计算数据；移除正在使用的主题后恢复默认绿白。

`schema` 固定为 `fpilot.theme.v1`。`base` 可选 `celadon`、`polar`、`route`、`orbit`、`paper`；没有填写的颜色、字体与尺寸继承基础主题。`appearance` 可选 `light` / `dark`，控制原生滚动条、选择框及流程控件的浅色/深色模式。建议直接选同模式的基础主题再改颜色，以保证所有表面协调。

最小可用示例：

```json
{
  "schema": "fpilot.theme.v1",
  "id": "my-paper",
  "name": "我的纸页",
  "base": "paper",
  "colors": {"accent": "#765a42"},
  "fonts": {
    "headingChinese": ["Songti SC", "Noto Serif CJK SC", "serif"],
    "headingEnglish": ["Georgia", "Palatino", "serif"]
  }
}
```

## 颜色对应位置

颜色统一使用六位十六进制色号，如 `#19816b`。不接受 CSS 表达式或外部资源。下表也是默认模板完整配色：

| colors 字段 | 默认色号 | 控制的位置 |
|---|---|---|
| background | #f3f7f5 | 页面正文底色 |
| surface | #ffffff | 卡片、输入框、弹窗、顶栏 |
| surfaceRaised | #f5f9f7 | 次级面板、悬停、辅助区域 |
| sidebar | #ffffff | 项目侧栏 |
| sidebarText | #254b41 | 侧栏主文字 |
| sidebarMuted | #63786f | 侧栏辅助文字 |
| text | #263c34 | 正文、标题 |
| mutedText | #60756c | 说明、次级文字 |
| border | #dce7e1 | 边框、分隔线 |
| accent | #19816b | 主按钮、选中线、链接 |
| accentText | #ffffff | 主按钮文字 |
| accentSoft | #f2fcf7 | 选中背景、浅色强调区域 |
| success / successSoft | #24714e / #eaf7ee | 成功状态文字与背景 |
| warning / warningSoft | #8b5b16 / #fff4df | 提醒文字与背景 |
| danger / dangerSoft | #b5414a / #fcecef | 错误、删除文字与背景 |
| canvas | #f5f9f6 | 流程画布、三维结构底色 |
| canvasGrid | #d4e4da | 流程点阵 |
| canvasEdge | #7a9e8e | 流程连线 |
| codeBackground / codeText | #eef4f0 / #304b3d | 输入文件、代码与日志 |
| plotText / plotGrid | #53685f / #dce7e1 | 通用图表文字、辅助线 |

元素、晶格基矢、轨道和曲线类别的专用颜色维持原科学含义，不随主题重新分配。数据、单位、能量参考与来源也不改变。内置交互图在夜间使用深色坐标背景，保存的科研图片继续使用白底，并恢复当前屏幕主题。建议文字与背景有足够对比，尤其是 `accentText` / `accent` 和 `mutedText` / `surface`。导入的自由配色需要自行核对可读性。

## 中文与英文字体

每个字段是字体名称数组，按顺序寻找本机已安装字体。英文命名字体先匹配拉丁字形，中文命名字体匹配中文；通用 `serif`、`sans-serif`、`monospace` 最后作为后备。未安装指定字体时自动使用后备字体，不联网下载。名称要与系统字体册一致。

| fonts 字段 | 应用位置 | 示例 |
|---|---|---|
| chinese | 中文正文、表单、按钮 | `["PingFang SC", "Microsoft YaHei", "sans-serif"]` |
| english | 英文正文、按钮 | `["Inter", "-apple-system", "Segoe UI", "sans-serif"]` |
| headingChinese | 中文标题、项目名称 | `["Songti SC", "STSong", "serif"]` |
| headingEnglish | 英文标题、项目名称 | `["Georgia", "Iowan Old Style", "serif"]` |
| monospace | 输入文件、日志、代码数值 | `["SFMono-Regular", "Menlo", "Consolas", "monospace"]` |

`metrics.fontSize` 控制正文（14–18 px，默认 15）；`secondaryFontSize` 控制辅助文字（13–16 px，默认 13）；`radius` 控制主要卡片与节点圆角（0–20 px，默认 12）。标题单独使用层级字号，文件和表格避免因放大而丢失内容，可横向滚动。

实现沿用 React 与原生 CSS 变量，无新主题依赖；变量及原生模式参见 [MDN CSS 自定义属性](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Cascading_variables/Using_custom_properties) 与 [color-scheme](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/color-scheme)。
