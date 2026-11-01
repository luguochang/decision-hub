"""Build the design-review PDF from the final prototype screenshots.
Requires Playwright + Chromium. No network and no external font downloads.
The review HTML is saved in source/design-review.html for editable handoff.
"""
from pathlib import Path
import base64, json, html, os, shutil
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
SCREENS=json.loads((ROOT/'docs/screen-manifest.json').read_text(encoding='utf-8'))
PAGES={p['id']:p for p in json.loads((ROOT/'docs/page-index.json').read_text(encoding='utf-8'))}
QA=json.loads((ROOT/'qa/results.json').read_text(encoding='utf-8'))
def data(rel):
    p=ROOT/rel
    mime={'.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg'}[p.suffix]
    return 'data:'+mime+';base64,'+base64.b64encode(p.read_bytes()).decode()
def img(rel,cls='',alt=''):
    return f'<img src="{data(rel)}" class="{cls}" alt="{html.escape(alt)}">'
def esc(s):return html.escape(str(s))
def desktop(s):return next(v for v in SCREENS if v['device']=='desktop' and v['id']==s)
sections=[]
def page(title,subtitle,content,cls='',id=''):
    n=len(sections)+1
    sections.append(f'''<section class="page {cls}" id="{id or 'page-'+str(n)}"><header><div class="brand">{img('assets/logo.svg')}<span>Decision Hub</span><small>UI Design Package V2.1</small></div><span class="edition">2026.09.23 / Design handoff</span></header><div class="heading"><h1>{title}</h1><p>{subtitle}</p></div><div class="content">{content}</div><footer><span>Decision Hub · 设计与前端重构交付 / 离线原型，未接入生产服务</span><b>{n:02d} / 52</b></footer></section>''')
