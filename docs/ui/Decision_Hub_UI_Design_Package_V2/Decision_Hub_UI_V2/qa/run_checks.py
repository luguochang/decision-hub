"""Run real Chromium interaction checks on the self-contained delivered HTML.
Use set_content to avoid network and file-navigation restrictions in the runner.
No real API calls, uploads, model invocations, or trading actions are performed.
"""
from pathlib import Path
import json, traceback, time, os, shutil
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'Decision_Hub_UI_Prototype.html').read_text(encoding='utf-8')
results=[]; errors=[]
def record(name,fn):
 try:
  out=fn();results.append({'name':name,'pass':True,'detail':out or ''});print('PASS',name,flush=True)
 except Exception as e:
  results.append({'name':name,'pass':False,'detail':str(e)[:1400]});print('FAIL',name,str(e)[:200],flush=True)
def check(value,msg='assertion failed'):
 if not value:raise AssertionError(msg)
with sync_playwright() as p:
 executable=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')
 b=p.chromium.launch(**({'executable_path':executable} if executable else {}))
 page=b.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
 page.set_default_timeout(4500)
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(HTML,wait_until='load')
 page.clock.install()
 def route(s,extra=''):
  page.evaluate('(h)=>location.hash=h','#/'+s+('?' + extra if extra else ''))
  page.wait_for_timeout(65)
 def reset():
  page.evaluate('DH_TEST.reset()');page.wait_for_timeout(50)
 def count():return page.evaluate('DH_TEST.getState().runs.length')
 def scene():return page.evaluate('DH_TEST.getScene()')
 def params():return page.evaluate('DH_TEST.getParams()')
 record('首页渲染，主操作与最新视觉存在',lambda:(check(page.locator('#primary-research').is_visible()),check('hero-landscape' not in page.locator('body').inner_text())))
 record('业务主导航不包含 DSH',lambda:check('DSH' not in page.locator('aside.sidebar nav').inner_text()))
 def catalog_test():
  page.locator('[data-action=catalog]').first.click();check(page.locator('[role=dialog]').is_visible());check(page.locator('.catalog-link').count()==38);page.keyboard.press('Escape');check(page.locator('[role=dialog]').count()==0)
 record('页面目录 38 项，Esc 关闭',catalog_test)
 def empty_submit():
  reset();route('compose');n=count();page.locator('#research-focus').fill('');page.locator('#submit-research').click();page.wait_for_timeout(60);check(scene()=='submitting');check(count()==n+1);state=page.evaluate('DH_TEST.getState()');r=state['runs'][0];check(r['focus']=='获取最新操作建议');check(page.locator('textarea').get_attribute('readonly') is not None);page.clock.run_for(850);check(scene()=='queued');page.clock.run_for(1500);check(scene()=='researching');page.clock.run_for(5000);check(scene()=='report');check(params()['run']==r['id']);return r['id']
 record('空输入 → 提交 → 排队 → 研究 → 同一 Run 报告',empty_submit)
 def duplicate_submit():
  reset();route('compose');page.locator('#research-focus').fill('幂等演示关注点');page.locator('#submit-research').dblclick();page.wait_for_timeout(60);check(count()==6);rid=page.evaluate('DH_TEST.getState().runs[0].id');route('compose');page.locator('#research-focus').fill('幂等演示关注点');page.locator('#submit-research').click();page.wait_for_timeout(60);check(count()==6);check(params()['run']==rid)
 record('双击与同窗口相同意图不重复创建任务',duplicate_submit)
 def leave_run():
  reset();route('compose');page.locator('#submit-research').click();page.clock.run_for(850);route('runs');page.clock.run_for(7500);check(scene()=='runs');check(page.evaluate('DH_TEST.getState().runs[0].status')=='completed')
 record('离开进度页不会取消任务或强制跳走',leave_run)
 def rerun():
  reset();route('report','run=demo-r01');page.locator('#rerun').click();page.wait_for_timeout(50);check(scene()=='compose');check(params()['parent']=='demo-r01');page.locator('#submit-research').click();page.wait_for_timeout(50);s=page.evaluate('DH_TEST.getState()');check(s['runs'][0]['parent']=='demo-r01');check(next(r for r in s['runs'] if r['id']=='demo-r01')['status']=='completed')
 record('重新研究创建子 Run，不覆盖原报告',rerun)
 def retry():
  reset();route('failed','run=demo-r04');page.locator('#research-focus').fill('修订后再核查资金流');page.locator('[data-action=retry-research]').click();page.wait_for_timeout(50);s=page.evaluate('DH_TEST.getState()');check(s['runs'][0]['focus']=='修订后再核查资金流');check(s['runs'][0]['parent']=='demo-r04');check(next(r for r in s['runs'] if r['id']=='demo-r04')['status']=='failed')
 record('失败重试保留编辑后的输入与失败原记录',retry)
 def filters():
  reset();route('runs');page.locator('#run-search').fill('BTC');page.locator('#run-status').select_option('completed');check(page.locator('#run-table tr:visible').count()==2);url=page.evaluate('location.hash');page.locator('#open-demo-r01').click();page.wait_for_timeout(50);check(params()['run']=='demo-r01');page.locator('#page-back').click();page.wait_for_timeout(50);check(scene()=='runs');check(page.locator('#run-search').input_value()=='BTC');check(page.locator('#run-status').input_value()=='completed');check(page.evaluate('location.hash')==url)
 record('任务筛选 → 报告 → 返回，关键词与状态保持',filters)
 def parent_test():
  reset();route('report-incomplete','run=demo-r05');check('结论不完整' in page.locator('#main').inner_text());ln=page.get_by_text('父任务 demo-r01',exact=True);ln.click();page.wait_for_timeout(50);check(params()['run']=='demo-r01');page.locator('#page-back').click();page.wait_for_timeout(50);check(params()['run']=='demo-r05')
 record('父子任务报告双向返回',parent_test)
 record('不存在的 Run 不替换成默认报告',lambda:(route('report','run=not-a-real-run'),check('研究任务不存在' in page.locator('#main').inner_text())))
 record('未完成 Run 不冒充完整报告',lambda:(route('report','run=demo-r02'),check('任务尚未完成' in page.locator('#main').inner_text())))
 def evidence_test():
  reset();route('report','run=demo-r01');page.locator('#open-evidence').click();page.wait_for_timeout(50);check(scene()=='evidence');check(params()['evidence']=='EV-001');check('交接稿 · 第 7 页' in page.locator('[role=dialog]').inner_text());page.keyboard.press('Escape');page.wait_for_timeout(50);check(scene()=='report');check(params()['run']=='demo-r01')
 record('报告与证据抽屉保留同一 Run',evidence_test)
 def event_test():
  route('calendar-all');page.locator('#event-market').select_option('欧元区');page.locator('#event-search').fill('CPI');check(page.locator('#event-table tr:visible').count()==1);before=page.evaluate('location.hash');page.locator('#open-event-03').click();page.wait_for_timeout(50);check('欧元区 CPI（同比）' in page.locator('[role=dialog]').inner_text());check(params()['event']=='event-03');page.keyboard.press('Escape');page.wait_for_timeout(50);check(page.evaluate('location.hash')==before);check(page.locator('#event-market').input_value()=='欧元区');check(page.locator('#event-search').input_value()=='CPI')
 record('事件按 ID 打开，关闭后保留市场与搜索筛选',event_test)
 def history():
  route('calendar-history');txt=page.locator('#main').inner_text();check('尚未采集历史发布' in txt);check(page.locator('#event-table tr').count()==0)
 record('历史发布为 0 时不复制未来事件',history)
 def unknown():
  route('home-unknown');check('未确认在线' in page.locator('#service-status').inner_text());check('心跳未确认' in page.locator('.service-strip').inner_text());page.locator('#service-status').click();page.wait_for_timeout(50);check(params()['health']=='unknown');check('未确认在线' in page.locator('#main').inner_text())
 record('未知服务与来源页不显示绿色在线',unknown)
 def permission():
  route('report','run=demo-r01');txt=page.locator('#main').inner_text();check('未校准' in txt and '未授权' in txt and 'no_trade' in txt);check('62%' not in txt and '校准正常' not in txt)
 record('方向、no_trade、校准与交易授权分离',permission)
 def graph_zoom():
  route('graph','run=demo-r01');g=page.evaluate('DH_TEST.getGraph()');page.locator('[data-action=graph-zoom-in]').click();check(page.evaluate('DH_TEST.getGraph().z')>g['z']);page.locator('[data-action=graph-fit]').click();check(abs(page.evaluate('DH_TEST.getGraph().z')-g['z'])<.01)
 record('图谱缩放与 Fit View 实际改变视口',graph_zoom)
 def graph_pan():
  c=page.locator('#graph-canvas').bounding_box();g=page.evaluate('DH_TEST.getGraph()');page.mouse.move(c['x']+60,c['y']+80);page.mouse.down();page.mouse.move(c['x']+155,c['y']+155,steps=6);page.mouse.up();g2=page.evaluate('DH_TEST.getGraph()');check(abs(g2['x']-g['x'])>70 and abs(g2['y']-g['y'])>50)
 record('图谱拖拽平移有效',graph_pan)
 def graph_keyboard():
  page.locator('#graph-canvas').focus();g=page.evaluate('DH_TEST.getGraph()');page.keyboard.press('ArrowRight');check(page.evaluate('DH_TEST.getGraph().x')>g['x']);page.keyboard.press('+');check(page.evaluate('DH_TEST.getGraph().z')>g['z'])
 record('图谱可通过键盘平移和缩放',graph_keyboard)
 def minimap():
  g=page.evaluate('DH_TEST.getGraph()');page.locator('#graph-minimap').click(position={'x':20,'y':20});g2=page.evaluate('DH_TEST.getGraph()');check(abs(g2['x']-g['x'])>10 or abs(g2['y']-g['y'])>10)
 record('Mini Map 点击定位有效',minimap)
 def graph_filter():
  page.locator('#graph-filter').select_option('evidence');check(page.locator('.graph-node:not(.hidden-node)').count()==3);page.locator('#graph-search').fill('不存在的节点');check(page.locator('#graph-empty').is_visible());page.locator('[data-action=graph-reset]').click();check(page.locator('.graph-node:not(.hidden-node)').count()==15)
 record('图谱节点筛选与真实空态',graph_filter)
 def graph_collapse():
  page.locator('[data-action=graph-collapse]').click();check(page.locator('.graph-node:not(.hidden-node)').count()==12);check(page.locator('#collapsed-tools').is_visible());page.locator('[data-action=graph-collapse]').click();check(page.locator('.graph-node:not(.hidden-node)').count()==15)
 record('工具分组折叠后保留关联证据',graph_collapse)
 def graph_list():
  page.locator('#graph-view-toggle').click();check(page.locator('#graph-list-panel').is_visible());page.locator('#graph-view-toggle').click();check(not page.locator('#graph-list-panel').is_visible())
 record('图谱列表视图提供非画布访问方式',graph_list)
 def graph_evidence():
  page.locator('[data-action=graph-fit]').click();page.locator('#node-round-2').click();page.wait_for_timeout(50);check(scene()=='graph-node');check('round-2' in page.locator('.graph-inspector').inner_text());g=page.evaluate('DH_TEST.getGraph()');page.locator('.graph-inspector #evidence-EV-003').click();page.wait_for_timeout(50);check(scene()=='evidence');page.keyboard.press('Escape');page.wait_for_timeout(50);check(scene()=='graph-node');check(params()['node']=='round-2');g2=page.evaluate('DH_TEST.getGraph()');check(abs(g2['z']-g['z'])<.01)
 record('节点 → Evidence → 返回，节点与缩放不丢失',graph_evidence)
 def scroll_return():
  reset();route('report','run=demo-r01');page.locator('#report-lower-graph').scroll_into_view_if_needed();page.wait_for_timeout(100);page.locator('#report-lower-graph').focus();y=page.evaluate('scrollY');page.locator('#report-lower-graph').click();page.wait_for_timeout(70);check(scene()=='graph');page.locator('#exit-graph').click();page.wait_for_timeout(70);check(scene()=='report');y2=page.evaluate('scrollY');check(abs(y2-y)<4,f'expected scroll {y}, got {y2}');check(page.evaluate('document.activeElement.id')=='report-lower-graph');return {'expected_scroll':y,'restored_scroll':y2}
 record('退出全屏恢复报告滚动位置和触发焦点',scroll_return)
 def dsh_return():
  route('technical','run=demo-r05');page.get_by_role('link',name='打开 DSH 技术轨迹',exact=True).click();page.wait_for_timeout(50);check(scene()=='dsh');check(params()['run']=='demo-r05');page.locator('#dsh-return').click();page.wait_for_timeout(50);check(scene()=='technical');check(params()['run']=='demo-r05')
 record('DSH 从当前 Run 进入并返回相同技术页',dsh_return)
 def log_switch():
  route('dsh','run=demo-r01');page.locator('#select-log-05').click();page.wait_for_timeout(50);check('待核验' in page.locator('.log-inspector').inner_text());check('review_started' in page.locator('#log-summary').inner_text());page.locator('#log-level').select_option('ERROR');check(page.locator('#log-table tr:visible').count()==1)
 record('日志选择对应事件，未知结果不显示成功',log_switch)
 def docs_gate():
  reset();route('document-review','doc=demo-doc');check(page.locator('#review-confirm').is_disabled());check(page.locator('#accept-blk-03').is_disabled());raw=page.locator('#original-text').inner_text();page.locator('#accept-blk-01').click();page.locator('#accept-blk-02').click();check(page.locator('#review-confirm').is_disabled());page.locator('#reject-blk-03').click();check(page.locator('#review-confirm').is_enabled());check(page.locator('#original-text').inner_text()==raw);check('概率未校准，交易权限未授权。' in page.locator('#proposed-text').inner_text())
 record('逐块审核、敏感变更阻断、原文不被覆盖',docs_gate)
 def confirm_doc():
  page.locator('#review-confirm').click();page.wait_for_timeout(50);check(scene()=='document-confirm');check(page.locator('#confirm-final').is_disabled());page.locator('#review-ack').check();check(page.locator('#confirm-final').is_enabled());page.locator('#confirm-final').click();page.wait_for_timeout(50);check(scene()=='document-confirmed');check('质量复检' in page.locator('#main').inner_text());page.clock.run_for(1700);check(scene()=='document-ready');check(page.locator('#link-document').is_enabled())
 record('明确确认 → 独立质量复检 → 允许接入',confirm_doc)
 def doc_link():
  n=count();page.locator('#link-document').click();page.wait_for_timeout(60);check(count()==n+1);s=page.evaluate('DH_TEST.getState()');r=s['runs'][0];check(r['doc']=='demo-doc');check(s['documents']['demo-doc']['linked']==r['id']);page.clock.run_for(7500);check(scene()=='report');check(params()['run']==r['id']);ln=page.get_by_role('link',name='demo-doc',exact=True);ln.click();page.wait_for_timeout(70);check(scene()=='document-linked');page.locator('#linked-report').click();page.wait_for_timeout(70);check(scene()=='report');check(params()['run']==r['id'])
 record('资料与新研究 Run 双向关联并返回',doc_link)
 def upload_validation():
  route('upload');page.locator('#upload-submit').click();check('请选择' in page.locator('#file-error').inner_text());check('请输入' in page.locator('#title-error').inner_text());page.locator('#file-input').set_input_files({'name':'demo.exe','mimeType':'application/octet-stream','buffer':b'demo'});check('不支持' in page.locator('#file-error').inner_text());page.locator('#file-input').set_input_files({'name':'research.txt','mimeType':'text/plain','buffer':'研究资料'.encode()});check(page.locator('#doc-title').input_value()=='research');page.locator('#upload-submit').click();page.wait_for_timeout(50);check(scene()=='document-uploading');page.clock.run_for(1200);check(scene()=='document-parsing');page.clock.run_for(2200);check(scene()=='document-review')
 record('上传表单真实校验与演示解析状态推进',upload_validation)
 def quality_block():
  route('document-quality','preview=1');check(page.get_by_role('button',name='结合此资料获取最新建议',exact=True).is_disabled())
 record('资料硬缺口未解决时禁止进入研究',quality_block)
 def focus_trap():
  route('calendar-all');page.locator('#open-event-01').click();page.wait_for_timeout(50);d=page.locator('[role=dialog]');els=d.locator('button:not([disabled]),a[href],input:not([disabled]),textarea,select');els.last.focus();page.keyboard.press('Tab');check(page.evaluate('document.activeElement.closest("[role=dialog]")!==null'));page.keyboard.press('Shift+Tab');check(page.evaluate('document.activeElement.closest("[role=dialog]")!==null'));page.keyboard.press('Escape')
 record('弹层焦点锁定与 Esc 恢复',focus_trap)
 def browser_back():
  reset();route('runs','q=BTC');page.locator('#open-demo-r01').click();page.wait_for_timeout(60);check(scene()=='report');page.go_back();page.wait_for_timeout(60);check(scene()=='runs');check(page.locator('#run-search').input_value()=='BTC');page.go_forward();page.wait_for_timeout(60);check(scene()=='report')
 record('浏览器后退与前进保持真实路由',browser_back)
 def coverage():
  bad=[];missing=[]
  pages=page.evaluate('DH_PAGES')
  for s,_,_ in pages:
   ex='preview=1&run='+('demo-r05' if s=='report-incomplete' else 'demo-r01')+('&event=event-01' if s=='event' else '')
   route(s,ex)
   if page.evaluate('document.documentElement.scrollWidth>innerWidth+1'):bad.append(s)
   if page.locator('h1').count()==0:missing.append(s)
  check(not bad,'desktop overflow: '+str(bad));check(not missing,'missing page headings: '+str(missing));return {'desktop_routes':len(pages),'overflow':bad}
 record('38 个桌面页面与状态均渲染且无整页横向溢出',coverage)
 def mobile():
  page.set_viewport_size({'width':390,'height':844});bad=[]
  for s in ['home','report','calendar-all','document-review','graph-node','dsh']:
   route(s,'preview=1&run=demo-r01'+('&node=round-2' if s=='graph-node' else ''))
   if page.evaluate('document.documentElement.scrollWidth>innerWidth+1'):bad.append(s)
  check(not bad,'mobile overflow: '+str(bad));return {'viewport':'390x844','routes':6}
 record('6 个移动视图无整页横向溢出',mobile)
 def accessible_names():
  page.set_viewport_size({'width':1440,'height':1080});bad=[]
  for s in ['home','compose','calendar-all','report','graph-node','document-review','dsh']:
   route(s,'preview=1&run=demo-r01')
   missing=page.evaluate('''Array.from(document.querySelectorAll('button,a[href]')).filter(e=>e.getClientRects().length&&!e.textContent.trim()&&!e.getAttribute('aria-label')&&!e.getAttribute('title')).map(e=>e.outerHTML.slice(0,140))''')
   if missing:bad.append({s:missing})
  check(not bad,str(bad))
 record('抽样页面所有可见图标操作具备可访问名称',accessible_names)
 record('测试期间无未处理 JavaScript 异常',lambda:check(not errors,str(errors)))
 b.close()
report={'runner':'Chromium + Playwright / set_content / no network','checks':len(results),'passed':sum(x['pass'] for x in results),'failed':sum(not x['pass'] for x in results),'page_errors':errors,'results':results}
(ROOT/'qa/results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2))

raise SystemExit(1 if report["failed"] else 0)
