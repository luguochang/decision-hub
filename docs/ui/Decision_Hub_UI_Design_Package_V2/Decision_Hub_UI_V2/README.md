# Decision Hub · UI Design Package V2

**交付版本：2.1 / 2026-09-23**

这是按本次会话最新蓝白设计图重建的设计与前端重构交付包，不是只有图片的概念板，也不是已接入生产后端的应用。所有演示日程、任务进度、计数与图表均标注为离线演示；来源明确的原始业务要求与设计补充分别记录。

## 从这里开始

| 文件 | 用途 |
|---|---|
| `Decision_Hub_UI_Prototype.html` | **优先打开。** 单文件可点击原型；内嵌 CSS、JS、图标和页头背景，无 CDN 依赖。 |
| `Decision_Hub_UI_Design_Review.pdf` | 52 页设计评审：8 页交付说明 + 38 个桌面页面/状态 + 6 个移动视图。 |
| `Decision_Hub_UI_Overview.png` | 从实际 HTML 页面截图制作的八页面总览，不是重新生成的概念图。 |
| `screens/index.html` | 逐页查看截图、完整长图，并直接打开相应可点击状态。 |
| `assets/index.html` | 图标、功能图形、品牌标识与背景素材目录。 |
| `index.html` + `source/` | 分文件开发版本，便于替换样例数据和迁移组件。 |
| `docs/` | 布局、交互、状态机、接口对照、业务修正和重构说明。 |
| `qa/` | 实际执行的浏览器检查脚本、首轮问题、修复后结果、SVG 与对比度检查。 |
| `references/` | 用户原始 13 页交接稿，以及本次会话 11 张最新生成设计图/规范板。 |

打开原型后，左下角 **“页面目录”** 或 `Ctrl / Cmd + K` 可以切换全部 38 个桌面页面与状态。目录中的状态带 `preview=1`，用于静态评审，不会自动跳走；通过“获取最新操作建议 → 开始分析”可观察完整的模拟运行流程。移动端通过浏览器缩窄窗口，或者查看 `screens/mobile/`。

## 交付数量

38 个桌面首屏 PNG、6 个移动端首屏 PNG，另附 20 个完整长图，共 **64 个实际原型截图文件**。素材包括 **64 个线性 SVG 图标、12 个功能 SVG 图标、1 个品牌 SVG、1 个 SVG Sprite**，以及对应 PNG；不是把位图放进 SVG 外壳。布局、按钮、表格、图表、节点和文字均可编辑。

## 启动与重建

单文件 HTML 无需安装依赖。分文件版本也可通过本地静态服务器打开：

```bash
# 在解压目录执行；访问终端显示的地址
python -m http.server 8080

# 修改 source/ 后，重新生成单文件原型（仅需 Python 标准库）
python tools/build_standalone.py

# 重跑交互检查或重新截图（需要 Playwright 与浏览器）
pip install playwright
playwright install chromium
python qa/run_checks.py
python tools/capture_screens.py

# 重新生成设计评审 PDF（截图与文档不变时直接执行）
python tools/build_review.py
```

脚本默认寻找系统 Chromium，否则使用 Playwright 安装的 Chromium。可通过环境变量 `CHROMIUM_PATH` 指定本机浏览器可执行文件。没有附带字体文件；中文显示使用操作系统字体回退。

## 关键边界

本包没有向 GitHub 仓库推送代码，也没有连接 Hub API、上传真实文件、调用模型或执行交易。解析、质量复检和研究进度是可交互的本地模拟。资料确认、重试和接入研究的真实 API 合同尚需确认，不能把原型中的成功状态当成后端已支持。

本次浏览器检查使用 Chromium 的 `set_content` 加载与交付完全相同的 HTML。运行环境禁止直接导航到本地文件或本地 HTTP 地址，因此这两种启动方式未在此环境完成导航验证；没有绕过该限制。已检查 DOM 交互、真实 hash 历史、返回位置、图谱操作和页面布局。真实 API、真实浏览器持久化、Safari / Firefox、完整无障碍和性能验收仍在生产接入阶段完成。

详见 `docs/QA_Report.md` 和 `KNOWN_LIMITATIONS.md`。这是一份可继续重构的设计交付，不是“生产系统已全部验收”的声明。