# 01. Actual deliverables, not a promotional mockup.
page('让设计图变成可操作的工作台','最新蓝白视觉 · 清晰返回路径 · 研究与执行权限分离',f'''
<div class="cover-head"><div><span class="pill">完整文件交付</span><h2>不是展示图。<br>是一套可以继续重构的设计包。</h2><p>可点击原型、逐页截图、真实 SVG 资产、业务与接口说明，<br>以及实际执行的检查记录。</p></div><div class="cover-counts"><div><strong>38</strong><span>桌面页面 / 状态</span></div><div><strong>6</strong><span>移动视图</span></div><div><strong>39</strong><span>浏览器检查通过</span></div></div></div>
<div class="cover-shots"><div>{img(desktop('home')['path'],alt='研究工作台实际截图')}<span>研究指挥台 / 同一 Run 串联任务、事件与报告</span></div><div>{img(desktop('graph-node')['path'],alt='分析图谱实际截图')}<span>全屏图谱 / 节点检查、证据联动与退出恢复</span></div></div>
<div class="callout">本文件中的界面页来自同一份可点击 HTML 实际渲染。生成参考图单独保存在 references/，不代替业务合同或运行证据。</div>
''','cover')
# 02.
page('先打开这些文件','三份主要交付，加上源码、资产与可重跑检查',f'''
<div class="two"><div class="panel"><h2>使用入口</h2><table><tr><th>文件</th><th>用途</th></tr>
<tr><td><code>Decision_Hub_UI_Prototype.html</code></td><td>单文件离线原型，内嵌资源</td></tr><tr><td><code>Decision_Hub_UI_Design_Review.pdf</code></td><td>本份 52 页设计评审</td></tr><tr><td><code>screens/index.html</code></td><td>逐页截图、长图与状态入口</td></tr><tr><td><code>assets/index.html</code></td><td>图标与素材浏览目录</td></tr><tr><td><code>index.html + source/</code></td><td>可编辑分文件开发版本</td></tr><tr><td><code>docs/ + qa/</code></td><td>规范、接口、测试与剩余限制</td></tr></table><div class="callout">原型左下“页面目录”或 Ctrl / Cmd + K 可直接打开全部 38 个状态。不必逐个走流程。</div></div>
<div class="panel"><h2>依据不是混在一起的</h2><div class="source-step"><b>S1 / 原业务交接稿</b><p>references/00_original_handoff.pdf<br>业务语义、状态和明确列出的 API。</p></div><div class="source-step"><b>V / 最新生成设计图</b><p>11 张页面、设计系统与流程参考图。<br>负责视觉，不证明其中数值与状态正确。</p></div><div class="source-step"><b>P / 本次可执行实现</b><p>HTML、CSS、JS、SVG 与状态规范。<br>实际布局、交互和修正记录可检查。</p></div><p class="small">生成图与 S1 冲突时，保留视觉方向，按原业务要求修正，并记录差异；未提供的后端合同不擅自补造。</p></div></div>
<div class="note-row"><b>评审模式与正常流程</b><span>目录链接带 preview=1，冻结状态用于核对。通过“开始分析”发起新研究，才能观察自动推进的本地模拟链路。</span></div>
''')
# 03.
colors=[('主色','#2563eb'),('正文','#122247'),('次级','#516585'),('成功','#087a55'),('警告','#9c5907'),('错误','#bc3049'),('紫色','#703cd2'),('画布','#f3f8ff')]
swatches=''.join(f'<div class="swatch"><i style="background:{c}"></i><b>{t}</b><code>{c}</code></div>' for t,c in colors)
feature_icons=''.join(f'<div>{img("assets/features/svg/"+n+".svg")}<span>{t}</span></div>' for n,t in [('research','主行动'),('evidence','证据'),('calendar','日历'),('coverage','覆盖'),('gate','质量门'),('counter','反方'),('audit','复核'),('trace','轨迹')])
page('一套收敛的视觉系统','保留最新图的视觉丰富度，不继承生成文字中的冲突',f'''
<div class="swatches">{swatches}</div><div class="two"><div class="panel"><h2>尺寸、密度与层级</h2><table><tr><td>桌面 / 移动评审</td><td>1440 × 1080 / 390 × 844</td></tr><tr><td>侧栏 / 顶栏</td><td>208px / 60px</td></tr><tr><td>标题 / 正文 / 辅助</td><td>28px / 14px / 12px</td></tr><tr><td>控件 / 卡片 / 弹层圆角</td><td>8px / 12px / 16px</td></tr><tr><td>间距</td><td>4 / 8 / 12 / 16 / 20 / 24 / 32px</td></tr></table><p class="small">执行值以 source/tokens.css 与 docs/design-tokens.json 为准。中文使用系统字体回退，包内不附带字体。</p></div><div class="panel"><h2>图标与图形分工</h2><div class="feature-icons">{feature_icons}</div><p>64 个线性图标处理导航与行内动作；12 个功能图标强调重点入口。品牌标识和 SVG Sprite 另附。</p><div class="callout">按钮、文字、表格和图谱节点保持 DOM / SVG。参考切片不是生产按钮，不能用一张截图覆盖整页。</div></div></div><div class="note-row"><b>视觉核对重点</b><span>主行动蓝色渐变、轻量山峦与地球页头、语义彩色图标、不同层次卡片。状态必须有文字，未知不能仅靠颜色猜测。</span></div>
''')
# 04.
corrections=[('校准与置信度','不继承生成图里的 62% 和“校准正常”。未校准就是未校准。','S1 第 7 页'),('研究与交易','偏多、no_trade、质量门和交易许可分开呈现。研究可读不等于授权买入。','S1 第 7、12 页'),('历史与缺失值','history=0 保持空态；实际值缺失显示 —，不复制未来事件、不写成 0。','S1 第 5–6 页'),('状态与幂等','失败是分支；同内容/窗口幂等，不提供关闭安全保护的开关。','S1 第 4–5 页'),('原文与版本','逐条审核、显式确认、质量复检独立。改变事实或权限的建议被拦截。','S1 第 10–11 页'),('DSH 的位置','仅在技术上下文进入，携带 Run 返回；不新增第二个用户业务首页。','S1 第 1、12 页')]
page('先修正业务语义，再加视觉','生成图是风格参考，不是接口和业务规则的事实来源',f'''
<div class="panel corrections"><table><tr><th>关键边界</th><th>本交付处理</th><th>原始依据</th></tr>{''.join(f'<tr><td><b>{a}</b></td><td>{b}</td><td>{c}</td></tr>' for a,b,c in corrections)}</table></div><div class="two"><div class="callout"><b>不静默修改原需求</b><p>所有冲突逐项记录在 Business_Corrections.md。上传格式、隐私枚举与新写接口不因图片画出来了就视为后端已经支持。</p></div><div class="callout amber"><b>样例必须显式可辨</b><p>计数、日期、进度、曲线均为离线设计样例。没有继承图片里的伪来源 URL，也不把 S1 的历史 403 当实时监测。</p></div></div>
''')
# 05.
page('从入口到返回，保留同一上下文','解决操作孤岛，不止是加一个返回箭头',f'''
<div class="flow"><div><b>研究指挥台</b><span>确定关注点</span></div><i>→</i><div><b>输入与提交</b><span>同意图幂等</span></div><i>→</i><div><b>任务状态</b><span>同一 Run</span></div><i>→</i><div><b>用户报告</b><span>方向与权限分离</span></div></div>
<div class="two"><div class="panel"><h2>深入查看，不另起任务</h2><div class="return-item"><b>报告 ⇄ Evidence</b><p>证据 ID、Run、来源路径；关闭回到原位置。</p></div><div class="return-item"><b>报告 ⇄ 图谱 ⇄ 节点 ⇄ Evidence</b><p>保持选中节点与缩放；退出全屏恢复报告滚动和焦点。</p></div><div class="return-item"><b>技术详情 ⇄ DSH</b><p>相同 Run 返回；技术日志不变成另一份交易结论。</p></div></div><div class="panel"><h2>支撑流程也有出口</h2><div class="return-item"><b>任务筛选 ⇄ 报告</b><p>关键词、状态、触发类型留在 URL，返回不重置。</p></div><div class="return-item"><b>日历筛选 ⇄ 事件抽屉</b><p>市场、重要性、关键词与范围保持。</p></div><div class="return-item"><b>资料审核 → 质量门 → 新 Run ⇄ 资料</b><p>原版本保留，已确认版本与新任务双向关联。</p></div></div></div><div class="code-panel">对象身份：run / doc / event / evidence / node<br>来源：白名单 back URL　　恢复：筛选 + 滚动 + 触发焦点<br>直接进入深层页：没有 back 时，按同对象上下文提供明确兜底。</div><div class="callout">首轮实际发现两处筛选返回丢失，已同步更新下钻链接的 back 参数并回归通过；首轮失败记录仍在 qa/ 中。</div>
''')
# 06.
groups=[('研究与任务',list(range(1,8))),('事件、报告与图谱',list(range(8,18))),('资料全流程',list(range(18,29))),('健康、规范与 DSH',list(range(29,39)))]
cols=[]
ps=list(PAGES.values())
for title,indices in groups:
 li=''.join(f'<a href="#desktop-{ps[i-1]["id"]}"><span>{i:02d}</span>{esc(ps[i-1]["title"])}</a>' for i in indices)
 cols.append(f'<div class="index-col"><h2>{title}</h2>{li}</div>')
