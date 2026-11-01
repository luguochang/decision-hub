# 前端重构接入顺序

## 不要把整个页面切成大图

用户要的素材包用于减少绘制劳动，不是把 UI 整页做成背景图。`references/` 中的生成图供风格核对，`assets/reference-crops/` 只供对照。文字、表单、按钮、Badge、表格、图表和图谱节点应保持真实 DOM / SVG；否则点击、搜索、响应式、无障碍和返回都无法成立。

原型未绑定 React / Vue 框架；本轮未核实当前仓库的技术栈。因此交付原生 HTML/CSS/JS，可作为组件迁移参考，不把某个框架假定为用户项目现状。

## 推荐实施顺序

**第一阶段：壳层和导航。** 迁移 `tokens.css`、AppShell、导航选中态、面包屑、服务状态与通用按钮 / 弹层。搭好对象 ID 和来源路径协议，保证直接打开、浏览器前进后退和明确返回一致。评审用“页面目录”不要直接带到正式用户主导航。

**第二阶段：研究主链。** 接入已知 summary / health / runs / observations / view。先保证空输入、幂等、queued / researching / failed / completed 和未完整报告正确，再接入新视觉。报告方向、概率、质量和权限分别绑定，不使用通用 success 字段偷换。

**第三阶段：可复核详情。** 接入 inspector / observability / decision-graph / forecast-audit。复用同一 Run 与 Evidence 身份。图谱点击进入证据后返回，应恢复 viewport 和选中节点。复杂生产图谱可以迁移到项目已有图形库；本原型的 15 节点实现不代表大规模图谱的性能方案。

**第四阶段：资料版本。** 接入文档列表、上传、blocks、correction。先确认尚缺失的写合同、版本并发与质量门，再开放“确认”“接入研究”。不要把前端按钮锁定当作质量门或权限控制。输入文件的真实解析结果必须替换演示 diff。

**第五阶段：内部排查。** 仅在权限满足时将当前 Run 映射到 DSH Session。生产中需要服务端授权与脱敏；客户端隐藏导航不是安全实现。原生报告只作为技术记录，不替代 Hub 用户报告。

## 在源文件里从哪里改

| 位置 | 负责的内容 |
|---|---|
| `source/tokens.css` | 全局颜色、字体、尺寸与间距 |
| `source/app.css` | 页面结构、组件、移动适配、图谱与差异审核样式 |
| `source/icons.js` | 与 SVG 文件一一对应的路径数据 |
| `source/fixtures.js` | 38 个状态目录及明确标注的设计样例 |
| `source/app.js` 的 `href / returnHref / updateQuery` | 路由、对象上下文及筛选返回 |
| `rememberPosition / render` | 滚动、焦点与状态恢复 |
| `submitResearch / beginDoc / act` | 本地模拟；生产须替换服务调用 |
| `initGraph / updateGraphTransform` | 图谱交互与视口 |
| `modal / closeOverlay` | 抽屉、确认层、焦点锁定与返回 |
| `source/contracts.d.ts` | 组件视图提案，不是后端实际 schema |

## 重构回归入口

先运行 `qa/run_checks.py` 理解交互断言，再将同一用例指向真实前端。除了 39 项原型检查，还必须增加身份权限、API 失败、过期数据、幂等冲突、文件格式/大小、并发版本确认、服务端硬缺口、真实来源链接、刷新恢复、大列表 / 大图性能和多浏览器检查。参考 `qa/round-1-results.json`，防止再次引入筛选后返回丢上下文的问题。

单文件由 `tools/build_standalone.py` 生成；不要同时手改单文件与 source 两套代码。修改后重建、测试、截图，保持交付文件与实际页面一致。
