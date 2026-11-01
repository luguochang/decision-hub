"""Bundle local editable sources and the decorative crop into one offline HTML.
Run: python tools/build_standalone.py (standard library only).
"""
from pathlib import Path
import re,base64
r=Path(__file__).resolve().parents[1]
h=(r/'index.html').read_text()
css=(r/'source/tokens.css').read_text()+'\n'+(r/'source/app.css').read_text()
bg=base64.b64encode((r/'assets/backgrounds/hero-landscape.png').read_bytes()).decode()
css=css.replace("url('../assets/backgrounds/hero-landscape.png')",f"url('data:image/png;base64,{bg}')")
h=re.sub(r'<link rel="stylesheet"[^>]+>','',h)
h=h.replace('</head>','<style>'+css+'</style></head>')
h=re.sub(r'<link rel="icon"[^>]+>','',h)
for n in ['icons','fixtures','app']:
 s=(r/f'source/{n}.js').read_text().replace('</script','<\\/script')
 h=h.replace(f'<script src="source/{n}.js"></script>','<script>\n'+s+'\n</script>')
(r/'Decision_Hub_UI_Prototype.html').write_text(h)
print('Standalone HTML',len(h),'characters')