page('38 个桌面页面 / 状态目录','它们不是 38 个独立业务模块；以下目录可在 PDF 中跳转',f'<div class="index-grid">{"".join(cols)}</div><div class="note-row"><b>另附六个移动视图</b><span>研究指挥台、报告、日历、语义修正审核、图谱节点、DSH。响应式复用同一业务逻辑。</span></div>')
# 07.
page('前端接入：已知路径与未确认合同','API 清单来自 S1 第 12–13 页；没有进行真实后端联调',f'''
<div class="two"><div class="panel"><h2>已有路径可先核对</h2><div class="api-group"><b>工作台与研究</b><code>/v1/decision-desk/summary<br>/v1/health<br>/v1/research/runs<br>POST /v1/research/observations<br>/v1/runs/{{id}}/view</code></div><div class="api-group"><b>审计与图谱</b><code>/v1/research/runs/{{id}}/decision-graph<br>/v1/research/runs/{{id}}/inspector<br>/v1/research/runs/{{id}}/observability<br>/v1/research/runs/{{id}}/forecast-audit</code></div><p class="small">完整 15 项在 API_Contract_Map.md。路径已列出，不代表请求体、响应字段、写入语义已核验。</p></div><div class="panel"><h2>不能从图里推断的能力</h2><p>确认修正稿、重新解析、语义修正、质量复检、资料关联 Run、独立 Evidence 详情、任务取消、分享导出、日历订阅、DSH 映射。</p><div class="callout amber">这些动作的具体写合同未在 S1 提供。原型展示本地交互，不编造 POST 路径，也不向用户承诺已调用成功。</div><h2 class="mt">需要独立核验</h2><p>幂等键、重试创建语义、权限枚举、缺失值、版本并发、上传限制、可见范围、来源脱敏与过期数据。</p><p class="small">contracts.d.ts 是组件视图提案，不是实际后端 schema。不要将其中字段直接当作 observation 请求体。</p></div></div><div class="note-row"><b>重构顺序</b><span>壳层与返回协议 → 研究主链 → Evidence / 图谱 → 资料版本与质量门 → 内部 DSH。先接真实数据边界，再开放写操作。</span></div>
''')
# 08.
page('检查记录与交付边界','结果可复查，限制不隐藏',f'''
<div class="qa-counts"><div><strong>{QA['checks']}</strong><span>浏览器检查</span></div><div><strong>{QA['passed']}</strong><span>最终通过</span></div><div><strong>{QA['failed']}</strong><span>最终失败</span></div><div><strong>0</strong><span>未处理 JS 异常</span></div></div><div class="two"><div class="panel"><h2>实际执行过</h2><p>空输入、幂等、失败恢复、父子 Run、任务 / 日历筛选返回、证据与图谱联动、缩放平移、小地图、资料审核与质量门、DSH 返回、弹层焦点、前进后退。</p><p>38 个桌面状态与 6 个移动视图检查整页横向溢出；8 组语义文字颜色对比度达到 4.5:1；78 个 SVG 可解析。</p><div class="callout"><b>首轮 37 / 39 → 修复后 39 / 39</b><p>两处筛选返回问题已修复。顺序审查与自动化执行；没有伪称独立多 Agent 并行验收。</p></div></div><div class="panel"><h2>尚未验收的部分</h2><p>真实 API、权限、服务端幂等、上传 / OCR、云端持久化、真实质量检查、跨标签页、Safari / Firefox、完整屏幕阅读器与大规模性能。</p><div class="callout amber"><b>浏览器执行方式</b><p>环境禁止直接本地文件 / HTTP 导航。本次使用同一交付 HTML 的 set_content 离线执行，没有绕过策略。因此这不是文件导航或真实部署验证。</p></div><p class="small">原始脚本和记录：qa/run_checks.py、qa/results.json、qa/round-1-results.json、qa/assets-and-contrast.json。</p></div></div>
''')
# 09-46 desktop screens. All come from the same built prototype.
for v in [v for v in SCREENS if v['device']=='desktop']:
    n=len(sections)+1
    p=PAGES[v['id']]
    full='另附完整长图' if v.get('full_path') else '完整单屏'
    sections.append(f'''<section class="page screenshot-page" id="desktop-{v['id']}"><header><div class="brand">{img('assets/logo.svg')}<span>Decision Hub</span><small>Desktop / D{v['number']:02d}</small></div><span class="edition">实际 HTML 渲染 · 1440 × 1080</span></header><div class="screen-heading"><h1>{esc(v['title'])}</h1><p>{esc(p.get('interaction',v['description']))}</p></div><div class="screen-stage">{img(v['path'],alt=v['title']+'实际原型截图')}</div><div class="screen-caption"><span>{esc(p.get('return_rule',v['description']))}</span><code>{esc(Path(v['path']).name)} · {full}</code></div><footer><span>桌面页面与状态 / 图内数据为标注的离线演示</span><b>{n:02d} / 52</b></footer></section>''')
