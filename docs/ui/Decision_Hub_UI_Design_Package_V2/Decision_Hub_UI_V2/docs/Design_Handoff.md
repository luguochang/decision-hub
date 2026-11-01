# 视觉、布局与组件交付

## 1. 依据与优先级

**S1：** `references/00_original_handoff.pdf`，用户提供的 13 页《Decision Hub 页面级使用、交互与截图交接稿》，负责说明已知的业务边界、状态与接口。**V1–V11：** `references/01_home.png` 至 `11_user_flow.png`，本次会话中的最新生成设计图，负责说明视觉方向。**P：** `Decision_Hub_UI_Prototype.html` 及 `source/`，是本次可执行布局、交互和修正后的组件规范。

三类文件的优先级并不相同：业务语义以 S1 为依据，视觉采用 V 系列，具体尺寸和交互实现以 P 为本次重构参考。生成图片中出现的百分比、市场数值、模型名、隐私名称和状态冲突不能反向覆盖 S1；差异见 `Business_Corrections.md`。尚未由 S1 支持的交互补充明确标为设计提案。

## 2. 视觉方向：保留最新图，不退回朴素旧版

保留冷白与浅蓝画布、蓝色渐变主行动、透明地球与山峦页头、彩色语义图标、分组卡片和图谱节点。最强视觉关注点留给“获取最新操作建议”和当前研究结论；运行健康、覆盖、权限是辅助信息，不与主行动争抢注意力。

图标有两个层次：导航及行内操作使用 24 viewBox、1.7px 笔画的线性图标；少量关键入口与章节使用 64 viewBox 的双色功能图标。禁止把每个文本字段都装进彩色卡片，禁止用同一种绿色同时表达服务健康、研究方向、证据质量和交易许可。

页头背景是从 V1 精确裁出的 568 × 130 PNG；裁剪坐标记录在 `asset-crop-manifest.json`。它是装饰资源，不承载动态信息。生成图中的按钮和证据卡另附参考切片，但不能直接作为生产按钮使用。

## 3. 核心尺寸与令牌

| 项目 | 实现值 | 用法 |
|---|---:|---|
| 桌面评审画布 | 1440 × 1080 | PNG 首屏；长页另附完整长图 |
| 移动评审画布 | 390 × 844 | 六个重点流程验证 |
| 左侧导航 | 208px | 品牌、Research 子区域、评审辅助入口 |
| 顶部栏 | 60px | 面包屑、服务状态、页面目录 |
| 主标题 | 28px / 1.4 | 用于页面标题，避免巨大营销字 |
| 正文 | 14px / 1.6 | 数据密集型研究工作台 |
| 辅助文字 | 12px | 时间、来源、注释，不承载唯一关键限制 |
| 基础间距 | 4 / 8 / 12 / 16 / 20 / 24 / 32px | 页面、卡片与控件统一节奏 |
| 圆角 | 8 / 12 / 16px | 控件 / 卡片 / 弹层区分层级 |
| 主色 | #2563EB | 导航选中、焦点、链接、行动 |
| 正文 / 次级 | #122247 / #516585 | 在白底上保持足够对比 |
| 成功 / 警告 / 错误 | #087A55 / #9C5907 / #BC3049 | 同时提供文字或符号 |

完整令牌见 `design-tokens.json`，实际样式见 `source/tokens.css`。生成的设计系统板中存在两套不一致的主色与文字尺寸，本次已收敛为以上一套，不将图片里的冲突同时交给开发。

## 4. 页面布局

研究指挥台采用“主行动 + 快照条 + 事件 / 任务 / 报告”布局。报告预览不只是静态卡片，任务选择、查看证据、技术详情与图谱均携带 Run。手机端优先显示主行动和选中报告，再显示事件与任务；这属于本次响应式提案。

输入页采用左右布局：左侧是可选关注点、默认研究范围和主按钮，右侧是当前 Run 的状态。提交、排队、研究、失败的 UI 独立存在。失败不是排在 completed 后的下一步，未运行的状态也不会同时显示成功。

宏观日历以列表而非大月历为主，因为主要任务是比较来源、前值、预期、实际和意外。市场、重要性、关键词写入 URL。事件打开侧边抽屉，返回恢复筛选；历史发布为空时保持真实空态。日期、地域和数值是设计样例，页面有标识，不代表官方日历。

研究报告保留顶部结论带和 30m / 24h / 72h 三张候选卡。未校准、未授权与执行缺口在首屏可见，不能藏在长文底部。主要依据、反方限制、触发 / 失效条件、图表及资料关联位于下方；完整长图供开发核对。

技术详情包含 S1 的八个模块，结论相关模块先展示，原始工具参数放二级位置。图谱是独立的全屏工作空间，不是第二个报告页面；节点检查器在桌面右侧，在手机下方。

资料库、上传、解析、差异审核、确认、质量复检、可接入、已连接分别有可见状态。审核界面以原文 / 修正建议并排，移动端改为纵向；底部确认操作不能覆盖当前可聚焦控件。

DSH 保留内部控制台的密度，但只在当前 Run 技术上下文中进入，不在普通用户主导航新增第二业务首页。日志右侧检查器按所选记录更新。

## 5. 组件边界

建议拆分为 AppShell、Breadcrumbs、ServiceStatus、ActionCard、RunList、EventTable、EventDrawer、ResearchComposer、RunTimeline、ReportSummary、CandidateCard、EvidenceDrawer、AuditAccordion、DecisionGraph、NodeInspector、DocumentStepper、DiffBlock、ReviewConfirmation、SourceStatus、TraceList、TraceInspector。

全局只有一套 Badge / Button / Dialog / Empty / Error 组件。颜色只表达一个维度；disabled 必须配可理解的原因。弹层支持 Esc、焦点锁定及关闭后恢复；抽屉不丢失来源 URL。图谱还提供列表、键盘与 Fit View，避免只能拖拽操作。

## 6. 参考 Skill 的实际使用范围

前序检索已经阅读 Anthropic `frontend-design`、`nextlevelbuilder/ui-ux-pro-max-skill` 与 Vercel `web-design-guidelines` 的内容。本包借用了“建立统一令牌、强调真实内容、避免模板化装饰、可见焦点、明确错误恢复、状态反映 URL”等方法，并以实际截图和浏览器检查复核。

本轮没有运行这些仓库的搜索 CLI，没有取得外部团队或第三方认证，也没有启动独立子 Agent。不能把方法参考写成未执行的工具调用记录。相关原文地址：

- https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md
- https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/blob/main/.claude/skills/ui-ux-pro-max/SKILL.md
- https://github.com/vercel-labs/agent-skills/blob/main/skills/web-design-guidelines/SKILL.md
