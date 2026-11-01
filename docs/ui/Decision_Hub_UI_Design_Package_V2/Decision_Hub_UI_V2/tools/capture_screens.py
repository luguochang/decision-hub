"""Capture the real, offline HTML prototype, not image-only mockups.
Install: pip install playwright; playwright install chromium
Optional: CHROMIUM_PATH=/path/to/chromium python tools/capture_screens.py
"""
from pathlib import Path
import json, os, shutil
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'Decision_Hub_UI_Prototype.html').read_text(encoding='utf-8')
manifest=[]
with sync_playwright() as p:
    executable=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')
    browser=p.chromium.launch(**({'executable_path':executable} if executable else {}))
    page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1,reduced_motion='reduce')
    page.set_content(HTML,wait_until='load')
    page.evaluate('document.fonts.ready')
    pages=page.evaluate('DH_PAGES')
    old_index={v['id']:v for v in json.loads((ROOT/'docs/page-index.json').read_text(encoding='utf-8'))} if (ROOT/'docs/page-index.json').exists() else {}
    (ROOT/'docs/page-index.json').write_text(json.dumps([{**old_index.get(i,{}),'id':i,'title':t,'description':d} for i,t,d in pages],ensure_ascii=False,indent=2),encoding='utf-8')
    targets=[(i+1,'desktop',s,t,d,1440,1080) for i,(s,t,d) in enumerate(pages)]
    targets += [(i+1,'mobile',s,next(v[1] for v in pages if v[0]==s),next(v[2] for v in pages if v[0]==s),390,844) for i,s in enumerate(['home','report','calendar-all','document-review','graph-node','dsh'])]
    for num,device,scene,title,desc,w,h in targets:
        page.set_viewport_size({'width':w,'height':h})
        page.evaluate('DH_TEST.reset()')
        qp={'preview':'1','run':'demo-r05' if scene=='report-incomplete' else 'demo-r01'}
        if scene=='event': qp['event']='event-01'
        if scene=='graph-node': qp['node']='round-2'
        if scene=='evidence': qp['evidence']='EV-001'
        route='#/'+scene+'?'+urlencode(qp)
        page.evaluate('(hash)=>location.hash=hash',route)
        page.wait_for_timeout(200)
        page.evaluate('document.fonts.ready')
        path=ROOT/'screens'/device/f'{num:02d}_{scene}.png'
        page.screenshot(path=str(path),full_page=False,animations='disabled')
        height=page.evaluate('document.documentElement.scrollHeight')
        entry={'number':num,'device':device,'id':scene,'title':title,'description':desc,'route':page.evaluate('location.hash'),'viewport':[w,h],'path':str(path.relative_to(ROOT)),'content_height':height}
        if height>h+24:
            full=path.with_name(path.stem+'_full.png')
            page.screenshot(path=str(full),full_page=True,animations='disabled')
            entry['full_path']=str(full.relative_to(ROOT))
        manifest.append(entry)
        print(device,scene,w,h,'body',height,flush=True)
    browser.close()
(ROOT/'docs/screen-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print('Saved',len(manifest),'screen entries')