# 47-52 mobile; keep readable phone-size reproduction with meaningful notes.
mobile_notes={
'home':['首要行动和选中报告优先；其余任务、事件沿单列继续。','侧栏改为菜单入口；页面目录保留快捷入口。','查看报告与任务共用同一 Run，不建移动专属副本。'],
'report':['方向、未校准、未授权在内容前部直接呈现。','分周期候选在单列继续，完整长图包含报告后半段。','证据、技术详情和全屏链路保留真实返回。'],
'calendar-all':['筛选控件换行；事件保留时间、来源与状态。','详情抽屉占用适合手机的宽度，关闭保留当前筛选。','历史发布为空时不以日程样例代替真实历史。'],
'document-review':['并排 diff 转为纵向；原文仍为只读。','逐块审核与高风险阻断保留，不以手机空间不足省略。','明确确认和质量门保持两个独立步骤。'],
'graph-node':['图谱与节点检查器上下布局，避免横向挤压。','适配、缩放和列表视图提供不依赖精确拖拽的入口。','关闭节点、退出全屏分别回到对应层级。'],
'dsh':['内部用途提示与返回研究信息优先展示。','日志列表和详情顺序排列，技术数据不代替用户结论。','整个技术路径携带相同 Run。']}
for v in [v for v in SCREENS if v['device']=='mobile']:
    n=len(sections)+1
    p=PAGES[v['id']]
    notes=''.join(f'<div class="return-item"><b>{i+1:02d}</b><p>{s}</p></div>' for i,s in enumerate(mobile_notes[v['id']]))
    fullnote=f'完整长图：{Path(v["full_path"]).name}' if v.get('full_path') else '该状态为完整单屏'
    sections.append(f'''<section class="page mobile-page" id="mobile-{v['id']}"><header><div class="brand">{img('assets/logo.svg')}<span>Decision Hub</span><small>Mobile / M{v['number']:02d}</small></div><span class="edition">实际 HTML 渲染 · 390 × 844</span></header><div class="screen-heading"><h1>{esc(v['title'])}</h1><p>同一响应式原型，不是另外一套示意图。</p></div><div class="mobile-stage">{img(v['path'],alt=v['title']+'移动版截图')}</div><div class="mobile-notes"><h2>移动端交互重点</h2>{notes}<div class="callout"><b>返回规则</b><p>{esc(p.get('return_rule',v['description']))}</p></div><p class="small">{esc(fullnote)}</p><p class="small">重点验证：指定 390px 视口无整页横向溢出。真实设备、多浏览器及完整无障碍仍需在接入阶段补测。</p></div><footer><span>移动重点视图 / 内容更长时见 screens/mobile/ 下的 full PNG</span><b>{n:02d} / 52</b></footer></section>''')
