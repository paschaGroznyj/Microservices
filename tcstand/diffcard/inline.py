"""stand.html -> out/ws_diff_mockup.html: один файл, CSS/JS/шрифты вшиты."""
import base64, re, pathlib
HERE = pathlib.Path(__file__).resolve().parent
html = (HERE / "stand.html").read_text()
def css_inline(path):
    p = (HERE / path).resolve(); s = p.read_text()
    def font(m):
        u = m.group(1)
        if u.startswith("data:"): return m.group(0)
        f = (p.parent / u).resolve()
        if not f.exists(): return m.group(0)
        return 'url("data:font/woff2;base64,' + base64.b64encode(f.read_bytes()).decode() + '")'
    return re.sub(r'url\("?([^")]+\.woff2)"?\)', font, s)
def link(m):
    href, rest = m.group(1), m.group(2) or ""
    dis = " disabled" if "disabled" in rest else ""
    idm = re.search(r'id="([^"]+)"', rest)
    return f'<style{(" id=" + chr(34) + idm.group(1) + chr(34)) if idm else ""}{" media=" + chr(34) + "not all" + chr(34) if dis else ""}>' + css_inline(href) + "</style>"
html = re.sub(r'<link rel="stylesheet" href="([^"]+)"([^>]*)>', link, html)
html = re.sub(r'<script src="([^"]+)"></script>', lambda m: "<script>" + (HERE / m.group(1)).read_text() + "</script>", html)
# переключатель темы hljs через media вместо disabled
html = html.replace("document.getElementById('hl-light').disabled=t==='dark';document.getElementById('hl-dark').disabled=t!=='dark';",
                    "document.getElementById('hl-light').media=t==='dark'?'not all':'all';document.getElementById('hl-dark').media=t!=='dark'?'not all':'all';")
(HERE / "out").mkdir(exist_ok=True)
(HERE / "out" / "ws_diff_mockup.html").write_text(html)
print(len(html))