assert len(sections)==52,len(sections)
CSS=r'''
@page { size:1280px 960px; margin:0; }
*{box-sizing:border-box}html,body{margin:0;padding:0;color:#122247;font:17px/1.65 "Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif;-webkit-print-color-adjust:exact;print-color-adjust:exact}body{background:white}a{color:#2563eb;text-decoration:none}img{display:block;max-width:100%}h1,h2,h3,p{margin:0}h1{font-size:33px;line-height:1.45;letter-spacing:-.5px}h2{font-size:23px;line-height:1.5;margin:0 0 12px}p{color:#516585;margin:10px 0}code{font:14px/1.7 Consolas,monospace;overflow-wrap:anywhere}.small{font-size:14px;color:#637593}.page{position:relative;width:1280px;height:960px;break-after:page;page-break-after:always;background:#f4f8ff;overflow:hidden}.page:last-child{break-after:auto;page-break-after:auto}header{position:absolute;left:48px;right:48px;top:22px;display:flex;justify-content:space-between;align-items:center;height:40px}.brand{display:flex;align-items:center;gap:10px;font-size:18px;font-weight:700}.brand img{width:30px;height:30px}.brand small{font-size:12px;font-weight:500;color:#637593;margin-left:10px}.edition{font-size:12px;color:#637593}.heading{position:absolute;left:48px;right:48px;top:89px}.heading>p{font-size:16px;margin-top:6px}.content{position:absolute;top:191px;left:48px;right:48px;bottom:76px}footer{position:absolute;left:48px;right:48px;bottom:23px;border-top:1px solid #dfe9f7;padding-top:12px;display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#637593}.panel{background:white;border:1px solid #dfe9f7;border-radius:14px;padding:24px}.two{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:stretch}.two>.panel{min-width:0}.two p{font-size:17px}.callout{border:1px solid #d6e5fb;background:#edf5ff;border-radius:10px;padding:16px 20px;color:#315689;font-size:16px;margin-top:18px}.callout p{font-size:15px;margin:5px 0 0}.callout.amber{background:#fff6e9;border-color:#f2ddbf;color:#9c5907}.note-row{display:flex;align-items:center;gap:24px;margin-top:22px;padding:20px 24px;border-radius:12px;background:white;border:1px solid #dfe9f7}.note-row>b{white-space:nowrap;color:#2563eb}.note-row>span{color:#516585;font-size:16px}.pill{display:inline-block;border-radius:8px;color:#2563eb;background:#e8f1ff;padding:5px 12px;font-size:14px;font-weight:600}.cover-head{display:flex;justify-content:space-between;align-items:center}.cover-head h2{font-size:30px;line-height:1.5;margin:10px 0}.cover-head p{font-size:17px;line-height:1.7}.cover-counts{display:flex;gap:32px}.cover-counts>div{display:flex;flex-direction:column}.cover-counts strong{color:#2563eb;font-size:52px;line-height:1.3}.cover-counts span{font-size:14px;color:#516585}.cover-shots{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:25px}.cover-shots>div{background:white;border:1px solid #dfe9f7;border-radius:12px;padding:10px}.cover-shots img{height:325px;width:100%;object-fit:contain;background:#f3f8ff}.cover-shots span{display:block;padding:8px 10px 0;font-size:14px;color:#516585}.cover .content{top:185px}.cover .callout{font-size:14px;margin-top:14px;padding:12px 16px}table{border-collapse:collapse;width:100%;font-size:16px}td,th{text-align:left;vertical-align:top;border-bottom:1px solid #e6eef9;padding:12px 8px}th{background:#f3f7fd;font-weight:600;color:#516585}td code{font-size:12px}.source-step{padding:14px 0;border-bottom:1px solid #e6eef9}.source-step>b{color:#2563eb;font-size:18px}.source-step p{font-size:16px;margin:6px 0}.swatches{display:flex;justify-content:space-between;gap:12px;margin-bottom:24px}.swatch{width:135px;background:white;border:1px solid #dfe9f7;border-radius:10px;padding:12px}.swatch i{display:block;height:54px;border-radius:6px;border:1px solid #dfe9f7;margin-bottom:8px}.swatch b{font-size:15px;display:block}.swatch code{font-size:12px;color:#637593}.feature-icons{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin:18px 0}.feature-icons>div{display:flex;flex-direction:column;align-items:center;gap:8px}.feature-icons img{width:60px;height:60px}.feature-icons span{font-size:14px;color:#637593}.corrections{padding:10px 22px}.corrections td{padding:17px 8px;font-size:17px}.corrections td:first-child{width:170px}.corrections td:last-child{width:150px;color:#637593;font-size:14px}.flow{display:flex;align-items:center;gap:16px;margin-bottom:22px}.flow>div{flex:1;border:1px solid #ccddf9;background:#eaf3ff;border-radius:12px;padding:19px;text-align:center}.flow b{display:block;font-size:20px}.flow span{display:block;font-size:14px;color:#637593;margin-top:5px}.flow>i{font-size:24px;color:#2563eb;font-style:normal}.return-item{padding:12px 0;border-bottom:1px solid #e6eef9}.return-item b{font-size:17px;color:#2563eb}.return-item p{font-size:15px;margin:4px 0}.code-panel{background:#172949;color:#e7f0ff;border-radius:12px;padding:16px 22px;margin-top:20px;font-size:16px;line-height:1.8}.index-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.index-col{padding:18px;background:white;border:1px solid #dfe9f7;border-radius:12px}.index-col h2{font-size:18px;margin:4px 0 12px}.index-col a{display:flex;align-items:center;gap:9px;font-size:15px;padding:9px 0;border-bottom:1px solid #eef3fa;color:#122247;white-space:nowrap}.index-col a>span{font-size:12px;color:#2563eb;background:#edf4ff;border-radius:5px;padding:1px 5px}.api-group{padding:10px 0;border-bottom:1px solid #e6eef9}.api-group>b{display:block;margin-bottom:8px}.api-group code{font-size:14px;color:#516585;display:block}.mt{margin-top:22px}.qa-counts{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-bottom:24px}.qa-counts>div{padding:17px 24px;background:white;border:1px solid #dfe9f7;border-radius:12px}.qa-counts strong{font-size:45px;color:#2563eb;line-height:1.3;display:block}.qa-counts span{font-size:15px;color:#637593}.screen-heading{position:absolute;left:48px;right:48px;top:77px;display:flex;align-items:baseline;gap:22px}.screen-heading h1{font-size:25px;white-space:nowrap}.screen-heading p{font-size:13px;margin:0;color:#637593}.screen-stage{position:absolute;left:48px;right:48px;top:130px;bottom:86px;display:flex;align-items:center;justify-content:center;background:white;border:1px solid #dfe9f7;border-radius:8px;overflow:hidden}.screen-stage img{height:100%;width:100%;object-fit:contain}.screen-caption{position:absolute;left:48px;right:48px;bottom:61px;display:flex;justify-content:space-between;gap:20px;font-size:11px;color:#516585}.screen-caption code{font-size:10px;white-space:nowrap}.screenshot-page footer,.mobile-page footer{font-size:11px}.mobile-stage{position:absolute;left:100px;top:130px;bottom:75px;width:350px;display:flex;justify-content:center;align-items:center;border:1px solid #d8e4f4;border-radius:16px;overflow:hidden;background:white;box-shadow:0 10px 25px #34558815}.mobile-stage img{width:100%;height:100%;object-fit:contain}.mobile-notes{position:absolute;left:520px;right:80px;top:155px;bottom:100px}.mobile-notes .return-item{padding:20px 0}.mobile-notes .return-item p{font-size:18px;margin-top:8px}.mobile-notes .return-item b{font-size:15px}.mobile-notes .callout{margin:28px 0}.mobile-notes .small{font-size:14px}
#page-3 .panel{padding:18px}#page-3 .feature-icons{gap:12px;margin:12px 0}#page-3 .panel p{font-size:15px}#page-3 .swatch i{height:44px}#page-3 .note-row{margin-top:16px;padding:13px 20px}
'''
HTML='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Decision Hub UI Design Review V2.1</title><style>'+CSS+'</style></head><body>'+''.join(sections)+'</body></html>'
(ROOT/'source/design-review.html').write_text(HTML,encoding='utf-8')
with sync_playwright() as p:
    executable=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')
    browser=p.chromium.launch(**({'executable_path':executable} if executable else {}))
    page=browser.new_page(viewport={'width':1280,'height':960})
    page.set_content(HTML,wait_until='load',timeout=120000)
    page.evaluate('document.fonts.ready')
    page.wait_for_timeout(500)
    metrics=page.evaluate('''Array.from(document.querySelectorAll('.page')).map((p,i)=>{const content=p.querySelector('.content');return {page:i+1,scroll:p.scrollHeight,height:p.clientHeight,content_overflow:content?Math.max(0,...Array.from(content.children).map(c=>c.getBoundingClientRect().bottom-content.getBoundingClientRect().bottom)):0}})''')
    (ROOT/'qa/pdf-layout.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    bad=[m for m in metrics if m['content_overflow']>3]
    if bad:print('CONTENT OVERFLOW',bad,flush=True)
    page.pdf(path=str(ROOT/'Decision_Hub_UI_Design_Review.pdf'),print_background=True,prefer_css_page_size=True,outline=True,tagged=True)
    browser.close()
print('Built PDF with',len(sections),'design pages; source HTML retained.')
